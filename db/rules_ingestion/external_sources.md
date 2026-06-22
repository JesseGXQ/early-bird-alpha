# External Sources

本文档记录外部数据源的表结构、字段语义、数据粒度、适用范围和入库注意事项。

## Source Access Status

| Source | Status | Notes |
| --- | --- | --- |
| BigQuant `cn_future_product_info` | 可访问 | 期货品种信息，官方页面可读取字段结构。 |
| BigQuant `cn_future_basic_info` | 可访问 | 期货合约基本信息，官方页面可读取字段结构。 |
| BigQuant `cn_future_static_data` | 可访问 | 合约-交易日静态 / 状态数据，官方页面可读取字段结构。 |
| BigQuant `cn_future_level1_snapshot` | 可访问 | 期货快照行情，官方页面可读取字段结构。 |
| Tushare `fut_basic` / `fut_trade_cal` / `fut_settle` / `ft_limit` | 已由本地补充文档提供 | 字段级画像来自 `db/rules_ingestion/supplements/Tushare期货数据介绍/Tushare 期货数据库介绍.md`。 |

## BigQuant

BigQuant 的期货数据按表可分为品种级、合约级、合约-交易日级和盘中快照级。对本地交易规则数据库最有价值的是 `cn_future_product_info`、`cn_future_basic_info` 和 `cn_future_static_data`；`cn_future_level1_snapshot` 更偏行情和盘中状态验证。

BigQuant 合约代码有平台后缀，例如 `rb2408.SHF`、`SR409.CZC`。交易所后缀为 3 位大写：`CZC`、`DCE`、`SHF`、`CFE`、`INE`、`GFE`。需要注意品种代码大小写：郑商所多为大写，大商所、上期所、能源中心、广期所多为小写。

### `cn_future_product_info`

- 官方页面：<https://bigquant.com/data/datasources/cn_future_product_info>
- 数据粒度：品种级。
- 更新特征：若有新品种上市，最晚在上市当天 19:00 前更新。
- 主要用途：本地 TIER 1 品种级基础属性、当前规则线索、交易时间和手续费初始线索。

字段画像：

| Field | Type | Meaning | Local relevance |
| --- | --- | --- | --- |
| `code` | string | 期货品种代码 | `StaticMeta.product` |
| `exchange` | string | 交易所 | `StaticMeta.exchange` |
| `name` | string | 品种中文简称 | `StaticMeta.name` |
| `price_tick` | double | 当前最小变动价格 | `PriceTickRule` 当前值线索 |
| `old_price_tick` | double | 旧最小变动价格 | 仅能提示发生过变更，不能单独确定 `effective_date` |
| `multiplier` | double | 合约大小 | `MultiplierRule` 当前值线索 |
| `commission_type` | int8 | 费率类型 | `TransactionCostRule.commission_type` 线索，需确认编码 |
| `commission_open` | double | 开仓费率 | `TransactionCostRule.open` 当前值线索 |
| `commission_today` | double | 平今费率 | `TransactionCostRule.close_today` 当前值线索 |
| `commission_yesterday` | double | 平昨费率 | `TransactionCostRule.close_yesterday` 当前值线索 |
| `trading_period` | string | 交易时间段 | `BaseSessions` 当前值线索 |
| `month` | string | 交易月份 | 合约生命周期 / 上市月份规则线索 |
| `last_trading_day` | string | 最后交易日规则描述 | 合约生命周期规则线索 |
| `margin_rate` | double | 保证金费率 | 保证金规则当前值线索 |
| `decimal_digit` | int8 | 小数点位数 | 价格格式校验 |
| `old_code` | string | 旧品种代码 | 品种代码变更映射线索 |

入库判断：

- 可直接用于 `product`、`exchange`、`name` 的初始入库。
- 对 `price_tick`、`multiplier`、手续费、交易时间等字段，只能视为当前品种级规则线索；若本地字段需要版本管理，必须结合日频表、交易所公告或其他来源确认历史变更。
- `commission_type` 的枚举编码需在 API 实样或官方补充文档中确认后，才能映射为本地 `flat` / `ratio`。

### `cn_future_basic_info`

- 官方页面：<https://bigquant.com/data/datasources/cn_future_basic_info>
- 数据粒度：合约级。
- 更新频率：不定期。
- 主要用途：本地 TIER 2 `InstrumentRegistry` 的候选主来源。

字段画像：

| Field | Type | Meaning | Local relevance |
| --- | --- | --- | --- |
| `instrument` | string | 合约代码 | `InstrumentRegistry.instrument` |
| `name` | string | 合约名称 | `InstrumentRegistry.name` |
| `exchange` | string | 交易市场 | `InstrumentRegistry.exchange` |
| `list_date` | timestamp | 上市日期 | `InstrumentRegistry.list_date` |
| `delist_date` | timestamp | 退市日期 | 候选映射到 `last_trade_date`，需与语义校验 |
| `last_deliver_date` | timestamp | 最后交割日 | 候选映射到 `end_deliv_date` 或交割属性，需对比交易所语义 |
| `product_code` | string | 品种代码 | `InstrumentRegistry.product` |
| `trading_code` | string | 交易代码 | `InstrumentRegistry.exchange_inst` 候选 |
| `price_tick` | double | 最小变动价位 | 合约级静态线索，可辅助校验品种规则 |
| `multiplier` | double | 合约乘数 | `InstrumentRegistry.multiplier` 候选 |
| `uinstrument` | string | 未说明 | 暂不入库，待样本确认 |

入库判断：

- 适合构建合约注册表，但字段不完全覆盖本地 `InstrumentRegistry`，例如 `delivery_year`、`delivery_month`、`start_deliv_date` 可能需要从 `cn_future_static_data` 或合约代码解析补充。
- `delist_date`、`last_deliver_date` 与本地 `last_trade_date`、`end_deliv_date` 的语义不能默认等同，需要与交易所或其他来源校验。
- 表内 `price_tick` 和 `multiplier` 可用于校验 TIER 1 规则是否能解释所有合约，但不宜直接替代版本化规则。

### `cn_future_static_data`

- 官方页面：<https://bigquant.com/data/datasources/cn_future_static_data>
- 数据粒度：合约-交易日级。
- 采集时点：每个交易日夜盘开始前获取每个合约当日状态。
- 主要用途：本地 TIER 2 注册属性补全、TIER 3 日频状态，以及从合约-日期数据推断 TIER 1 版本化规则。

字段画像：

| Field | Type | Meaning | Local relevance |
| --- | --- | --- | --- |
| `date` | timestamp | 日期 | 日频主键候选 |
| `trading_day` | int32 | 交易日 | `InstrumentDailyStatus.trading_date` 候选 |
| `instrument` | string | 合约代码 | 合约主键 |
| `name` | string | 合约名称 | 注册属性校验 |
| `exchange` | string | 交易所 | 注册属性校验 |
| `product_id` | string | 品种代码 | 品种归属 |
| `trading_code` | string | 交易代码 | 报单代码候选 |
| `product_class` | string | 产品类型 | `InstrumentRegistry.product_class` 候选，需编码映射 |
| `open_date` | timestamp | 合约开始交易日期 | `InstrumentRegistry.list_date` 候选 |
| `expire_date` | timestamp | 合约到期日 | 与 `last_trade_date` / 到期日语义需校验 |
| `delivery_year` | int32 | 交割年份 | `InstrumentRegistry.delivery_year` |
| `delivery_month` | int8 | 交割月份 | `InstrumentRegistry.delivery_month` |
| `start_deliv_date` | timestamp | 开始交割日 | `InstrumentRegistry.start_deliv_date` |
| `end_deliv_date` | timestamp | 最后交割日 | `InstrumentRegistry.end_deliv_date` |
| `volume_multiplier` | double | 合约数量乘数 | `MultiplierRule` / `InstrumentRegistry.multiplier` |
| `price_tick` | double | 价格档位 | `PriceTickRule` 历史推断核心字段 |
| `max_market_order_volume` | int32 | 市价单最大下单量 | `OrderVolumeLimitsRule.max_market_order_volume` |
| `min_market_order_volume` | int32 | 市价单最小下单量 | `OrderVolumeLimitsRule.min_market_order_volume` |
| `max_limit_order_volume` | int32 | 限价单最大下单量 | `OrderVolumeLimitsRule.max_limit_order_volume` |
| `min_limit_order_volume` | int32 | 限价单最小下单量 | `OrderVolumeLimitsRule.min_limit_order_volume` |
| `inst_life_phase` | string | 合约生命周期状态 | `InstrumentDailyStatus.inst_life_phase` |
| `is_trading` | int8 | 当前交易状态 | `InstrumentDailyStatus.is_tradable` 候选 |
| `long_margin_ratio` | double | 多头保证金率 | `InstrumentDailyStatus.long_margin_ratio` |
| `short_margin_ratio` | double | 空头保证金率 | `InstrumentDailyStatus.short_margin_ratio` |
| `position_date_type` | int8 | 持仓日期类型 | `StaticMeta.position_date_type` 候选，需编码映射 |
| `position_type` | int8 | 持仓类型 | 暂待业务含义确认 |
| `max_margin_side_algorithm` | int8 | 是否使用大额单边保证金算法 | `StaticMeta.max_margin_side_algorithm` 候选 |
| `combination_type` | int8 | 组合类型 | `StaticMeta.combination_type` 候选 |
| `create_date` | timestamp | 数据生成日期 | provenance / 快照审计字段 |

入库判断：

- 这是规则推断中最关键的 BigQuant 表，因为它提供合约-交易日级历史截面。
- 对本地 TIER 1 品种级版本化规则，需要先做粒度压缩。例如下单量限制应校验“同一品种、同一交易日、所有合约值是否一致”；若不一致，说明本地模型可能需要 `scope` 或例外表达。
- `price_tick` 可用于识别 `effective_date`，并区分一刀切与新老划断：新老划断时需要记录仍保持旧规则的合约列表。
- 日频状态字段适合直接生成 `InstrumentDailyStatus`，但 `is_trading`、`inst_life_phase` 的编码和值域需要用样本数据确认。

### `cn_future_level1_snapshot`

- 官方页面：<https://bigquant.com/data/datasources/cn_future_level1_snapshot>
- 数据粒度：合约-盘中时间级快照。
- 更新频率：每个交易日。
- 官方说明的表主键：`date`、`instrument`。
- 主要用途：行情、涨跌停、成交和持仓快照；对交易规则库是辅助来源，不是主要规则来源。

字段画像：

| Field | Type | Meaning | Local relevance |
| --- | --- | --- | --- |
| `date` | timestamp | 快照时间 / 日期 | 盘中时间索引 |
| `trading_day` | int32 | 交易日 | 交易日校验 |
| `time` | int32 | 盘中时间 | 交易时段验证线索 |
| `instrument` | string | 合约代码 | 合约主键 |
| `product_code` | string | 品种代码 | 品种归属 |
| `trading_code` | string | 交易代码 | 报单代码校验 |
| `open` / `high` / `low` / `price` | float | OHLC / 最新价或收盘价字段 | 行情快照 |
| `bid_price1` / `ask_price1` | float | 买一 / 卖一价 | 行情快照 |
| `bid_volume1` / `ask_volume1` | int64 | 买一 / 卖一量 | 行情快照 |
| `volume` | int64 | 成交量 | 行情快照 |
| `amount` | double | 成交金额 | 行情快照 |
| `open_interest` | int32 | 持仓量 | 行情快照 |
| `pre_open_interest` | int32 | 昨持仓量 | 行情快照 |
| `settle` / `pre_settle` | float | 结算价 / 前结算价 | 日频行情校验 |
| `upper_limit` / `lower_limit` | float | 涨停价 / 跌停价 | 涨跌停规则 / 日频快照校验 |
| `pre_close` | float | 前收盘价 | 日频行情校验 |

入库判断：

- 可辅助验证交易时段：实际有快照的时间段可以与 `BaseSessions` / `TradingSessions` 做交叉检查。
- 可辅助生成或校验日频快照数据库中的涨跌停、结算价、持仓量等字段。
- 不适合作为期货交易规则字段的主来源，因为它是行情事实，不是规则定义。

## Tushare

Tushare 的期货数据由本地补充文档完成字段级画像。交易所代码使用 `CFFEX`、`DCE`、`CZCE`、`SHFE`、`INE`、`GFEX` 等格式；合约代码通常为 `CU1811.SHF`、`P0805.DCE` 这类 Tushare 后缀格式。与 BigQuant 的 `SHF`、`CZC` 等后缀基本接近但不完全一致，后续需要建立交易所代码标准化映射。

### `fut_basic`

- 官方文档：<https://tushare.pro/document/2?doc_id=135>
- 本地补充：`db/rules_ingestion/supplements/Tushare期货数据介绍/Tushare 期货数据库介绍.md`
- 接口描述：获取期货合约列表数据。
- 数据粒度：合约级。
- 限制：单次最大 10000 行；需要至少 2000 积分。
- 主要用途：本地 TIER 2 `InstrumentRegistry` 的候选来源，也可补充合约交易时间说明。

输入参数：

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `exchange` | str | Y | 交易所代码 |
| `fut_type` | str | N | 合约类型，`1` 普通合约，`2` 主力与连续合约，默认全部 |
| `fut_code` | str | N | 标准合约代码，如白银 `AG`、苹果 `AP` |
| `list_date` | str | N | 上市开始日期，格式 `YYYYMMDD` |

输出字段画像：

| Field | Type | Meaning | Local relevance |
| --- | --- | --- | --- |
| `ts_code` | str | 合约代码 | `InstrumentRegistry.instrument` 候选 |
| `symbol` | str | 交易标识 | `InstrumentRegistry.exchange_inst` 候选 |
| `exchange` | str | 交易市场 | `InstrumentRegistry.exchange` |
| `name` | str | 中文简称 | `InstrumentRegistry.name` |
| `fut_code` | str | 合约产品代码 | `InstrumentRegistry.product` |
| `multiplier` | float | 合约乘数，只适用于国债期货、指数期货 | `InstrumentRegistry.multiplier` 的部分来源，覆盖范围有限 |
| `trade_unit` | str | 交易计量单位 | 注册属性补充说明 |
| `per_unit` | float | 交易单位（每手） | 合约乘数 / 交易单位校验线索 |
| `quote_unit` | str | 报价单位 | 价格单位说明 |
| `quote_unit_desc` | str | 最小报价单位说明 | `PriceTickRule` 的文本线索，需解析 |
| `d_mode_desc` | str | 交割方式说明 | 注册属性补充说明 |
| `list_date` | str | 上市日期 | `InstrumentRegistry.list_date` |
| `delist_date` | str | 最后交易日期 | `InstrumentRegistry.last_trade_date` 候选 |
| `d_month` | str | 交割月份 | `delivery_year` / `delivery_month` 解析线索 |
| `last_ddate` | str | 最后交割日 | `InstrumentRegistry.end_deliv_date` 候选 |
| `trade_time_desc` | str | 交易时间说明 | `BaseSessions` / 交易时段规则文本线索 |

入库判断：

- 适合作为 `InstrumentRegistry` 的重要来源，尤其是 `list_date`、`delist_date`、`last_ddate`、`fut_code`、`symbol` 等字段。
- `multiplier` 明确说明只适用于国债期货、指数期货，因此不能作为全市场合约乘数主来源；普通商品期货更可能需要依赖 BigQuant 或交易所规则。
- `quote_unit_desc`、`trade_time_desc` 是文本说明，适合做规则校验或人工辅助，不宜直接无解析入库。
- 应过滤 `fut_type=1` 的普通合约，避免主力、连续合约污染合约注册表。

### `fut_trade_cal`

- 官方文档：<https://tushare.pro/document/2?doc_id=467>
- 本地补充：`db/rules_ingestion/supplements/Tushare期货数据介绍/Tushare 期货数据库介绍.md`
- 接口描述：获取各大期货交易所交易日历数据。
- 数据粒度：交易所-自然日。
- 权限：需要至少 2000 积分。
- 主要用途：构建各交易所交易日历，作为日频状态、日频快照和规则版本生效日期的基础索引。

输入参数：

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `exchange` | str | N | 交易所代码 |
| `start_date` | str | N | 开始日期 |
| `end_date` | str | N | 结束日期 |
| `is_open` | int | N | 是否交易，`0` 休市，`1` 交易 |

输出字段画像：

| Field | Type | Meaning | Local relevance |
| --- | --- | --- | --- |
| `exchange` | str | 交易所 | 交易日历分区键 |
| `cal_date` | str | 日历日期 | 自然日 |
| `is_open` | int | 是否交易，`0` 休市，`1` 交易 | 交易日判断 |
| `pretrade_date` | str | 上一个交易日 | 交易日序列校验和回溯 |

入库判断：

- 适合作为交易日历主来源之一。
- 需要确认不同交易所日历是否完全一致；若不一致，本地应按交易所维护日历。
- 夜盘归属交易日、节假日前夜盘安排等问题不一定由该表直接表达，仍需交易时段规则和交易所公告补充。

### `fut_settle`

- 官方文档：<https://tushare.pro/document/2?doc_id=141>
- 本地补充：`db/rules_ingestion/supplements/Tushare期货数据介绍/Tushare 期货数据库介绍.md`
- 接口描述：获取每日结算参数数据，包括交易和交割费率等。
- 数据粒度：合约-交易日级。
- 限制：单次最大 1600 行，可按日期循环；需要至少 2000 积分。
- 主要用途：手续费、保证金率、结算价等历史日频参数；特别适合辅助推断 `TransactionCostRule`。

输入参数：

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `trade_date` | str | N | 交易日期，`trade_date` / `ts_code` 至少输入一个 |
| `ts_code` | str | N | 合约代码 |
| `start_date` | str | N | 开始日期 |
| `end_date` | str | N | 结束日期 |
| `exchange` | str | N | 交易所代码 |

输出字段画像：

| Field | Type | Meaning | Local relevance |
| --- | --- | --- | --- |
| `ts_code` | str | 合约代码 | 合约主键 |
| `trade_date` | str | 交易日期 | `InstrumentDailyStatus.trading_date` / 日频索引 |
| `settle` | float | 结算价 | 日频快照数据库候选字段 |
| `trading_fee_rate` | float | 交易手续费率 | `TransactionCostRule` 按比例收费线索 |
| `trading_fee` | float | 交易手续费 | `TransactionCostRule` 按手数收费线索 |
| `delivery_fee` | float | 交割手续费 | 暂不属于当前本地模型，可记录为扩展候选 |
| `b_hedging_margin_rate` | float | 买套保交易保证金率 | 保证金扩展候选 |
| `s_hedging_margin_rate` | float | 卖套保交易保证金率 | 保证金扩展候选 |
| `long_margin_rate` | float | 买投机交易保证金率 | `InstrumentDailyStatus.long_margin_ratio` 候选 |
| `short_margin_rate` | float | 卖投机交易保证金率 | `InstrumentDailyStatus.short_margin_ratio` 候选 |
| `offset_today_fee` | float | 平今仓手续率 | `TransactionCostRule.close_today` 核心线索 |
| `exchange` | str | 交易所 | 分区与校验字段 |

入库判断：

- 是手续费历史推断的关键 Tushare 表，因为它提供合约-交易日级手续费和保证金参数。
- `trading_fee` 与 `trading_fee_rate` 可能分别对应按手数和按比例收取，但二者与本地 `commission_type` 的映射需要通过样本和交易所规则确认。
- 文档未区分开仓与平昨手续费；初步假设 `open == close_yesterday` 需要公告或样本验证。
- `offset_today_fee` 明确对应平今仓手续费线索，但字段描述写作“手续率”，需确认它在不同品种上是金额还是比例，或是否跟随 `trading_fee_rate` / `trading_fee` 的计价方式。
- 由于单次 1600 行，批量下载应按交易日和交易所循环，并保留原始快照。

### `ft_limit`

- 官方文档：<https://tushare.pro/document/2?doc_id=368>
- 本地补充：`db/rules_ingestion/supplements/Tushare期货数据介绍/Tushare 期货数据库介绍.md`
- 接口描述：获取所有期货合约每天的涨跌停价格及最低保证金率，数据开始于 2005 年。
- 数据粒度：合约-交易日级。
- 限制：单次最大 4000 行，可按日期、合约代码等循环；需要 5000 积分。
- 主要用途：日频快照数据库中的盘前涨跌停价格和最低保证金率，也可与 BigQuant 快照表交叉校验。

输入参数：

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `ts_code` | str | N | 合约代码 |
| `trade_date` | str | N | 交易日期，格式 `YYYYMMDD` |
| `start_date` | str | N | 开始日期 |
| `end_date` | str | N | 结束日期 |
| `cont` | str | N | 品种 / 合约产品代码，例如 `CU` |
| `exchange` | str | N | 交易所代码，例如 `DCE` |

输出字段画像：

| Field | Type | Meaning | Local relevance |
| --- | --- | --- | --- |
| `trade_date` | str | 交易日期 | 日频索引 |
| `ts_code` | str | TS 合约代码 | 合约主键 |
| `name` | str | 合约名称 | 注册属性校验 |
| `up_limit` | float | 涨停价 | 日频快照字段 |
| `down_limit` | float | 跌停价 | 日频快照字段 |
| `m_ratio` | float | 最低交易保证金率（%） | 保证金校验 / 扩展候选 |
| `cont` | str | 合约产品代码 | 品种归属 |
| `exchange` | str | 交易所代码 | 分区与校验字段 |

入库判断：

- 适合构建或校验日频快照数据库中的盘前涨跌停价格。
- 可与 BigQuant `cn_future_level1_snapshot.upper_limit` / `lower_limit` 对照，但需注意一个是盘前参数，一个是盘中快照字段。
- `m_ratio` 是最低交易保证金率，和 BigQuant / Tushare 结算参数中的多空、投机、套保保证金率不是完全同一语义，不能直接混用。

## Cross-Source Notes

- BigQuant `cn_future_product_info` 更适合提供品种级当前规则线索；BigQuant `cn_future_static_data` 更适合从合约-交易日历史中反推版本化规则。
- Tushare 结算参数预计对手续费历史更关键，因为 BigQuant 品种信息中的手续费字段粒度偏粗，难以完整还原历史上的平今特殊调整。
- 交易所公告仍应作为高优先级证据源，尤其用于确认 `price_tick` 变更、手续费调整、交易时段调整和特殊合约范围。
- 后续变量级方案中，应为每个本地字段明确主来源、辅助来源、校验来源和人工确认条件。

## References

- BigQuant 期货品种信息：<https://bigquant.com/data/datasources/cn_future_product_info>
- BigQuant 期货基本信息：<https://bigquant.com/data/datasources/cn_future_basic_info>
- BigQuant 期货静态信息：<https://bigquant.com/data/datasources/cn_future_static_data>
- BigQuant 期货快照数据：<https://bigquant.com/data/datasources/cn_future_level1_snapshot>
- Tushare 期货合约信息表：<https://tushare.pro/document/2?doc_id=135>
- Tushare 期货交易日历：<https://tushare.pro/document/2?doc_id=467>
- Tushare 结算参数：<https://tushare.pro/document/2?doc_id=141>
- Tushare 期货合约涨跌停价格（盘前）：<https://tushare.pro/document/2?doc_id=368>
