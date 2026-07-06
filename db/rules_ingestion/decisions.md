# Decisions

本文档用于记录规则入库任务中需要人工决策的问题。

它不是执行日志，也不是完整设计文档。只有当某个问题会影响本地领域模型、数据口径、入库规则、数据源优先级或人工校验策略，并且无法仅靠代码或现有文档安全决定时，才记录在这里。每条决策应尽量包含背景、可选方案、影响范围和最终结论，便于后续审计和复盘。


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

## 2026-06-24 - StaticMeta 多值字段的当前静态值口径

- Status: pending
- Topic: `StaticMeta.max_margin_side_algorithm` 在外部合约-交易日级数据中出现历史多值时，是否允许本地静态字段使用最新横截面的众数。
- Context: 2026-06-24 执行 `StaticMeta` 变量生成时，`position_date_type` 和 `combination_type` 在候选产品中可以稳定压缩，但 `max_margin_side_algorithm` 有 29 个产品出现历史 0/1 多值。涉及品种包括 CFFEX 的 `T/TF/TL/TS`，INE 的 `BC/EC/LU/NR/SC`，以及 SHFE 的 `AD/AG/AL/AO/AU/BR/BU/CU/FU/HC/NI/OP/PB/RB/RU/SN/SP/SS/WR/ZN`。
- Decision needed: 当前 `StaticMeta` 是非版本化字段，遇到历史多值时，正式 TIER1 YAML 应采用最新横截面众数、只允许全历史唯一值入库，还是扩展模型。
- Options:
  - A: 正式入库采用最新 `trading_day` 横截面的众数，并保留审计报告说明历史多值。
  - B: 坚持 2026-06-21 已决策口径，仅全历史唯一时自动入库；上述 29 个产品的 `max_margin_side_algorithm` 暂缓或人工维护。
  - C: 将 `max_margin_side_algorithm` 从 `StaticMeta` 扩展为历史版本规则。
- Recommended option: A。
- Rationale: 该字段当前本地模型明确是静态布尔值，下游读取也只能得到单值；若目标是尽快形成可用的当前规则库，最新横截面众数比全历史众数更符合当前交易规则查询语义。但这会放宽此前“只有全历史唯一才自动入库”的保守口径，因此需要人工二次确认。
- Impact: 影响 29 个候选产品的正式 TIER1 YAML 是否可直接合并，以及未来是否需要对该字段做版本化。
- Final decision:
- Follow-up: 相关候选和冲突样本见 `db/rules_ingestion/data/derived/20260624/product_meta_static/static_meta_multi_value_fields.csv`。

## Decided Assumptions


## Decided and superseded decisions


## 2026-06-21 - 合约生命周期日期字段语义优先级

- Status: superceded
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
- Follow-up: TIER2 入库时完全根据 tushare 作为事实来源，因而不存在冲突/优先级问题。Status 已改为 superceded。

## 2026-06-21 - 无交割日期合约的空值策略

- Status: superceded.
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
- Follow-up: 更新后 `InstrumentRegistry` 已删去 `start_deliv_date`, `end_deliv_date` 属性。

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
- Final decision: 采纳 Recommended option。本地 canonical code 的具体规范为：产品代码统一大写，例如 `RB`；合约代码不包含交易所后缀或供应商后缀，例如使用 `IF2406` 而不是 `IF2406.CFE`。交易所归属由独立 `exchange` 字段表达，例如 `CFFEX`、`SHFE`、`INE`。
- Follow-up: 入库 adapter 需将 BigQuant、Tushare、交易所原始结算表等来源代码统一映射为上述 canonical code，并保留 source-to-local 映射逻辑。TIER2 实际入库时，`InstrumentRegistry.instrument` 的解析来源已定为 Tushare `ts_code` 的点号前缀，而不是 `symbol`；`symbol` 用于生成交易所报单口径的 `exchange_inst`。二者与交易所标准代码的主要差异体现在 CZCE：交易所报单代码常用三位交割数字，例如 `CF001`，而本地 `instrument` 需要用 `ts_code` 保留/拓展为四位交割数字，例如 `CF1001`、`CF2001`，以避免跨 decade 合约冲突并满足全市场唯一主键约束。

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
