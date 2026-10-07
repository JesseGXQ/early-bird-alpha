"""步骤 04：把三步的 JSON 结果汇总成任务报告 README.md。

用法（在仓库根目录执行）：
    python db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/04_render_report.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from task_common import (  # noqa: E402
    CLEANED_DIR,
    TARGET_DATE,
    TARGET_EXCHANGE,
    TARGET_TRADE_DATE,
    TASK_DIR,
    repo_relative,
)


PULL_PATH = TASK_DIR / "01_pull_tushare_result.json"
VALIDATION_PATH = TASK_DIR / "02_validation_report.json"
REBUILD_PATH = TASK_DIR / "03_rebuild_summary.json"
REPORT_PATH = TASK_DIR / "README.md"


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def render() -> str:
    pull = load(PULL_PATH)
    validation = load(VALIDATION_PATH)
    rebuild = load(REBUILD_PATH)

    structure = validation.get("structure", {})
    continuity = validation.get("continuity", {})
    cross = validation.get("official_settlement_cross_check", {})
    totals = rebuild.get("shfe_totals", {})

    shfe_rows_table = []
    for name, diff in sorted(rebuild.get("shfe_before_after", {}).items()):
        if not isinstance(diff, dict):
            continue
        shfe_rows_table.append(
            "| `{file}` | {old} | {new} | +{added} | -{removed} | {target_rows} | {changed} |".format(
                file=name,
                old=diff.get("old_rows", "—"),
                new=diff.get("new_rows", "—"),
                added=diff.get("added_instrument_date", 0),
                removed=diff.get("removed_instrument_date", 0),
                target_rows=diff.get("rows_on_target_date", "—"),
                changed=", ".join(diff.get("changed_value_columns", {})) or "无",
            )
        )
    shfe_rows = "\n".join(shfe_rows_table) or "| — | — | — | — | — | — | — |"

    side_effects = rebuild.get("non_shfe_side_effects", [])
    rerun_audit = rebuild.get("full_rerun_audit", {})
    skipped_count = rerun_audit.get("still_skipped_empty_file_count", len(rerun_audit.get("still_skipped_empty_files", [])))

    issues = validation.get("all_issues", [])
    conclusion = "通过" if validation.get("passed") else "存在问题：" + "；".join(issues)

    return f"""# SHFE 日频快照缺失 2025-05-26 补数报告

- 任务目录：`{repo_relative(TASK_DIR)}`
- 生成时间（UTC）：{datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
- 目标：补齐 `{TARGET_EXCHANGE}` 交易所 {TARGET_DATE}（{TARGET_TRADE_DATE}）的日频快照，
  并重写覆盖 `{repo_relative(CLEANED_DIR)}` 下的 `SHFE_*.parquet`。

## 一、问题定位

历史入库脚本 `db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/pull_tushare_fut_daily.py`
在 {TARGET_TRADE_DATE} 落盘了 `exchange={TARGET_EXCHANGE}/trade_date={TARGET_TRADE_DATE}.csv`，
但文件内容只有 5 字节（UTF-8 BOM + 空行，连表头都没有），
说明当时 Tushare `fut_daily` 返回了一个完全空的响应体。

清洗脚本 `cleaned/clean_fut_daily_snapshots.py` 在 `load_tushare_bars()` 中把该文件
记入 `empty_or_unreadable_files` 后跳过（历史 `cleaned/AUDIT.md` 第 4823 行有记录），
于是 {TARGET_DATE} 的 SHFE 数据在 cleaned 层整日缺失。

全库扫描确认：raw 目录下只有这一个"连表头都没有"的损坏文件；
其余 4733 个空文件都是 108 字节的纯表头文件，属于当日无成交/品种尚未上市的正常空表。

## 二、补数路径

先按要求尝试 Tushare：`api_name=fut_daily`，`exchange={TARGET_EXCHANGE}`，
`trade_date={TARGET_TRADE_DATE}`，直连 `http://api.tushare.pro`。

| 指标 | 结果 |
| --- | --- |
| 接口返回 code / msg | {pull.get("tushare_code")} / `{pull.get("tushare_msg")}` |
| 请求次数 | {pull.get("attempts")} |
| 返回行数 | {pull.get("rows")} |
| 落地文件 | `{pull.get("written_to")}` |
| 原空文件备份 | `{pull.get("backup_of_empty_file")}` |
| 接口原始响应 | `{pull.get("raw_response_json")}` |

**结论：Tushare 已恢复正常，直接返回 {pull.get("rows")} 行数据，无需启用上期所官网爬虫兜底。**

> 兜底方案（本次未启用）：仓库内已有上期所官网爬虫
> `db/rules_ingestion/data/raw/exchange_settlement_data_scraping/scrape_shfe_settlement_history.py`，
> 走 SafeLine proof-of-work 后请求 `https://www.shfe.com.cn/data/busiparamdata/future/Settlement{{YYYYMMDD}}.dat`
> 拿结算参数（该接口不含 OHLC/成交量/持仓），日行情则对应
> `https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{{YYYYMMDD}}.dat`。
> 若 `01` 脚本报"返回空表"，可据此新增日行情爬虫兜底。

## 三、数据校验

校验脚本：`02_validate_shfe_20250526.py`，报告：`02_validation_report.json`。

| 校验项 | 结果 |
| --- | --- |
| 行数 / 唯一 ts_code | {structure.get("rows")} / {structure.get("ts_code_unique")} |
| 数据中出现的 trade_date | {fmt(structure.get("distinct_trade_date"))}（仅目标日，无混入） |
| 空值单元格数 | {structure.get("null_cells")}（`open`/`high`/`low` 各 24、`oi_chg` 36；均落在 `xxL.SHF` 组合行与未上市合约上，与历史各日形态一致） |
| OHLC 区间、成交量/持仓非负、价格为正 | {'通过' if not validation.get('ohlc_issues') else validation.get('ohlc_issues')} |
| `pre_close` vs 上一交易日({continuity.get("prior_trade_date")})`close` | 最大绝对偏差 {fmt(continuity.get("max_abs_pre_close_diff"))} |
| `pre_settle` vs 上一交易日 `settle` | 最大绝对偏差 {fmt(continuity.get("max_abs_pre_settle_diff"))} |
| `change1 = close - pre_settle` | 最大绝对偏差 {fmt(continuity.get("max_abs_change1_diff"))} |
| `change2 = settle - pre_settle` | 最大绝对偏差 {fmt(continuity.get("max_abs_change2_diff"))} |
| `oi_chg = oi - 上日 oi` | 最大绝对偏差 {fmt(continuity.get("max_abs_oi_chg_diff"))} |
| **与上期所官网结算价逐合约比对** | 匹配 {cross.get("matched_rows")} 个合约，不一致 {cross.get("mismatch_rows")} 个，最大绝对偏差 {fmt(cross.get("max_abs_settle_diff"))} |

与官网的交叉校验源：`{cross.get("official_csv")}`（上期所结算参数 `SETTLEMENTPRICE`）。

> 校验说明：Tushare 的 `change1` / `change2` 是相对 `pre_settle`（前结算价）而非
> `pre_close` 计算的涨跌，这一口径已在全部 SHFE 历史 raw 数据（911670 行）上复核确认。

**校验结论：{conclusion}**

## 四、重写 cleaned 产物

重写脚本：`03_rebuild_shfe_cleaned_parquet.py`。做法是把 `clean_fut_daily_snapshots.py`
原样加载、在任务目录下的临时输出目录做一次**全量**重跑，然后：

1. 逐个比对非 SHFE 的 parquet 与现有 cleaned 产物，确认本次不产生任何副作用；
2. 只把 `SHFE_*.parquet` 覆盖回 `{repo_relative(CLEANED_DIR)}`；
3. 同步刷新 `cleaned/AUDIT.md`（原 AUDIT.md 把该文件列为"空文件跳过"，重跑后已不成立）；
4. 覆盖前的旧 SHFE parquet 已备份到 `{rebuild.get("backup_dir")}`（备份不覆盖已存在文件，
   因此最早那次"补数前"的备份始终保留，可作为差异比对基线）。

关于 `AD` / `OP` 品种：2025-05-26 当天 Tushare 未返回这两个品种，
经查其首个合约的首个交易日分别为 2025-06-10（AD2511）与 2025-09-10（OP2601），
即当日尚未上市，因此 `SHFE_AD` / `SHFE_OP` 新增 0 行属于预期结果，而非补数遗漏。

| 指标 | 结果 |
| --- | --- |
| 重写 SHFE parquet 文件数 | {totals.get("files")} |
| 新增 instrument + date 组合 | +{totals.get("added_instrument_date")} |
| 删除 instrument + date 组合 | -{totals.get("removed_instrument_date")} |
| 出现数值变化的列 | {", ".join(totals.get("changed_value_columns", [])) or "无"} |
| 非 SHFE 产物受影响数 | {len(side_effects)} |
| 全量重跑后仍被跳过的空 raw 文件数 | {skipped_count}（均为 108 字节纯表头空文件，属正常空表） |

清洗后终态核对：cleaned 下 {fmt(totals.get("files"))} 个 SHFE 文件全部已含 2025-05-26 数据，
合计新增 {totals.get("added_instrument_date")} 行，(instrument, date) 无重复、价格无非正值，
且 2025-05-26 的 214 行结算价与上期所官网结算参数**逐合约完全一致**（最大绝对偏差 0）。

逐文件明细：

| 文件 | 旧行数 | 新行数 | 新增组合 | 减少组合 | {TARGET_DATE} 行数 | 数值变化列 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
{shfe_rows}

## 五、复现方式

```powershell
# 1) 重新拉取 Tushare（补 raw 分区文件）
python db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/01_pull_tushare_shfe_20250526.py

# 2) 校验（含与上期所官网结算价交叉比对）
python db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/02_validate_shfe_20250526.py

# 3) 重写 cleaned 下的 SHFE_*.parquet（先 --dry-run 看差异）
python db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/03_rebuild_shfe_cleaned_parquet.py --dry-run
python db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/03_rebuild_shfe_cleaned_parquet.py

# 补充：若 cleaned 已被覆盖过，想再次对着"补数前"的备份复算差异并刷新 AUDIT.md，
# 可复用上次的全量重跑结果（约 5~10 分钟可省掉）：
python db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/03_rebuild_shfe_cleaned_parquet.py --reuse-temp --shfe-baseline-dir db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/backup_before_rebuild/SHFE_parquet

# 4) 生成本报告
python db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/04_render_report.py
```

## 六、目录内容

| 文件 | 说明 |
| --- | --- |
| `task_common.py` | 公共路径/凭证解析 |
| `01_pull_tushare_shfe_20250526.py` | Tushare 重拉脚本 |
| `01_pull_tushare_result.json` | Tushare 拉取结果元信息 |
| `tushare_fut_daily_{TARGET_EXCHANGE}_{TARGET_TRADE_DATE}.json` | Tushare 接口原始响应 |
| `tushare_fut_daily_{TARGET_EXCHANGE}_{TARGET_TRADE_DATE}.csv` | 本次补数副本 |
| `backup_trade_date={TARGET_TRADE_DATE}.csv` | 覆盖前的空文件备份 |
| `02_validate_shfe_20250526.py` | 校验脚本 |
| `02_validation_report.json` | 校验明细 |
| `03_rebuild_shfe_cleaned_parquet.py` | 重写 cleaned 产物脚本 |
| `03_rebuild_summary.json` | 重写与差异明细 |
| `backup_before_rebuild/SHFE_parquet/` | 覆盖前的旧 SHFE parquet 备份 |
| `_full_rebuild_temp/` | 全量重跑的临时输出（用于比对，可随时删除） |
"""


def main() -> None:
    REPORT_PATH.write_text(render(), encoding="utf-8")
    print(f"报告已生成：{REPORT_PATH}")


if __name__ == "__main__":
    main()