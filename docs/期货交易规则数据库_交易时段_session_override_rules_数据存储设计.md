# 期货交易规则数据库：交易时段 `session_override_rules` 数据存储设计

> 设计依据：`中国期货市场交易时段规则特殊安排_CodeX.md`
> 适用范围：中国内地期货品种级 YAML 规则库
> 设计目标：在不引入关系型数据库和复杂规则引擎的前提下，完整、可审计地保存交易时段特殊规则。

## 一、核心结论

本设计采用以下原则：

1. 基础交易时段继续保存在品种 YAML 的 `sessions_history` 中，并按 `effective_date` 管理历史版本。
2. 特殊安排保存在同一品种 YAML 的 `session_override_rules` 中。
3. `session_override_rules` 内部分成三类数据，不能混用同一种记录：
   - `recurring_rules`：可以根据合约生命周期重复计算的规则。
   - `dated_overrides`：由交易所规则或公告确认、作用于明确交易日或交易日区间的计划时段变更。
   - `realized_session_events`：熔断、技术暂停等发生后才能确定的实际市场状态。
4. 整日休市、假期延长等改变“某日是否为交易日”的事项不存入本结构，应修改交易所交易日历。
5. 所有日期选择均使用 `trading_date`，不使用公告中的物理日晚间日期作为查询键。
6. 截至研究资料截止日，没有官方证据支持“商品期货进入交割月后统一取消夜盘”，因此不得加入通用的 `delivery_month -> remove_night_session` 规则。
7. 下游组件不得直接解释 YAML。`ProductMetaManager` 负责读库，`TradingRuleService` 负责组合基础时段、合约生命周期和 override。

真实交易时段的构造关系为：

```text
scheduled_sessions
    = base_sessions
    + matched_recurring_rules
    + matched_dated_overrides

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

### 2.2 应存入 `recurring_rules`

仅保存能够由现有结构化字段稳定判断的重复规则，例如：

- 某合约在 `last_trade_date` 删除下午盘；
- 某合约在 `last_trade_date` 将下午盘结束时间提前至指定时间。

第一版不提供任意条件表达式，不使用 `eval`，也不设计通用 DSL。新增触发条件时，应扩展受控的 `trigger.type` 枚举和对应代码。

### 2.3 应存入 `dated_overrides`

以下规则应按明确的目标 `trading_date` 保存：

- 法定节假日后首个交易日无夜盘；
- 新品种上市首日无夜盘；
- 疫情期间暂停夜盘；
- 结算系统故障导致夜盘延迟开市；
- 其他公告确定的临时开收市调整。

节假日规则采用明确日期而不是自动推导，原因是当前 `TradingCalendar` 只保存交易日列表，没有法定节假日分类。直接录入年度交易所通知中的目标交易日，既简单，也能避免周末、调休和假期延长造成误判。

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
| `opening_auction` | 可选映射 | 在已经核实集合竞价规则的品种中，根据最终是否存在夜盘选择集合竞价时段。 |

`opening_auction` 的建议结构如下：

```yaml
opening_auction:
  when_night_session_exists:
    - ["20:55", "21:00"]
  when_no_night_session:
    - ["08:55", "09:00"]
```

配套代码应在全部 scheduled override 应用完毕后检查最终 `night_sessions`：

- 非空时选择 `when_night_session_exists`；
- 为空时选择 `when_no_night_session`；
- YAML 未提供 `opening_auction` 时返回空列表，不猜测集合竞价时段。

上述集合竞价时间仅应写入已有官方依据的品种。不能把郑商所规则直接复制到其他交易所。

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
  recurring_rules: []
  dated_overrides: []
  realized_session_events: []
```

| 字段 | 类型 | 配套代码用途 |
|---|---|---|
| `schema_version` | 整数 | 加载时选择解析器版本；遇到高于代码支持范围的版本应报错。 |
| `recurring_rules` | 列表 | 保存根据合约生命周期重复匹配的规则。 |
| `dated_overrides` | 列表 | 保存明确交易日或交易日区间的公告安排。 |
| `realized_session_events` | 列表 | 保存盘中实际暂停、集合竞价或提前结束等事后事实。 |

空列表必须保留，使 YAML 结构稳定，也便于校验和后续批量维护。

## 五、`recurring_rules` 设计

### 5.1 结构

```yaml
recurring_rules:
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
      - title: "10年期国债期货合约表"
        notice_no: null
        published_date: null
        url: "http://www.cffex.com.cn/cn/10t.html"
        evidence_level: "A"
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

新品种首次上市日通常是一次性、公告型事实，建议放入 `dated_overrides`，不要为了一个日期增加复杂的产品生命周期表。

## 六、`dated_overrides` 设计

### 6.1 结构

```yaml
dated_overrides:
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
      - title: "疫情期间商品期货夜盘暂停及恢复资料"
        notice_no: null
        published_date: null
        url: "https://finance.sina.com.cn/money/future/roll/2020-04-27/doc-iircuyvi0000794.shtml"
        evidence_level: "B"
        note: "原始公告中的物理日晚间已转换为目标 trading_date"
```

### 6.2 字段及代码用途

| 字段 | 类型 | 配套代码用途 |
|---|---|---|
| `event_id` | 字符串 | 品种文件内唯一事件标识；用于去重、追踪和结果审计。 |
| `name` | 字符串 | 人类可读名称；用于日志和数据维护。 |
| `event_type` | 枚举字符串 | 用于分类、检索和报表，不参与核心 session 计算。 |
| `date_selector` | 映射 | 按明确 `trading_date` 列表或闭区间匹配。 |
| `scope` | 映射 | 与 `recurring_rules.scope` 相同。 |
| `actions` | 列表 | 修改该日期的 planned/scheduled session。 |
| `sources` | 列表 | 保存公告、发布日期、链接和证据等级。 |

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

离散日期适合年度节假日通知：

```yaml
date_selector:
  type: "dates"
  dates:
    - "2026-01-05"
    - "2026-02-24"
```

连续区间适合暂停夜盘等事件：

```yaml
date_selector:
  type: "range"
  start: "2020-02-04"
  end: "2020-05-06"
```

代码用途：

- `dates` 直接进行集合成员判断；
- `range` 使用闭区间判断；
- 所有值都必须是交易日语义；
- 若日期不在对应交易所交易日历中，加载或离线校验程序应报错。

不设置 `calendar_date` 和 `trading_date` 两套可选字段，避免调用方混用时间口径。物理日晚间信息可记录在 `sources.note` 中。

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
      - title: "股指期货熔断实际触发资料"
        notice_no: null
        published_date: "2016-01-04"
        url: null
        evidence_level: "A"
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
| `session_group` | 帮助把时间映射到正确的物理日期，尤其用于夜盘。 |
| `start`、`end` | 交易日语义下的异常区间边界，采用左闭右开。 |
| `market_state` | `halted` 表示不可交易；`call_auction` 表示集合竞价而非连续竞价。 |

`TradingRuleService` 可以据此提供两种结果：

- `view="scheduled"`：忽略 `realized_session_events`；
- `view="realized"`：返回异常区间及扣除不可连续交易区间后的实际时段。

不得把 realized 结果覆盖回 `sessions_history`，否则会失去计划时段基准，影响行情缺口检测和事件研究。

## 九、证据来源 `sources`

每条规则或事件都应保存至少一个来源：

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
| `note` | 记录物理日到交易日的转换、历史网页缺失或其他核验信息。 |

证据等级 C 的内容可以保留在研究文档或待核验清单中，不应作为可执行 override 入库。

## 十、规则匹配与应用顺序

`TradingRuleService.get_trading_sessions(instrument, trading_date, view)` 建议按以下顺序执行：

1. 通过 `InstrumentRegistryManager` 查询合约的 `product`、`list_date`、`last_trade_date` 等属性。
2. 校验合约所属品种、交易所与品种 YAML 一致。
3. 调用 `ProductMetaManager.get_base_sessions(product, trading_date)` 取得基础时段历史版本。
4. 从 `recurring_rules` 中筛选有效期、scope 和 trigger 均匹配的规则并应用。
5. 从 `dated_overrides` 中筛选目标交易日和 scope 匹配的事件并应用。
6. 根据最终是否存在夜盘选择 `opening_auction`。
7. 当 `view="realized"` 时，再叠加 `realized_session_events`。
8. 返回最终 session、集合竞价信息和所有已应用规则 ID。

建议缓存键为：

```text
(instrument, trading_date, view)
```

不能继续使用 `(product, trading_date)`，因为同一品种同一天的到期合约与远月合约可能具有不同 session。

### 10.1 冲突处理

不增加人工 `priority` 字段，采用固定规则降低维护复杂度：

1. `dated_overrides` 比 `recurring_rules` 更具体，在同一 session 属性上以后者结果为基础、前者结果为最终值。
2. 同一层中，重复且取值相同的动作视为幂等。
3. 同一层中，同一属性出现不同目标值时应报错，不静默选择。
4. 同一层中若某分组既被删除又被修改边界，删除优先，边界修改忽略并记录诊断信息。
5. `replace_session_group` 与同层其他边界修改同时作用于同一分组时应报错。

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
    opening_auction_sessions: list[list[str]]
    view: Literal["scheduled", "realized"]
    applied_override_ids: tuple[str, ...]
    realized_exception_intervals: tuple[RealizedSessionInterval, ...]
```

各字段用途：

- `trading_date`、`instrument`：明确结果查询键；
- 三个 session 分组：供日历映射、标准时间轴和策略模块直接消费；
- `opening_auction_sessions`：避免删除夜盘后仍保留夜盘集合竞价；
- `view`：防止调用方混淆计划时段和实际时段；
- `applied_override_ids`：便于回测复现、日志和单元测试；
- `realized_exception_intervals`：保留暂停或集合竞价状态，不丢失事件细节。

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

  recurring_rules: []

  dated_overrides:
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
        - title: "2019-12-25夜盘延迟开市资料"
          notice_no: null
          published_date: "2019-12-25"
          url: "https://www.guancha.cn/politics/2019_12_25_529522.shtml"
          evidence_level: "B"
          note: "物理日2019-12-25晚转换为交易日2019-12-26"

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
        - title: "疫情期间商品期货夜盘暂停及恢复资料"
          notice_no: null
          published_date: "2020-04-27"
          url: "https://finance.sina.com.cn/money/future/roll/2020-04-27/doc-iircuyvi0000794.shtml"
          evidence_level: "B"
          note: "正式建库时应补充上期所原始公告"

  realized_session_events: []
```

RB 不应加入“进入交割月后取消夜盘”的 recurring rule。若未来找到针对 RB 特定合约或时期的正式公告，应按实际证据加入 `dated_overrides` 或受限 `scope`，不能扩展成全市场假设。

## 十三、国债与股指期货示例

### 13.1 国债期货最后交易日删除下午盘

```yaml
session_override_rules:
  schema_version: 1
  recurring_rules:
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
        - title: "10年期国债期货合约表"
          notice_no: null
          published_date: null
          url: "http://www.cffex.com.cn/cn/10t.html"
          evidence_level: "A"
          note: null
  dated_overrides: []
  realized_session_events: []
```

此规则只作用于 `trading_date == 当前合约.last_trade_date` 的合约。当天仍在交易的其他 T 合约保留下午盘。

### 13.2 股指期货历史提前收盘

```yaml
recurring_rules:
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
      - title: "股指期货历史交易时间资料"
        notice_no: null
        published_date: null
        url: "http://www.cffex.com.cn/cn/mtxw/20100416/8248.html"
        evidence_level: "B"
        note: "2016-01-01后基础时段已统一收至15:00，本 override 终止"
```

该例说明 override 自身必须有终止日期。不能用当前合约表反推全部历史。

## 十四、加载校验

`ProductMetaManager` 加载 YAML 时至少应校验：

1. `schema_version` 是否受支持。
2. `rule_id` 和 `event_id` 在品种文件内是否唯一。
3. 日期和时间是否符合 ISO 格式。
4. `effective_from <= effective_to`，`range.start <= range.end`。
5. `scope.mode` 与 `instruments` 是否匹配。
6. 指定合约是否确实属于当前品种。
7. `session_group` 和 `operation` 是否属于受控枚举。
8. 修改边界后区间是否仍为正长度。
9. 除夜盘外，是否存在不允许的跨零点区间。
10. `dated_overrides` 的日期是否存在于交易所交易日历。
11. 每条可执行规则是否至少具有一个 A 或 B 级来源。
12. 是否出现同层冲突动作。
13. `realized_session_events` 是否落在对应 scheduled session 内。

YAML 加载失败时应明确指出品种、规则 ID、字段路径和错误原因，不能静默忽略错误记录。

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

`TradingRuleService` 使用其中的 `product`、`list_date` 和 `last_trade_date` 匹配 recurring trigger。

### `TradingRuleService`

建议提供：

```text
get_trading_sessions(instrument, trading_date, view="scheduled")
```

职责：

- 组合 Tier 1 基础时段、Tier 1 override 和 Tier 2 合约生命周期；
- 执行动作并检查冲突；
- 生成 scheduled 或 realized 结果；
- 对下游隐藏 YAML 结构和规则解释细节。

### `TradingCalendar`

应改为依赖 `TradingRuleService` 获取合约级时段，并将相关接口的查询键升级为合约维度。尤其是：

```text
get_session_id
get_time_offset
get_calendar_timestamp_from_date_session_offset
get_standard_timestamps
get_calendar_timestamp_from_trading_date_and_time
```

这些方法不能再只依赖 `product + trading_date`。

## 十六、第一阶段实施范围

为了控制个人项目复杂度，第一阶段建议只实现：

1. `sessions_history` 显式拆分 `morning_sessions`、`afternoon_sessions`、`night_sessions`。
2. `recurring_rules` 支持 `instrument_last_trade_date`。
3. `dated_overrides` 支持离散日期和日期区间。
4. 四种 session action。
5. `TradingRuleService` 的 scheduled 视图。
6. 规则 ID、来源和基础校验。

第二阶段再实现：

1. `opening_auction` 联动。
2. `realized_session_events` 和 realized 视图。
3. 更完整的官方公告数据录入。
4. 面向全库的 YAML schema 校验工具。

这种分期不会破坏数据结构，也不需要引入新的数据库服务。即使第二阶段尚未完成，第一阶段已经能够正确覆盖国债最后交易日、历史股指提前收盘、节假日无夜盘、疫情停夜盘和临时延迟开市等主要 scheduled override。
