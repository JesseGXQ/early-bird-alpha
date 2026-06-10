# 期货交易规则数据库设计审查报告（CodeX）

生成日期：2026-06-09

## 0. 审查范围与结论

本报告基于以下材料审查：

- `docs/代码修改笔记/20260531 代码修改笔记.md` 中“本地期货信息数据库架构设计”章节。
- `data/metadata/product_meta_manager.py`
- `data/metadata/instrument_registry_manager.py`
- `data/metadata/instrument_daily_status_manager.py`

按本次需求对齐，本报告重点审查数据库架构、字段归属、当前配套代码是否能支撑该架构，以及它与风险管理、保证金、手续费、撮合、交易日历等下游组件的衔接能力。不评价具体数据来源选择、采集脚本和入库流程编排。

总体结论：当前“三层规则数据库”方向是正确的，而且已经抓住了中国期货交易规则里最关键的几个难点：品种级规则版本化、合约生命周期、日频保证金状态、交易时段历史、最小变动价位新老划断。对于一个项目初始阶段，这是很好的底座。

但如果目标是逐步走向工业级系统，目前设计里有几个结构性风险需要尽早厘清：

1. 部分“品种级规则”在真实业务中会出现“同一品种、同一日期、不同合约适用不同规则”的情况，典型是 `price_tick`、`multiplier`、手续费特殊规则。因此查询键不能长期停留在 `(product, trading_date)`，关键 API 应升级为 `(instrument, trading_date)` 或至少接受可选 `instrument`。
2. `multiplier_history` 同时存在于 Tier 1，`multiplier` 又存在于 Tier 2，容易破坏 SSOT。建议明确：运行时计算合约价值、保证金、盈亏时，Tier 2 的合约固定乘数是唯一真实来源；Tier 1 的乘数历史只作为生成/校验合约注册表的规则来源。
3. 交易时段目前只表达“品种-日期”的常规规则，尚未承载“最后交易日提前收盘”“临近交割月无夜盘”等合约生命周期例外。这个问题会直接影响分钟线对齐、撮合时段校验、日内策略尾盘处理。
4. Tier 3 的 `is_trading` 和 `inst_life_phase` 需要重新定义语义。CTP 的 `IsTrading` 更接近查询时点状态，而你的表是日频状态表。如果不定义清楚，历史回测里容易把“当时瞬时状态”误用成“整日可交易状态”。
5. 当前三层 Manager 是不错的底层读库对象，但下游组件不宜直接拼接三层逻辑。建议后续增加一个只读的 `TradingRuleService` / `InstrumentRuleService` 门面，为下游提供“合约 + 日期 -> 完整交易规则”的统一接口。

## 1. 三层架构设计评估

### 1.1 当前分层是合理的

当前三层分别是：

- Tier 1：`product_meta`，品种级规则配置，包含静态规则和带 `effective_date` 的历史规则。
- Tier 2：`instrument_registry`，合约注册层，记录合约终身不变的注册属性。
- Tier 3：`instrument_daily_status`，合约-交易日粒度的动态状态。

这个分层符合期货系统的基本事实：

- 有些规则属于品种或交易所，例如持仓日期类型、常规交易时段、下单量限制。
- 有些属性属于合约出生时确定、之后不变，例如交割年月、上市日期、交割日期、固定合约乘数。
- 有些属性随交易日变化，例如保证金率、生命周期状态、是否可交易。

这种结构比把所有字段揉进一张静态表更好，也比完全依赖行情快照更干净。它有利于防止未来函数、减少字段冗余，并且让下游组件按职责读取所需规则。

### 1.2 核心结构性问题：规则粒度不总是“品种 + 日期”

当前 Tier 1 的版本化规则大多通过 `(product, trading_date)` 查询。这对大部分常规规则够用，但对工业级期货系统并不总是成立。

典型反例：

- `price_tick`：你已经意识到铁矿石这类新老划断问题，并设计了 `remain_old_rule_instruments`。但当前 `ProductMetaManager.get_price_tick(product, trading_date)` 没有 `instrument` 参数，因此 Manager 本身无法判断该合约是否在“老规则保留列表”里。
- `multiplier`：郑醇/甲醇这类乘数新老划断，本质上是合约级结果。对于已注册合约，运行时不应再用 `(product, trading_date)` 推断乘数。
- `transaction_cost_history`：国内市场存在“某些合约”“主力合约”“日内交易”“特定日期区间”手续费不同的情况。单纯品种级 `open/close_today/close_yesterday` 只能覆盖粗粒度版本。

建议原则：

- Tier 1 可以继续保存“规则历史”，但关键查询 API 应该逐步支持 `instrument`。
- 对合约终身不变的规则结果，落到 Tier 2 后应由 Tier 2 提供运行时查询。
- 对合约-日期动态规则结果，落到 Tier 3 或衍生规则服务后应由 `(instrument, trading_date)` 查询。

### 1.3 不建议增加第四个“万能规则层”

当前不需要一开始就新增一个包罗万象的复杂规则数据库。更务实的做法是：

- 继续保留三层数据库。
- 在三层之上新增只读查询门面，例如 `TradingRuleService`。
- 让门面负责跨层组合与特殊规则解释。

这样可以避免底层 schema 过早复杂化，同时防止下游组件各自复制一套规则拼接逻辑。

## 2. 字段归属审查

### 2.1 Tier 1：`product_meta`

| 字段/规则 | 当前归属 | 评价 | 建议 |
| --- | --- | --- | --- |
| `product` | Tier 1 | 合理。 | 保留。建议统一大小写和文件命名规范。 |
| `exchange` | Tier 1 | 合理，但它更像 product 的归属属性。 | 保留。若后续出现跨交易所同代码品种，应以 `(exchange, product)` 作为唯一键。 |
| `position_type` | Tier 1 | 可接受。国内期货基本按交易所/品种规则。 | 保留。类型上建议避免 `bool`，改用枚举值更清晰，例如 `net/gross`。 |
| `position_date_type` | Tier 1 | 合理。对平今/平昨拆单非常关键。 | 保留。也建议用枚举值，例如 `distinguish_today_yesterday`。 |
| `max_margin_side_algorithm` | Tier 1 | 作为简化 flag 可以，但真实组合保证金规则比布尔值复杂。 | 初期保留。未来应版本化，或迁移到更专门的组合保证金规则模型。 |
| `combination_type` | Tier 1 | 当前过粗。跨期、跨品种、对锁、组合优惠往往有条件和生效期。 | 初期可保留为粗标记。不要让保证金引擎过度依赖它完成真实组合保证金。 |
| `transaction_cost_history` | Tier 1 | 方向正确，但粒度偏粗。 | 保留；建议未来增加 `scope/condition`，支持按合约、主力、投机/套保、开平、平今平昨细分。 |
| `price_tick_history` | Tier 1 | 放 Tier 1 是合理的，但查询必须考虑合约。 | 保留；Manager API 建议升级为 `get_price_tick(product, trading_date, instrument=None)` 或由统一门面提供 `get_price_tick(instrument, trading_date)`。 |
| `sessions_history` | Tier 1 | 常规交易时段放 Tier 1 合理。 | 保留；另需表达合约生命周期导致的 session override。 |
| `multiplier_history` | Tier 1 | 作为“生成合约注册表的规则历史”合理；作为运行时查询来源不稳。 | 建议改名或明确语义为 `default_multiplier_history`。运行时乘数以 Tier 2 为准。 |
| `order_volume_limits_history` | Tier 1 | 大体合理。 | 保留。未来可考虑按订单类型、业务类型、交易所特殊限制扩展。 |

Tier 1 当前最值得优先调整的不是增加很多字段，而是明确哪些字段只是“规则生成依据”，哪些字段是下游运行时可以直接消费的规则。

### 2.2 Tier 2：`instrument_registry`

当前字段：

- `instrument`
- `name`
- `product`
- `exchange`
- `list_date`
- `delivery_year`
- `delivery_month`
- `start_deliv_date`
- `end_deliv_date`
- `expire_date`
- `exchange_inst`
- `multiplier`

整体方向合理。Tier 2 应该是“合约出生证明”，记录一经确认后不应变化的字段。

建议补充或澄清：

1. 明确 `expire_date` 是否等同“最后交易日”。
   文档中有地方写 `expire_date` 是“合约到期日”，也有 CTP 对照里写它是“合约最后交易日”。对风控引擎来说，“最后交易日”是非常核心的字段，建议显式增加或至少语义固定为：
   - `last_trade_date`
   - `expire_date`
   - `start_deliv_date`
   - `end_deliv_date`

   如果数据源只能给一个日期，也建议在 schema 注释里写明该字段等价关系。

2. 建议增加 `product_class`。
   即便当前只做期货，`product_class` 也能帮助你明确排除期权、组合、套利合约，避免后续误读 CTP 或第三方静态表。

3. `multiplier` 应是运行时合约乘数的 SSOT。
   对于任何 PnL、名义价值、保证金、手续费金额换算，优先查询 Tier 2 的 `multiplier`，不要再用 Tier 1 的 `(product, date)` 规则推断。

4. `product` 与 `exchange` 在 Tier 2 中虽然看似冗余，但实际是合理的。
   这属于必要的反范式化，能让合约注册表独立回答“这个合约属于哪个品种/交易所”，对下游查询效率很重要。

### 2.3 Tier 3：`instrument_daily_status`

当前字段：

- `trading_date`
- `instrument`
- `inst_life_phase`
- `is_trading`
- `long_margin_ratio`
- `short_margin_ratio`

Tier 3 的方向正确，但语义需要更硬一些。

建议：

1. 重新定义 `is_trading`。
   如果它来自 CTP 的 `IsTrading`，它更像查询时刻的瞬时状态；如果你要做历史回测，日频表里的 `is_trading` 应定义为“该交易日该合约是否可交易/是否整日停牌/是否处于有效上市交易状态”。建议考虑命名为：
   - `is_tradable_on_date`
   - 或 `is_daily_tradable`

2. `inst_life_phase` 与 `is_trading` 有重叠但不完全重复。
   `inst_life_phase` 表示生命周期，`is_trading` 表示交易可用性。建议保留两者，但在 schema 中写清楚组合关系，例如：
   - 未上市：`inst_life_phase=not_started`, `is_daily_tradable=False`
   - 正常交易：`started`, `True`
   - 暂停交易：`paused`, `False`
   - 已退市：`expired`, `False`

3. 保证金率字段需要明确来源。
   `long_margin_ratio` / `short_margin_ratio` 是交易所保证金，还是期货公司保证金，还是回测使用的统一保证金？建议至少在字段说明中固定语义。更稳妥的长期设计是：
   - `exchange_long_margin_ratio`
   - `exchange_short_margin_ratio`
   - 可选的 `broker_long_margin_ratio`
   - 可选的 `broker_short_margin_ratio`

4. 涨跌停价放 Daily Snapshot 是可接受的。
   这不是错误。涨跌停价既是交易规则约束，也是每日行情快照的一部分。关键是后续风控引擎需要一个统一接口同时拿到 Tier 3 保证金状态和 Daily Snapshot 的 `upper_price_limit/lower_price_limit`，不要让风控直接到处读文件。

## 3. 缺失字段、冗余字段与命名风险

### 3.1 建议补充的字段

优先级较高：

- `last_trade_date`：建议在 Tier 2 显式存在，或将 `expire_date` 的语义固定为最后交易日。
- `product_class`：建议在 Tier 2 存储，用于区分期货、期权、组合合约。
- `margin_ratio_source` 或明确字段命名：至少澄清 Tier 3 保证金率来自交易所还是经纪商。
- `schema_version`：可以先放在 YAML 顶层或表级 metadata 中，方便以后迁移。

中期可考虑：

- `trading_status_reason`：用于解释暂停交易、退市、异常状态原因。
- `session_override_rules`：用于表达最后交易日提前收盘、交割月无夜盘等规则。
- `commission_scope` / `commission_condition`：用于表达主力合约、特定合约、特定开平标志下的手续费例外。

暂时不建议急着加入：

- 期权相关字段，如 `underlying_instr_id`、`strike_price`、`options_type`。如果当前系统目标是期货 CTA，先不要让期权字段污染期货主路径。

### 3.2 可能冗余或语义冲突的字段

1. Tier 1 的 `multiplier_history` 与 Tier 2 的 `multiplier`。
   这是当前最明显的 SSOT 风险。建议明确：Tier 2 的 `multiplier` 是合约运行时真实乘数；Tier 1 的 `multiplier_history` 是生成/校验 Tier 2 的规则历史。

2. Tier 3 的 `inst_life_phase` 与 `is_trading`。
   这两个字段可以共存，但必须定义差异。否则下游可能不知道应该以哪个字段作为拒单依据。

3. `product` 和 `exchange` 同时出现在 Tier 1 和 Tier 2。
   这是合理冗余，不建议删除。合约表保留它们能提高查询和排错效率。

### 3.3 命名与唯一键风险

当前代码和文档之间存在命名不一致：

- 文档/历史 TODO 中提到 Tier 1 YAML 可能采用 `{EXCHANGE}_{PRODUCT}.yaml`。
- `ProductMetaManager._load()` 实际使用 `{product}.yaml`。
- `InstrumentDailyStatusManager` 使用 `{exchange}_{product}.parquet`。
- `EnvironmentConfig` 已经定义 `product_meta_dir`，但 `data/factory.py` 仍有旧的 `env_config.meta_dir` 使用痕迹。

建议尽早统一：

- 品种唯一键：初期可以用 `product`，但更工业级的是 `(exchange, product)`。
- 文件命名：建议统一为 `{EXCHANGE}_{PRODUCT}.yaml` / `{EXCHANGE}_{PRODUCT}.parquet`。
- Manager API：要么传 `product` 并内部解析唯一文件，要么显式传 `(exchange, product)`，不要混用。

## 4. 配套代码审查

### 4.1 `ProductMetaManager`

优点：

- 使用 `@dataclass(frozen=True)` 返回不可变规则对象，这对防止下游误改全局规则很有价值。
- `_get_effective_rule()` 通过 `effective_date <= trading_date` 选择最后一条规则，方向正确，有助于防止未来函数。
- YAML 懒加载和缓存适合初期开发，也适合实盘启动后预热。

主要问题：

1. `get_price_tick(product, trading_date)` 无法真正解释 `remain_old_rule_instruments`。
   既然 schema 允许同一品种同一日期下不同合约保留旧规则，查询函数就必须知道 `instrument`，否则只能把解释责任丢给下游。

2. `get_multiplier(product, trading_date)` 容易误导下游。
   对合约乘数，运行时更应该调用 Tier 2 的 `InstrumentRegistryManager.get_instrument(instrument).multiplier`。否则遇到新老划断时，`product + date` 会返回错误乘数。

3. YAML 文件命名和配置体系没有完全对齐。
   当前 `_load()` 固定读取 `{product}.yaml`。如果你决定采用 `{EXCHANGE}_{PRODUCT}.yaml`，这个 Manager 需要调整，否则后续会出现“文档设计正确、代码找不到文件”的问题。

4. 缺少 schema 校验。
   当前代码会在字段缺失时抛错，但还没有系统校验：
   - 历史规则是否按日期无重复。
   - 数值是否为正。
   - `commission_type` 是否只允许 `flat/ratio`。
   - `day_sessions/night_sessions` 时间格式是否正确。
   - `product/exchange` 是否与文件名一致。

5. `_get_effective_rule()` 每次查询都会排序。
   初期可以接受。后续可以在加载 YAML 时预排序并缓存。

6. f-string 写法依赖较新的 Python 语法。
   `sorted_history[0]['effective_date']` 嵌在单引号 f-string 内，在 Python 3.12 可解析，但对旧解释器和人工阅读都不友好。建议后续改成双引号外层或提前赋值。

### 4.2 `InstrumentRegistryManager`

优点：

- 将全市场注册表合并并设为 `instrument` 索引，适合高频查询。
- `InstrumentRegistry` 不可变，适合被风控、保证金、持仓、PnL 引擎共享。
- 一次加载、多次查询的模式符合合约注册表的读多写少特征。

主要问题：

1. 文档允许 CSV/Parquet，但代码只读取 Parquet。
   这不是架构错误，但需要二选一：要么文档收紧为 Parquet，要么代码支持 CSV fallback。

2. 查询入参没有统一大小写。
   `_load_all()` 将索引转成大写，但 `get_instrument(contract)` 没有先 `contract.upper()`。如果调用方传入 `rb2401`，可能无法命中。

3. 没有检测重复合约。
   多个交易所文件拼接后，如果 `instrument` 重复，`self._registry_df.loc[contract]` 可能返回 DataFrame 而不是 Series，后续 dataclass 构造会出错。建议加载后检查 index 唯一性。

4. 缺少必要列校验和空值处理。
   对 `start_deliv_date/end_deliv_date` 等字段，金融期货或某些特殊品种可能为空。当前 `pd.to_datetime(...).date()` 对空值不友好。

5. 查询能力偏少。
   下游风控和回测常需要：
   - 获取某品种全部合约。
   - 获取某交易日已上市且未退市合约。
   - 获取某品种某交割月合约。

   这些不一定现在就做，但它们是 Tier 2 Manager 很自然的扩展方向。

### 4.3 `InstrumentDailyStatusManager`

优点：

- 以 `product + trading_date` 做批量加载，再缓存到 `instrument + trading_date`，适合回测里按品种逐日访问。
- 通过 `ProductMetaManager` 获取交易所，再定位 `{exchange}_{product}.parquet`，方向合理。
- 返回 `InstrumentDailyStatus` 不可变对象，符合规则状态表的消费方式。

主要问题：

1. 通过正则从合约代码解析品种，长期不稳。
   `re.match(r'^[a-zA-Z]+', contract_upper)` 对普通期货合约足够，但对期权、组合、带交易所前缀的合约、特殊编码都可能失败。更稳妥的方式是依赖 `InstrumentRegistryManager` 从合约注册表反查 product。

2. `bool(row['is_trading'])` 有潜在误判。
   如果数据中 `is_trading` 是字符串 `"0"` 或 `"False"`，Python 的 `bool("0")` 会得到 `True`。建议后续使用明确解析函数或在 schema 层强制布尔类型。

3. 查询某日无数据时会返回空 dict。
   这有时是合理的，但也可能掩盖数据缺失。建议明确策略：对于交易日但无状态数据，是返回空、报警，还是抛错。

4. 缓存无上限。
   初期可以接受。长期跑多年多品种回测时，应考虑 `clear_cache()` 或 LRU 策略。

5. Manager 当前没有使用 Tier 2。
   从架构上看，Tier 3 的 `instrument` 属于 Tier 2 管辖，Tier 3 Manager 如果能依赖 Registry 做合约合法性校验和 product 解析，会更稳。

## 5. 与下游组件的衔接能力

### 5.1 风险管理引擎

风控引擎通常需要：

- 合约是否存在、是否上市、是否过期。
- 当前交易日是否可交易。
- 委托价格是否符合 tick size。
- 委托价格是否在涨跌停范围内。
- 单笔下单量是否在交易所限制内。
- 是否临近交割月或最后交易日。

当前三层数据库已经覆盖大部分原始字段，但缺少一个统一查询接口。尤其是 tick size 可能需要合约级解释，交易时段也可能需要合约生命周期修正。

建议后续给风控暴露类似接口：

```python
rules = rule_service.get_order_rules(
    instrument="RB2610",
    trading_date=date(2026, 6, 9),
)
```

返回对象可包含：

- `price_tick`
- `max_limit_order_volume`
- `min_limit_order_volume`
- `upper_price_limit`
- `lower_price_limit`
- `is_tradable`
- `last_trade_date`
- `start_deliv_date`

这样风控不用直接关心三层数据库怎么拼。

### 5.2 保证金引擎

当前设计能支持基础保证金计算：

- Tier 2 给 `multiplier`。
- Tier 3 给 `long_margin_ratio/short_margin_ratio`。
- Tier 1 给 `max_margin_side_algorithm/combination_type`。

但它还不能完整支持复杂组合保证金。原因是 `combination_type` 和 `max_margin_side_algorithm` 太粗，无法描述跨期、跨品种、套保/投机、盘中/结算后优惠等条件。

建议阶段化：

- 初期：按单合约单边保证金 + 简单大额单边逻辑。
- 中期：将组合保证金优惠独立成规则服务或专门表。
- 不要现在就把组合保证金塞进 Tier 3，否则会把账户维度和合约规则维度混在一起。

### 5.3 手续费引擎

当前 `transaction_cost_history` 足以支撑粗粒度回测成本估算，尤其是按品种区分开仓、平今、平昨。

不足在于：

- 不能自然表达特定合约例外。
- 不能自然表达主力合约特殊平今手续费。
- 不能区分交易所手续费和期货公司加收。
- `ratio` 类型手续费依赖成交价、乘数、成交量，实际计算时必须与 Tier 2 乘数联动。

建议未来手续费查询也由统一门面提供：

```python
fee_rule = rule_service.get_commission_rule(
    instrument="RB2610",
    trading_date=date(2026, 6, 9),
    offset="close_today",
)
```

### 5.4 交易日历与撮合引擎

当前 `TradingCalendar` 已经使用 `ProductMetaManager.get_sessions(product, trading_date)` 获取交易时段，这说明 Tier 1 已经接入下游。

但撮合和日内回测还需要更细的语义：

- 某合约最后交易日是否提前收盘。
- 某合约进入交割月后是否无夜盘。
- 某日是否存在临时休市、节假日前夜盘关闭等特殊安排。

这些不一定都要落入 Tier 3，但需要在查询层被解释。建议后续增加：

```python
sessions = rule_service.get_trading_sessions(
    instrument="T2609",
    trading_date=date(2026, 9, 11),
)
```

由它内部组合 Tier 1 常规 session、Tier 2 生命周期日期、必要的 session override。

### 5.5 连续合约/主力合约

主力连续映射不应该塞进这三层规则数据库。它更像基于行情、成交量、持仓量计算出来的衍生数据。

你当前已有 `master_table_dir` 和 `ContinuousContractManager` 方向，这是合理的。三层规则库只需要能支持：

- 判断候选合约是否有效上市。
- 查询候选合约的乘数、tick、交易时段。
- 在换月日计算复权和真实收益时提供规则上下文。

因此“主力映射不在三层规则库里”不是缺陷。

## 6. 优先级建议

### P0：进入下游引擎前建议先确定

1. 统一文件命名和唯一键。
   建议决定 Tier 1 是 `{PRODUCT}.yaml` 还是 `{EXCHANGE}_{PRODUCT}.yaml`。若目标是工业级，推荐后者。

2. 明确乘数 SSOT。
   建议运行时一律以 Tier 2 `instrument_registry.multiplier` 为准，Tier 1 `multiplier_history` 只作为生成和校验规则。

3. 给 `price_tick` 查询增加合约上下文。
   否则 `remain_old_rule_instruments` 只是被存了下来，但没有被 Manager 正确消费。

4. 明确 `expire_date` 与 `last_trade_date`。
   若二者等价，写死在 schema 说明中；若不等价，新增 `last_trade_date`。

5. 增加轻量 schema 校验。
   不需要一开始上很重的框架，至少要校验必需字段、日期格式、正数约束、枚举值、重复 effective_date。

### P1：开始开发风险/保证金/手续费模块时做

1. 增加 `TradingRuleService` / `InstrumentRuleService`。
   这是下游组件稳定接入三层数据库的关键。

2. 将交易时段查询升级为 `instrument + trading_date`。
   支持最后交易日提前收盘、交割月无夜盘等例外。

3. 保证金率字段明确来源。
   至少在字段文档里区分交易所保证金和经纪商保证金。

4. Registry 增加按品种、日期、交割月的查询方法。

5. 统一 `instrument` / `contract` 命名。
   建议数据库字段坚持 `instrument`，业务层可以接受 `contract` 作为别名，但不要在核心 schema 中混用。

### P2：系统成熟后再做

1. 组合保证金规则模型。
2. 手续费特殊规则条件化。
3. 数据来源、更新时间、schema 版本、审计字段。
4. 缓存上限和批量预热机制。
5. 更完整的合约类型支持，例如期权、套利组合。

## 7. 建议的目标形态

不建议推翻当前设计。更好的路线是保留三层结构，但把它们定位得更清晰：

- `product_meta`：品种级规则历史与默认规则。
- `instrument_registry`：合约级不可变事实，尤其是乘数和生命周期日期。
- `instrument_daily_status`：合约-交易日动态事实，尤其是保证金和日级可交易状态。
- `TradingRuleService`：面向下游的规则解释层，负责把三层数据组合成可直接消费的合约规则。

最终下游组件不应该直接问：

- “我该读 Tier 1 还是 Tier 2？”
- “tick size 新老划断怎么判断？”
- “最后交易日时段要不要提前收盘？”
- “乘数到底取 product_meta 还是 registry？”

它们只应该问：

```python
rule_service.get_contract_rule(instrument, trading_date)
rule_service.get_order_rule(instrument, trading_date)
rule_service.get_margin_rule(instrument, trading_date)
rule_service.get_commission_rule(instrument, trading_date, offset)
rule_service.get_trading_sessions(instrument, trading_date)
```

这就是当前三层数据库从“结构设计”走向“工业级可消费规则系统”的关键一步。

## 8. 最终评价

当前设计已经明显高于“简单回测脚本”的水平，尤其是你没有把静态合约表、动态保证金、历史交易时段、手续费、乘数、tick size 全部混在一起。这一点非常重要。

接下来真正需要警惕的不是字段不够多，而是“查询语义不够精确”。期货规则系统最怕的不是少一个字段，而是同一个字段在不同场景下有两个真实来源，或者一个 API 看似返回了规则，实际上缺少合约上下文。

所以建议当前阶段的建设重点是：

- 少扩字段，多定语义。
- 少让下游拼规则，多提供统一查询门面。
- 对 `price_tick`、`multiplier`、`sessions`、`commission` 这几个最容易出错的字段，优先把“查询键”和“真实来源”定死。

按这个方向推进，这套三层规则数据库可以稳稳支撑后续风险管理、保证金、手续费和撮合模块。
