"""步骤 02：校验补回的 SHFE 20250526 日频数据。

校验维度：
1. 结构校验：字段齐全、行数非空、`trade_date` 全部等于目标日；
2. 勾稽校验：`high >= max(open, close, low)`、`low <= min(open, close, high)`、`vol >= 0`、`oi >= 0`；
3. 连续性校验：`pre_close` 应等于上一交易日（20250523）的 `close`，`pre_settle` 应等于上一日 `settle`；
4. 涨跌校验：Tushare 的 `change1` / `change2` 均以 `pre_settle` 为基准
   （`change1 = close - pre_settle`、`change2 = settle - pre_settle`），`oi_chg ≈ oi - 上日 oi`；
5. 官方源交叉校验：与仓库内已有的上期所官网结算参数
   `db/rules_ingestion/data/raw/exchange_settlement_data_scraping/exchange=SHFE/trading_day=20250526`
   中的 SETTLEMENTPRICE 逐合约比对。

用法（在仓库根目录执行）：
    python db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/02_validate_shfe_20250526.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from task_common import (  # noqa: E402
    SHFE_SETTLEMENT_DIR,
    TARGET_DATE,
    TARGET_EXCHANGE,
    TARGET_TRADE_DATE,
    TUSHARE_RAW_DIR,
    log,
    raw_csv_path,
    repo_relative,
)


VALIDATION_PATH = Path(__file__).resolve().parent / "02_validation_report.json"
PRIOR_TRADE_DATE = "20250523"


def load_daily(trade_date: str) -> pd.DataFrame:
    path = raw_csv_path(TARGET_EXCHANGE, trade_date)
    frame = pd.read_csv(path, encoding="utf-8", dtype={"trade_date": str})
    return frame


def check_structure(frame: pd.DataFrame) -> dict[str, Any]:
    issues: list[str] = []
    if frame.empty:
        issues.append("数据为空")
    bad_dates = sorted(set(frame["trade_date"].astype(str)) - {TARGET_TRADE_DATE})
    if bad_dates:
        issues.append(f"存在非目标交易日数据：{bad_dates}")
    null_cells = int(frame.isna().sum().sum())
    return {
        "rows": int(len(frame)),
        "columns": list(frame.columns),
        "distinct_trade_date": sorted(set(frame["trade_date"].astype(str))),
        "null_cells": null_cells,
        "ts_code_unique": int(frame["ts_code"].nunique()),
        "exchange_suffix_values": sorted(set(frame["ts_code"].astype(str).str.rsplit(".", n=1).str[-1])),
        "issues": issues,
    }


def check_ohlc(frame: pd.DataFrame) -> list[str]:
    issues: list[str] = []
    upper = frame[["open", "close"]].max(axis=1)
    lower = frame[["open", "close"]].min(axis=1)
    bad_high = frame[frame["high"] < upper]
    bad_low = frame[frame["low"] > lower]
    if len(bad_high):
        issues.append(f"high < max(open, close) 的行数：{len(bad_high)}")
    if len(bad_low):
        issues.append(f"low > min(open, close) 的行数：{len(bad_low)}")
    if (frame["vol"] < 0).any():
        issues.append("存在 vol < 0")
    if (frame["oi"] < 0).any():
        issues.append("存在 oi < 0")
    if (frame[["open", "high", "low", "close", "settle"]] <= 0).any().any():
        issues.append("存在非正价格字段")
    return issues


def check_continuity(frame: pd.DataFrame) -> dict[str, Any]:
    """用上一交易日的收盘/结算/持仓校验 pre_close / pre_settle / oi_chg。"""
    result: dict[str, Any] = {"prior_trade_date": PRIOR_TRADE_DATE}
    prior_path = raw_csv_path(TARGET_EXCHANGE, PRIOR_TRADE_DATE)
    if not prior_path.exists():
        result["issues"] = [f"缺少上一交易日 raw 文件：{repo_relative(prior_path)}"]
        return result

    prior = pd.read_csv(prior_path, encoding="utf-8")
    merged = frame.merge(prior, on="ts_code", how="inner", suffixes=("", "_prior"))
    issues: list[str] = []

    if merged.empty:
        result["issues"] = ["与上一交易日 ts_code 无交集"]
        return result

    pre_close_diff = (merged["pre_close"] - merged["close_prior"]).abs()
    pre_settle_diff = (merged["pre_settle"] - merged["settle_prior"]).abs()
    change1_diff = (merged["change1"] - (merged["close"] - merged["pre_settle"])).abs()
    change2_diff = (merged["change2"] - (merged["settle"] - merged["pre_settle"])).abs()

    oi_prior_ok = merged["oi_prior"].notna()
    oi_chg_diff = (merged.loc[oi_prior_ok, "oi_chg"] - (merged.loc[oi_prior_ok, "oi"] - merged.loc[oi_prior_ok, "oi_prior"])).abs()

    result.update(
        {
            "compared_rows": int(len(merged)),
            "max_abs_pre_close_diff": float(pre_close_diff.max()),
            "max_abs_pre_settle_diff": float(pre_settle_diff.max()),
            "max_abs_change1_diff": float(change1_diff.max()),
            "max_abs_change2_diff": float(change2_diff.max()),
            "max_abs_oi_chg_diff": float(oi_chg_diff.max()) if len(oi_chg_diff) else None,
            "issues": issues,
        }
    )
    for key, tol in (
        ("max_abs_pre_close_diff", 1e-6),
        ("max_abs_pre_settle_diff", 1e-6),
        ("max_abs_change1_diff", 1e-6),
        ("max_abs_change2_diff", 1e-6),
        ("max_abs_oi_chg_diff", 1e-6),
    ):
        value = result.get(key)
        if value is None:
            continue
        if value > tol:
            issues.append(f"{key}={value} 超过容差 {tol}")
    return result


def check_against_official_settlement(frame: pd.DataFrame) -> dict[str, Any]:
    """与上期所官网结算参数中的 SETTLEMENTPRICE 交叉校验。"""
    day_dir = SHFE_SETTLEMENT_DIR / f"trading_day={TARGET_TRADE_DATE}"
    csv_path = day_dir / f"Settlement{TARGET_TRADE_DATE}.csv"
    result: dict[str, Any] = {
        "official_source": repo_relative(day_dir),
        "official_csv": repo_relative(csv_path),
        "official_csv_exists": csv_path.exists(),
    }
    if not csv_path.exists():
        result["issues"] = ["官方结算参数文件不存在，无法交叉校验"]
        return result

    official = pd.read_csv(csv_path)
    result["official_rows"] = int(len(official))

    # 官方合约号为小写无点格式（cu2506）；Tushare 为大写带交易所后缀（CU2506.SHF）
    official["ts_code"] = (
        official["INSTRUMENTID"].astype(str).str.strip().str.upper() + ".SHF"
    )
    official = official[["ts_code", "SETTLEMENTPRICE"]].rename(columns={"SETTLEMENTPRICE": "official_settle"})

    merged = frame.merge(official, on="ts_code", how="inner")
    issues: list[str] = []
    result.update(
        {
            "matched_rows": int(len(merged)),
            "tushare_rows_without_official": int(len(frame) - len(merged)),
        }
    )
    if merged.empty:
        issues.append("与官方结算价无任何匹配合约")
    else:
        diff = (merged["settle"] - merged["official_settle"]).abs()
        mismatched = merged.loc[diff > 1e-6, ["ts_code", "settle", "official_settle"]]
        result.update(
            {
                "max_abs_settle_diff": float(diff.max()),
                "mismatch_rows": int(len(mismatched)),
                "mismatch_samples": mismatched.head(10).to_dict(orient="records"),
            }
        )
        if len(mismatched):
            issues.append(f"{len(mismatched)} 个合约结算价与上期所官网不一致")

    # 官网结算参数里的组合/总表行（instrument 含 '.'）在 Tushare 中不存在，属于预期差异
    result["issues"] = issues
    return result


def main() -> None:
    log(f"校验 {TUSHARE_RAW_DIR.name}/{TARGET_EXCHANGE}/trade_date={TARGET_TRADE_DATE}.csv")
    frame = load_daily(TARGET_TRADE_DATE)

    report: dict[str, Any] = {
        "target": {"exchange": TARGET_EXCHANGE, "trade_date": TARGET_TRADE_DATE, "date": TARGET_DATE},
        "source_csv": repo_relative(raw_csv_path(TARGET_EXCHANGE, TARGET_TRADE_DATE)),
    }
    structure = check_structure(frame)
    report["structure"] = structure
    report["ohlc_issues"] = check_ohlc(frame)
    report["continuity"] = check_continuity(frame)
    report["official_settlement_cross_check"] = check_against_official_settlement(frame)

    issues = list(structure["issues"]) + report["ohlc_issues"] + report["continuity"].get("issues", [])
    issues += report["official_settlement_cross_check"].get("issues", [])
    report["all_issues"] = issues
    report["passed"] = not issues

    VALIDATION_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    log(f"结构：rows={structure['rows']} ts_code_unique={structure['ts_code_unique']}")
    log(f"连续性：compared_rows={report['continuity'].get('compared_rows')} "
        f"max|pre_close-prev_close|={report['continuity'].get('max_abs_pre_close_diff')} "
        f"max|pre_settle-prev_settle|={report['continuity'].get('max_abs_pre_settle_diff')}")
    cross = report["official_settlement_cross_check"]
    log(f"官网结算价交叉校验：matched={cross.get('matched_rows')} mismatch={cross.get('mismatch_rows')} "
        f"max_abs_diff={cross.get('max_abs_settle_diff')}")
    log(f"校验结论：{'通过' if report['passed'] else '存在问题 -> ' + '; '.join(issues)}")
    log(f"报告：{VALIDATION_PATH}")

    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()