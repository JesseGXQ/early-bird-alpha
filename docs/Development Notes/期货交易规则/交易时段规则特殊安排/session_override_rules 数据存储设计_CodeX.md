# 期货交易规则数据库：交易时段 `session_override_rules` 数据存储设计

**IMPORTANT**: 已决定将所有的 rules 都调整为市场级，不再保留品种级规则 YAML。见`session_override_rules 数据存储设计_修改建议2.md`。

---

> 设计依据：`中国期货市场交易时段规则特殊安排_CodeX.md`
> 适用范围：中国内地期货品种级 YAML 规则库
> 设计目标：在不引入关系型数据库和复杂规则引擎的前提下，完整、可审计地保存交易时段特殊规则。

## 一、核心结论

本设计采用以下原则：

1. 基础交易时段继续保存在品种 YAML 的 `sessions_history` 中，并按 `effective_date` 管理历史版本。
2. 特殊安排保存在同一品种 YAML 的 `session_override_rules` 中。
3. `session_override_rules` 内部分成三类数据，不能混用同一种记录：
   - `recurring_instrument_lifecycle_rules`：可以根据合约生命周期重复计算的规则。
   - `scheduled_dated_overrides`：由交易所规则或公告确认、作用于明确交易日或交易日区间的计划时段变更。
   - `realized_session_events`：熔断、技术暂停等发生后才能确定的实际市场状态。
4. 整日休市、假期延长等改变“某日是否为交易日”的事项不存入本结构，应修改交易所交易日历。
5. 品种 YAML 中的日期选择使用 `trading_date`；交易所节假日 CSV 例外，按公告原文保存 `no_night_calendar_date`，并由离线脚本生成目标 `inferred_no_night_trading_date`。
6. 截至研究资料截止日，没有官方证据支持“商品期货进入交割月后统一取消夜盘”，因此不得加入通用的 `delivery_month -> remove_night_session` 规则。
7. 下游组件不得直接解释 YAML。`ProductMetaManager` 负责读库，`TradingRuleService` 负责组合基础时段、合约生命周期和 override。

真实交易时段的构造关系为：

```text
scheduled_sessions
    = base_sessions
    + matched_recurring_instrument_lifecycle_rules
    + matched_scheduled_dated_overrides

realized_sessions
    = scheduled_sessions
    - realized_session_events 中的暂停或非连续竞价区间
```

## 二、数据边界

### 2.1 应存入 `sessions_history`

以下属于基础时段的新历史版本，不属于 override：

- 某品种永久启用夜盘；
- 某品种永久修改日盘或夜盘开收市时间；
- 某品种自始至终没有夜盘；
- 集合竞价制度发生永久变更。

### 2.2 应存入 `recurring_instrument_lifecycle_rules`

仅保存能够由现有结构化字段稳定判断的重复规则，例如：

- 某合约在 `last_trade_date` 删除下午盘；
- 某合约在 `last_trade_date` 将下午盘结束时间提前至指定时间。

第一版不提供任意条件表达式，不使用 `eval`，也不设计通用 DSL。新增触发条件时，应扩展受控的 `trigger.type` 枚举和对应代码。

### 2.3 应存入 `scheduled_dated_overrides`

以下规则应按明确的目标 `trading_date` 保存：

- 法定节假日后首个交易日无夜盘；
- 新品种上市首日无夜盘；
- 疫情期间暂停夜盘；
- 结算系统故障导致夜盘延迟开市；
- 其他公告确定的临时开收市调整。

节假日无夜盘安排不重复写入每个品种 YAML，而是统一保存在交易所级 CSV 中。品种 YAML 只保存 `event_type: holiday` 对应的动作定义；日期匹配由 `ExchangeHolidayManager` 提供。

### 2.4 应存入 `realized_session_events`

以下事实只有发生后才能确定，不得提前生成计划 session：

- 熔断导致的盘中暂停；
- 技术故障导致的盘中暂停；
- 盘中提前结束交易；
- 暂停后进入集合竞价再恢复连续交易。

行情中没有成交或没有收到数据，不足以自动生成此类记录。记录必须有规则、公告或其他可靠来源支持。

### 2.5 不应存入本结构

- 整个交易日临时休市或顺延，应修改交易日历；
- 客户进入交割月后不得持仓或开仓，属于客户及持仓规则；
- 涨跌停、无成交和行情缺口，不等于 session 被关闭；
- 期货公司自行实施的交易限制，不等于交易所 session 变化。

## 三、基础时段结构调整

当前 `Session.day_sessions` 依靠列表位置推断早盘和下午盘。国债期货最后交易日删除下午盘后，列表长度和位置假设会失效。因此建议将 `sessions_history` 调整为显式分组：

```yaml
sessions_history:
  - effective_date: "2015-07-09"
    morning_sessions:
      - ["09:00", "10:15"]
      - ["10:30", "11:30"]
    afternoon_sessions:
      - ["13:30", "15:00"]
    night_sessions:
      - ["21:00", "23:00"]
```

时间区间统一采用左闭右开语义：

```text
[start, end)
```

### 3.1 基础时段字段

| 字段 | 类型 | 配套代码用途 |
|---|---|---|
| `effective_date` | ISO 日期字符串 | `ProductMetaManager.get_base_sessions()` 选择不晚于查询交易日的最新版本。 |
| `morning_sessions` | 时间区间列表 | `TradingCalendar` 判断早盘、生成标准时间戳和计算早盘 offset。 |
| `afternoon_sessions` | 时间区间列表 | 可以被最后交易日规则整体删除，不再依赖 `day_sessions[-1]`。 |
| `night_sessions` | 时间区间列表 | 处理夜盘、跨零点映射和无夜盘 override。 |
第一版不设计 `opening_auction` 字段，也不实现集合竞价联动代码。删除夜盘可能改变集合竞价时段，这一事实仍需保留为后续完善事项；未来补充时，应逐交易所核实规则，不能把某一交易所的集合竞价安排直接复制到其他交易所。

### 3.2 兼容迁移

迁移期间，`ProductMetaManager` 可以暂时兼容旧字段：

```yaml
day_sessions:
  - ["09:00", "10:15"]
  - ["10:30", "11:30"]
  - ["13:30", "15:00"]
```

但旧结构只能作为过渡输入。新 YAML 应显式写入 `morning_sessions` 和 `afternoon_sessions`，否则代码仍需依赖“最后一个元素是下午盘”的不可靠假设。

## 四、顶层 YAML 结构

每个品种文件增加：

```yaml
session_override_rules:
  schema_version: 1
  recurring_instrument_lifecycle_rules: []
  scheduled_dated_overrides: []
  realized_session_events: []
```

| 字段 | 类型 | 配套代码用途 |
|---|---|---|
| `schema_version` | 整数 | 加载时选择解析器版本；遇到高于代码支持范围的版本应报错。 |
| `recurring_instrument_lifecycle_rules` | 列表 | 保存根据合约生命周期重复匹配的规则。 |
| `scheduled_dated_overrides` | 列表 | 保存明确交易日或交易日区间的已规划安排。 |
| `realized_session_events` | 列表 | 保存盘中实际暂停、集合竞价或提前结束等事后事实。 |

空列表必须保留，使 YAML 结构稳定，也便于校验和后续批量维护。

## 五、`recurring_instrument_lifecycle_rules` 设计

### 5.1 结构

```yaml
recurring_instrument_lifecycle_rules:
  - rule_id: "T_LAST_TRADE_DATE_REMOVE_AFTERNOON"
    name: "国债期货最后交易日仅上午交易"
    effective_from: "2015-03-20"
    effective_to: null
    scope:
      mode: "all_instruments"
    trigger:
      type: "instrument_last_trade_date"
    actions:
      - operation: "remove_session_group"
        session_group: "afternoon"
    sources:
      - url: "http://www.cffex.com.cn/cn/10t.html"
        note: "最后交易日仅进行上午交易"
```

### 5.2 字段及代码用途

| 字段 | 类型 | 配套代码用途 |
|---|---|---|
| `rule_id` | 字符串 | 品种文件内唯一标识；用于去重、日志、测试和返回结果中的 `applied_override_ids`。 |
| `name` | 字符串 | 人类可读名称；用于错误信息和审计输出，不参与规则判断。 |
| `effective_from` | ISO 日期字符串 | 仅当 `trading_date >= effective_from` 时规则可匹配。 |
| `effective_to` | ISO 日期字符串或 `null` | 闭区间终点；`null` 表示持续有效。历史规则必须填写真实终止日。 |
| `scope` | 映射 | 限定规则作用于当前品种的全部合约或指定合约。 |
| `trigger` | 映射 | 指定由 `TradingRuleService` 使用哪一种受控判断逻辑。 |
| `actions` | 列表 | 对基础 session 执行删除、截短、延迟或替换。 |
| `sources` | 列表 | 保存证据来源；加载校验和审计使用，不直接改变 session。 |

### 5.3 `scope`

支持两种模式：

```yaml
scope:
  mode: "all_instruments"
```

```yaml
scope:
  mode: "include_instruments"
  instruments: ["IF1506", "IF1507"]
```

| 字段 | 配套代码用途 |
|---|---|
| `mode` | `all_instruments` 对当前品种所有合约生效；`include_instruments` 只匹配列出的合约。 |
| `instruments` | `include_instruments` 时必填；加载时校验这些合约均属于当前品种。 |

由于规则已经位于品种 YAML 中，不重复保存 `product` 和 `exchange`，避免同一事实出现冲突副本。

### 5.4 `trigger`

第一版仅建议支持：

| `trigger.type` | 配套代码判断 |
|---|---|
| `instrument_last_trade_date` | `trading_date == registry.last_trade_date` |
| `instrument_list_date` | `trading_date == registry.list_date`，仅在未来取得明确规则依据时使用。 |

新品种首次上市日通常是一次性、公告型事实，建议放入 `scheduled_dated_overrides`，不要为了一个日期增加复杂的产品生命周期表。

## 六、`scheduled_dated_overrides` 设计

### 6.1 结构

```yaml
scheduled_dated_overrides:
  - event_id: "RB_2020_COVID_NIGHT_SUSPENSION"
    name: "疫情期间暂停夜盘"
    event_type: "temporary_policy"
    date_selector:
      type: "range"
      start: "2020-02-04"
      end: "2020-05-06"
    scope:
      mode: "all_instruments"
    actions:
      - operation: "remove_session_group"
        session_group: "night"
    sources:
      - url: "https://finance.sina.com.cn/money/future/roll/2020-04-27/doc-iircuyvi0000794.shtml"
        note: "原始公告中的日历日晚间已转换为目标 trading_date"
```

### 6.2 字段及代码用途

| 字段 | 类型 | 配套代码用途 |
|---|---|---|
| `event_id` | 字符串 | 品种文件内唯一事件标识；用于去重、追踪和结果审计。 |
| `name` | 字符串 | 人类可读名称；用于日志和数据维护。 |
| `event_type` | 枚举字符串 | 用于分类、检索和匹配分派；`holiday` 会触发交易所节假日查询。 |
| `date_selector` | 映射 | 非节假日事件按明确 `trading_date` 列表或闭区间匹配；节假日事件查询 `ExchangeHolidayManager`。 |
| `scope` | 映射 | 与 `recurring_instrument_lifecycle_rules.scope` 相同。 |
| `actions` | 列表 | 修改该日期的 planned/scheduled session。 |
| `sources` | 列表 | 第一版只保存 `url` 和 `note`。 |

建议的 `event_type` 枚举：

```text
holiday
new_product_listing
temporary_policy
system_failure
emergency_adjustment
other
```

### 6.3 `date_selector`

非节假日的一次性事件可以使用离散交易日：

```yaml
date_selector:
  type: "dates"
  dates:
    - "2019-12-26"
```

连续区间适合暂停夜盘等事件：

```yaml
date_selector:
  type: "range"
  start: "2020-02-04"
  end: "2020-05-06"
```

对于 `event_type: holiday`，品种 YAML 不保存节假日日期列表，而只声明日期来源：

```yaml
- event_id: "RB_HOLIDAY_NO_NIGHT_SESSION"
  name: "交易所节假日无夜盘"
  event_type: "holiday"
  date_selector:
    type: "exchange_holiday_arrangements"
  scope:
    mode: "all_instruments"
  actions:
    - operation: "remove_session_group"
      session_group: "night"
  sources:
    - url: null
      note: "具体日期及公告来源由交易所级节假日 CSV 维护"
```

代码用途：

- `dates` 直接进行集合成员判断；
- `range` 使用闭区间判断；
- `exchange_holiday_arrangements` 根据品种所属交易所查询 `ExchangeHolidayManager`；
- `dates` 和 `range` 中的日期使用 `trading_date` 语义；
- 交易所节假日 CSV 同时保存公告原文的日历日，以及离线推断出的目标交易日。

### 6.4 交易所节假日安排

同一交易所的节假日安排不应重复写入各品种 YAML。单独建立：

```text
db/reference/exchange_holidays/
├── SHFE.csv
├── INE.csv
├── DCE.csv
└── CZCE.csv
```

每个 CSV 保存三列：

```csv
no_night_calendar_date,holiday_str,inferred_no_night_trading_date
2026-02-13,春节,2026-02-24
```

| 字段 | 含义及代码用途 |
|---|---|
| `no_night_calendar_date` | 交易所公告明确“不进行夜盘交易”的日历日，与公告口径保持一致。 |
| `holiday_str` | 节假日中文名称，仅用于人工识别、检查和日志。 |
| `inferred_no_night_trading_date` | 由离线脚本根据交易所交易日历推断的目标交易日；生产运行时由 `TradingRuleService` 直接用于日期匹配。 |

新增离线脚本：

```text
infer_trading_date_from_calendar_date.py
```

脚本与生产运行环境分离，处理流程为：

1. 读取交易所 CSV 中的 `no_night_calendar_date`。
2. 创建不持有 `TradingRuleService` 的 `TradingCalendar`。
3. 调用 `TradingCalendar.next_trading_date(no_night_calendar_date)`。
4. 将结果写入 `inferred_no_night_trading_date`。

新增轻量级 `ExchangeHolidayManager`，负责按交易所加载和缓存三列 CSV。实例化时必须校验 `inferred_no_night_trading_date` 列已经存在，不在生产运行时临时推导或回写数据。

`TradingRuleService` 在匹配 `event_type: holiday` 时：

1. 根据品种取得交易所。
2. 使用 `ExchangeHolidayManager` 查询该交易所的 `inferred_no_night_trading_date`。
3. 若发现查询 `trading_date` 是因节假日导致的无夜盘交易日，则收集品种 YAML 中该 holiday override 的 actions。

这里的 CSV 只保存交易所节假日无夜盘安排，不保存 morning、afternoon、night 或任何其他 session override。具体如何修改 session，仍由品种 YAML 的 `actions` 定义。

为避免对象装配时的循环依赖，`TradingCalendar` 可选持有 `TradingRuleService`：

```python
TradingCalendar(
    product=product,
    calendar_dir=calendar_dir,
    trading_rule_service=None,
)
```

- `is_trading_date()`、`next_trading_date()` 等基础交易日期方法不依赖 `TradingRuleService`，在未注入规则服务时仍可使用。
- 需要查询真实交易时段的方法才依赖 `TradingRuleService`；未注入时调用这些方法，应明确抛出运行时异常。
- 离线脚本使用不持有 `TradingRuleService` 的 `TradingCalendar`，只调用 `next_trading_date()`。
- 生产环境中的 `TradingCalendar` 持有 `TradingRuleService`，供交易时段相关方法使用。
- `ExchangeHolidayManager` 不持有 `TradingCalendar`，也不负责日期推断，只读取已生成的派生列。

因此，生产环境的运行时依赖为：

```text
TradingCalendar
    -> TradingRuleService
        -> ExchangeHolidayManager
```

离线数据生成链为：

```text
TradingCalendar（无 TradingRuleService）
    -> infer_trading_date_from_calendar_date.py
        -> exchange_holidays CSV
```

两条链在运行时相互分离，不构成循环持有。

## 七、`actions` 设计

第一版采用有限的动作枚举，不接受任意 Python 表达式。

### 7.1 删除整个 session 分组

```yaml
- operation: "remove_session_group"
  session_group: "night"
```

适用于无夜盘、删除下午盘等情况。`session_group` 取值：

```text
morning
afternoon
night
```

代码将对应区间列表置为空。重复删除是幂等操作。

### 7.2 修改第一个区间的开始时间

```yaml
- operation: "set_first_interval_start"
  session_group: "night"
  time: "22:30"
```

适用于临时延迟开市。代码只修改该分组第一个 interval 的起点，保留原有收市时间和后续 interval。若该分组原本为空，应报数据错误。

### 7.3 修改最后一个区间的结束时间

```yaml
- operation: "set_last_interval_end"
  session_group: "afternoon"
  time: "15:00"
```

适用于历史上的最后交易日提前收盘。代码只修改该分组最后一个 interval 的终点，保留原有开市时间和前序 interval。

### 7.4 替换整个 session 分组

```yaml
- operation: "replace_session_group"
  session_group: "night"
  intervals:
    - ["22:30", "23:00"]
```

仅在无法通过修改首尾边界准确表达时使用。该动作应保存完整的新区间列表。

### 7.5 动作字段

| 字段 | 配套代码用途 |
|---|---|
| `operation` | 分派到受控的动作处理函数。未知值直接报错。 |
| `session_group` | 指定修改 `morning_sessions`、`afternoon_sessions` 或 `night_sessions`。 |
| `time` | `set_first_interval_start` 和 `set_last_interval_end` 的新边界。 |
| `intervals` | `replace_session_group` 的完整替换值。 |

## 八、`realized_session_events` 设计

盘中事件不修改 scheduled session，而是在请求 realized 视图时叠加。

```yaml
realized_session_events:
  - event_id: "IF_20160104_CIRCUIT_BREAKER"
    name: "股指期货熔断实际触发"
    event_type: "circuit_breaker"
    trading_date: "2016-01-04"
    scope:
      mode: "all_instruments"
    exception_intervals:
      - session_group: "afternoon"
        start: "<待官方资料核实>"
        end: "<待官方资料核实>"
        market_state: "halted"
    sources:
      - url: null
        note: "正式入库前必须补齐准确起止时间和官方链接"
```

上例中的占位符只用于说明结构，不是可加载的生产数据。未核实准确时间前，应保持列表为空，不得猜测。

### 8.1 字段及代码用途

| 字段 | 配套代码用途 |
|---|---|
| `event_id` | 唯一标识实际事件，并写入 realized 查询结果的追踪信息。 |
| `name` | 日志、审计和人工维护。 |
| `event_type` | 建议取 `circuit_breaker`、`technical_halt`、`early_termination` 或 `other`。 |
| `trading_date` | 只在该交易日匹配。 |
| `scope` | 限制受影响合约。 |
| `exception_intervals` | 在 scheduled session 上标记异常市场状态。 |
| `sources` | 证明事件确实发生及其准确时间。 |

`exception_intervals` 字段：

| 字段 | 配套代码用途 |
|---|---|
| `session_group` | 帮助把时间映射到正确的日历日期，尤其用于夜盘。 |
| `start`、`end` | 交易日语义下的异常区间边界，采用左闭右开。 |
| `market_state` | `halted` 表示不可交易；`call_auction` 表示集合竞价而非连续竞价。 |

`TradingRuleService` 可以据此提供两种结果：

- `view="scheduled"`：忽略 `realized_session_events`；
- `view="realized"`：返回异常区间及扣除不可连续交易区间后的实际时段。

不得把 realized 结果覆盖回 `sessions_history`，否则会失去计划时段基准，影响行情缺口检测和事件研究。

## 九、证据来源 `sources`

第一版为控制数据录入和代码复杂度，每条规则或事件的 `sources` 只保存 `url` 和 `note`：

```yaml
sources:
  - url: "https://example.com/official-notice"
    note: "必要的时间归属或适用范围说明"
```

| 字段 | 配套代码用途 |
|---|---|
| `url` | 保存原文链接；历史链接缺失时允许为 `null`。 |
| `note` | 记录日历日到交易日的转换、历史网页缺失或其他核验信息。 |

后续若规则库进入更严格的审计和多人维护阶段，可扩展为完整结构：

```yaml
sources:
  - title: "关于某事项的通知"
    notice_no: "交易所公告编号"
    published_date: "2020-01-01"
    url: "https://example.com/official-notice"
    evidence_level: "A"
    note: "必要的时间归属或适用范围说明"
```

| 字段 | 配套代码用途 |
|---|---|
| `title` | 人工识别来源，校验时必填。 |
| `notice_no` | 公告编号；无法取得时允许为 `null`。 |
| `published_date` | 公告发布日期；用于审计，不作为规则生效日。 |
| `url` | 原文链接；历史链接缺失时允许为 `null`，但应在 `note` 说明。 |
| `evidence_level` | `A`、`B`、`C`。生产可执行规则至少应有一个 A 或 B 来源。 |
| `note` | 记录日历日到交易日的转换、历史网页缺失或其他核验信息。 |

完整结构中的证据等级 C 内容可以保留在研究文档或待核验清单中，不应作为可执行 override 入库。第一版尚未结构化保存 `evidence_level`，因此证据可靠性由录入者通过 `url` 和 `note` 人工保证。

## 十、规则匹配与应用顺序

`TradingRuleService.get_trading_sessions(instrument, trading_date, view)` 建议按以下顺序执行：

1. 通过 `InstrumentRegistryManager` 查询合约的 `product`、`list_date`、`last_trade_date` 等属性。
2. 校验合约所属品种、交易所与品种 YAML 一致。
3. 调用 `ProductMetaManager.get_base_sessions(product, trading_date)` 取得基础时段历史版本。
4. 从 `recurring_instrument_lifecycle_rules` 中筛选有效期、scope 和 trigger 均匹配的规则。
5. 从 `scheduled_dated_overrides` 中筛选日期、scope 均匹配的事件；当 `event_type: holiday` 时，使用 `ExchangeHolidayManager` 返回的 `inferred_no_night_trading_date` 直接完成日期匹配。
6. 收集并归并上述匹配规则的 actions。
7. 当 `view="realized"` 时，匹配 `realized_session_events` 并收集 `exception_intervals`。
8. 分别对 night、morning、afternoon 执行动作并进行有效性检验。
9. 返回最终 session 和所有已应用规则 ID。

建议缓存键为：

```text
(instrument, trading_date, view)
```

不能继续使用 `(product, trading_date)`，因为同一品种同一天的到期合约与远月合约可能具有不同 session。

### 10.1 动作的收集、归并和执行

先收集所有匹配的 `recurring_instrument_lifecycle_rules` 和 `scheduled_dated_overrides` 中的 actions。若请求 `view="realized"`，再收集匹配的 `realized_session_events.exception_intervals`。

night、morning、afternoon 三个 session group 分别执行动作，优先级为：

```text
remove_session_group
    > replace_session_group
    > 边界收缩
```

执行规则：

1. 存在 `remove_session_group` 时，删除该 session group，跳过 replace 和边界收缩。
2. 不存在 remove、但存在 `replace_session_group` 时，执行 replace，跳过边界收缩。
3. 仅存在边界收缩动作时：
   - 所有 `set_first_interval_start` 取最晚的开盘时间；
   - 所有 `set_last_interval_end` 取最早的收盘时间。
4. 当 `view="realized"` 且该 session group 未被 remove 时，根据 `exception_intervals` 将 scheduled intervals 替换为扣除暂停区间后的 realized intervals。

伪代码如下：

```text
function build_sessions(instrument, trading_date, view):
    sessions = deep_copy(get_base_sessions(instrument.product, trading_date))

    matched_lifecycle_rules =
        match_recurring_instrument_lifecycle_rules(instrument, trading_date)

    matched_scheduled_overrides =
        match_scheduled_dated_overrides(instrument, trading_date)

    actions =
        collect_actions(matched_lifecycle_rules)
        + collect_actions(matched_scheduled_overrides)

    if view == "realized":
        matched_realized_events =
            match_realized_session_events(instrument, trading_date)
        exception_intervals =
            collect_exception_intervals(matched_realized_events)
    else:
        matched_realized_events = []
        exception_intervals = []

    for session_group in ["night", "morning", "afternoon"]:
        group_actions = actions_for_group(actions, session_group)

        if exists(group_actions, "remove_session_group"):
            sessions[session_group] = []
            session_removed = true

        else if exists(group_actions, "replace_session_group"):
            replacement = get_unique_replacement(group_actions)
            sessions[session_group] = replacement.intervals
            session_removed = false

        else:
            start_changes =
                all(group_actions, "set_first_interval_start")
            end_changes =
                all(group_actions, "set_last_interval_end")

            if start_changes is not empty:
                require sessions[session_group] exists
                sessions[session_group].first.start =
                    latest_time(start_changes)

            if end_changes is not empty:
                require sessions[session_group] exists
                sessions[session_group].last.end =
                    earliest_time(end_changes)

            session_removed = false

        if view == "realized" and not session_removed:
            group_exceptions =
                exceptions_for_group(exception_intervals, session_group)
            sessions[session_group] =
                apply_exception_intervals(
                    sessions[session_group],
                    group_exceptions
                )

        validate_session_group(sessions[session_group])

    return TradingSessions(
        trading_date=trading_date,
        instrument=instrument,
        morning_sessions=sessions["morning"],
        afternoon_sessions=sessions["afternoon"],
        night_sessions=sessions["night"],
        view=view,
        applied_override_ids=ids(
            matched_lifecycle_rules,
            matched_scheduled_overrides,
            matched_realized_events
        )
    )
```

### 10.2 有效性检验

第一版只保留以下三项：

1. 同一 session group 存在多个内容不同的 `replace_session_group` 时，报错。
2. 动作合并后，任一区间的开始时间不得晚于或等于结束时间。
3. 不得对基础时段中不存在的 session group 执行 replace 或边界收缩；remove 空分组可视为幂等操作。

## 十一、返回对象建议

建议用新的返回对象替代当前依赖 `day_sessions` 位置的 `Session`：

```python
@dataclass(frozen=True)
class TradingSessions:
    trading_date: date
    instrument: str
    morning_sessions: list[list[str]]
    afternoon_sessions: list[list[str]]
    night_sessions: list[list[str]]
    view: Literal["scheduled", "realized"]
    applied_override_ids: tuple[str, ...]
```

各字段用途：

- `trading_date`、`instrument`：明确结果查询键；
- 三个 session 分组：供日历映射、标准时间轴和策略模块直接消费；
- `view`：防止调用方混淆计划时段和实际时段；
- `applied_override_ids`：便于回测复现、日志和单元测试，也可据此追踪造成时段缺口或缩减的规则及事件。

第一版不返回 `realized_exception_intervals`，以保持对象简洁。后续若行情缺口监测、事件审计或市场状态研究需要直接取得原始异常区间，可以再补充该属性。

## 十二、RB 品种 YAML 示例

以下示例展示如何在现有 `RB_mock.yaml` 中加入本设计。示例来源信息仍需在正式建库时用交易所原始公告补齐。

```yaml
sessions_history:
  - effective_date: "2012-01-01"
    morning_sessions:
      - ["09:00", "10:15"]
      - ["10:30", "11:30"]
    afternoon_sessions:
      - ["13:30", "15:00"]
    night_sessions: []

  - effective_date: "2015-07-09"
    morning_sessions:
      - ["09:00", "10:15"]
      - ["10:30", "11:30"]
    afternoon_sessions:
      - ["13:30", "15:00"]
    night_sessions:
      - ["21:00", "23:00"]

session_override_rules:
  schema_version: 1

  recurring_instrument_lifecycle_rules: []

  scheduled_dated_overrides:
    - event_id: "RB_20191226_DELAYED_NIGHT_OPEN"
      name: "结算系统故障导致夜盘延迟开市"
      event_type: "system_failure"
      date_selector:
        type: "dates"
        dates: ["2019-12-26"]
      scope:
        mode: "all_instruments"
      actions:
        - operation: "set_first_interval_start"
          session_group: "night"
          time: "22:30"
      sources:
        - url: "https://www.guancha.cn/politics/2019_12_25_529522.shtml"
          note: "日历日2019-12-25晚转换为交易日2019-12-26"

    - event_id: "RB_2020_COVID_NIGHT_SUSPENSION"
      name: "疫情期间暂停夜盘"
      event_type: "temporary_policy"
      date_selector:
        type: "range"
        start: "2020-02-04"
        end: "2020-05-06"
      scope:
        mode: "all_instruments"
      actions:
        - operation: "remove_session_group"
          session_group: "night"
      sources:
        - url: "https://finance.sina.com.cn/money/future/roll/2020-04-27/doc-iircuyvi0000794.shtml"
          note: "正式建库时应补充上期所原始公告"

  realized_session_events: []
```

RB 不应加入“进入交割月后取消夜盘”的 lifecycle rule。若未来找到针对 RB 特定合约或时期的正式公告，应按实际证据加入 `scheduled_dated_overrides` 或受限 `scope`，不能扩展成全市场假设。

## 十三、国债与股指期货示例

### 13.1 国债期货最后交易日删除下午盘

```yaml
session_override_rules:
  schema_version: 1
  recurring_instrument_lifecycle_rules:
    - rule_id: "T_LAST_TRADE_DATE_REMOVE_AFTERNOON"
      name: "最后交易日仅上午交易"
      effective_from: "2015-03-20"
      effective_to: null
      scope:
        mode: "all_instruments"
      trigger:
        type: "instrument_last_trade_date"
      actions:
        - operation: "remove_session_group"
          session_group: "afternoon"
      sources:
        - url: "http://www.cffex.com.cn/cn/10t.html"
          note: null
  scheduled_dated_overrides: []
  realized_session_events: []
```

此规则只作用于 `trading_date == 当前合约.last_trade_date` 的合约。当天仍在交易的其他 T 合约保留下午盘。

### 13.2 股指期货历史提前收盘

```yaml
recurring_instrument_lifecycle_rules:
  - rule_id: "IF_LAST_TRADE_DATE_EARLY_CLOSE_2010_2015"
    name: "历史上最后交易日提前至15:00收盘"
    effective_from: "2010-04-16"
    effective_to: "2015-12-31"
    scope:
      mode: "all_instruments"
    trigger:
      type: "instrument_last_trade_date"
    actions:
      - operation: "set_last_interval_end"
        session_group: "afternoon"
        time: "15:00"
    sources:
      - url: "http://www.cffex.com.cn/cn/mtxw/20100416/8248.html"
        note: "2016-01-01后基础时段已统一收至15:00，本 override 终止"
```

该例说明 override 自身必须有终止日期。不能用当前合约表反推全部历史。

## 十四、数据校验（第一版）

作为个人项目的初版实现，仅保留以下基础校验：

1. `schema_version` 是否受支持。
2. `rule_id` 和 `event_id` 在品种文件内是否唯一。
3. 日期和时间是否符合 ISO 格式。
4. `effective_from <= effective_to`，`range.start <= range.end`。
5. `scope.mode` 与 `instruments` 是否匹配。
6. 指定合约是否确实属于当前品种。
7. `session_group` 和 `operation` 是否属于受控枚举。

YAML 加载失败时应明确指出品种、规则 ID、字段路径和错误原因，不能静默忽略错误记录。

动作执行后的区间有效性由第 10.2 节负责，不重复放入加载阶段。更严格的跨零点、来源等级、交易日合法性和 realized 区间覆盖检查，可在后续版本中按实际需要补充。

## 十五、配套代码职责

### `ProductMetaManager`

建议提供：

```text
get_base_sessions(product, trading_date)
get_session_override_rules(product)
```

职责：

- 加载、解析、校验和缓存品种 YAML；
- 返回强类型的基础时段和 override 数据；
- 不查询合约注册表；
- 不判断某合约是否处于最后交易日；
- 不直接构造合约真实时段。

### `InstrumentRegistryManager`

继续提供：

```text
get_instrument_registry(instrument)
```

`TradingRuleService` 使用其中的 `product`、`list_date` 和 `last_trade_date` 匹配 lifecycle trigger。

### `ExchangeHolidayManager`

建议提供：

```text
get_holiday_arrangements(exchange)
is_no_night_trading_date(exchange, trading_date)
```

职责：

- 加载并缓存 `db/reference/exchange_holidays/{EXCHANGE}.csv`；
- 校验 CSV 包含 `no_night_calendar_date`、`holiday_str` 和 `inferred_no_night_trading_date` 三列；
- 返回交易所公告口径下的日历日及离线推断的目标交易日；
- 直接使用 `inferred_no_night_trading_date` 判断查询交易日是否为节假日无夜盘交易日；
- 不持有或调用 `TradingCalendar`；
- 不保存或解释任何 session action；
- 不管理节假日以外的 session override。

### `TradingRuleService`

建议提供：

```text
get_trading_sessions(instrument, trading_date, view="scheduled")
```

职责：

- 组合 Tier 1 基础时段、Tier 1 override 和 Tier 2 合约生命周期；
- 调用 `ExchangeHolidayManager` 匹配交易所节假日无夜盘安排；
- 收集、归并和执行动作，并进行基础有效性检验；
- 生成 scheduled 或 realized 结果；
- 对下游隐藏 YAML 结构和规则解释细节。

### `TradingCalendar`

采用可选依赖注入：

```python
TradingCalendar(..., trading_rule_service=None)
```

`is_trading_date()`、`next_trading_date()` 等基础日期方法不需要规则服务。需要获取真实交易时段的方法则要求已经注入 `TradingRuleService`，并将相关接口的查询键升级为合约维度。尤其是：

```text
get_session_id
get_time_offset
get_calendar_timestamp_from_date_session_offset
get_standard_timestamps
get_calendar_timestamp_from_trading_date_and_time
```

这些方法不能再只依赖 `product + trading_date`。

当 `trading_rule_service is None` 时调用上述交易时段方法，应抛出明确异常，而不是退回品种级基础时段或静默生成不完整结果。
