"""步骤 03：重写并覆盖 cleaned 目录下的 SHFE 各品种日频快照表。

设计要点：
- 直接复用 `cleaned/clean_fut_daily_snapshots.py` 的清洗逻辑（importlib 加载），
  避免在补数任务里复制一份会漂移的清洗代码；
- 为了能在不破坏其他交易所产物的前提下重跑，把 `OUTPUT_DIR` 指向任务目录下的临时目录，
  做一次**全量**重跑，然后：
    1. 逐个比对非 SHFE 产物的 instrument/date/数值是否与现有 cleaned 完全一致（证明无副作用）；
    2. 仅把 SHFE_*.parquet 覆盖回 `cleaned/`，并额外刷新 `AUDIT.md`
       （原 AUDIT.md 把 trade_date=20250526.csv 列为"空文件跳过"，重跑后已不再成立）；
    3. 覆盖前把旧的 SHFE_*.parquet 备份到任务目录，并生成新旧差异报告。

用法（在仓库根目录执行）：
    python db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/03_rebuild_shfe_cleaned_parquet.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from task_common import (  # noqa: E402
    CLEANED_DIR,
    TARGET_DATE,
    TARGET_EXCHANGE,
    TARGET_TRADE_DATE,
    TASK_DIR,
    log,
    raw_csv_path,
    repo_relative,
)


CLEAN_SCRIPT = CLEANED_DIR / "clean_fut_daily_snapshots.py"
TEMP_REBUILD_DIR = TASK_DIR / "_full_rebuild_temp"
BACKUP_DIR = TASK_DIR / "backup_before_rebuild" / "SHFE_parquet"
SUMMARY_PATH = TASK_DIR / "03_rebuild_summary.json"

COMPARE_KEYS = [
    "pre_close",
    "pre_settle",
    "open",
    "high",
    "low",
    "close",
    "settle",
    "change1",
    "change2",
    "vol",
    "amount",
    "oi",
    "oi_chg",
    "upper_limit",
    "lower_limit",
]


def load_clean_module() -> Any:
    spec = importlib.util.spec_from_file_location("clean_fut_daily_snapshots", CLEAN_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载清洗脚本：{CLEAN_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def backup_existing_shfe() -> list[str]:
    """把当前 cleaned 下的 SHFE parquet 备份到任务目录。

    已存在的备份文件不会被覆盖，保证最早那次（补数前）的备份始终可用作基线。
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backed_up: list[str] = []
    for path in sorted(CLEANED_DIR.glob("SHFE_*.parquet")):
        target = BACKUP_DIR / path.name
        if target.exists():
            backed_up.append(path.name)
            continue
        shutil.copy2(path, target)
        backed_up.append(path.name)
    return backed_up


def compare_frames(old: pd.DataFrame, new: pd.DataFrame) -> dict[str, Any]:
    """按 instrument+date 对齐比较两份日频快照。"""
    keys = ["instrument", "date"]
    old_sorted = old.sort_values(keys).reset_index(drop=True)
    new_sorted = new.sort_values(keys).reset_index(drop=True)

    added = new_sorted.merge(old_sorted[keys], on=keys, how="left", indicator=True)
    added_keys = added.loc[added["_merge"] == "left_only", keys]
    removed = old_sorted.merge(new_sorted[keys], on=keys, how="left", indicator=True)
    removed_keys = removed.loc[removed["_merge"] == "left_only", keys]

    aligned = old_sorted.merge(new_sorted, on=keys, how="inner", suffixes=("_old", "_new"))
    changed_columns: dict[str, int] = {}
    for column in COMPARE_KEYS:
        old_col = aligned[f"{column}_old"]
        new_col = aligned[f"{column}_new"]
        differs = ~(
            (old_col.isna() & new_col.isna())
            | (old_col == new_col)
        )
        if bool(differs.any()):
            changed_columns[column] = int(differs.sum())

    return {
        "old_rows": int(len(old_sorted)),
        "new_rows": int(len(new_sorted)),
        "added_instrument_date": int(len(added_keys)),
        "removed_instrument_date": int(len(removed_keys)),
        "added_samples": added_keys.head(20).astype(str).to_dict(orient="records"),
        "removed_samples": removed_keys.head(20).astype(str).to_dict(orient="records"),
        "changed_value_columns": changed_columns,
    }


def temp_rebuild_is_fresh() -> bool:
    """校验临时全量重跑结果是否不早于补数后的 raw 文件，避免复用过期结果。"""
    parquets = list(TEMP_REBUILD_DIR.glob("*.parquet"))
    if not parquets:
        return False
    raw_file = raw_csv_path(TARGET_EXCHANGE, TARGET_TRADE_DATE)
    oldest_temp = min(path.stat().st_mtime for path in parquets)
    return raw_file.exists() and oldest_temp >= raw_file.stat().st_mtime


def output_summary_from_dir(directory: Path) -> pd.DataFrame:
    """从重跑输出目录反推 `merge_and_write` 的 output_summary。

    这些 parquet 就是 `merge_and_write` 刚写出的文件，因此重建出的汇总表与
    直接使用函数返回值等价；这样 `--reuse-temp` 也能渲染出与全量重跑一致的 AUDIT.md。
    """
    rows = []
    for path in sorted(directory.glob("*.parquet")):
        frame = pd.read_parquet(path, columns=["exchange", "product", "date", "instrument", "upper_limit"])
        rows.append(
            {
                "file": path.name,
                "exchange": frame["exchange"].iloc[0],
                "product": frame["product"].iloc[0],
                "rows": len(frame),
                "instruments": frame["instrument"].nunique(),
                "min_date": frame["date"].min(),
                "max_date": frame["date"].max(),
                "missing_limit_rows": int(frame["upper_limit"].isna().sum()),
            }
        )
    return pd.DataFrame(rows).sort_values(["exchange", "product"]).reset_index(drop=True)


def load_summary_json() -> dict[str, Any]:
    """读取上一次运行留下的摘要（--reuse-temp 时用于取回全量重跑的审计数据）。"""
    if not SUMMARY_PATH.exists():
        raise RuntimeError(f"--reuse-temp 需要 {SUMMARY_PATH} 中的上一次运行记录，但该文件不存在")
    return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild and overwrite SHFE cleaned daily snapshot parquets.")
    parser.add_argument(
        "--no-audit-refresh",
        action="store_true",
        help="不覆盖 cleaned/AUDIT.md（默认会一并刷新）。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做全量重跑与比对，不覆盖 cleaned 下的文件。",
    )
    parser.add_argument(
        "--reuse-temp",
        action="store_true",
        help="复用已存在的 _full_rebuild_temp 结果，跳过耗时的全量重跑（会校验其新鲜度）。",
    )
    parser.add_argument(
        "--shfe-baseline-dir",
        type=Path,
        default=None,
        help=(
            "SHFE 产物差异比对的基线目录，默认为 cleaned 目录。"
            "补数后重跑时应指向补数前的备份目录；非 SHFE 产物始终与 cleaned 目录比对。"
        ),
    )
    args = parser.parse_args()

    shfe_baseline_dir: Path = args.shfe_baseline_dir or CLEANED_DIR

    module = load_clean_module()
    # 把输出重定向到任务目录，避免在比对完成前动到 cleaned 里的现有产物
    module.OUTPUT_DIR = TEMP_REBUILD_DIR
    module.AUDIT_PATH = TEMP_REBUILD_DIR / "AUDIT.md"

    reuse = bool(args.reuse_temp) and temp_rebuild_is_fresh()
    if args.reuse_temp and not reuse:
        log("--reuse-temp 不可用（临时目录缺失或早于补数后的 raw 文件），改为全量重跑")

    if reuse:
        log(f"复用已存在的全量重跑结果：{TEMP_REBUILD_DIR}")
        cached = load_summary_json()
        tushare_audit = cached["full_rerun_audit"]["tushare"]
        bigquant_audit = cached["full_rerun_audit"]["bigquant"]
        merge_audit = cached["full_rerun_audit"]["merge"]
    else:
        if TEMP_REBUILD_DIR.exists():
            shutil.rmtree(TEMP_REBUILD_DIR)
        TEMP_REBUILD_DIR.mkdir(parents=True, exist_ok=True)
        log("开始全量重跑清洗流水线（输出到临时目录）…")
        registry = module.load_registry()
        bars, tushare_audit = module.load_tushare_bars(registry)
        limits, bigquant_audit = module.translate_bigquant_limits(registry)
        output_summary, merge_audit = module.merge_and_write(bars, limits)
        log(
            f"重跑完成：tushare mapped={tushare_audit['mapped_rows']} "
            f"limits={bigquant_audit['translated_unique_instrument_date_rows']} "
            f"rows={merge_audit['merged_rows']} files={merge_audit['output_files']}"
        )

    # 渲染与全量重跑一致的 AUDIT.md（render_audit 只有在 clean 脚本的 main() 里才会被调用）
    output_summary = output_summary_from_dir(TEMP_REBUILD_DIR)
    TEMP_REBUILD_DIR.mkdir(parents=True, exist_ok=True)
    module.AUDIT_PATH.write_text(
        module.render_audit(tushare_audit, bigquant_audit, merge_audit, output_summary),
        encoding="utf-8",
    )
    log(f"已渲染临时 AUDIT.md：{module.AUDIT_PATH}")

    # 非 SHFE 产物一致性核对：证明本次只影响 SHFE（这些文件本次不会被写入）
    side_effects: list[dict[str, Any]] = []
    for path in sorted(TEMP_REBUILD_DIR.glob("*.parquet")):
        if path.name.startswith(f"{TARGET_EXCHANGE}_"):
            continue
        current = CLEANED_DIR / path.name
        if not current.exists():
            side_effects.append({"file": path.name, "status": "missing_in_cleaned"})
            continue
        diff = compare_frames(pd.read_parquet(current), pd.read_parquet(path))
        identical = (
            diff["added_instrument_date"] == 0
            and diff["removed_instrument_date"] == 0
            and not diff["changed_value_columns"]
        )
        if not identical:
            side_effects.append({"file": path.name, "status": "differs", "diff": diff})
    log(f"非 {TARGET_EXCHANGE} 产物差异数：{len(side_effects)}")

    # SHFE 新旧差异
    shfe_diff: dict[str, Any] = {}
    new_shfe_files: list[str] = []
    for path in sorted(TEMP_REBUILD_DIR.glob(f"{TARGET_EXCHANGE}_*.parquet")):
        new_shfe_files.append(path.name)
        current = shfe_baseline_dir / path.name
        if not current.exists():
            shfe_diff[path.name] = {"status": "new_file"}
            continue
        diff = compare_frames(pd.read_parquet(current), pd.read_parquet(path))
        new_frame = pd.read_parquet(path)
        new_frame["date"] = pd.to_datetime(new_frame["date"])
        target_ts = pd.Timestamp(TARGET_DATE)
        diff["rows_on_target_date"] = int((new_frame["date"] == target_ts).sum())
        diff["instruments_on_target_date"] = int(
            new_frame.loc[new_frame["date"] == target_ts, "instrument"].nunique()
        )
        diff["rows_without_limit_prices"] = int(new_frame["upper_limit"].isna().sum())
        shfe_diff[path.name] = diff

    total_added = sum(
        d.get("added_instrument_date", 0)
        for d in shfe_diff.values()
        if isinstance(d, dict)
    )
    total_removed = sum(
        d.get("removed_instrument_date", 0)
        for d in shfe_diff.values()
        if isinstance(d, dict)
    )
    changed_cols = sorted(
        {
            col
            for d in shfe_diff.values()
            if isinstance(d, dict)
            for col in d.get("changed_value_columns", {})
        }
    )

    skipped_files = list(tushare_audit["empty_or_unreadable_files"])
    # AUDIT.md 里已经完整列出被跳过的空文件与未映射 ts_code，摘要只保留计数与样本
    slim_tushare_audit = {
        key: value
        for key, value in tushare_audit.items()
        if key not in {"empty_or_unreadable_files", "top_unmapped_ts_code"}
    }
    slim_tushare_audit["empty_or_unreadable_file_count"] = len(skipped_files)
    slim_tushare_audit["empty_or_unreadable_file_sample"] = skipped_files[:10]
    slim_tushare_audit["top_unmapped_ts_code_count"] = len(tushare_audit["top_unmapped_ts_code"])
    slim_tushare_audit["top_unmapped_ts_code_sample"] = tushare_audit["top_unmapped_ts_code"][:10]

    summary: dict[str, Any] = {
        "step": "03_rebuild_shfe_cleaned",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "target": {"exchange": TARGET_EXCHANGE, "trade_date": TARGET_TRADE_DATE, "date": TARGET_DATE},
        "full_rerun_audit": {
            "tushare": slim_tushare_audit,
            "bigquant": bigquant_audit,
            "merge": merge_audit,
            # 数千条被跳过的空文件路径只留计数与样本，避免摘要 JSON 膨胀到 MB 级
            "still_skipped_empty_file_count": len(skipped_files),
            "still_skipped_empty_file_sample": skipped_files[:10],
        },
        "non_shfe_side_effects": side_effects,
        "shfe_before_after": shfe_diff,
        "shfe_totals": {
            "files": len(new_shfe_files),
            "added_instrument_date": total_added,
            "removed_instrument_date": total_removed,
            "changed_value_columns": changed_cols,
        },
        "cleaned_dir": repo_relative(CLEANED_DIR),
        "shfe_baseline_dir": repo_relative(shfe_baseline_dir),
        "reused_temp_rebuild": reuse,
    }

    if args.dry_run:
        summary["status"] = "dry_run"
        SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        log(f"dry-run：未覆盖任何文件。摘要：{SUMMARY_PATH}")
        return

    backed_up = backup_existing_shfe()
    log(f"已备份旧 {TARGET_EXCHANGE} 产物 {len(backed_up)} 个文件到 {BACKUP_DIR}")

    for name in new_shfe_files:
        shutil.copy2(TEMP_REBUILD_DIR / name, CLEANED_DIR / name)
    log(f"已覆盖 {len(new_shfe_files)} 个 {TARGET_EXCHANGE} parquet 到 {CLEANED_DIR}")

    audit_refreshed = False
    if not args.no_audit_refresh and (TEMP_REBUILD_DIR / "AUDIT.md").exists():
        shutil.copy2(TEMP_REBUILD_DIR / "AUDIT.md", CLEANED_DIR / "AUDIT.md")
        audit_refreshed = True
        log("已刷新 cleaned/AUDIT.md")

    summary.update(
        {
            "status": "ok",
            "backed_up_files": backed_up,
            "backup_dir": repo_relative(BACKUP_DIR),
            "overwritten_files": new_shfe_files,
            "audit_md_refreshed": audit_refreshed,
            "temp_rebuild_dir": repo_relative(TEMP_REBUILD_DIR),
        }
    )
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    log(
        f"SHFE 差异：新增 instrument+date {total_added} 组，删除 {total_removed} 组，"
        f"数值变化列：{changed_cols or '无'}"
    )
    log(f"摘要：{SUMMARY_PATH}")


if __name__ == "__main__":
    main()