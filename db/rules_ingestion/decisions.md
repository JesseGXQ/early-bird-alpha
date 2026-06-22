# Decisions

本文档用于记录规则入库任务中需要人工决策的问题。

它不是执行日志，也不是完整设计文档。只有当某个问题会影响本地领域模型、数据口径、入库规则、数据源优先级或人工校验策略，并且无法仅靠代码或现有文档安全决定时，才记录在这里。每条决策应尽量包含背景、可选方案、影响范围和最终结论，便于后续审计和复盘。

## List of Decided Decisions

- [本地标准代码体系](#2026-06-21---本地标准代码体系)：本地统一采用交易所官方/CTP 风格代码，并在入库层维护 source-to-local 映射。
- [StaticMeta 是否允许历史变化](#2026-06-21---staticmeta-是否允许历史变化)：当前保持静态模型，只有全历史唯一时自动入库，多值问题后续再决策。
- [手续费字段语义与 scope 口径](#2026-06-21---手续费字段语义与-scope-口径)：先用 Tushare 数值模式初判并记录过程，后续用交易所公告抽样校验。
- [产品级规则压缩失败时的处理方式](#2026-06-21---产品级规则压缩失败时的处理方式)：只有当前模型能表达的例外才入库，无法覆盖的情况进入本文档等待拍板。
- [session override 字典 schema](#2026-06-21---session-override-字典-schema)：该问题由既有 schema 设计文档取代，正式存储 schema 参考对应设计文档。
- [InstrumentRegistry.product_class 本地枚举](#2026-06-21---instrumentregistryproduct_class-本地枚举)：当前只覆盖普通期货合约，普通期货临时约定为 `1`，其他类型等待后续扩展。
- [合约生命周期日期字段语义优先级](#2026-06-21---合约生命周期日期字段语义优先级)：按字段语义分工，Tushare `delist_date` 主导最后交易日，BigQuant `start_deliv_date/end_deliv_date` 主导交割日期。
- [无交割日期合约的空值策略](#2026-06-21---无交割日期合约的空值策略)：无实物交割日期时允许空值，并已将 manager/dataclass 类型扩展为可空日期。
- [同一合约，不同外部数据源的取值不同如何处理](#2026-06-21---同一合约不同外部数据源的取值不同如何处理)：同一合约同一字段跨数据源取值不同时视为数据异常，记录冲突供人工核验。
- [TIER3 日期与合约覆盖范围](#2026-06-21---tier3-日期与合约覆盖范围)：TIER3 只生成交易所开市日、且来源表存在记录的普通合约状态，不主动补全上市前或到期后记录。
- [is_trading 与 inst_life_phase 映射口径](#2026-06-21---is_trading-与-inst_life_phase-映射口径)：先统计 BigQuant 原始字符串值域并记录结果，后续再人工建立显式映射关系，初次分析暂不处理组合冲突。
- [保证金率来源优先级与单位归一化](#2026-06-21---保证金率来源优先级与单位归一化)：BigQuant 作为保证金率主来源，Tushare `fut_settle` 做校验，入库统一归一化为小数。

## Template

```markdown
## YYYY-MM-DD - short_title

- Status: pending / decided / superseded
- Topic:
- Context:
- Decision needed:
- Options:
  - A:
  - B:
  - C:
- Recommended option:
- Rationale:
- Impact:
- Final decision:
- Follow-up:
```

## Pending Decisions

暂无。

## Decided and superseded decisions

## 2026-06-21 - TIER3 日期与合约覆盖范围

- Status: decided
- Topic: `InstrumentDailyStatus` 是否只为交易所开市日、普通合约生命周期内的合约生成记录。
- Context: BigQuant `cn_future_static_data` 是合约-交易日级来源，Tushare `fut_trade_cal` 是交易所-自然日级日历。TIER3 可以只保存真实交易日上的合约状态，也可以扩展到休市日、上市前、到期后的完整日历状态。
- Decision needed: TIER3 parquet 的行覆盖范围应如何定义。
- Options:
  - A: 只生成交易所开市日、且来源表存在记录的普通合约状态；上市前和到期后状态仅用于校验，不主动补全。
  - B: 对每个合约从上市前到到期后按交易所日历补全状态，显式写入 `not_started` 和 `expired`。
  - C: 按自然日全量补齐，包括休市日。
- Recommended option: A。
- Rationale: 当前 TIER3 服务回测和交易规则查询，过度补全会显著增加数据量和歧义；生命周期边界已经由 TIER2 registry 表达。
- Impact: 影响 `trading_date` 覆盖、`is_tradable` 缺失解释、manager 查询不到记录时的语义。
- Final decision: A。
- Follow-up:

## 2026-06-21 - is_trading 与 inst_life_phase 映射口径

- Status: decided
- Topic: BigQuant `is_trading`、`inst_life_phase` 如何映射为本地 `is_tradable` 和四类生命周期枚举。
- Context: 本地模型要求 `inst_life_phase` 只能为 `not_started`、`started`、`paused`、`expired`，且与 `is_tradable` 有组合约束；BigQuant 字段值域需要实样确认。
- Decision needed: 当 BigQuant 原始值域与本地四类枚举不完全一致，或 `started` 与 `is_trading=False` 同时出现时，应如何处理。
- Options:
  - A: 先统计原始值域并建立显式映射表；无法映射或组合冲突的记录进入异常报告，不自动入库。
  - B: 用 `list_date/last_trade_date/is_trading` 重新推导生命周期阶段，忽略外部 `inst_life_phase`。
  - C: 直接保留 BigQuant 原始值，并扩展本地模型枚举。
- Recommended option: A。
- Rationale: BigQuant 字段最接近日频状态事实，但本地 literal 需要严格控制；先映射后校验能避免模型被外部脏值污染。
- Impact: 影响 TIER3 日频状态字段质量，以及下游对暂停、未上市、到期状态的判断。
- Final decision: 将原始值域（原始值为 string ）做统计分析，并将统计结果写入文档，等待后续人工建立显示映射关系。初次分析时，暂时忽略组合关系冲突问题。
- Follow-up:

## 2026-06-21 - 保证金率来源优先级与单位归一化

- Status: decided
- Topic: `long_margin_ratio`、`short_margin_ratio` 在 BigQuant、Tushare 之间如何选择主来源，并统一小数/百分数单位。
- Context: BigQuant `long_margin_ratio/short_margin_ratio` 与 Tushare `fut_settle.long_margin_rate/short_margin_rate` 都可提供合约-交易日级保证金率；Tushare `ft_limit.m_ratio` 是最低交易保证金率，语义不同。不同来源可能存在单位差异或投机/套保、最低/实际口径差异。
- Decision needed: 入库时应采用哪一来源为主、如何归一化单位、冲突如何处理。
- Options:
  - A: BigQuant 作为主来源，Tushare `fut_settle` 做校验；所有保证金率统一写小数，跨源冲突进入报告。
  - B: Tushare `fut_settle` 作为主来源，BigQuant 做校验。
  - C: 按品种或交易所选择来源优先级。
- Recommended option: A。
- Rationale: TIER3 其他状态字段已以 BigQuant `cn_future_static_data` 为主，保持同表同粒度可降低 join 和缺失风险；Tushare 适合做独立校验。
- Impact: 影响保证金日频历史准确性、单位归一化逻辑和跨源冲突报告。
- Final decision: A。
- Follow-up:

## 2026-06-21 - 同一合约，不同外部数据源的取值不同如何处理
- Status: decided
- Topic: 同一合约、同一注册字段在 BigQuant、Tushare 等不同外部数据源中取值不一致时，入库层如何处理。
- Context: TIER2 注册信息原则上在合约上市时即已确定，并在合约生命周期内保持固定；如果不同外部数据源对同一合约同一字段给出不同值，更可能是来源口径、代码映射或数据质量问题，而不是合约注册信息真实发生历史变化。
- Decision needed: 遇到跨数据源字段冲突时，是视为数据异常、按来源优先级取值，还是触发模型扩展。
- Options:
  - A: 视为数据异常；记录冲突来源和值，进入人工核验，不自动入库。
  - B: 按预设数据源优先级取值，低优先级来源只做校验备注。
  - C: 为冲突字段扩展来源版本表，保留多个外部值。
- Recommended option: A。
- Rationale: 合约注册字段是稳定事实，跨源不一致应先被当作数据质量或口径问题处理；直接择优或扩展模型都可能把脏数据固化为本地事实。
- Impact: 影响 `multiplier`、`exchange_inst`、日期字段等合约级字段的跨源校验、异常记录和人工核验流程。
- Final decision: 同一份外部数据源，合约的某个字段只可能有一个值；若多个数据源，同一合约同一字段，出现值不相同的情况，应视为数据异常。将异常记录在本文档供后续人工核验。
- Follow-up:

## 2026-06-21 - InstrumentRegistry.product_class 本地枚举

- Status: decided
- Topic: `InstrumentRegistry.product_class` 的本地整数枚举定义。
- Context: 本地模型要求 `product_class: int`，BigQuant `cn_future_static_data.product_class` 可能是字符串或外部编码；Tushare `fut_type` 主要用于过滤普通合约，不能完整表达产品类型。
- Decision needed: 本地应如何定义 `product_class` 的整数枚举，以及当前入库是否只覆盖普通期货合约。
- Options:
  - A: 先只允许普通期货合约入库，并临时约定普通期货为 `1`；其他类型隔离，等待后续模型扩展。
  - B: 立即定义完整枚举，如期货、期权、组合、套利、主力连续等。
  - C: 将本地字段改为字符串，直接保留外部类型。
- Recommended option: A。
- Rationale: 当前任务聚焦期货交易规则数据库，先保证普通期货合约注册表准确，避免合成合约污染。
- Impact: 影响 TIER2 合约过滤、`product_class` 列写入和后续期权/组合扩展。
- Final decision: A。
- Follow-up:

## 2026-06-21 - 合约生命周期日期字段语义优先级

- Status: decided
- Topic: `list_date`、`last_trade_date`、`start_deliv_date`、`end_deliv_date` 在多个来源不一致时的优先级。
- Context: Tushare `delist_date` 明确描述为最后交易日期，BigQuant 同时存在 `delist_date`、`expire_date`、`last_deliver_date`、`start_deliv_date`、`end_deliv_date` 等字段，命名语义不能默认等同。
- Decision needed: 多来源日期冲突时，采用哪一套优先级和人工复核规则。
- Options:
  - A: 以字段语义最明确者为主：Tushare `delist_date` 主导最后交易日，BigQuant `start_deliv_date/end_deliv_date` 主导交割日期，冲突时查交易所规则。
  - B: 以 BigQuant 为统一主来源，Tushare 只做校验。
  - C: 以交易所公告/合约规则为唯一主来源，外部结构化数据只做下载缓存。
- Recommended option: A。
- Rationale: 结构化来源能提高覆盖率，但日期字段的语义风险较高，必须按字段含义分工。
- Impact: 影响 TIER2 注册表生命周期字段的准确性和后续 session override、交割月逻辑。
- Final decision: A。
- Follow-up:

## 2026-06-21 - 无交割日期合约的空值策略

- Status: decided
- Topic: 金融期货或其他无实物交割品种的 `start_deliv_date`、`end_deliv_date` 如何存储。
- Context: 当前 `InstrumentRegistry` dataclass 将交割日期声明为 `date`，但部分品种可能没有实物交割起止日期，外部字段可能为空。
- Decision needed: 本地 parquet 和模型是否允许这些字段为空，或是否需要占位日期/模型扩展。
- Options:
  - A: parquet 允许空值；manager/dataclass 后续扩展为 `date | None`。
  - B: 使用 `last_trade_date` 或现金交割日期填充缺失交割日期。
  - C: 使用固定占位日期，并在文档中声明。
- Recommended option: A。
- Rationale: 缺失交割日期是业务语义，不应伪造日期；但这会要求后续代码类型同步。
- Impact: 影响 `InstrumentRegistry` 类型、parquet schema、金融期货注册表入库。
- Final decision: A。且 manager/dataclass 类型拓展已完成。
- Follow-up: 

## 2026-06-21 - 本地标准代码体系

- Status: decided
- Topic: BigQuant、Tushare 与本地数据库之间的产品代码、交易所代码、合约代码标准。
- Context: BigQuant 常见交易所后缀包括 `SHF`、`CZC`、`CFE`、`GFE`，Tushare 常见代码包括 `SHFE`、`CZCE`、`CFFEX`、`GFEX`。本地 YAML 文件和模型需要稳定主键。
- Decision needed: 本地标准应采用哪套交易所代码和合约代码格式。
- Options:
  - A: 本地统一采用交易所官方/CTP 风格，如 `SHFE`、`CZCE`、`CFFEX`，产品代码大写。
  - B: 本地沿用 BigQuant 风格，如 `SHF`、`CZC`、`CFE`，减少 BigQuant 入库转换。
  - C: 本地同时保留 canonical code 与 source code，所有表增加来源代码字段。
- Recommended option: A，并在入库层维护 source-to-local 映射表。
- Rationale: 本地规则库应尽量接近交易所和交易接口口径，外部供应商代码差异留在 adapter 层。
- Impact: 影响 TIER1 YAML 文件名、`StaticMeta.exchange/product`、TIER2 合约代码标准化和跨源 join。
- Final decision: 采纳 Recommended option。
- Follow-up:

## 2026-06-21 - StaticMeta 是否允许历史变化

- Status: decided
- Topic: `position_date_type`、`max_margin_side_algorithm`、`combination_type` 当前为静态字段，但外部来源是合约-交易日级。
- Context: 若这些字段在历史上发生变化，或同一产品不同合约存在差异，当前 `StaticMeta` 无法表达版本。
- Decision needed: 遇到多值时，是扩展模型为历史版本，还是只记录当前/主流值并人工备注。
- Options:
  - A: 保持当前静态模型；仅在全历史唯一时自动入库，多值进入人工处理。
  - B: 将相关字段版本化，新增 history 结构。
  - C: 保持静态字段，同时增加 exceptions 字段。
- Recommended option: A，先用数据画像确认多值问题是否真实存在，再决定是否扩展模型。
- Rationale: 当前阶段以稳健入库为主，避免过早扩展领域模型。
- Impact: 影响 TIER1 静态字段自动化覆盖率和后续模型演进。
- Final decision: 采纳 Recommended option。
- Follow-up:

## 2026-06-21 - 手续费字段语义与 scope 口径

- Status: decided
- Topic: Tushare `trading_fee`、`trading_fee_rate`、`offset_today_fee` 与本地 `commission_type/open/close_yesterday/close_today/scope` 的映射。
- Context: Tushare 结算参数未明确区分开仓和平昨，`offset_today_fee` 描述中有“手续费率”字样，但数值可能随品种表现为金额或比例。部分手续费规则可能只适用于特定合约。
- Decision needed: 如何确认字段语义，以及何时使用 `scope`。
- Options:
  - A: 以 Tushare 数值模式初判，交易所公告抽样确认；同产品同日合约不一致时用 `scope` 列举合约。
  - B: 不从 Tushare 自动生成手续费历史，只用交易所公告人工维护。
  - C: 先只入库当前手续费规则，历史版本暂缓。
- Recommended option: A，但所有非全产品规则必须保留公告证据。
- Rationale: Tushare 日频结算参数是历史覆盖最好的结构化来源，但手续费是高风险字段，需要公告校验。
- Impact: 影响 `transaction_cost_history` 的自动化程度和历史回测成本准确性。
- Final decision: 采用 Option A。先用数值模式初步判断，但记录分析和判断过程。后续人工查询交易所公告检验。
- Follow-up:

## 2026-06-21 - 产品级规则压缩失败时的处理方式

- Status: decided
- Topic: 从合约-交易日级字段反推产品级规则时，同一产品同一日不同合约的值不一致。
- Context: 下单量限制、乘数、价位、手续费等都可能出现合约级差异。当前 TIER1 模型只有部分字段支持例外表达，例如手续费 `scope`、价位 `remain_old_rule_instruments`。
- Decision needed: 对无法压缩为产品级规则的字段，是扩展模型、跳过自动入库，还是写入显式例外。
- Options:
  - A: 只有模型能表达的例外才入库；其他差异进入人工决策。
  - B: 为所有历史规则增加通用 `scope` 字段。
  - C: 强行取主流值，差异作为数据质量备注。
- Recommended option: A。
- Rationale: 强行压缩会制造错误规则；全面扩展模型应等真实差异画像完成后再做。
- Impact: 影响 `order_volume_limits_history`、`multiplier_history` 等字段的覆盖范围。
- Final decision: Option A。目前的模型设计无法覆盖的情况，请在后续数据分析后，写入本文档等待拍板。
- Follow-up:

## 2026-06-21 - session override 字典 schema

- Status: superseded
- Topic: `session_override_rules` 三类列表中的字典字段规范。
- Context: 当前模型允许 `list[dict[str, Any]]`，但实际需要表达适用范围、有效期、触发条件、动作、证据、计划/实际视图等信息。
- Decision needed: 是否在正式入库前定义统一 schema。
- Options:
  - A: 先定义轻量 schema：`id`、`scope`、`effective_start/end`、`trigger`、`action`、`source`、`evidence_level`、`notes`。
  - B: 保持自由字典，等写入多个品种后再归纳。
  - C: 设计完整事件规则 DSL。
- Recommended option: A。
- Rationale: session override 是高复杂度字段；轻量 schema 能保证可审计和可复算，又不会过度工程化。
- Impact: 影响 TIER1 YAML 可维护性，以及 TIER3 `TradingSessions.applied_override_ids` 的生成。
- Final decision: 这个问题有误。 session_override 字段如何在 YAML 中存储，我们已确定了 schema 设计，详见"docs/代码修改笔记/期货交易规则/交易时段规则特殊安排/session_override_rules 数据存储设计_CodeX.md"。
- Follow-up:

## Decided

暂无。
