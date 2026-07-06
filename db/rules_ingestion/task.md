# Task Log

## 2026-06-21

- 任务描述：调研 BigQuant 与 Tushare 的期货相关外部数据源说明页面，梳理表结构、数据粒度、字段语义、适用范围和入库注意事项，并更新 `external_sources.md`。
- 执行结果：已完成 BigQuant `cn_future_product_info`、`cn_future_basic_info`、`cn_future_static_data` 的字段级画像和本地入库适用性分析；Tushare 官方文档当前跳转登录页，未能可靠获取字段表，已在 `external_sources.md` 中标记为待本地文档补充。

## 2026-06-21

- 任务描述：读取本地补充文档 `db/rules_ingestion/supplements/Tushare期货数据介绍/Tushare 期货数据库介绍.md`，补全 Tushare 期货数据源画像。
- 执行结果：已补充 Tushare `fut_basic`、`fut_trade_cal`、`fut_settle` 的输入参数、输出字段、本地模型关联、数据粒度、权限限制和入库注意事项，并更新 `external_sources.md`。

## 2026-06-21

- 任务描述：按“一事一议”原则，为 TIER1-TIER3 本地期货交易规则数据库编写变量级入库方案，并将结果写入 `variable_ingestion_plan.md`。本阶段先建立 TODO 清单，确认覆盖范围后再展开具体方案。
- 执行结果：已完成。TIER1/TIER2/TIER3 变量级入库方案已写入 `variable_ingestion_plan.md`，并已将需要人工拍板的问题写入 `decisions.md`；本轮 TIER3 仅覆盖 `InstrumentDailyStatus`，不包含由本地规则解析生成的 `TradingSessions`。

### TODO - Variable Ingestion Plan

#### TIER1 - Product-Level Rules (`ProductMetaManager` / product YAML)

- [x] `StaticMeta.product`
- [x] `StaticMeta.exchange`
- [x] `StaticMeta.name`
- [x] `StaticMeta.position_date_type`
- [x] `StaticMeta.max_margin_side_algorithm`
- [x] `StaticMeta.combination_type`
- [x] `TransactionCostRule.effective_date`
- [x] `TransactionCostRule.commission_type`
- [x] `TransactionCostRule.open`
- [x] `TransactionCostRule.close_yesterday`
- [x] `TransactionCostRule.close_today`
- [x] `TransactionCostRule.scope`
- [x] `TransactionCostRule.condition`
- [x] `PriceTickRule.effective_date`
- [x] `PriceTickRule.price_tick`
- [x] `PriceTickRule.remain_old_rule_instruments`
- [x] `MultiplierRule.effective_date`
- [x] `MultiplierRule.multiplier`
- [x] `OrderVolumeLimitsRule.effective_date`
- [x] `OrderVolumeLimitsRule.max_market_order_volume`
- [x] `OrderVolumeLimitsRule.min_market_order_volume`
- [x] `OrderVolumeLimitsRule.max_limit_order_volume`
- [x] `OrderVolumeLimitsRule.min_limit_order_volume`
- [x] `BaseSessions.effective_date`
- [x] `BaseSessions.morning_sessions`
- [x] `BaseSessions.afternoon_sessions`
- [x] `BaseSessions.night_sessions`
- [x] `SessionOverrideRules.recurring_instrument_lifecycle_rules`
- [x] `SessionOverrideRules.scheduled_dated_overrides`
- [x] `SessionOverrideRules.realized_session_events`

Note: `session_override_rules` cannot be fully provided by BigQuant or Tushare. Ingestion should use the historical facts already collected in `docs/代码修改笔记/期货交易规则/交易时段规则特殊安排/中国期货市场交易时段规则特殊安排_历史事实_CodeX.md` and write them into product YAML files.

#### TIER2 - Instrument Registry (`InstrumentRegistryManager` / registry parquet)

- [x] `InstrumentRegistry.instrument`
- [x] `InstrumentRegistry.name`
- [x] `InstrumentRegistry.product`
- [x] `InstrumentRegistry.product_class`
- [x] `InstrumentRegistry.exchange`
- [x] `InstrumentRegistry.list_date`
- [x] `InstrumentRegistry.last_trade_date`
- [x] `InstrumentRegistry.delivery_year`
- [x] `InstrumentRegistry.delivery_month`
- [x] `InstrumentRegistry.start_deliv_date`
- [x] `InstrumentRegistry.end_deliv_date`
- [x] `InstrumentRegistry.exchange_inst`
- [x] `InstrumentRegistry.multiplier`

#### TIER3 - Instrument Daily Status (`InstrumentDailyStatusManager` / product-date parquet)

- [x] `InstrumentDailyStatus.trading_date`
- [x] `InstrumentDailyStatus.instrument`
- [x] `InstrumentDailyStatus.is_tradable`
- [x] `InstrumentDailyStatus.inst_life_phase`
- [x] `InstrumentDailyStatus.long_margin_ratio`
- [x] `InstrumentDailyStatus.short_margin_ratio`

#### Supporting Inputs

- [x] Exchange trading calendars from Tushare `fut_trade_cal`
- [x] External source code normalization between BigQuant and Tushare
- [x] Provenance fields / source snapshots for auditability

## 2026-06-22 

### TODO - 外部数据拉取

API权限：
* Tushare： 已付费一年。频次权限，每分钟频次200，每天总量上限 100000 次/个API。
* BigQuant：目前为试用版，试用截止日期：2026-06-29。频次权限，每周配额：50000000 cells。

#### 测试
- [x] 使用简单样例，测试 BigQuant 和 Tushare 的数据下载 API 可用性。

说明：
* 对于Tushare 和 BigQuant 的每张表，进行一次简单的API调用测试。
    - Tushare 数据表：`fut_basic`、`fut_trade_cal`、`fut_settle`。
    - BigQuant 数据表：`cn_future_product_info`、`cn_future_basic_info`、`cn_future_static_data`。
    - Double check：以上表名均已在 `external_sources.md` 外部数据画像中建立字段级画像或表级画像。
* API说明：
    - BigQuant： [bigquant SDK API 手册](https://bigquant.com/wiki/doc/064mz8zFJD)。 见 2. 数据模块 (dai)。
    - Tushare：见 `db/rules_ingestion/supplements/Tushare期货数据介绍/Tushare 期货数据库介绍.md`。
* 执行结果：已完成，6/6 张表 API 调用成功，且最终样例均返回非空 DataFrame。详细执行过程、样例文件、表级结果和发现的问题见 `execution_log.md` 中 `2026-06-22 - API Sample Availability Test`。

#### 拉取全量数据

- [x] 对Tushare的三张表和BigQuant的三张表，拉取一次全量数据到本地。

## 2026-06-24

### TODO - Ingestion Execution

任务描述：根据 `variable_ingestion_plan.md`，使用已拉取的原始数据执行 TIER1-TIER3 规则变量生成，并将生成过程、校验结果、人工假设和未决问题持续记录到 `execution_log.md`、`decisions.md` 和本任务清单中。

输入数据：
* BigQuant raw snapshots: `rules_ingestion/data/raw/20260622/bigquant`
* Tushare raw snapshots: `rules_ingestion/data/raw/20260622/tushare`

执行原则：
* 每个变量按 plan 中的来源、粒度转换、校验和输出格式执行。
* 若外部粒度无法安全压缩成本地模型粒度，先输出冲突样本和待决策事项，不静默入库。
* TIER1/TIER2/TIER3 必须复用同一套本地标准代码体系和交易所映射。
* 生成文件前后均需通过对应 manager 或模型约束进行读取/结构校验。

#### TIER1 - Product-Level Rules (`ProductMetaManager` / product YAML)

- [x] `StaticMeta.product`
- [x] `StaticMeta.exchange`
- [x] `StaticMeta.name`
- [x] `StaticMeta.position_date_type`
- [x] `StaticMeta.max_margin_side_algorithm`
- [x] `StaticMeta.combination_type`
- [x] `TransactionCostRule.effective_date`
- [x] `TransactionCostRule.commission_type`
- [x] `TransactionCostRule.open`
- [x] `TransactionCostRule.close_yesterday`
- [x] `TransactionCostRule.close_today`
- [x] `TransactionCostRule.scope`
- [x] `TransactionCostRule.condition`
- [ ] `PriceTickRule.effective_date`
- [ ] `PriceTickRule.price_tick`
- [ ] `PriceTickRule.remain_old_rule_instruments`
- [ ] `MultiplierRule.effective_date`
- [ ] `MultiplierRule.multiplier`
- [ ] `OrderVolumeLimitsRule.effective_date`
- [ ] `OrderVolumeLimitsRule.max_market_order_volume`
- [ ] `OrderVolumeLimitsRule.min_market_order_volume`
- [ ] `OrderVolumeLimitsRule.max_limit_order_volume`
- [ ] `OrderVolumeLimitsRule.min_limit_order_volume`
- [ ] `BaseSessions.effective_date`
- [ ] `BaseSessions.morning_sessions`
- [ ] `BaseSessions.afternoon_sessions`
- [ ] `BaseSessions.night_sessions`
- [ ] `SessionOverrideRules.recurring_instrument_lifecycle_rules`
- [ ] `SessionOverrideRules.scheduled_dated_overrides`
- [ ] `SessionOverrideRules.realized_session_events`

Note: `session_override_rules` cannot be fully generated from BigQuant or Tushare. Execution should combine the downloaded raw data with the historical facts already collected in `docs/代码修改笔记/期货交易规则/交易时段规则特殊安排/中国期货市场交易时段规则特殊安排_历史事实_CodeX.md`.

#### TIER2 - Instrument Registry (`InstrumentRegistryManager` / registry parquet)

- [ ] `InstrumentRegistry.instrument`
- [ ] `InstrumentRegistry.name`
- [ ] `InstrumentRegistry.product`
- [ ] `InstrumentRegistry.product_class`
- [ ] `InstrumentRegistry.exchange`
- [ ] `InstrumentRegistry.list_date`
- [ ] `InstrumentRegistry.last_trade_date`
- [ ] `InstrumentRegistry.delivery_year`
- [ ] `InstrumentRegistry.delivery_month`
- [ ] `InstrumentRegistry.start_deliv_date`
- [ ] `InstrumentRegistry.end_deliv_date`
- [ ] `InstrumentRegistry.exchange_inst`
- [ ] `InstrumentRegistry.multiplier`

#### TIER3 - Instrument Daily Status (`InstrumentDailyStatusManager` / product-date parquet)

- [ ] `InstrumentDailyStatus.trading_date`
- [ ] `InstrumentDailyStatus.instrument`
- [ ] `InstrumentDailyStatus.is_tradable`
- [ ] `InstrumentDailyStatus.inst_life_phase`
- [ ] `InstrumentDailyStatus.long_margin_ratio`
- [ ] `InstrumentDailyStatus.short_margin_ratio`

#### Supporting Execution Items

- [ ] Build and validate external-to-local product, instrument and exchange code normalization.
- [ ] Validate Tushare `fut_trade_cal` exchange calendars against BigQuant daily records.
- [ ] Generate raw-to-derived provenance and conflict reports for auditability.
- [ ] Run manager/model load checks for generated TIER1 YAML, TIER2 registry parquet and TIER3 daily status parquet.
