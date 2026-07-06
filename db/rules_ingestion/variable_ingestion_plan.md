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

TIER1 的目标是生成每个品种一份产品级 YAML，由 `ProductMetaManager` 读取。外部来源里，BigQuant `cn_future_product_info` 提供产品级当前规则线索，BigQuant `cn_future_static_data` 提供合约-交易日级历史截面，Tushare `fut_basic` 提供合约文本说明和交易时间说明。手续费、保证金和下单量限制已迁移为 TIER3 日频字段，不再从合约日频表压缩生成产品级规则。凡是从合约日频表反推产品级历史规则，都必须先检查同一产品、同一交易日、不同合约之间是否一致；不一致时不能直接压缩成产品级规则。

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

### `TransactionCostRule.*` - migrated to TIER3

- Local model: no longer written to TIER1 `product_meta`.
- Tier: TIER1 historical note only.
- Versioned: not applicable in TIER1.
- Local meaning: 手续费规则已从产品级历史规则迁移为合约-交易日级动态字段，归入 `InstrumentDailyStatus.commission_*`。
- External sources: 见 TIER3 中 `InstrumentDailyStatus.commission_type`、`commission_open`、`commission_open_today`、`commission_close`、`commission_close_today` 的入库方案。
- Grain mismatch: 原产品级压缩会丢失同一产品、同一交易日、不同合约之间可能存在的手续费差异，因此不再从日频结算参数表压缩生成 TIER1 `transaction_cost_history`。
- Analysis: 后续若仍需要产品级“摘要手续费规则”，应从 TIER3 结果派生为分析视图或报告，而不是作为 TIER1 权威数据源。
- Validation: TIER1 YAML 不应再包含 `transaction_cost_history`；手续费字段完整性、单位归一化和跨源校验均在 TIER3 执行。
- Output: none in TIER1。
- Open questions: 暂无。

### `PriceTickRule.effective_date`

- Local model: `PriceTickRule`
- Tier: TIER1
- Versioned: yes, `price_tick_history`
- Local meaning: 最小变动价位规则开始生效的交易日。
- External sources: `docs/代码修改笔记/期货交易规则/中国期货市场历史品种最小价格变动（Price Tick 最小变动价位）变更调研报告 _Antigravity.md` 为唯一权威来源。
- Grain mismatch: 调研报告为品种级历史事实，与本地产品级版本规则匹配。
- Analysis: 以调研报告列出的历史变更日期作为 `effective_date` 事实边界。经调研，中国期货历史上最小变动价位变更不采用新老划断方案，因此每次变更按产品级规则直接切换。
- Validation: 不再使用外部数据源做二次校验；入库结果应完整覆盖调研报告列出的真实历史变更。
- Output: `price_tick_history[].effective_date`。
- Open questions: 暂无。

### `PriceTickRule.price_tick`

- Local model: `PriceTickRule`
- Tier: TIER1
- Versioned: yes
- Local meaning: 最小变动价位数值。
- External sources: `docs/代码修改笔记/期货交易规则/中国期货市场历史品种最小价格变动（Price Tick 最小变动价位）变更调研报告 _Antigravity.md` 为唯一权威来源。
- Grain mismatch: 调研报告给出品种级变更前后值，与本地产品级版本规则匹配。
- Analysis: 对每个规则版本直接采用调研报告中的变更后最小变动价位；首个历史版本采用调研报告中的变更前最小变动价位或上市初始规则。调研报告中列为非真实变更、非标准品种、旧代码或冗余记录的内容不得写入真实历史版本。
- Validation: 不再使用外部数据源做二次校验；版本序列应完整覆盖调研报告列出的真实变更品种，且不存在需要由 `PriceTickRule` 承接的合约级新老划断例外。
- Output: `price_tick_history[].price_tick`。
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

### `OrderVolumeLimitsRule.*` - migrated to TIER3

- Local model: no longer written to TIER1 `product_meta`.
- Tier: TIER1 historical note only.
- Versioned: not applicable in TIER1.
- Local meaning: 下单量限制规则已从品种级历史规则迁移为合约-交易日级动态字段，归入 `InstrumentDailyStatus.max_market_order_volume`、`min_market_order_volume`、`max_limit_order_volume`、`min_limit_order_volume`。
- External sources: 见 TIER3 中四个 `InstrumentDailyStatus.*_order_volume` 字段的入库方案。
- Grain mismatch: BigQuant `cn_future_static_data` 的四个 order volume 字段本身是合约-交易日级。2026-07-02 的数据画像显示，多个产品在同一交易日的不同合约之间存在真实 tuple 差异，例如上期所/能源中心临近交割合约最小下单量不同，中金所不同合约组的市价单最大量不同。因此该变量不能安全压缩为纯品种级规则。
- Analysis: TIER1 不再生成 `order_volume_limits_history`。若未来仍需要品种级默认值，可从 TIER3 派生分析视图或服务端 fallback，但不得作为权威 TIER1 入库结果。
- Validation: TIER1 YAML 不应再包含 `order_volume_limits_history`；下单量限制字段完整性、同一 `(trading_date, instrument)` 唯一性和缺失值策略均在 TIER3 执行。
- Output: none in TIER1。
- Open questions: 领域模型和 manager 中现存 `OrderVolumeLimitsRule` / `get_order_volume_limits()` 后续是否删除或改为兼容旧数据，另行决定。

### `BaseSessions.effective_date`

- Local model: `BaseSessions`
- Tier: TIER1
- Versioned: yes, `sessions_history`
- Local meaning: 基础交易时段规则开始生效的交易日，不包含临时或生命周期 override。
- External sources:
  - universe：`db/rules_ingestion/data/derived/20260624/product_meta_static/yaml/*.yaml`。
  - 历史事实主来源：`docs/代码修改笔记/期货交易规则/交易时段基础规则/期货品种上市时间汇总.md`、`期货品种夜盘开始日期和调整情况汇总报告.md`、`期货品种日盘交易时段调整报告.md`。
  - 当前规则校验来源：BigQuant `db/rules_ingestion/data/raw/20260622/bigquant/cn_future_product_info/full.parquet` 的 `trading_period`。
  - 日期转换辅助：Tushare 交易日历 `db/rules_ingestion/data/raw/20260622/tushare/fut_trade_cal/`。
- Grain mismatch: BigQuant `trading_period` 是品种级当前文本/编码，只反映当前普通交易日规则；基础规则历史文档包含上市日、夜盘物理日晚间日期和历史调整日期，需要转换为 `sessions_history` 的产品级版本序列。
- Analysis: 以 97 个 static YAML 品种为入库 universe。每个产品至少生成一个上市日起的基础 session 版本；夜盘启用和永久时段调整生成新的基础版本；中金所日盘永久调整生成新的基础版本。夜盘报告中的 `night_session_start_from`、夜盘调整日期若为物理日晚间口径，应结合交易日历转换为目标 `trading_date` 口径，并在中间审计输出保留原始物理日期和转换后交易日。BigQuant `trading_period` 只用于解析和校验最新版本，不用于反推历史生效日。
- Validation: 每个 universe 品种必须至少有一个 `sessions_history` 版本；同一产品版本按 `effective_date` 升序且不得重复；最新版本应与 BigQuant `trading_period` 一致，不一致进入审计；历史版本必须可追溯到基础规则文档。节假日无夜盘、疫情暂停夜盘、系统故障延迟开市、最后交易日缩短、熔断等不得写入 `sessions_history`。
- Output: `sessions_history[].effective_date`。
- Open questions: 暂无。

### `BaseSessions.morning_sessions`

- Local model: `BaseSessions`
- Tier: TIER1
- Versioned: yes
- Local meaning: 上午连续竞价小节列表，左闭右开语义。
- External sources: 基础规则历史文档为历史版本主来源；BigQuant `trading_period` 为当前版本校验来源；Tushare 交易时间文本仅作为辅助线索。
- Grain mismatch: 外部多为 `090000-101500` 或自然语言文本，需要解析为 `[['09:00', '10:15'], ['10:30', '11:30']]`；中金所与商品交易所上午结构不同，且中金所股指/国债存在历史开盘时间调整。
- Analysis: 商品交易所普通上午盘通常为 `09:00-10:15`、`10:30-11:30`；中金所当前股指和国债通常为 `09:30-11:30`，历史上股指和国债部分品种曾为 `09:15-11:30`。上午盘基础永久调整写入新的 `sessions_history` 版本；最后交易日、熔断或临时暂停造成的上午差异不写入基础版本。
- Validation: 时间格式统一为 `HH:MM`；每个小节按交易日内时间顺序排列且不重叠；最新上午盘应能从 BigQuant 当前 `trading_period` 解析得到；中金所历史版本须与日盘调整报告一致。
- Output: `sessions_history[].morning_sessions`。
- Open questions: 暂无。

### `BaseSessions.afternoon_sessions`

- Local model: `BaseSessions`
- Tier: TIER1
- Versioned: yes
- Local meaning: 下午连续竞价小节列表。
- External sources: 基础规则历史文档为历史版本主来源；BigQuant `trading_period` 为当前版本校验来源；Tushare 交易时间文本仅作为辅助线索。
- Grain mismatch: 外部多为文本或压缩时间串，需要解析为 `[['13:30', '15:00']]`、`[['13:00', '15:00']]` 或 `[['13:00', '15:15']]` 等结构化区间。
- Analysis: 商品交易所普通下午盘通常为 `13:30-15:00`；中金所股指当前为 `13:00-15:00`，国债当前为 `13:00-15:15`。IF/IH/IC 在 2016-01-01 起下午收盘由 `15:15` 调整为 `15:00`，应作为基础版本变化；TF/T/TS 在 2020-07-20 只调整上午开盘，下午仍为 `15:15`。最后交易日删除下午盘或提前收盘属于 `session_override_rules`，不得改写基础规则。
- Validation: 最新下午盘应与 BigQuant 当前 `trading_period` 一致；中金所历史下午盘版本须与日盘调整报告一致；所有区间须满足开始早于结束。
- Output: `sessions_history[].afternoon_sessions`。
- Open questions: 暂无。

### `BaseSessions.night_sessions`

- Local model: `BaseSessions`
- Tier: TIER1
- Versioned: yes
- Local meaning: 夜盘连续竞价小节列表，无夜盘则为空列表。
- External sources:
  - 历史事实主来源：`期货品种夜盘开始日期和调整情况汇总报告.md`。
  - 当前规则校验来源：BigQuant `cn_future_product_info.trading_period`。
  - 日期转换辅助：Tushare 交易日历 `fut_trade_cal`。
  - 特殊安排边界参考：`中国期货市场交易时段规则特殊安排_历史事实_CodeX.md`。
- Grain mismatch: 夜盘启用和调整文档中的日期常为物理日历日晚间，而 YAML `effective_date` 使用交易日口径；BigQuant 当前文本只显示当前夜盘是否存在及当前收市时间，不包含历史启用、暂停和恢复过程。
- Analysis: 夜盘从无到有属于基础 session 新版本；夜盘永久缩短或永久调整也属于基础 session 新版本。夜盘区间按产品写入，例如 `[['21:00', '23:00']]`、`[['21:00', '01:00']]`、`[['21:00', '02:30']]`；无夜盘写 `[]`。历史上的临时停夜盘、节假日后首个交易日无夜盘、2019-12-26 延迟开市、熔断等不写入基础夜盘，而进入 `session_override_rules` 或 realized 事件。疫情期间夜盘暂停是日期区间 override，不应生成“暂停”和“恢复”两个基础版本；PG 等上市时处于疫情暂停期的品种，基础规则仍可具有夜盘，实际无夜盘由疫情 override 解释。
- Validation: 最新夜盘区间应与 BigQuant 当前 `trading_period` 一致；夜盘启用前版本必须为 `[]`；永久调整版本必须与夜盘调整报告一致；无夜盘产品最新版本必须输出 `[]`。所有跨零点区间以 `HH:MM` 字符串保存，由下游 resolver 按交易日语义解释。
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

TIER2 的目标是生成合约注册表。每个交易所一个 parquet 文件，包含其下所有合约。当前 `InstrumentRegistryManager` 会读取目录下所有 `*.parquet`，拼接后以本地标准 `instrument` 作为唯一索引。TIER2 应优先服务“合约身份、生命周期、交割属性和报单代码”的稳定查询，不承载日频状态变化。TIER2 合约 universe 统一来自 Tushare `fut_basic`，并只保留真实可交易合约；主连、指数、连续等人为构造合约应剔除。由于交易所结算参数表历史覆盖不完整，TIER2 的 `fut_basic` 合约 universe 会大于 TIER3 的交易所结算参数表 universe，这是预期现象。

预期生成文件：
* `SHFE.parquet`
* `INE.parquet`
* `DCE.parquet`
* `CZCE.parquet`
* `GFEX.parquet`
* `CFFEX.parquet`

### `InstrumentRegistry.instrument`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 本地标准合约代码，全市场唯一主键。
- External sources: Tushare `fut_basic.symbol` 为计划解析来源；实际执行中见下方 `Plan deviation`。
- Grain mismatch: `fut_basic` 同时包含真实可交易合约和人为构造的主连、指数、连续等合约，需要先做 universe 过滤。
- Analysis: 计划直接使用 `fut_basic.symbol` 生成本地 canonical instrument，不使用 BigQuant 或交易所结算表做校验，也不再维护多来源 source-to-local 映射。真实可交易合约的代码应为品种字母后接 3/4 位交割年月数字；`IC`、`ICL`、`ICL1` 这类指数、主连或连续合约应剔除。
- Validation: `instrument` 必须全市场唯一。
- Output: registry parquet 列 `instrument`。
- Plan deviation: TIER2 实际入库时改用 `ts_code` 点号前缀生成本地 `instrument`，因为 CZCE 的 `symbol` 会折叠 decade 信息，例如 `CF1001.ZCE` 与 `CF2001.ZCE` 都可能对应 `symbol=CF001`，无法满足全市场唯一主键约束。`fut_basic.symbol` 改用于生成 `exchange_inst`，并按交易所报单代码大小写约定标准化：CFFEX/CZCE 品种字母保留大写，SHFE/DCE/GFEX/INE 品种字母使用小写。
- Open questions: 暂无，沿用 `decisions.md` 中“本地标准代码体系”。

### `InstrumentRegistry.product`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 合约所属本地标准品种代码。
- External sources: Tushare `fut_basic.fut_code` 与 `fut_basic.symbol`。
- Grain mismatch: 无。
- Validation: `product` 必须能在 TIER1 产品 YAML 中找到；`product` 应与 `instrument` 的品种前缀一致。
- Output: registry parquet 列 `product`。
- Open questions: 沿用 `decisions.md` 中“本地标准代码体系”。


### `InstrumentRegistry.exchange`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 本地标准交易所代码。
- External sources: Tushare `fut_basic.exchange`。
- Grain mismatch: 无。
- Analysis: 使用 `fut_basic.exchange` 生成本地 `exchange` 字段。registry parquet 可按交易所分区保存，但行内仍保留 `exchange` 字段。
- Validation: `exchange` 必须为本地支持的交易所代码；与产品所属交易所不一致时进入人工复核。
- Output: registry parquet 列 `exchange`。
- Open questions: 暂无，沿用 `decisions.md` 中“本地标准代码体系”。

### `InstrumentRegistry.list_date`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 合约上市或开始交易日期。
- External sources: Tushare `fut_basic.list_date` 。无需利用 BigQuant 数据做二次校验。
- Grain mismatch: 无。
- Analysis: 无。
- Validation: 无。
- Output: registry parquet 列 `list_date`，日期格式。
- Open questions: 无。

### `InstrumentRegistry.last_trade_date`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 合约最后交易日。
- External sources: Tushare `fut_basic.delist_date`。无需利用 BigQuant 数据做校验。。
- Grain mismatch: 无。
- Analysis: 无。
- Validation: 无。
- Output: registry parquet 列 `last_trade_date`。
- Open questions: 无。

### `InstrumentRegistry.delivery_year`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 交割年份。
- External sources: Tushare `fut_basic.d_month`解析。
- Grain mismatch: 无。
- Analysis: 无。
- Validation: 无。
- Output: registry parquet 列 `delivery_year`。
- Open questions: 暂无。

### `InstrumentRegistry.delivery_month`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 交割月份。
- External sources: Tushare `fut_basic.d_month`、解析。
- Grain mismatch: 无。
- Analysis: 无。
- Validation: 无。
- Output: registry parquet 列 `delivery_month`。
- Open questions: 暂无。


### `InstrumentRegistry.end_deliv_date`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 最后交割日。
- External sources: Tushare `fut_basic.last_ddate`
- Grain mismatch: 无。
- Analysis: 无。
- Validation: 无。
- Output: registry parquet 列 `end_deliv_date`。
- Open questions: 见 `decisions.md` “无交割日期合约的空值策略”。


### `InstrumentRegistry.multiplier`

- Local model: `InstrumentRegistry`
- Tier: TIER2
- Versioned: no
- Local meaning: 该合约注册层面的合约乘数。
- External sources: Tushare `fut_basic.per_unit/multiplier` 。
- Grain mismatch: 无。
- Analysis: 对 CFFEX，取 `multiplier`列；其他交易所取`per_unit`列。
- Validation: 无。
- Output: registry parquet 列 `multiplier`。
- Open questions: 无。

## TIER 3

TIER3 的目标是生成合约-交易日级日频状态 parquet，供 `InstrumentDailyStatusManager` 按 `{exchange}_{product}.parquet` 读取。该层只处理外部数据源可以直接或间接提供的日频状态字段；`TradingSessions` 虽然也是合约-交易日级领域模型，但它由本地 `BaseSessions` 与 `SessionOverrideRules` 解析生成，不属于“外部数据源 -> 本地数据库”的入库对象。保证金与手续费字段的数据来源限定为已下载的交易所历史结算参数表；下单量限制字段使用 BigQuant `cn_future_static_data` 的合约-交易日级观测值，不再压缩为 TIER1 产品级规则。由于交易所结算参数表和 BigQuant 静态日频表历史覆盖均不完整，TIER3 合约 universe 和字段非空覆盖会小于 TIER2 `fut_basic` universe。

目标文件命名：`{EXCHANGE}_{PRODUCT}.parquet`，即交易所代码和品种代码都大写。

### `InstrumentDailyStatus.trading_date`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 交易日，是合约日频状态表的日期主键之一。
- External sources: 交易所原始结算参数表所在日期目录，例如 `trading_day=20260521`。
- Grain mismatch: 大多数交易所结算参数表按交易日下载，目录日期即可直接作为 TIER3 `trading_date`；CFFEX 结算参数表不是每个交易日都有，更像参数发布/变更表。
- Analysis: 除 CFFEX 外，直接从原始目录名解析交易日，并统一转换为 `datetime.date`。CFFEX 需先从 TIER2 合约注册信息获取每张合约的 `list_date` 和 `last_trade_date`，再使用 CFFEX 交易日历生成该生命周期内的交易日列表，并将最近一次有效结算参数向后展开到后续交易日。
- Validation: 非 CFFEX 数据的 `trading_date` 必须与原始结算参数表目录日期一致；CFFEX 展开日期必须落在合约上市日至最后交易日之间；同一 parquet 文件内 `(trading_date, instrument)` 唯一。
- Output: product-date parquet 列 `trading_date`。
- Open questions: 暂无。

### `InstrumentDailyStatus.instrument`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 本地标准合约代码，是合约日频状态表的合约主键。
- External sources: 交易所原始结算参数表中的合约字段，例如 DCE `contractID`、SHFE/INE `INSTRUMENTID`、CZCE `合约代码`、CFFEX `期货合约` 。TIER2 registry 的 `exchange_inst -> instrument` 映射作为交易所合约代码转本地合约代码的权威映射。
- Grain mismatch: 交易所原始合约代码是 exchange format，下单/交易所口径可能与本地 canonical code 不一致；TIER3 universe 由交易所结算参数表决定，可能小于 TIER2 的 Tushare `fut_basic` universe。
- Analysis: 先读取交易所结算参数表中的原始合约字段，并按 TIER2 的 `exchange_inst` 大小写口径归一为 exchange format；再使用 TIER2 registry 中同一交易所下的 `exchange_inst -> instrument` 映射生成本地 `instrument`。不得仅凭字符串规则直接把交易所原始代码改写成本地主键，尤其是 CZCE 跨 decade 合约会出现重复 `exchange_inst`，需要结合 TIER2 注册信息和交易日/合约生命周期处理映射歧义。CFFEX 虽然结算表非日频，但已人工核对确认其结算表合约 universe 包含 Tushare `fut_basic` 的真实可交易合约 universe，因此无需再做覆盖检查，也无需回退到 Tushare universe。
- Validation: 映射后的 `instrument` 必须存在于 TIER2 registry，且其 `exchange` 和 `product` 与输出文件归属一致；同一 `(trading_date, instrument)` 不得重复；若交易所结算参数表合约字段无法映射到 TIER2 `exchange_inst`，需进入审计而不是临时按字符串规则生成本地 `instrument`；TIER3 缺少 TIER2 中某些合约时，应优先解释为交易所结算参数表历史覆盖不足，而不是 TIER2 universe 错误。
- Output: product-date parquet 列 `instrument`。
- Open questions: 暂无，沿用“本地标准代码体系”和 TIER2 普通合约过滤决策。

### `InstrumentDailyStatus.is_tradable`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 该合约在该交易日是否可交易。
- External sources: 暂无；当前不再使用外部数据源推断该字段。
- Grain mismatch: 交易所结算参数表不能稳定表达单合约当日是否可交易，且 BigQuant 日频静态表时间跨度有限。
- Analysis: 本阶段直接忽略该字段，不从外部数据推断交易状态。入库时统一写空值。
- Validation: 确认输出 parquet 中该列存在且全为空；后续如需要恢复该字段，应另行设计来源和规则。
- Output: product-date parquet 列 `is_tradable = None`。
- Open questions: 暂无。

### `InstrumentDailyStatus.inst_life_phase`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 合约生命周期阶段，只允许 `not_started`、`started`、`paused`、`expired`。
- External sources: 暂无；当前不再使用 BigQuant `cn_future_static_data.inst_life_phase`。
- Grain mismatch: 交易所结算参数表不提供本地四类生命周期枚举，且 BigQuant 日频静态表时间跨度有限。
- Analysis: 本阶段直接忽略该字段，不从上市日、最后交易日或结算表出现情况反推生命周期状态。入库时统一写空值。
- Validation: 确认输出 parquet 中该列存在且全为空。
- Output: product-date parquet 列 `inst_life_phase = None`。
- Open questions: 暂无。

### `InstrumentDailyStatus.max_market_order_volume`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 合约在该交易日的市价单最大下单手数。
- External sources: BigQuant `cn_future_static_data.max_market_order_volume`。
- Grain mismatch: 来源为合约-交易日级，与 TIER3 目标粒度一致；不再反推产品级历史规则。
- Analysis: 直接按 `(instrument, trading_date)` 写入。BigQuant `cn_future_static_data` 覆盖日期较短，当前快照最早约为 2024 年；早期年份或 BigQuant 缺失日期不得向前填充、不得使用产品级默认值补齐，统一写 `null`。若后续 TIER3 主表以交易所结算参数表或 registry/calendar 展开了更长日期范围，则只在 BigQuant 有同一合约同一交易日观测值时填入，否则保持空值。`0` 是有效观测值，尤其可能表示该合约/合约组不支持市价单或市价单最大量为零，不能当作缺失。
- Validation: 非空值应为非负整数；若同一 `(trading_date, instrument)` 出现多个值，必须进入审计而不是取众数；与 `min_market_order_volume` 同时非空时应满足 `max_market_order_volume >= min_market_order_volume`，除非经人工确认 `max_market_order_volume = 0` 表示不支持市价单。
- Output: product-date parquet 列 `max_market_order_volume`，允许 `null`。
- Open questions: 当前 `InstrumentDailyStatus` dataclass 尚未包含四个 order volume 字段，正式入库前需要同步扩展模型、manager 和 parquet schema。

### `InstrumentDailyStatus.min_market_order_volume`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 合约在该交易日的市价单最小下单手数。
- External sources: BigQuant `cn_future_static_data.min_market_order_volume`。
- Grain mismatch: 来源为合约-交易日级，与 TIER3 目标粒度一致。
- Analysis: 与其他三个下单量字段一起从 BigQuant 合约日频静态表写入，不做产品级压缩。BigQuant 覆盖前的早期年份或缺失交易日直接写 `null`，不向前填充到合约上市日，也不从同产品其他合约推断。
- Validation: 非空值应为正整数；若 `max_market_order_volume` 非空且大于 0，则应满足 `min_market_order_volume <= max_market_order_volume`；若同一 `(trading_date, instrument)` 多值，进入审计。
- Output: product-date parquet 列 `min_market_order_volume`，允许 `null`。
- Open questions: 同 `max_market_order_volume`。

### `InstrumentDailyStatus.max_limit_order_volume`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 合约在该交易日的限价单最大下单手数。
- External sources: BigQuant `cn_future_static_data.max_limit_order_volume`。
- Grain mismatch: 来源为合约-交易日级，与 TIER3 目标粒度一致。
- Analysis: 直接按合约日频观测写入。BigQuant 覆盖期之外的早期年份和缺失日期写 `null`；不使用产品级默认值、同日其他合约值或最新观测值做补齐。
- Validation: 非空值应为正整数；通常应大于等于 `min_limit_order_volume`，且通常不小于 `max_market_order_volume`，但跨交易所差异需以来源字段为准；同一 `(trading_date, instrument)` 多值进入审计。
- Output: product-date parquet 列 `max_limit_order_volume`，允许 `null`。
- Open questions: 同 `max_market_order_volume`。

### `InstrumentDailyStatus.min_limit_order_volume`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 合约在该交易日的限价单最小下单手数。
- External sources: BigQuant `cn_future_static_data.min_limit_order_volume`。
- Grain mismatch: 来源为合约-交易日级，与 TIER3 目标粒度一致。
- Analysis: 与四字段 tuple 同步处理，按 `(instrument, trading_date)` 原样保留合约级差异。BigQuant 覆盖期之外的早期年份和缺失日期写 `null`，不向前填充。
- Validation: 非空值应为正整数；不得大于 `max_limit_order_volume`；同一 `(trading_date, instrument)` 多值进入审计。
- Output: product-date parquet 列 `min_limit_order_volume`，允许 `null`。
- Open questions: 同 `max_market_order_volume`。

### `InstrumentDailyStatus.long_margin_ratio`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 交易所多头保证金率，按小数存储，例如 `0.08` 表示 8%。
- External sources: 本地已下载交易所结算参数表，目录 `db/rules_ingestion/data/raw/exchange_settlement_data_scraping/`；CFFEX 字段 `合约多头保证金`，SHFE/INE 字段 `SPEC_LONGMARGINRATIO`，CZCE 字段 `交易保证金率` 或 `买交易保证金率`，DCE/GFEX 字段 `specBuyRate`。
- Grain mismatch: 交易所结算参数表通常是合约-交易日级或参数发布日级；CFFEX 更像参数变更列表，需向后展开到后续交易日。CZCE 历史表头变动较多；SHFE 原始目录中包含 INE 品种，但入库时 INE 品种的 `exchange` 必须改回 `INE`。
- Analysis: 按交易所建立字段解析器，先统一合约代码、交易日和交易所代码，再按交易所单位规则归一化为小数。CFFEX 保证金字段按 `12%` 这类百分号文本解析；DCE/GFEX 的 `specBuyRate` 直接使用原值，例如 `0.09` 表示 9%；SHFE/INE 清洗后字段直接使用原值。CZCE 只有单一 `交易保证金率` 时，同时映射为多头和空头；存在买/卖字段时分别取买方字段。CFFEX 参数发布日数据需结合交易日历向前填充，但不得填充到合约上市前或最后交易日后。
- Validation: 数值应在合理区间内，通常 `0 < ratio < 1`；同一 `(trading_date, instrument)` 只能有一个多头保证金率；与 `short_margin_ratio` 的差异应可由交易所字段解释。
- Output: product-date parquet 列 `long_margin_ratio`。
- Open questions: 暂无。

### `InstrumentDailyStatus.short_margin_ratio`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 交易所空头保证金率，按小数存储，例如 `0.08` 表示 8%。
- External sources: 本地已下载交易所结算参数表；CFFEX 字段 `合约空头保证金`，SHFE/INE 字段 `SPEC_SHORTMARGINRATIO`，CZCE 字段 `交易保证金率` 或 `卖交易保证金率`，DCE/GFEX 字段 `specSellRate`。
- Grain mismatch: 与多头保证金率相同，需处理交易所目录归属、表头历史变化、发布日向交易日展开和单位归一化。
- Analysis: 与 `long_margin_ratio` 成对处理。CZCE 只有单一保证金率字段时可同时写入多空字段；存在买/卖字段时空头取卖方字段。若某来源只给最低保证金率或无法区分多空，不得静默填充为空头字段，除非该交易所该表头明确表示买卖同率。
- Validation: 数值应在合理区间内；同一合约同一日多空字段缺一时不得静默补零；INE 品种必须在输出文件名和行内 `exchange` 口径中归属 `INE`。
- Output: product-date parquet 列 `short_margin_ratio`。
- Open questions: 暂无。

### `InstrumentDailyStatus.commission_type`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 手续费计价方式，`flat` 表示按手收取，`ratio` 表示按成交金额比例收取。
- External sources: 本地交易所结算参数表；CFFEX `交易手续费标准` 文本解析，SHFE/INE `TRADEFEERATION` 与 `TRADEFEEUNIT` 非零/非空字段判断，CZCE `手续费收取方式`，DCE/GFEX `style`。
- Grain mismatch: 交易所结算参数表是合约-交易日级或参数发布日级，本地 TIER3 也是合约-交易日级；CFFEX 参数发布日需展开。外部字段可能以中文文本、字段二选一、枚举或数值单位表达同一语义。
- Analysis: 建立交易所专用映射。CFFEX 文本含“元/手”映射为 `flat`，含“万分之”或比例表达映射为 `ratio`；SHFE/INE 优先使用非零非空的 `TRADEFEERATION` 或 `TRADEFEEUNIT` 判断比例/金额，清洗后数值直接使用；DCE/GFEX 直接映射 `style`，其中 `绝对值` 为元/手，`比例值` 为万分之；CZCE 若 `手续费收取方式` 缺失，则按表头和字段语义推断，缺失收取方式且 `交易手续费=5` 这类数值按 `5` 元/手处理。
- Validation: `commission_type` 必须为 `flat` 或 `ratio`；同一日同一合约不得出现多种类型；类型应与手续费数值数量级一致。
- Output: product-date parquet 列 `commission_type`。
- Open questions: 暂无。

### `InstrumentDailyStatus.commission_open`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 开仓手续费，单位由 `commission_type` 决定；`flat` 为元/手，`ratio` 为成交金额比例小数。
- External sources: 本地交易所结算参数表；CFFEX `交易手续费标准`，SHFE/INE `TRADEFEERATION` 或 `TRADEFEEUNIT`，CZCE `交易手续费`，DCE/GFEX `openFee`。
- Grain mismatch: 大多数来源可直接定位到合约-交易日；CFFEX 发布日需要向后展开；部分交易所字段同时表达开仓和平仓默认费率。
- Analysis: 按 `commission_type` 和交易所单位规则归一化数值。若交易所只给一个普通交易手续费字段，则写入 `commission_open`，并作为 `commission_close` 的默认候选；DCE/GFEX 明确给出 `openFee` 时优先使用该字段，且按 `style` 判断元/手或万分之。文本如“万分之0.23”需转换为 `0.000023`，金额如“3元/手”转换为 `3.0`。CZCE 在 `手续费收取方式=比例值` 时，`交易手续费=1` 表示万分之一；没有该字段时，`交易手续费=5` 表示 5 元/手。
- Validation: 不允许负数；`ratio` 通常应远小于 `1`，`flat` 应为合理元/手金额；同一 `(trading_date, instrument)` 必须唯一；归一化前后值需进入中间审计报告。
- Output: product-date parquet 列 `commission_open`。
- Open questions: 暂无。

### `InstrumentDailyStatus.commission_open_today`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 日内开平仓对应的开仓侧特殊手续费；无特殊规则时允许为空。
- External sources: DCE/GFEX `shortOpenFee` 为主；其他交易所结算参数表目前未提供稳定对应字段。
- Grain mismatch: 该字段只在部分交易所/部分历史阶段存在；不能从普通开仓手续费机械复制。
- Analysis: 仅当交易所结算参数表明确给出日内开仓优惠或特殊开仓字段时写入。DCE/GFEX 使用 `shortOpenFee`；其他交易所默认 `None`，除非结算参数表字段明确支持。
- Validation: 若非空，必须与 `commission_type` 的单位一致；不得大于明显异常数量级；缺失时保留为空，不补零。
- Output: product-date parquet 列 `commission_open_today`。
- Open questions: 暂无。

### `InstrumentDailyStatus.commission_close`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 普通平仓手续费，区别于平今仓手续费 `commission_close_today`。
- External sources: 本地交易所结算参数表；CFFEX `交易手续费标准`，SHFE/INE `TRADEFEERATION` 或 `TRADEFEEUNIT`，CZCE `交易手续费`，DCE/GFEX `offsetFee`。
- Grain mismatch: 有些交易所用同一字段表达开仓和普通平仓；DCE/GFEX 明确区分 `openFee` 与 `offsetFee`。
- Analysis: 若交易所提供独立平仓字段，直接使用；若只提供普通交易手续费字段，则普通平仓默认与开仓同值，但需在中间审计报告中标记为字段共用而非独立观测。入库 parquet 统一输出新版字段 `commission_close`。
- Validation: `commission_close` 与 `commission_open` 不一致时应能由交易所字段解释；平今字段不得覆盖普通平仓字段。
- Output: product-date parquet 列 `commission_close`。
- Open questions: 暂无。

### `InstrumentDailyStatus.commission_close_today`

- Local model: `InstrumentDailyStatus`
- Tier: TIER3
- Versioned: yes, daily
- Local meaning: 平今仓手续费；无平今特殊规则时允许为空，读取后由业务逻辑判断是否回退普通平仓费。
- External sources: 本地交易所结算参数表；CFFEX `平今仓收取率` 结合 `交易手续费标准` 推断，SHFE/INE `DISCOUNTRATE` 结合 `TRADEFEERATION` 或 `TRADEFEEUNIT` 计算，CZCE `日内平今仓手续费`，DCE/GFEX `shortOffsetFee`。
- Grain mismatch: 平今字段可能是直接金额/比例，也可能是相对普通手续费的收取率；部分交易所或历史阶段缺失。
- Analysis: 直接字段优先。CFFEX 若 `平今仓收取率` 表示普通手续费的倍数或比例，则先解析普通手续费，再计算平今值并保留推断来源。SHFE/INE 先用 `TRADEFEERATION` 或 `TRADEFEEUNIT` 得到普通手续费率或手续费额，再按相同单位计算平今值：平今仓手续费率 = `DISCOUNTRATE * TRADEFEERATION`；平今仓手续费额 = `DISCOUNTRATE * TRADEFEEUNIT`；若 `DISCOUNTRATE=0`，则该合约免收平今仓手续费，`commission_close_today=0`。CZCE/DCE/GFEX 使用对应字段。字段缺失时保留 `None`，不自动等同普通平仓费，避免掩盖交易所未披露或历史缺口。
- Validation: 非空值必须与 `commission_type` 单位一致；若由收取率或折扣率推断，需保留原始普通手续费字段、`平今仓收取率`/`DISCOUNTRATE` 和计算结果。
- Output: product-date parquet 列 `commission_close_today`。
- Open questions: 暂无。

### TIER3 supporting ingestion notes

- Calendar input: 非 CFFEX 交易日直接来自交易所结算参数表目录日期；CFFEX 需使用 CFFEX 交易日历在合约生命周期内展开日频记录。
- Code normalization: TIER3 合约代码应先归一为 TIER2 `exchange_inst` 的交易所报单口径，再通过 TIER2 registry 映射到本地 `instrument`。本地 `instrument` 口径为“品种字母大写 + 交割数字、无交易所后缀”，并与 TIER2 实际入库时由 `ts_code` 点号前缀生成的 `instrument` 保持一致。
- Settlement source priority: TIER3 保证金、手续费、交易日和合约字段以本地已下载交易所结算参数表为入库来源，不使用 BigQuant 或 Tushare 做二次校验；下单量限制四字段例外，使用 BigQuant `cn_future_static_data` 的合约-交易日级字段作为唯一来源。BigQuant 覆盖期之外的早期年份或缺失日期直接写 `null`，不向前填充。
- Unit normalization: CFFEX 原始字段通常带清晰文本单位，保证金如 `12%` 按百分号解析，手续费如 `万分之0.23` 或 `3元/手` 按文本语义解析。CZCE 需结合表头和 `手续费收取方式` 解析，缺少收取方式时 `交易手续费` 按元/手处理；若 `手续费收取方式=比例值`，则 `交易手续费` 按万分之处理。DCE/GFEX 依赖 `style` 字段，`绝对值` 为元/手，`比例值` 为万分之；`specBuyRate` 和 `specSellRate` 直接使用原值。SHFE/INE 清洗后的 `TRADEFEERATION`、`TRADEFEEUNIT`、保证金率和 `DISCOUNTRATE` 直接使用原值。
- INE handling: INE 品种虽然可能位于 `exchange=SHFE` 原始目录或与 SHFE 共用抓取流程，但清洗后和 TIER3 输出中必须恢复为 `exchange=INE`，并按 `INE_{PRODUCT}.parquet` 的本地交易所口径管理。
- Provenance: 当前 `InstrumentDailyStatus` 模型没有 provenance 字段；建议在原始下载区和中间审计报告中保留来源表、下载批次、字段冲突、缺失字段、推断规则和单位归一化记录，而不是向 TIER3 parquet 增加未建模字段。
