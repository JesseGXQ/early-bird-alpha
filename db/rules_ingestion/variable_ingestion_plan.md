# Variable Ingestion Plan

本文档按本地数据库层级和变量逐项记录入库方案。每个字段都尽量明确主来源、辅助来源、粒度错配、校验方式和待决策事项。

## Template

```markdown
## variable_name

- Local model:
- Tier:
- Versioned:
- Local meaning:
- External sources:
- Grain mismatch:
- Analysis:
- Validation:
- Output:
- Open questions:
```

## TIER 1 - Product-Level Rules

TIER1 的目标是生成每个品种一份产品级 YAML，由 `ProductMetaManager` 读取。外部来源里，BigQuant `cn_future_product_info` 提供产品级当前规则线索，BigQuant `cn_future_static_data` 提供合约-交易日级历史截面，Tushare `fut_settle` 提供手续费和保证金日频线索，Tushare `fut_basic` 提供合约文本说明和交易时间说明。凡是从合约日频表反推产品级历史规则，都必须先检查同一产品、同一交易日、不同合约之间是否一致；不一致时不能直接压缩成产品级规则。

### `StaticMeta.product`

- Local model: `StaticMeta`
- Tier: TIER1
- Versioned: no
- Local meaning: 本地标准品种代码，作为 `{PRODUCT}.yaml` 文件名和产品级主键。
- External sources: BigQuant `cn_future_product_info.code` 为主；BigQuant `cn_future_static_data.product_id`、`cn_future_basic_info.product_code`、Tushare `fut_basic.fut_code` 为校验。
- Grain mismatch: 主来源为品种级，辅助来源为合约级或合约-交易日级。
- Analysis: 先建立 BigQuant/Tushare/本地三套代码标准化映射，例如 `SHF/SHFE`、`CZC/CZCE` 后缀差异，以及郑商所品种大小写差异。YAML 内统一写大写产品代码。
- Validation: 同一产品下所有合约解析出的产品代码应一致；`product` 应与文件名一致。
- Output: YAML 顶层 `product: RB`。
- Open questions: 见 `decisions.md` 中“本地标准代码体系”。

### `StaticMeta.exchange`

- Local model: `StaticMeta`
- Tier: TIER1
- Versioned: no
- Local meaning: 本地标准交易所代码。
- External sources: BigQuant `cn_future_product_info.exchange` 为主；BigQuant `cn_future_static_data.exchange`、Tushare `fut_basic.exchange` 为校验。
- Grain mismatch: BigQuant 多用 `SHF/CZC/CFE/GFE`，Tushare 多用 `SHFE/CZCE/CFFEX/GFEX`，需要映射到本地口径。
- Analysis: 先按产品建立唯一交易所映射；若同一产品在不同来源映射出不同交易所，进入人工复核。
- Validation: 产品代码和交易所组合必须唯一；所有合约级记录的交易所应与产品级交易所一致。
- Output: YAML 顶层 `exchange: SHFE`。
- Open questions: 见 `decisions.md` 中“本地标准代码体系”。

### `StaticMeta.name`

- Local model: `StaticMeta`
- Tier: TIER1
- Versioned: no
- Local meaning: 品种中文简称。
- External sources: BigQuant `cn_future_product_info.name` 为主；Tushare `fut_basic.name` 去除合约月份后的文本为辅助。
- Grain mismatch: Tushare `name` 通常是合约名，不一定是纯品种名。
- Analysis: 优先采用产品级名称；合约级名称只用于发现简称变更、异常文本或新品种补充。
- Validation: 同一产品下合约名称应能稳定归约到同一品种中文名。
- Output: YAML 顶层 `name: 螺纹钢`。
- Open questions: 暂无。

### `StaticMeta.position_date_type`

- Local model: `StaticMeta`
- Tier: TIER1
- Versioned: no in current model
- Local meaning: 是否区分今仓和昨仓，取值应为 `distinguish_today_yesterday` 或 `undistinguished`。
- External sources: BigQuant `cn_future_static_data.position_date_type` 为主；手续费字段中 `close_today` 与 `close_yesterday` 是否长期分离为辅助线索。
- Grain mismatch: 外部字段是合约-交易日级，当前本地模型是产品级静态字段。
- Analysis: 对每个产品统计所有合约、所有日期的 `position_date_type` 值域。如果全历史只有一个稳定值，可压缩到产品级静态字段；如果发生历史变化或合约间差异，应暂缓自动入库，并判断本地模型是否需要版本化。
- Validation: 与交易所口径交叉校验：上期所/能源中心通常更需要区分今昨仓；其他交易所需以数据和规则为准。
- Output: YAML 顶层 `position_date_type: distinguish_today_yesterday`。
- Open questions: 见 `decisions.md` 中“StaticMeta 是否允许历史变化”。

### `StaticMeta.max_margin_side_algorithm`

- Local model: `StaticMeta`
- Tier: TIER1
- Versioned: no in current model
- Local meaning: 是否使用大额单边保证金算法。
- External sources: BigQuant `cn_future_static_data.max_margin_side_algorithm` 为主。
- Grain mismatch: 外部为合约-交易日级，本地为产品级静态布尔值。
- Analysis: 将 BigQuant 的整数/布尔编码映射为 Python `bool`。先按产品、日期、合约检查一致性，再压缩。
- Validation: 若同一产品存在多个值，需确认是历史变化、组合合约差异，还是数据异常。
- Output: YAML 顶层 `max_margin_side_algorithm: true/false`。
- Open questions: 见 `decisions.md` 中“StaticMeta 是否允许历史变化”。

### `StaticMeta.combination_type`

- Local model: `StaticMeta`
- Tier: TIER1
- Versioned: no in current model
- Local meaning: 组合类型编码，目前本地模型仅保留整数。
- External sources: BigQuant `cn_future_static_data.combination_type` 为主。
- Grain mismatch: 外部为合约-交易日级，本地为产品级静态字段。
- Analysis: 先保留外部整数编码，不做语义翻译；后续如发现该字段只对组合/套利合约有效，应考虑是否仍写入普通期货产品 YAML。
- Validation: 对同一产品检查全历史唯一性；若出现多值，记录样本并人工确认。
- Output: YAML 顶层 `combination_type: 1`。
- Open questions: 见 `decisions.md` 中“StaticMeta 是否允许历史变化”。

### `TransactionCostRule.effective_date`

- Local model: `TransactionCostRule`
- Tier: TIER1
- Versioned: yes, `transaction_cost_history`
- Local meaning: 某条手续费规则开始生效的交易日。
- External sources: Tushare `fut_settle.trade_date` + `trading_fee_rate/trading_fee/offset_today_fee` 为主；BigQuant `cn_future_product_info.commission_*` 提供当前值校验；交易所手续费公告为最终证据。
- Grain mismatch: Tushare 是合约-交易日级，本地是产品级历史规则。
- Analysis: 对同一产品按交易日聚合，识别手续费值发生变化的日期。若所有活跃合约同日同值变化，可生成产品级 `effective_date`；若仅部分合约变化，需通过 `scope` 表达或人工确认公告范围。
- Validation: 变化日期应能被交易所公告解释；当前最新规则应与 BigQuant 产品级 `commission_*` 接近或一致。
- Output: `transaction_cost_history[].effective_date`，格式 `YYYY-MM-DD`。
- Open questions: 见 `decisions.md` 中“手续费字段语义与 scope 口径”。

### `TransactionCostRule.commission_type`

- Local model: `TransactionCostRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 手续费计价方式，`flat` 表示按手收取，`ratio` 表示按成交金额比例收取。
- External sources: BigQuant `cn_future_product_info.commission_type`、Tushare `fut_settle.trading_fee`、`trading_fee_rate`。
- Grain mismatch: BigQuant 是产品级当前编码，Tushare 是合约-交易日级双字段。
- Analysis: 初步规则为：`trading_fee` 非空且 `trading_fee_rate` 为空/零时映射 `flat`；`trading_fee_rate` 非空且 `trading_fee` 为空/零时映射 `ratio`。若两者同时非空，必须抽样比对交易所公告。
- Validation: `commission_type` 与 `open/close_today/close_yesterday` 的数量级一致；`ratio` 通常为很小的小数，`flat` 通常为元/手。
- Output: `commission_type: flat` 或 `ratio`。
- Open questions: 见 `decisions.md` 中“手续费字段语义与 scope 口径”。

### `TransactionCostRule.open`

- Local model: `TransactionCostRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 开仓手续费，单位由 `commission_type` 决定。
- External sources: Tushare `fut_settle.trading_fee` 或 `trading_fee_rate` 为主；BigQuant `cn_future_product_info.commission_open` 为当前值校验。
- Grain mismatch: Tushare 合约-交易日级需要压缩到产品级历史。
- Analysis: 根据 `commission_type` 选择金额或费率字段作为 `open`。同一产品同日不同合约不一致时，应判断是否存在按合约月份、主力/非主力、近月/远月的差异。
- Validation: 与当前产品级值、交易所公告样本、历史变化点一致。
- Output: `transaction_cost_history[].open`。
- Open questions: 见 `decisions.md` 中“手续费字段语义与 scope 口径”。

### `TransactionCostRule.close_yesterday`

- Local model: `TransactionCostRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 平昨手续费，单位由 `commission_type` 决定。
- External sources: Tushare `fut_settle.trading_fee` 或 `trading_fee_rate` 为主；BigQuant `cn_future_product_info.commission_yesterday` 为当前值校验。
- Grain mismatch: Tushare 文档未明确区分开仓和平昨，BigQuant 产品表仅提供当前值。
- Analysis: 初步假设平昨与开仓同值，但必须用交易所公告或 BigQuant 当前值验证。若某产品有平昨减免或特殊规则，应生成独立值。
- Validation: 对比 `commission_open` 与 `commission_yesterday`；抽样核对上期所、能源中心等区分今昨仓品种。
- Output: `transaction_cost_history[].close_yesterday`。
- Open questions: 见 `decisions.md` 中“手续费字段语义与 scope 口径”。

### `TransactionCostRule.close_today`

- Local model: `TransactionCostRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 平今手续费，单位由 `commission_type` 决定。
- External sources: Tushare `fut_settle.offset_today_fee` 为主；BigQuant `cn_future_product_info.commission_today` 为当前值校验；交易所公告为最终证据。
- Grain mismatch: Tushare 字段说明写作“平今仓手续费率”，但其数值可能需要结合 `commission_type` 判断。
- Analysis: 对每个产品检查 `offset_today_fee` 与 `trading_fee/trading_fee_rate` 的数量级关系。若平今字段为空，优先回退为与平昨相同，但需标记为推断值。
- Validation: 当前值与 BigQuant `commission_today` 一致；历史突变点可被公告解释。
- Output: `transaction_cost_history[].close_today`。
- Open questions: 见 `decisions.md` 中“手续费字段语义与 scope 口径”。

### `TransactionCostRule.scope`

- Local model: `TransactionCostRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 该条手续费规则只作用于特定合约时的合约代码列表；空列表表示全产品适用。
- External sources: Tushare `fut_settle.ts_code` 的合约级差异；交易所公告中的适用合约范围。
- Grain mismatch: 本字段正是为处理产品级压缩失败而保留。
- Analysis: 默认生成 `scope: []`。当同一产品同日仅部分合约出现手续费差异，且差异可被公告确认时，生成带合约列表的规则。
- Validation: `scope` 内合约必须属于该产品；如果合约范围可用规则表达但当前模型只能列举，应评估是否接受显式列表。
- Output: `transaction_cost_history[].scope`。
- Open questions: 见 `decisions.md` 中“手续费字段语义与 scope 口径”。

### `TransactionCostRule.condition`

- Local model: `TransactionCostRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 复杂手续费规则的补充条件表达，目前模型类型为 `Any`。
- External sources: 交易所公告、手续费特殊规则文本。
- Grain mismatch: 外部公告通常是文本规则，不是结构化字段。
- Analysis: 自动入库阶段默认写 `condition: null`。只有出现无法用 `scope` 和三类手续费数值表达的规则时，才写结构化字典，例如按成交额区间、套保/投机属性、客户类型等。
- Validation: 非空 `condition` 必须保留来源公告和人工确认记录。
- Output: `transaction_cost_history[].condition`。
- Open questions: 暂无；先采取保守默认值。

### `PriceTickRule.effective_date`

- Local model: `PriceTickRule`
- Tier: TIER1
- Versioned: yes, `price_tick_history`
- Local meaning: 最小变动价位规则开始生效的交易日。
- External sources: BigQuant `cn_future_static_data.price_tick` 为主；BigQuant `cn_future_product_info.price_tick/old_price_tick` 为当前和旧值线索；交易所公告为最终证据。
- Grain mismatch: 合约-交易日级反推产品级版本。
- Analysis: 按产品、交易日统计 `price_tick` 值域，识别值变化日。若变更日之后老合约仍保留旧值，则生成新规则并在 `remain_old_rule_instruments` 中列出老合约。
- Validation: 变化日期应与交易所公告一致；最新值应与 BigQuant 产品表 `price_tick` 一致。
- Output: `price_tick_history[].effective_date`。
- Open questions: 暂无。

### `PriceTickRule.price_tick`

- Local model: `PriceTickRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 最小变动价位数值。
- External sources: BigQuant `cn_future_static_data.price_tick` 为主；BigQuant `cn_future_product_info.price_tick`、合约表公告为校验。
- Grain mismatch: 同一产品不同历史合约可能因新老划断不同值。
- Analysis: 对每个规则版本选择新规则值。若同一日同一产品存在两个值，应通过上市日期、合约月份和公告判断哪个是新规则、哪个是保留旧规则。
- Validation: 合约级 `price_tick` 应能由产品级规则加 `remain_old_rule_instruments` 解释。
- Output: `price_tick_history[].price_tick`。
- Open questions: 暂无。

### `PriceTickRule.remain_old_rule_instruments`

- Local model: `PriceTickRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 新老划断时继续使用旧价位规则的合约列表。
- External sources: BigQuant `cn_future_static_data.instrument/price_tick` 的横截面差异；交易所公告中的老合约范围。
- Grain mismatch: 本字段显式承接合约级例外。
- Analysis: 在某个 `effective_date` 后，仍等于旧 `price_tick` 的存续合约进入列表；一刀切变更则为空列表。
- Validation: 列表合约必须在变更日已上市且仍未到期；列表应随公告范围闭合。
- Output: `price_tick_history[].remain_old_rule_instruments`。
- Open questions: 暂无。

### `MultiplierRule.effective_date`

- Local model: `MultiplierRule`
- Tier: TIER1
- Versioned: yes, `multiplier_history`
- Local meaning: 合约乘数规则开始生效的交易日。
- External sources: BigQuant `cn_future_static_data.volume_multiplier` 为主；BigQuant `cn_future_product_info.multiplier`、`cn_future_basic_info.multiplier` 为校验；Tushare `fut_basic.per_unit/multiplier` 为辅助。
- Grain mismatch: 合约-交易日级或合约级反推产品级历史。
- Analysis: 与价位规则类似，按产品和交易日识别乘数变化点。由于乘数变更通常影响合约价值，必须用交易所公告确认。
- Validation: 最新版本应解释合约注册表中的 `multiplier`；历史变化点不得仅凭孤立异常值生成。
- Output: `multiplier_history[].effective_date`。
- Open questions: 暂无。

### `MultiplierRule.multiplier`

- Local model: `MultiplierRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 每手合约代表的标的数量。
- External sources: BigQuant `cn_future_static_data.volume_multiplier` 为主；BigQuant `cn_future_product_info.multiplier` 为当前校验；Tushare `fut_basic.per_unit` 为文本/数值辅助。
- Grain mismatch: 外部可能按合约给出，本地按产品历史给出。
- Analysis: 优先从 BigQuant 日频静态表获得历史值；Tushare `multiplier` 因文档说明覆盖国债和指数期货为主，不作为全市场主来源。
- Validation: 同一产品同日存续合约乘数应一致；若不一致，需确认是否应扩展本地模型。
- Output: `multiplier_history[].multiplier`。
- Open questions: 暂无。

### `OrderVolumeLimitsRule.effective_date`

- Local model: `OrderVolumeLimitsRule`
- Tier: TIER1
- Versioned: yes, `order_volume_limits_history`
- Local meaning: 下单量限制规则开始生效的交易日。
- External sources: BigQuant `cn_future_static_data` 的四个 order volume 字段为主；交易所公告为校验。
- Grain mismatch: 合约-交易日级反推产品级历史。
- Analysis: 对四个字段组成 tuple，按产品、交易日识别 tuple 变化点。只有当同一产品同日所有适用合约 tuple 一致时，才自动生成产品级规则。
- Validation: 变化点与公告或合约规则表一致；异常单合约差异进入人工复核。
- Output: `order_volume_limits_history[].effective_date`。
- Open questions: 见 `decisions.md` 中“产品级规则压缩失败时的处理方式”。

### `OrderVolumeLimitsRule.max_market_order_volume`

- Local model: `OrderVolumeLimitsRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 市价单最大下单手数。
- External sources: BigQuant `cn_future_static_data.max_market_order_volume` 为主。
- Grain mismatch: 合约-交易日级到产品级。
- Analysis: 与其他三个下单量字段一起成组处理，不单独生成版本。
- Validation: 值应为正整数，且大于等于最小市价单下单量。
- Output: `order_volume_limits_history[].max_market_order_volume`。
- Open questions: 见 `decisions.md` 中“产品级规则压缩失败时的处理方式”。

### `OrderVolumeLimitsRule.min_market_order_volume`

- Local model: `OrderVolumeLimitsRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 市价单最小下单手数。
- External sources: BigQuant `cn_future_static_data.min_market_order_volume` 为主。
- Grain mismatch: 合约-交易日级到产品级。
- Analysis: 与四字段 tuple 同步处理。
- Validation: 值通常为 `1`；若为 `0` 或缺失需检查是否表示不支持市价单。
- Output: `order_volume_limits_history[].min_market_order_volume`。
- Open questions: 见 `decisions.md` 中“产品级规则压缩失败时的处理方式”。

### `OrderVolumeLimitsRule.max_limit_order_volume`

- Local model: `OrderVolumeLimitsRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 限价单最大下单手数。
- External sources: BigQuant `cn_future_static_data.max_limit_order_volume` 为主。
- Grain mismatch: 合约-交易日级到产品级。
- Analysis: 与四字段 tuple 同步处理。
- Validation: 值应为正整数，且通常大于等于市价单最大下单量。
- Output: `order_volume_limits_history[].max_limit_order_volume`。
- Open questions: 见 `decisions.md` 中“产品级规则压缩失败时的处理方式”。

### `OrderVolumeLimitsRule.min_limit_order_volume`

- Local model: `OrderVolumeLimitsRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 限价单最小下单手数。
- External sources: BigQuant `cn_future_static_data.min_limit_order_volume` 为主。
- Grain mismatch: 合约-交易日级到产品级。
- Analysis: 与四字段 tuple 同步处理。
- Validation: 值通常为 `1`；不得大于最大限价单下单量。
- Output: `order_volume_limits_history[].min_limit_order_volume`。
- Open questions: 见 `decisions.md` 中“产品级规则压缩失败时的处理方式”。

### `BaseSessions.effective_date`

- Local model: `BaseSessions`
- Tier: TIER1
- Versioned: yes, `sessions_history`
- Local meaning: 基础交易时段规则开始生效的交易日，不包含临时或生命周期 override。
- External sources: BigQuant `cn_future_product_info.trading_period`、Tushare `fut_basic.trade_time_desc` 为线索；交易所合约表、夜盘公告和历史规则文件为主证据。
- Grain mismatch: 外部结构化程度不足，且产品级文本通常只反映当前规则。
- Analysis: 不建议仅从文本自动入库。应先将交易所规则和历史公告整理成产品级基础 session 版本；BigQuant/Tushare 文本用于辅助解析当前日盘/夜盘边界。
- Validation: 当前版本应与 BigQuant/Tushare 文本一致；历史版本应与交易所公告一致；不能把节假日无夜盘、最后交易日缩短等写入基础 session。
- Output: `sessions_history[].effective_date`。
- Open questions: 暂无。

### `BaseSessions.morning_sessions`

- Local model: `BaseSessions`
- Tier: TIER1
- Versioned: yes
- Local meaning: 上午连续竞价小节列表，左闭右开语义。
- External sources: 交易所规则/合约表为主；BigQuant `trading_period`、Tushare `trade_time_desc` 为辅助。
- Grain mismatch: 来源多为文本，需要解析成 `[['09:00', '10:15'], ['10:30', '11:30']]`。
- Analysis: 按交易所和品种解析日盘上午结构，注意中金所和商品交易所开盘时间不同。
- Validation: 时间格式统一为 `HH:MM`；小节按时间升序且不重叠。
- Output: `sessions_history[].morning_sessions`。
- Open questions: 暂无。

### `BaseSessions.afternoon_sessions`

- Local model: `BaseSessions`
- Tier: TIER1
- Versioned: yes
- Local meaning: 下午连续竞价小节列表。
- External sources: 交易所规则/合约表为主；BigQuant `trading_period`、Tushare `trade_time_desc` 为辅助。
- Grain mismatch: 文本到结构化时间段。
- Analysis: 普通下午盘写入基础 session；最后交易日删除下午盘属于 `session_override_rules`，不得改写基础规则。
- Validation: 当前普通交易日计算结果与行情快照时段、交易所合约表一致。
- Output: `sessions_history[].afternoon_sessions`。
- Open questions: 暂无。

### `BaseSessions.night_sessions`

- Local model: `BaseSessions`
- Tier: TIER1
- Versioned: yes
- Local meaning: 夜盘连续竞价小节列表，无夜盘则为空列表。
- External sources: 交易所夜盘公告/合约表为主；BigQuant `trading_period`、Tushare `trade_time_desc`、BigQuant `cn_future_level1_snapshot.time` 为辅助校验。
- Grain mismatch: 夜盘物理日期与交易日归属不同，文本也常不说明历史启用日期。
- Analysis: 按产品维护夜盘启用、暂停、恢复和永久变更的基础版本；节假日、疫情暂停、技术故障等应进入 override，而不是基础 session，除非交易所永久修改规则。
- Validation: 有夜盘产品的当前夜盘收市时间应与数据源文本一致；无夜盘产品必须输出 `[]`。
- Output: `sessions_history[].night_sessions`。
- Open questions: 暂无。

### `SessionOverrideRules`

- Local model: `SessionOverrideRules`
- Tier: TIER1
- Versioned: yes, by rule/event effective period inside each dict
- Local meaning: 交易时段特殊安排集合，用于表达基础 session 之外的生命周期规则、指定日期安排和事后事件。
- External sources:
  - 数据来源：`docs/代码修改笔记/期货交易规则/交易时段规则特殊安排/中国期货市场交易时段规则特殊安排_历史事实_CodeX.md`
  - 数据储存 schema 参考：`docs/代码修改笔记/期货交易规则/交易时段规则特殊安排/session_override_rules 数据存储设计_CodeX.md`
- Grain mismatch: 外部材料主要是规则研究、交易所公告和事件事实，本地需要按既定 schema 结构化为 YAML 字典列表；行情数据只能辅助发现和校验，不能单独证明 session 规则。
- Analysis: 以历史事实研究文档作为事实边界，以 schema 设计文档作为 YAML 存储格式依据。三类字段分别承接不同语义：`recurring_instrument_lifecycle_rules` 存放可重复计算的合约生命周期类规则；`scheduled_dated_overrides` 存放可提前知道的日期或日期区间安排；`realized_session_events` 存放事后确认的实际事件。不能把基础交易时段永久变更写入 override，也不能把整日休市只写成 session override。
- Validation: 每条 override 必须能说明作用范围、交易日归属、触发条件或事件日期、动作、证据来源和可复算结果；对最后交易日等合约级规则，应只影响目标到期合约；对 realized 事件，应避免污染 scheduled 视图。
- Output: YAML 顶层 `session_override_rules`，包含 `recurring_instrument_lifecycle_rules`、`scheduled_dated_overrides`、`realized_session_events` 三个子列表。
- Open questions: 暂无；存储 schema 已由 `session_override_rules 数据存储设计_CodeX.md` 确定。

## TIER 2

TIER2 的目标是生成全市场合约注册表 parquet，当前 `InstrumentRegistryManager` 会读取目录下所有 `*.parquet`，拼接后以本地标准 `instrument` 作为唯一索引。TIER2 应优先服务“合约身份、生命周期、交割属性和报单代码”的稳定查询，不承载日频状态变化。

### `InstrumentRegistry.instrument`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 本地标准合约代码，全市场唯一主键。
- External sources: BigQuant `cn_future_basic_info.instrument` 为主；Tushare `fut_basic.ts_code`、BigQuant `cn_future_static_data.instrument` 为校验。
- Grain mismatch: BigQuant/Tushare 均为合约级或合约-交易日级，但后缀和大小写口径不同。
- Analysis: 按已决策的本地标准代码体系，将外部合约代码转换为本地 canonical code。普通合约必须过滤掉主力、连续、指数类合成合约；Tushare 侧应使用 `fut_type=1`。
- Validation: 转换后 `instrument` 必须全市场唯一；应能从 `instrument` 解析出 `product`、`delivery_year`、`delivery_month` 并与来源字段一致。
- Output: registry parquet 列 `instrument`。
- Open questions: 暂无，沿用 `decisions.md` 中“本地标准代码体系”。

### `InstrumentRegistry.name`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 合约中文名称。
- External sources: BigQuant `cn_future_basic_info.name` 为主；Tushare `fut_basic.name`、BigQuant `cn_future_static_data.name` 为校验。
- Grain mismatch: 合约级名称可能随数据源格式不同，例如是否包含交易所、月份或品种简称。
- Analysis: 优先保留外部合约级名称，不强行归约为品种名。若不同来源名称不一致，记录差异但不影响主键入库。
- Validation: 名称应能与 `product` 和交割月份大致对应；空值或明显非普通合约名称进入人工复核。
- Output: registry parquet 列 `name`。
- Open questions: 暂无。

### `InstrumentRegistry.product`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 合约所属本地标准品种代码。
- External sources: BigQuant `cn_future_basic_info.product_code` 为主；BigQuant `cn_future_static_data.product_id`、Tushare `fut_basic.fut_code`、合约代码解析结果为校验。
- Grain mismatch: 合约级字段和代码解析结果应一致。
- Analysis: 优先使用来源字段，若缺失则从 `instrument` 解析。郑商所等大小写差异统一转换为大写本地产品代码。
- Validation: `product` 必须能在 TIER1 产品 YAML 中找到；同一合约跨来源映射出的产品必须一致。
- Output: registry parquet 列 `product`。
- Open questions: 暂无。

### `InstrumentRegistry.product_class`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 产品类型编码，当前模型为 `int`，用于区分期货、期权、组合、套利等类别。
- External sources: BigQuant `cn_future_static_data.product_class` 为主；Tushare `fut_basic.fut_type` 仅用于过滤普通合约，不能直接替代产品类型。
- Grain mismatch: BigQuant 字段为字符串或外部编码，本地模型为整数枚举。
- Analysis: 先建立外部 `product_class` 到本地整数枚举的映射表。当前入库范围聚焦普通期货合约，因此可先只允许“期货”类型通过，其他类型进入隔离样本。
- Validation: `instrument` 不应包含主力、连续、期权、组合等合成或非普通期货合约；枚举未知值不得静默落库。
- Output: registry parquet 列 `product_class`。
- Open questions: 见 `decisions.md` 中“InstrumentRegistry.product_class 本地枚举”。

### `InstrumentRegistry.exchange`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 本地标准交易所代码。
- External sources: BigQuant `cn_future_basic_info.exchange` 为主；BigQuant `cn_future_static_data.exchange`、Tushare `fut_basic.exchange` 为校验。
- Grain mismatch: 外部交易所代码格式不同，需要映射到本地官方/CTP 风格。
- Analysis: 使用与 TIER1 相同的 source-to-local 交易所映射。registry parquet 可按交易所分区保存，但行内仍保留 `exchange` 字段。
- Validation: 合约后缀、产品所属交易所、来源字段三者应一致；不一致时暂停该合约入库。
- Output: registry parquet 列 `exchange`。
- Open questions: 暂无，沿用 `decisions.md` 中“本地标准代码体系”。

### `InstrumentRegistry.list_date`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 合约上市或开始交易日期。
- External sources: BigQuant `cn_future_basic_info.list_date` 为主；BigQuant `cn_future_static_data.open_date`、Tushare `fut_basic.list_date` 为校验。
- Grain mismatch: 合约级和合约-交易日级字段应最终压缩为单个日期。
- Analysis: 对同一合约取来源中的最早明确上市/开始交易日期。若 BigQuant `list_date` 与 `open_date` 不一致，应判断一个是否为数据记录起点，不能机械取最早。
- Validation: `list_date <= last_trade_date`；对于有夜盘品种，上市首日夜盘归属问题不改变注册表日期，只在 session 规则中处理。
- Output: registry parquet 列 `list_date`，日期格式。
- Open questions: 见 `decisions.md` 中“合约生命周期日期字段语义优先级”。

### `InstrumentRegistry.last_trade_date`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 合约最后交易日。
- External sources: Tushare `fut_basic.delist_date` 为主；BigQuant `cn_future_basic_info.delist_date`、BigQuant `cn_future_static_data.expire_date` 为校验；交易所合约规则用于异常确认。
- Grain mismatch: 不同来源可能使用退市日、到期日、最后交易日等不同命名。
- Analysis: 初步将 Tushare `delist_date` 作为最后交易日候选；BigQuant `delist_date/expire_date` 需通过样本确认其是否等同最后交易日。若来源不一致，优先交易所规则和 Tushare 明确语义。
- Validation: `last_trade_date` 应早于或等于 `end_deliv_date`；对金融期货无实物交割或交割日特殊的品种需单独校验。
- Output: registry parquet 列 `last_trade_date`。
- Open questions: 见 `decisions.md` 中“合约生命周期日期字段语义优先级”。

### `InstrumentRegistry.delivery_year`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 交割年份。
- External sources: BigQuant `cn_future_static_data.delivery_year` 为主；Tushare `fut_basic.d_month`、合约代码解析为校验。
- Grain mismatch: BigQuant 为合约-交易日级，需要压缩为合约级；Tushare 可能为文本日期。
- Analysis: 对同一合约取唯一 `delivery_year`。若缺失，从合约代码月份解析，但需处理三位/四位年月、郑商所早期合约代码等历史格式。
- Validation: `delivery_year` 与 `delivery_month` 组合应匹配 `instrument` 和 `d_month`。
- Output: registry parquet 列 `delivery_year`。
- Open questions: 暂无。

### `InstrumentRegistry.delivery_month`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 交割月份。
- External sources: BigQuant `cn_future_static_data.delivery_month` 为主；Tushare `fut_basic.d_month`、合约代码解析为校验。
- Grain mismatch: 同一合约日频记录应唯一。
- Analysis: 对同一合约取唯一月份；若来源缺失则从合约代码解析。
- Validation: 取值必须为 `1..12`；与产品可交易月份规则交叉校验。
- Output: registry parquet 列 `delivery_month`。
- Open questions: 暂无。

### `InstrumentRegistry.start_deliv_date`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 开始交割日。
- External sources: BigQuant `cn_future_static_data.start_deliv_date` 为主；交易所合约规则为校验。
- Grain mismatch: 只有 BigQuant 日频静态表直接提供，需从多日记录压缩为单值。
- Analysis: 对同一合约取非空唯一值。若金融期货或无实物交割品种没有开始交割日，应决定本地是否允许空值或使用特殊占位。
- Validation: 若存在，应满足 `last_trade_date <= start_deliv_date <= end_deliv_date` 的常见关系；不满足时按品种规则复核。
- Output: registry parquet 列 `start_deliv_date`。
- Open questions: 见 `decisions.md` 中“无交割日期合约的空值策略”。

### `InstrumentRegistry.end_deliv_date`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 最后交割日。
- External sources: BigQuant `cn_future_static_data.end_deliv_date` 为主；Tushare `fut_basic.last_ddate`、BigQuant `cn_future_basic_info.last_deliver_date` 为校验。
- Grain mismatch: 合约级和日频静态表均可能提供，但命名和语义需要校验。
- Analysis: 优先取 BigQuant `end_deliv_date`，再用 Tushare `last_ddate` 和 BigQuant `last_deliver_date` 交叉确认。若来源不一致，按交易所合约规则人工复核。
- Validation: `end_deliv_date` 应不早于 `last_trade_date`；无实物交割品种需符合空值策略。
- Output: registry parquet 列 `end_deliv_date`。
- Open questions: 见 `decisions.md` 中“合约生命周期日期字段语义优先级”和“无交割日期合约的空值策略”。

### `InstrumentRegistry.exchange_inst`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 交易所报单代码或交易代码，不含供应商后缀。
- External sources: BigQuant `cn_future_basic_info.trading_code`、BigQuant `cn_future_static_data.trading_code` 为主；Tushare `fut_basic.symbol` 为校验。
- Grain mismatch: 供应商可能将带后缀合约代码和交易所报单代码混用。
- Analysis: 优先保留交易所实际报单使用的短代码，例如 `rb2408` 或 `SR409`。本地 `instrument` 负责全市场唯一，`exchange_inst` 负责交易接口/交易所内部代码。
- Validation: `exchange_inst` 去除大小写差异后应能与 `instrument` 主体部分对应；同一交易所内应唯一。
- Output: registry parquet 列 `exchange_inst`。
- Open questions: 暂无。

### `InstrumentRegistry.multiplier`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 该合约注册层面的合约乘数。
- External sources: BigQuant `cn_future_basic_info.multiplier` 和 `cn_future_static_data.volume_multiplier` 为主；BigQuant `cn_future_product_info.multiplier`、TIER1 `MultiplierRule`、Tushare `fut_basic.per_unit/multiplier` 为校验。
- Grain mismatch: TIER1 是产品级历史规则，TIER2 是合约级最终属性；日频静态表需压缩为合约级。
- Analysis: 优先取合约自身来源。若某合约生命周期内 `volume_multiplier` 存在变化，应判断是否是数据修正、规则变更还是同一合约跨版本变化；当前 TIER2 只能存单值，无法表达合约级历史乘数。
- Validation: `multiplier` 应能被 TIER1 对应日期的 `MultiplierRule` 解释；必须为正整数。
- Output: registry parquet 列 `multiplier`。
- Open questions: 见 `decisions.md` 中“同一合约，不同外部数据源的取值不同如何处理”。

## TIER 3

TIER3 的目标是生成合约-交易日级日频状态 parquet，供 `InstrumentDailyStatusManager` 按 `{exchange}_{product}.parquet` 读取。该层只处理外部数据源可以直接或间接提供的日频状态字段；`TradingSessions` 虽然也是合约-交易日级领域模型，但它由本地 `BaseSessions` 与 `SessionOverrideRules` 解析生成，不属于“外部数据源 -> 本地数据库”的入库对象。

### `InstrumentDailyStatus.trading_date`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 交易日，是合约日频状态表的日期主键之一。
- External sources: BigQuant `cn_future_static_data.trading_day` 为主；Tushare `fut_trade_cal.cal_date/is_open/pretrade_date` 为交易日历校验；Tushare `fut_settle.trade_date`、`ft_limit.trade_date` 为覆盖校验。
- Grain mismatch: BigQuant 为合约-交易日级，Tushare 日历为交易所-自然日级。
- Analysis: 以 BigQuant 日频静态表中的 `trading_day` 作为入库日期，统一转换为 `datetime.date`。按交易所用 Tushare `fut_trade_cal` 校验该日期是否开市，并检查是否存在 BigQuant 有合约记录但交易日历标记休市、或交易日历开市但关键品种无任何合约记录的异常。
- Validation: `trading_date` 必须在对应交易所开市日历中；同一 parquet 文件内 `(trading_date, instrument)` 唯一；日期范围应覆盖 TIER2 注册合约生命周期内的主要交易日。
- Output: product-date parquet 列 `trading_date`。
- Open questions: 见 `decisions.md` 中“TIER3 日期与合约覆盖范围”。

### `InstrumentDailyStatus.instrument`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 本地标准合约代码，是合约日频状态表的合约主键。
- External sources: BigQuant `cn_future_static_data.instrument` 为主；TIER2 `InstrumentRegistry.instrument` 为本地主表校验；Tushare `fut_settle.ts_code` 为跨源校验。
- Grain mismatch: BigQuant/Tushare 供应商代码需要映射为本地 canonical code。
- Analysis: 对 BigQuant 合约代码执行与 TIER2 相同的 source-to-local 标准化。只允许能在 `InstrumentRegistry` 找到的普通期货合约进入 TIER3；主力、连续、指数、期权和组合合约应隔离。
- Validation: 每条 TIER3 记录必须能 join 到 TIER2 registry；`instrument` 对应的 `product` 必须与输出 parquet 文件的品种一致；同一 `(trading_date, instrument)` 不得重复。
- Output: product-date parquet 列 `instrument`。
- Open questions: 暂无，沿用“本地标准代码体系”和 TIER2 普通合约过滤决策。

### `InstrumentDailyStatus.is_tradable`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 该合约在该交易日是否可交易。
- External sources: BigQuant `cn_future_static_data.is_trading` 为主；Tushare `fut_trade_cal.is_open`、TIER2 `list_date/last_trade_date`、BigQuant `inst_life_phase` 为校验。
- Grain mismatch: `is_trading` 是合约-交易日状态，交易日历是交易所级状态，生命周期日期是合约级静态边界。
- Analysis: 将 BigQuant `is_trading` 映射为布尔值。若交易所休市，理论上不应生成该交易日记录；若生成，则 `is_tradable` 必须为 `False` 并进入异常分析。上市前、暂停、到期后状态应与 `inst_life_phase` 联动。
- Validation: 本地约束关系为：`not_started/paused/expired` 对应 `is_tradable=False`，`started` 通常对应 `True`。若 `inst_life_phase=started` 但 `is_tradable=False`，需判断是否为临时停牌、节假日、数据口径或交易暂停。
- Output: product-date parquet 列 `is_tradable`，供 manager 读取后转换为 `bool`。
- Open questions: 见 `decisions.md` 中“is_trading 与 inst_life_phase 映射口径”。

### `InstrumentDailyStatus.inst_life_phase`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 合约生命周期阶段，只允许 `not_started`、`started`、`paused`、`expired`。
- External sources: BigQuant `cn_future_static_data.inst_life_phase` 为主；TIER2 `list_date/last_trade_date`、BigQuant `is_trading` 为校验。
- Grain mismatch: BigQuant 字段值域可能是外部编码或英文/中文字符串，本地为固定 literal。
- Analysis: 需要先用实样统计 BigQuant `inst_life_phase` 的全量值域，再建立映射表。若来源已是相同英文值，可直接保留；若为数字或中文状态，应显式映射到本地四类枚举。
- Validation: `trading_date < list_date` 时应为 `not_started`；`trading_date > last_trade_date` 时应为 `expired`；上市至最后交易日期间大多数开市日应为 `started`，少数异常停牌可为 `paused`。
- Output: product-date parquet 列 `inst_life_phase`。
- Open questions: 见 `decisions.md` 中“is_trading 与 inst_life_phase 映射口径”。

### `InstrumentDailyStatus.long_margin_ratio`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 交易所多头保证金率，按小数存储，例如 `0.08` 表示 8%。
- External sources: BigQuant `cn_future_static_data.long_margin_ratio` 为主；Tushare `fut_settle.long_margin_rate` 为校验；Tushare `ft_limit.m_ratio` 只能作为最低保证金率辅助参考。
- Grain mismatch: BigQuant 和 Tushare 都是合约-交易日级，但单位可能是小数或百分数；`ft_limit.m_ratio` 语义是最低交易保证金率，不等同于多头保证金率。
- Analysis: 先用数值分布判断单位，统一归一化为小数。若 Tushare `long_margin_rate` 与 BigQuant `long_margin_ratio` 不一致，应区分投机/套保、最低/实际、交易所/期货公司口径，不可简单覆盖。
- Validation: 数值应在合理区间内，通常 `0 < ratio < 1`；与 `short_margin_ratio` 的差异应可由交易所规则解释；突变点应与交易所保证金调整公告或临近交割月规则一致。
- Output: product-date parquet 列 `long_margin_ratio`。
- Open questions: 见 `decisions.md` 中“保证金率来源优先级与单位归一化”。

### `InstrumentDailyStatus.short_margin_ratio`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 交易所空头保证金率，按小数存储，例如 `0.08` 表示 8%。
- External sources: BigQuant `cn_future_static_data.short_margin_ratio` 为主；Tushare `fut_settle.short_margin_rate` 为校验；Tushare `ft_limit.m_ratio` 只能作为最低保证金率辅助参考。
- Grain mismatch: 与多头保证金率相同，需处理来源语义和单位差异。
- Analysis: 与 `long_margin_ratio` 成对处理，不单独建立版本逻辑。若多空保证金率不同，应保留差异；若某来源只给最低保证金率，不得填充成多空字段。
- Validation: 数值应在合理区间内；与 Tushare `short_margin_rate` 的差异应输出冲突报告；同一合约同一日多空字段缺一时不得静默补零。
- Output: product-date parquet 列 `short_margin_ratio`。
- Open questions: 见 `decisions.md` 中“保证金率来源优先级与单位归一化”。

### TIER3 supporting ingestion notes

- Calendar input: Tushare `fut_trade_cal` 应作为交易所交易日历的主校验来源，但不直接生成某合约是否可交易。
- Code normalization: TIER3 必须复用 TIER1/TIER2 的 source-to-local 映射，避免同一合约在不同层级出现不同主键。
- Provenance: 当前 `InstrumentDailyStatus` 模型没有 provenance 字段；建议在原始下载区和中间审计报告中保留来源表、下载批次、字段冲突和单位归一化记录，而不是向 TIER3 parquet 增加未建模字段。
