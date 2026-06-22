# Task Log

## 2026-06-21

- 任务描述：调研 BigQuant 与 Tushare 的期货相关外部数据源说明页面，梳理表结构、数据粒度、字段语义、适用范围和入库注意事项，并更新 `external_sources.md`。
- 执行结果：已完成 BigQuant `cn_future_product_info`、`cn_future_basic_info`、`cn_future_static_data`、`cn_future_level1_snapshot` 的字段级画像和本地入库适用性分析；Tushare 官方文档当前跳转登录页，未能可靠获取字段表，已在 `external_sources.md` 中标记为待本地文档补充。

## 2026-06-21

- 任务描述：读取本地补充文档 `db/rules_ingestion/supplements/Tushare期货数据介绍/Tushare 期货数据库介绍.md`，补全 Tushare 期货数据源画像。
- 执行结果：已补充 Tushare `fut_basic`、`fut_trade_cal`、`fut_settle`、`ft_limit` 的输入参数、输出字段、本地模型关联、数据粒度、权限限制和入库注意事项，并更新 `external_sources.md`。

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
