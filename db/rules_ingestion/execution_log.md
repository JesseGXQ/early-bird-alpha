# Execution Log

本文档记录外部数据下载、分析、校验、规则生成和入库过程。

建议每次执行按以下模板记录。

## Template

```markdown
## YYYY-MM-DD - short_title

- Objective:
- Inputs:
- Commands / scripts:
- Outputs:
- Validation result:
- Issues found:
- Follow-up:
```

## Log

## 2026-07-05 - PriceTickRule First Effective Date Correction

- Objective: 将已生成的 97 个 TIER1 `price_tick_history` YAML 的首个 `effective_date` 从通用占位日期改为各品种实际上市日期。
- Inputs:
  - Listing-date source: `docs/代码修改笔记/期货交易规则/交易时段基础规则/期货品种上市时间汇总.md`
  - Existing output: `db/rules_ingestion/data/derived/20260624/product_meta_price_tick_rule/yaml/*.yaml`
- Commands / scripts:
  - 更新脚本：`db/rules_ingestion/script/product_meta/ingest_price_tick_rule.py`
  - 执行命令：`python db/rules_ingestion/script/product_meta/ingest_price_tick_rule.py`
- Outputs:
  - 覆盖写入：`db/rules_ingestion/data/derived/20260624/product_meta_price_tick_rule/yaml/*.yaml`
  - 更新审计报告：`db/rules_ingestion/data/derived/20260624/product_meta_price_tick_rule/audit_report.md`
  - 更新明细审计表：`db/rules_ingestion/data/derived/20260624/product_meta_price_tick_rule/price_tick_rule_audit.csv`
- Validation result:
  - Listing-date table products: 97。
  - Price tick YAML products: 97。
  - 缺失上市日期产品：0。
  - 已校验 97 个 YAML 的 `price_tick_history[0].effective_date` 全部等于上市时间汇总表中的 `list_date`。
  - 抽样：`AU` 首条改为 `2008-01-09`，`Y` 改为 `2006-01-09`，`LC` 改为 `2023-07-21`，`EC` 改为 `2023-08-18`，`L_F` 改为 `2025-10-28`。
  - 脚本通过 `python -m py_compile db/rules_ingestion/script/product_meta/ingest_price_tick_rule.py`。
- Issues found:
  - 暂无。
- Follow-up:
  - 后续如果上市时间汇总表继续修正，只需重跑 `ingest_price_tick_rule.py` 即可同步首个 `effective_date`。

## 2026-07-02 - InstrumentDailyStatus Order Volume Limits Ingestion

- Objective: 按新计划将下单量限制从 TIER1 产品级规则迁移为 TIER3 合约-交易日级日频字段，生成 `max_market_order_volume`、`min_market_order_volume`、`max_limit_order_volume`、`min_limit_order_volume`。
- Inputs:
  - BigQuant `cn_future_static_data`: `db/rules_ingestion/data/raw/20260622/bigquant/cn_future_static_data/exchange=*/product=*.parquet`
  - TIER2 registry: `db/rules_ingestion/data/derived/20260624/instrument_registry/*.parquet`
  - Tushare-to-local base mapping: `db/rules_ingestion/data/derived/20260624/instrument_registry/tushare_ts_code_to_instrument.parquet`
- Commands / scripts:
  - 生成脚本：`db/rules_ingestion/script/instrument_daily_status/ingest_order_volume_limits.py`
  - 执行命令：`python db/rules_ingestion/script/instrument_daily_status/ingest_order_volume_limits.py`
- Outputs:
  - 输出目录：`db/rules_ingestion/data/derived/20260624/instrument_daily_status_order_volume_limits/`
  - 产品级 parquet：`{EXCHANGE}_{PRODUCT}.parquet`
  - 审计报告：`AUDIT.md`
  - 跳过行明细：`unmapped_or_skipped_rows.csv`
  - 全局代码映射：`db/rules_ingestion/data/derived/instrument_code_source_local_mapping.parquet`
- Ingestion policy:
  - 使用 BigQuant `trading_code` 作为 exchange-format 合约代码，保留原始大小写。
  - 使用 TIER2 registry 的 `exchange_inst -> instrument` 映射，并由 `trading_date` 限定合约生命周期，语义与 `InstrumentRegistryManager.translate_code(direction="exchange_to_internal")` 一致。
  - 输出只写 BigQuant 实际观测到的合约-交易日记录；BigQuant 覆盖期外的日期不向前填充，后续与更长 TIER3 主表 join 时自然保留为空。
  - `max_market_order_volume = 0` 保留为有效观测值，不按缺失处理。
- Validation result:
  - Raw rows: 328725。
  - Registry lifecycle mapped rows: 326159。
  - Output rows: 326159。
  - Output parquet files: 88。
  - 所有输出 parquet 均严格包含 `instrument`、`trading_date`、`max_market_order_volume`、`min_market_order_volume`、`max_limit_order_volume`、`min_limit_order_volume` 六列。
  - 校验无重复 `(instrument, trading_date)`。
  - 新增 `instrument_code_source_local_mapping.parquet` 共 10711 行，列为 `ts_code`、`instrument`、`exchange_inst`、`trading_code_bigquant`；其中 2314 个合约存在 BigQuant 观测代码，其余历史未覆盖合约的 `trading_code_bigquant` 为空。
  - 脚本通过 `python -m py_compile db/rules_ingestion/script/instrument_daily_status/ingest_order_volume_limits.py`。
- Issues found:
  - 跳过 `unmapped_contract` 2566 行：`L_F`、`PP_F`、`V_F` 各 855 行，原因是 BigQuant 月均价/非标准 `*F` 合约不在 TIER2 真实期货 registry；`FU` 1 行，原因是 BigQuant `fu2612` 记录日期 `2025-11-28` 早于 registry `FU2612.list_date=2025-12-01`。
- Follow-up:
  - 后续若将四个 order volume 字段合并进正式 `InstrumentDailyStatus` 主 parquet，需要同步扩展 `InstrumentDailyStatus` dataclass、manager schema 和读取逻辑。

## 2026-07-03 - Instrument Code Mapping Source Correction

- Objective: 修正 `instrument_code_source_local_mapping.parquet` 中 `trading_code_bigquant` 的来源，将其从 BigQuant 日频 `cn_future_static_data.trading_code` 改为合约级 `cn_future_basic_info.trading_code`，避免因 static_data 覆盖期较短导致大量缺失。
- Inputs:
  - BigQuant `cn_future_basic_info`: `db/rules_ingestion/data/raw/20260622/bigquant/cn_future_basic_info/exchange=*.parquet`
  - TIER2 registry: `db/rules_ingestion/data/derived/20260624/instrument_registry/*.parquet`
  - Base mapping: `db/rules_ingestion/data/derived/20260624/instrument_registry/tushare_ts_code_to_instrument.parquet`
- Commands / scripts:
  - 更新脚本：`db/rules_ingestion/script/instrument_daily_status/ingest_order_volume_limits.py`
  - 执行命令：`python db/rules_ingestion/script/instrument_daily_status/ingest_order_volume_limits.py`
- Outputs:
  - 覆盖写入：`db/rules_ingestion/data/derived/instrument_code_source_local_mapping.parquet`
  - 同步更新审计报告：`db/rules_ingestion/data/derived/20260624/instrument_daily_status_order_volume_limits/AUDIT.md`
- Validation result:
  - Mapping rows: 10711。
  - Columns: `ts_code`、`instrument`、`exchange_inst`、`trading_code_bigquant`。
  - `exchange_inst` 缺失 0 行。
  - `trading_code_bigquant` 缺失 4 行，分别为 `IC2608`、`IF2608`、`IH2608`、`IM2608`，原因是 BigQuant `cn_future_basic_info` 当前未覆盖这 4 个 Tushare/TIER2 合约。
  - 相比此前使用 `cn_future_static_data` 时 8397 个 `trading_code_bigquant` 缺失，本次改用合约级来源后覆盖率已显著改善。
  - 脚本通过 `python -m py_compile db/rules_ingestion/script/instrument_daily_status/ingest_order_volume_limits.py`。
- Issues found:
  - 暂无阻塞问题；4 个缺失值保留为空，等待后续 BigQuant 合约表覆盖或人工补充。
- Follow-up:
  - 若后续要求 mapping 完全无缺失，可单独人工补 `IC2608/IF2608/IH2608/IM2608` 的 BigQuant trading code；按当前命名口径应与 `exchange_inst` 相同，但本次不做推断写入。

## 2026-07-02 - PriceTickRule Variable Ingestion

- Objective: 执行 TIER1 版本化最小变动价位规则生成：`PriceTickRule.effective_date`、`PriceTickRule.price_tick`。
- Inputs:
  - Product universe: `db/rules_ingestion/data/derived/20260624/product_meta_static/yaml/*.yaml`
  - 历史事实来源：`docs/代码修改笔记/期货交易规则/中国期货市场历史品种最小价格变动（Price Tick 最小变动价位）变更调研报告 _Antigravity.md`
  - 单版本当前 tick 补齐来源：`db/rules_ingestion/data/raw/20260622/bigquant/cn_future_product_info/full.parquet`
- Commands / scripts:
  - 生成脚本：`db/rules_ingestion/script/product_meta/ingest_price_tick_rule.py`
  - 执行命令：`python db/rules_ingestion/script/product_meta/ingest_price_tick_rule.py`
- Outputs:
  - 输出目录：`db/rules_ingestion/data/derived/20260624/product_meta_price_tick_rule/`
  - 候选 YAML：`yaml/*.yaml`
  - 审计报告：`audit_report.md`
  - 明细审计表：`price_tick_rule_audit.csv`
- Ingestion policy:
  - 产品 universe 严格来自 static meta YAML 目录，共 97 个产品。
  - 调研报告中的真实历史变更作为权威事实，覆盖 BigQuant 当前值或 `old_price_tick`。
  - BigQuant `old_price_tick` 不作为自动历史版本来源。
  - 对未发生真实历史变更的产品，使用 BigQuant `price_tick` 生成单版本规则，基准生效日写为 `1900-01-01`。
  - 对报告明确说明的非真实变更、旧代码冗余和月均价互换类记录，若它们存在于 static universe 中，则只保留单版本规则，不派生历史变更。
- Validation result:
  - 生成 YAML 文件 97 个，与 static meta YAML universe 完全一致。
  - 其中 15 个产品使用调研报告历史版本：`AU`、`BU`、`EC`、`FB`、`I`、`J`、`JM`、`L`、`LC`、`OI`、`P`、`TF`、`TS`、`V`、`Y`。
  - 82 个产品使用单版本 fallback 规则。
  - 已校验所有 YAML 均包含且仅包含 `price_tick_history[].effective_date` 和 `price_tick_history[].price_tick`，日期严格升序，tick 为正数。
  - 抽样校验近期变更后最新 tick：`Y=1.0`、`P=1.0`、`LC=20.0`、`EC=0.5`、`AU=0.02`、`BR=5.0`。
- Issues found:
  - 无阻塞问题。
- Follow-up:
  - 后续合并正式 TIER1 product YAML 时，可将本目录的 `price_tick_history` 与 static meta YAML 按产品名合并。

## 2026-06-22 - API Sample Availability Test

- Objective: 使用简单样例测试本轮交易规则建库范围内的 BigQuant 与 Tushare 数据下载 API 可用性，并保存最小样例数据。
- Inputs:
  - Tushare 数据表：`fut_basic`、`fut_trade_cal`、`fut_settle`。
  - BigQuant 数据表：`cn_future_product_info`、`cn_future_basic_info`、`cn_future_static_data`。
  - API 凭证：`db/rules_ingestion/supplements/API凭证.txt`。脚本只读取 Tushare token，不将 token 写入结果文件。
  - 执行环境：conda 环境 `bigquant`；BigQuant 使用环境中已保存的认证信息。
- Commands / scripts:
  - 测试脚本：`db/rules_ingestion/data/API_test/api_sample_test.py`。
  - 运行命令：`conda run -n bigquant python db/rules_ingestion/data/API_test/api_sample_test.py`。
- Outputs:
  - 汇总结果：`db/rules_ingestion/data/API_test/api_sample_test_summary.json`。
  - 样例数据目录：`db/rules_ingestion/data/API_test/`。
  - 样例 CSV：
    - `tushare_fut_basic.csv`
    - `tushare_fut_trade_cal.csv`
    - `tushare_fut_settle.csv`
    - `bigquant_cn_future_product_info.csv`
    - `bigquant_cn_future_basic_info.csv`
    - `bigquant_cn_future_static_data.csv`
- Validation result:

| Source | Table | Result | Rows | Sample file | Notes |
| --- | --- | --- | ---: | --- | --- |
| Tushare | `fut_basic` | 成功 | 5 | `tushare_fut_basic.csv` | 使用 `exchange='DCE'`、`fut_type='1'` 小样例。 |
| Tushare | `fut_trade_cal` | 成功 | 5 | `tushare_fut_trade_cal.csv` | 使用 `exchange='DCE'`、`20240101-20240110` 小日期段。 |
| Tushare | `fut_settle` | 成功 | 5 | `tushare_fut_settle.csv` | 使用本地 Tushare 补充文档样例 `trade_date='20181114'`、`exchange='SHFE'`。 |
| BigQuant | `cn_future_product_info` | 成功 | 5 | `bigquant_cn_future_product_info.csv` | 使用 `SELECT * FROM cn_future_product_info LIMIT 5`。 |
| BigQuant | `cn_future_basic_info` | 成功 | 5 | `bigquant_cn_future_basic_info.csv` | 使用 `SELECT * FROM cn_future_basic_info LIMIT 5`。 |
| BigQuant | `cn_future_static_data` | 成功 | 5 | `bigquant_cn_future_static_data.csv` | 使用 `SELECT * FROM cn_future_static_data LIMIT 5`。 |

- Issues found:
  - 沙箱内首次运行因网络访问超时；联网授权后运行成功。
  - Tushare `fut_settle` 对样例参数较敏感：API 可正常返回字段结构，但不保证任意交易日、交易所或合约组合都有数据。此前随手选择的 DCE 日期/合约参数可成功调用但返回空表，后改用本地 Tushare 补充文档样例获得非空结果。
  - BigQuant `dai.get_data_quota()` 可用；最终汇总记录显示周配额 `50000000` cells，已用 `560` cells。
- Follow-up:
  - 后续批量拉取时，`fut_settle` 应优先按有数据覆盖的历史交易日和交易所分批测试，再扩展到全量循环。
  - API 测试脚本可作为后续批量下载脚本的最小认证和连通性检查入口。

## 2026-06-23 - Raw Pull Dry Run SHFE RB Strategy Update

- Objective: 按新的全量拉取策略更新 raw data 拉取脚本，并对上海期货交易所螺纹钢品种重新执行 dry run，验证 6 张表的调用、落盘、schema 和 manifest 链路。
- Inputs:
  - 交易所：`SHFE`。
  - 品种：`RB`。
  - dry run 日期窗口：`20250225-20250303`，仅用于限制 `fut_trade_cal` 验证窗口和 `fut_settle` 循环窗口。
  - Tushare 数据表：`fut_basic`、`fut_trade_cal`、`fut_settle`。
  - BigQuant 数据表：`cn_future_product_info`、`cn_future_basic_info`、`cn_future_static_data`。
- Commands / scripts:
  - 拉取脚本：`db/rules_ingestion/data/raw/20260622/pull_raw_data.py`。
  - BigQuant 代码口径探针：`db/rules_ingestion/data/raw/20260622/probe_bigquant_codes.py`。
  - 最终 dry run 命令：`conda run -n bigquant python db/rules_ingestion/data/raw/20260622/pull_raw_data.py --dry-run --exchange SHFE --product RB --start-date 20250225 --end-date 20250303 --overwrite`。
- Outputs:
  - dry run 输出目录：`db/rules_ingestion/data/raw/20260622/dry_run/SHFE_RB/`。
  - 汇总结果：`db/rules_ingestion/data/raw/20260622/dry_run/SHFE_RB/run_summary.json`。
  - 分片 manifest：`db/rules_ingestion/data/raw/20260622/dry_run/SHFE_RB/manifest.jsonl`。
  - schema 快照目录：`db/rules_ingestion/data/raw/20260622/dry_run/SHFE_RB/schema/`。
- Validation result:

| Source | Table | Result | Rows | Empty | Notes |
| --- | --- | --- | ---: | --- | --- |
| Tushare | `fut_basic` | 成功 | 3572 | 否 | `exchange=SHFE` 整表拉取，不按产品过滤。 |
| Tushare | `fut_trade_cal` | 成功 | 7 | 否 | dry run 使用 `SHFE`、`20250225-20250303` 小窗口；正式全量使用 `exchange` 整表。 |
| Tushare | `fut_settle` | 成功 | 60 | 否 | 主路径按 `exchange + trade_date` 请求，再按 `ts_code` 品种前缀拆到 `product=RB`；本次未触发 1600 行 fallback。 |
| BigQuant | `cn_future_product_info` | 成功 | 105 | 否 | `SELECT * FROM cn_future_product_info` 全表拉取。 |
| BigQuant | `cn_future_basic_info` | 成功 | 3603 | 否 | API 侧 `SELECT * FROM cn_future_basic_info`，本地按 `exchange=SHFE` 分区落盘。 |
| BigQuant | `cn_future_static_data` | 成功 | 4800 | 否 | `exchange=SHFE`，`product_id=rb`，不加日期过滤。 |

- Issues found:
  - 沙箱内运行时 Tushare 网络访问被拦截，BigQuant 也无法取得已保存的 flight client；联网授权后运行成功。
  - Tushare `ts.set_token()` 会尝试写入用户目录 `C:\Users\Jesse\tk.csv`，脚本已改为 `ts.pro_api(token)`，避免在 raw 拉取过程中写用户目录。
  - Tushare `fut_settle` 文档虽列出 `start_date` / `end_date`，但实际调用要求 `trade_date` 或 `ts_code` 至少一个非空；脚本已改为按 `exchange + trade_date` 自然日循环，并在疑似触及 1600 行限制时 fallback 到 `ts_code + trade_date`。
  - BigQuant `cn_future_basic_info.product_code` 与 `cn_future_static_data.product_id` 对 RB 使用小写 `rb`；脚本已兼容大小写。
  - BigQuant `cn_future_static_data` 中 SHFE/RB 覆盖区间为 `20240918-20260622`。正式策略不对该表加日期过滤；本次 dry run 返回 4800 行。
- Follow-up:
  - 全量拉取前，可考虑把 `fut_settle` 的日期循环从自然日优化为交易日历驱动，减少无效 API 请求。
  - BigQuant 全量拉取可继续使用大小写兼容条件；后续分区落盘时应统一将本地 product 分区名规范为大写，例如 `product=RB`。

## 2026-06-24 - StaticMeta Variable Ingestion

- Objective: 执行 TIER1 非版本化静态变量生成：`StaticMeta.product`、`StaticMeta.exchange`、`StaticMeta.name`、`StaticMeta.position_date_type`、`StaticMeta.max_margin_side_algorithm`、`StaticMeta.combination_type`。
- Inputs:
  - BigQuant `cn_future_product_info`: `db/rules_ingestion/data/raw/20260622/bigquant/cn_future_product_info/full.parquet`
  - BigQuant `cn_future_basic_info`: `db/rules_ingestion/data/raw/20260622/bigquant/cn_future_basic_info/exchange=*.parquet`
  - BigQuant `cn_future_static_data`: `db/rules_ingestion/data/raw/20260622/bigquant/cn_future_static_data/exchange=*/product=*.parquet`
- Commands / scripts:
  - 生成脚本：`db/rules_ingestion/script/product_meta/ingest_static_meta.py`
  - 执行命令：`python db/rules_ingestion/script/product_meta/ingest_static_meta.py`
  - 执行清单：`db/rules_ingestion/script/product_meta/STATIC_META_EXECUTION_CHECKLIST.md`
- Output:
  - 输出目录：`db/rules_ingestion/data/derived/20260624/product_meta_static/`
  - 候选变量表：`candidate_static_meta.csv`
  - 候选 YAML：`yaml/*.yaml`
  - 审计报告：`audit_report.md`
  - 多值字段报告：`static_meta_multi_value_fields.csv`
  - 剔除代码报告：`rejected_product_info_codes.csv`
- Ingestion policy:
  - 仅接受本地标准代码体系中的中国期货交易所：`CFFEX`、`CZCE`、`DCE`、`GFEX`、`INE`、`SHFE`。
  - 产品必须同时出现在 BigQuant `cn_future_basic_info` 和 `cn_future_static_data`。
  - `cn_future_static_data.product_class` 必须全为 `1`，本批次才视作普通期货品种。
  - `position_date_type` 映射为 `1 -> distinguish_today_yesterday`、`2 -> undistinguished`。
  - 对非版本化字段的历史多值，本批候选使用最新 `trading_day` 横截面的众数，同时写入多值报告和 `decisions.md` 等待二次确认。
- Validation result:
  - 生成候选产品 91 个：CFFEX 8、CZCE 27、DCE 26、GFEX 5、INE 5、SHFE 20。
  - 生成候选 YAML 91 个。
  - 发现 `max_margin_side_algorithm` 历史多值 29 个产品字段组合，已写入 `static_meta_multi_value_fields.csv`。
  - 剔除 `cn_future_product_info` 中无法被基础信息/静态日频表支撑或明显非中国期货的代码 14 个，包括 `FX` 交易所外汇代码 `NZDUSD`、`USDCAD`、`USDCHF`、`XAUUSD`。
  - 通过隔离加载 `data.rules.models` 和 `data/rules/product_meta_manager.py` 的方式验证 91 个候选 YAML 均可被 `ProductMetaManager.get_static_meta()` 读取。
- Issues found:
  - 直接 `from data.rules.product_meta_manager import ProductMetaManager` 会触发仓库顶层 `data.__init__` 导入链，并在 `data/stitching/stitching.py` 因 `RolloverConfig` 未定义报错；本次未修改该无关问题，改用隔离模块加载验证 manager 本体。
  - `max_margin_side_algorithm` 在中金所国债、能源中心、上期所多个品种上出现历史 0/1 多值；当前 `StaticMeta` 无法表达历史变化，本批候选使用最新横截面众数。
- Follow-up:
  - 请复核 `decisions.md` 中新增的 StaticMeta 多值字段候选口径。
  - 若确认采用“当前静态值”口径，可将候选 YAML 合并进正式 TIER1 product YAML；若坚持全历史唯一才入库，则需暂缓 29 个多值品种的该字段或扩展模型。
