# SHFE 日频快照缺失 2025-05-26 补数报告

- 任务目录：`db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26`
- 生成时间（UTC）：2026-10-06T12:34:34+00:00
- 目标：补齐 `SHFE` 交易所 2025-05-26（20250526）的日频快照，
  并重写覆盖 `db/market/daily_snapshots/bar_1d/bar_1d_tushare/cleaned` 下的 `SHFE_*.parquet`。

## 一、问题定位

历史入库脚本 `db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/pull_tushare_fut_daily.py`
在 20250526 落盘了 `exchange=SHFE/trade_date=20250526.csv`，
但文件内容只有 5 字节（UTF-8 BOM + 空行，连表头都没有），
说明当时 Tushare `fut_daily` 返回了一个完全空的响应体。

清洗脚本 `cleaned/clean_fut_daily_snapshots.py` 在 `load_tushare_bars()` 中把该文件
记入 `empty_or_unreadable_files` 后跳过（历史 `cleaned/AUDIT.md` 第 4823 行有记录），
于是 2025-05-26 的 SHFE 数据在 cleaned 层整日缺失。

全库扫描确认：raw 目录下只有这一个"连表头都没有"的损坏文件；
其余 4733 个空文件都是 108 字节的纯表头文件，属于当日无成交/品种尚未上市的正常空表。

## 二、补数路径

先按要求尝试 Tushare：`api_name=fut_daily`，`exchange=SHFE`，
`trade_date=20250526`，直连 `http://api.tushare.pro`。

| 指标 | 结果 |
| --- | --- |
| 接口返回 code / msg | 0 / `` |
| 请求次数 | 1 |
| 返回行数 | 250 |
| 落地文件 | `db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/fut_daily/exchange=SHFE/trade_date=20250526.csv` |
| 原空文件备份 | `db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/backup_trade_date=20250526.csv` |
| 接口原始响应 | `db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/tushare_fut_daily_SHFE_20250526.json` |

**结论：Tushare 已恢复正常，直接返回 250 行数据，无需启用上期所官网爬虫兜底。**

> 兜底方案（本次未启用）：仓库内已有上期所官网爬虫
> `db/rules_ingestion/data/raw/exchange_settlement_data_scraping/scrape_shfe_settlement_history.py`，
> 走 SafeLine proof-of-work 后请求 `https://www.shfe.com.cn/data/busiparamdata/future/Settlement{YYYYMMDD}.dat`
> 拿结算参数（该接口不含 OHLC/成交量/持仓），日行情则对应
> `https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{YYYYMMDD}.dat`。
> 若 `01` 脚本报"返回空表"，可据此新增日行情爬虫兜底。

## 三、数据校验

校验脚本：`02_validate_shfe_20250526.py`，报告：`02_validation_report.json`。

| 校验项 | 结果 |
| --- | --- |
| 行数 / 唯一 ts_code | 250 / 250 |
| 数据中出现的 trade_date | ['20250526']（仅目标日，无混入） |
| 空值单元格数 | 108（`open`/`high`/`low` 各 24、`oi_chg` 36；均落在 `xxL.SHF` 组合行与未上市合约上，与历史各日形态一致） |
| OHLC 区间、成交量/持仓非负、价格为正 | 通过 |
| `pre_close` vs 上一交易日(20250523)`close` | 最大绝对偏差 0 |
| `pre_settle` vs 上一交易日 `settle` | 最大绝对偏差 0 |
| `change1 = close - pre_settle` | 最大绝对偏差 0 |
| `change2 = settle - pre_settle` | 最大绝对偏差 0 |
| `oi_chg = oi - 上日 oi` | 最大绝对偏差 0 |
| **与上期所官网结算价逐合约比对** | 匹配 214 个合约，不一致 0 个，最大绝对偏差 0 |

与官网的交叉校验源：`db/rules_ingestion/data/raw/exchange_settlement_data_scraping/exchange=SHFE/trading_day=20250526/Settlement20250526.csv`（上期所结算参数 `SETTLEMENTPRICE`）。

> 校验说明：Tushare 的 `change1` / `change2` 是相对 `pre_settle`（前结算价）而非
> `pre_close` 计算的涨跌，这一口径已在全部 SHFE 历史 raw 数据（911670 行）上复核确认。

**校验结论：通过**

## 四、重写 cleaned 产物

重写脚本：`03_rebuild_shfe_cleaned_parquet.py`。做法是把 `clean_fut_daily_snapshots.py`
原样加载、在任务目录下的临时输出目录做一次**全量**重跑，然后：

1. 逐个比对非 SHFE 的 parquet 与现有 cleaned 产物，确认本次不产生任何副作用；
2. 只把 `SHFE_*.parquet` 覆盖回 `db/market/daily_snapshots/bar_1d/bar_1d_tushare/cleaned`；
3. 同步刷新 `cleaned/AUDIT.md`（原 AUDIT.md 把该文件列为"空文件跳过"，重跑后已不成立）；
4. 覆盖前的旧 SHFE parquet 已备份到 `db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/backup_before_rebuild/SHFE_parquet`（备份不覆盖已存在文件，
   因此最早那次"补数前"的备份始终保留，可作为差异比对基线）。

关于 `AD` / `OP` 品种：2025-05-26 当天 Tushare 未返回这两个品种，
经查其首个合约的首个交易日分别为 2025-06-10（AD2511）与 2025-09-10（OP2601），
即当日尚未上市，因此 `SHFE_AD` / `SHFE_OP` 新增 0 行属于预期结果，而非补数遗漏。

| 指标 | 结果 |
| --- | --- |
| 重写 SHFE parquet 文件数 | 20 |
| 新增 instrument + date 组合 | +214 |
| 删除 instrument + date 组合 | -0 |
| 出现数值变化的列 | 无 |
| 非 SHFE 产物受影响数 | 0 |
| 全量重跑后仍被跳过的空 raw 文件数 | 4733（均为 108 字节纯表头空文件，属正常空表） |

清洗后终态核对：cleaned 下 20 个 SHFE 文件全部已含 2025-05-26 数据，
合计新增 214 行，(instrument, date) 无重复、价格无非正值，
且 2025-05-26 的 214 行结算价与上期所官网结算参数**逐合约完全一致**（最大绝对偏差 0）。

逐文件明细：

| 文件 | 旧行数 | 新行数 | 新增组合 | 减少组合 | 2025-05-26 行数 | 数值变化列 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `SHFE_AD.parquet` | 2824 | 2824 | +0 | -0 | 0 | 无 |
| `SHFE_AG.parquet` | 41034 | 41046 | +12 | -0 | 12 | 无 |
| `SHFE_AL.parquet` | 78445 | 78457 | +12 | -0 | 12 | 无 |
| `SHFE_AO.parquet` | 8560 | 8572 | +12 | -0 | 12 | 无 |
| `SHFE_AU.parquet` | 41186 | 41194 | +8 | -0 | 8 | 无 |
| `SHFE_BR.parquet` | 8168 | 8180 | +12 | -0 | 12 | 无 |
| `SHFE_BU.parquet` | 41093 | 41109 | +16 | -0 | 16 | 无 |
| `SHFE_CU.parquet` | 82162 | 82174 | +12 | -0 | 12 | 无 |
| `SHFE_FU.parquet` | 59700 | 59712 | +12 | -0 | 12 | 无 |
| `SHFE_HC.parquet` | 35657 | 35669 | +12 | -0 | 12 | 无 |
| `SHFE_NI.parquet` | 32694 | 32706 | +12 | -0 | 12 | 无 |
| `SHFE_OP.parquet` | 2138 | 2138 | +0 | -0 | 0 | 无 |
| `SHFE_PB.parquet` | 44174 | 44186 | +12 | -0 | 12 | 无 |
| `SHFE_RB.parquet` | 49989 | 50001 | +12 | -0 | 12 | 无 |
| `SHFE_RU.parquet` | 65595 | 65605 | +10 | -0 | 10 | 无 |
| `SHFE_SN.parquet` | 32694 | 32706 | +12 | -0 | 12 | 无 |
| `SHFE_SP.parquet` | 21656 | 21668 | +12 | -0 | 12 | 无 |
| `SHFE_SS.parquet` | 19424 | 19436 | +12 | -0 | 12 | 无 |
| `SHFE_WR.parquet` | 49475 | 49487 | +12 | -0 | 12 | 无 |
| `SHFE_ZN.parquet` | 56037 | 56049 | +12 | -0 | 12 | 无 |

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
| `tushare_fut_daily_SHFE_20250526.json` | Tushare 接口原始响应 |
| `tushare_fut_daily_SHFE_20250526.csv` | 本次补数副本 |
| `backup_trade_date=20250526.csv` | 覆盖前的空文件备份 |
| `02_validate_shfe_20250526.py` | 校验脚本 |
| `02_validation_report.json` | 校验明细 |
| `03_rebuild_shfe_cleaned_parquet.py` | 重写 cleaned 产物脚本 |
| `03_rebuild_summary.json` | 重写与差异明细 |
| `backup_before_rebuild/SHFE_parquet/` | 覆盖前的旧 SHFE parquet 备份 |
| `_full_rebuild_temp/` | 全量重跑的临时输出（用于比对，可随时删除） |
