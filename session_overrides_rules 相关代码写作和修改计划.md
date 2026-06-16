# `session_override_rules` 相关代码写作和修改计划

## 一、目标与实施边界

本次代码工作的目标，是落实《期货交易规则数据库_交易时段_session_override_rules_数据存储设计.md》中的存储和运行时设计，使系统能够按“合约 + 交易日”返回计划交易时段或实际交易时段。

核心调用关系确定为：

```text
TradingCalendar
    -> TradingRuleService
        -> ProductMetaManager
        -> InstrumentRegistryManager
        -> ExchangeHolidayManager
```

第一版只实现已敲定的受控枚举，不增加通用 DSL、任意条件表达式、`eval`、集合竞价联动或从行情缺口自动推断停市事件。整日休市继续由交易日历负责，不写入 `session_override_rules`。

## 二、新增模块

### 2.1 `data/rules/session_models.py`

集中保存基础时段、override 规则和最终查询结果的强类型只读数据对象，避免 `ProductMetaManager`、`TradingRuleService` 和 `TradingCalendar` 之间传递原始 YAML 字典。

| 类/类型 | 确定接口 | 主要功能 |
|---|---|---|
| `SessionView` | `Literal["scheduled", "realized"]` | 约束交易时段查询只支持计划视图和实际视图。 |
| `SessionGroup` | `Literal["morning", "afternoon", "night"]` | 约束所有 action 和异常区间只能作用于三个显式时段分组。 |
| `SessionInterval` | `tuple[str, str]` | 表示一个采用左闭右开语义的交易时间区间。 |
| `BaseSessions` | `BaseSessions(effective_date, morning_sessions, afternoon_sessions, night_sessions)` | 保存某个历史版本的品种基础交易时段，不包含任何 override。 |
| `RuleSource` | `RuleSource(url, note)` | 保存一条规则或事件的证据链接和审计备注。 |
| `RuleScope` | `RuleScope(mode, instruments)` | 保存 `all_instruments` 或 `include_instruments` 的适用范围。 |
| `SessionAction` | `SessionAction(operation, session_group, time=None, intervals=())` | 保存删除、替换或收缩 session group 的受控动作。 |
| `LifecycleTrigger` | `LifecycleTrigger(type)` | 保存 `instrument_last_trade_date` 或 `instrument_list_date` 生命周期触发器。 |
| `RecurringInstrumentLifecycleRule` | `RecurringInstrumentLifecycleRule(rule_id, name, effective_from, effective_to, scope, trigger, actions, sources)` | 保存可按合约生命周期重复计算的时段规则。 |
| `DateSelector` | `DateSelector(type, dates=(), start=None, end=None)` | 保存 `dates`、`range` 或 `exchange_holiday_arrangements` 日期选择器。 |
| `ScheduledDatedOverride` | `ScheduledDatedOverride(event_id, name, event_type, date_selector, scope, actions, sources)` | 保存明确交易日、区间或节假日来源的计划型时段变更。 |
| `SessionExceptionInterval` | `SessionExceptionInterval(session_group, start, end, market_state)` | 保存 realized 事件中的暂停或集合竞价异常区间。 |
| `RealizedSessionEvent` | `RealizedSessionEvent(event_id, name, event_type, trading_date, scope, exception_intervals, sources)` | 保存只在事件发生后录入的实际市场状态。 |
| `SessionOverrideRules` | `SessionOverrideRules(schema_version, recurring_instrument_lifecycle_rules, scheduled_dated_overrides, realized_session_events)` | 聚合一个品种 YAML 中的全部 session override 数据。 |
| `TradingSessions` | `TradingSessions(trading_date, instrument, morning_sessions, afternoon_sessions, night_sessions, view, applied_override_ids)` | 向下游返回合约级最终时段及已应用规则 ID。 |

所有集合字段在对象中使用不可变 `tuple`；`TradingRuleService` 执行动作时先复制为可变工作结构，返回前再转换为不可变结果，避免缓存对象被调用方修改。

### 2.2 `data/rules/exchange_holiday_manager.py`

新增交易所节假日无夜盘安排的只读加载器；该模块不持有 `TradingCalendar`，也不解释任何 session action。

| 类/方法 | 确定接口 | 主要功能 |
|---|---|---|
| `HolidayArrangement` | `HolidayArrangement(no_night_calendar_date, holiday_str, inferred_no_night_trading_date)` | 表示 CSV 中一条公告日历日及其离线推导交易日。 |
| `ExchangeHolidayManager` | `ExchangeHolidayManager(exchange_holiday_dir: str)` | 管理各交易所节假日 CSV 的校验、加载和内存缓存。 |
| `_load_exchange` | `_load_exchange(exchange: str) -> tuple[HolidayArrangement, ...]` | 懒加载 `{EXCHANGE}.csv`，校验文件名、三列结构、日期格式和重复记录。 |
| `get_holiday_arrangements` | `get_holiday_arrangements(exchange: str) -> tuple[HolidayArrangement, ...]` | 返回指定交易所的全部节假日无夜盘安排。 |
| `is_no_night_trading_date` | `is_no_night_trading_date(exchange: str, trading_date: date) -> bool` | 直接按 `inferred_no_night_trading_date` 判断目标交易日是否无夜盘。 |
| `clear_cache` | `clear_cache(exchange: str | None = None) -> None` | 清理指定交易所或全部 CSV 缓存，供测试和离线维护后刷新。 |

### 2.3 `data/rules/trading_rule_service.py`

新增面向下游的只读规则解释服务，统一组合 Tier 1 基础时段、Tier 1 override、Tier 2 合约生命周期和交易所节假日安排。

| 类/方法 | 确定接口 | 主要功能 |
|---|---|---|
| `TradingRuleService` | `TradingRuleService(meta_manager, registry_manager, holiday_manager)` | 持有三个底层 Manager，并以合约维度解释和缓存交易时段规则。 |
| `get_trading_sessions` | `get_trading_sessions(instrument: str, trading_date: date, view: SessionView = "scheduled") -> TradingSessions` | 按固定顺序匹配规则、归并动作并返回 scheduled 或 realized 时段。 |
| `clear_cache` | `clear_cache(instrument: str | None = None) -> None` | 清理全部或指定合约的 `(instrument, trading_date, view)` 查询缓存。 |
| `_validate_registry_consistency` | `_validate_registry_consistency(registry, static_meta) -> None` | 校验注册表中的 product、exchange 与品种 YAML 一致。 |
| `_scope_matches` | `_scope_matches(scope: RuleScope, instrument: str, product: str) -> bool` | 判断规则是否覆盖当前合约，并校验显式合约列表属于当前品种。 |
| `_lifecycle_rule_matches` | `_lifecycle_rule_matches(rule, registry, trading_date: date) -> bool` | 判断有效期、scope 和生命周期 trigger 是否同时匹配。 |
| `_scheduled_override_matches` | `_scheduled_override_matches(override, registry, trading_date: date) -> bool` | 匹配 dates、range 或交易所节假日日期选择器。 |
| `_realized_event_matches` | `_realized_event_matches(event, registry, trading_date: date) -> bool` | 匹配 realized 事件的交易日和合约 scope。 |
| `_collect_actions` | `_collect_actions(matched_rules) -> tuple[SessionAction, ...]` | 按规则声明顺序收集全部计划型 action。 |
| `_apply_actions` | `_apply_actions(base_sessions: BaseSessions, actions) -> dict[SessionGroup, list[SessionInterval]]` | 按 remove、replace、边界收缩的优先级生成计划时段。 |
| `_apply_exception_intervals` | `_apply_exception_intervals(sessions, exception_intervals) -> dict[SessionGroup, list[SessionInterval]]` | 在 realized 视图中从计划时段扣除 `halted` 或非连续竞价区间。 |
| `_validate_session_group` | `_validate_session_group(group, intervals) -> None` | 校验动作后的区间非空边界、顺序和不重叠约束。 |

动作归并规则固定为：

```text
remove_session_group
    > replace_session_group
    > set_first_interval_start / set_last_interval_end
```

同一分组出现多个不同 `replace_session_group` 时抛错；多个开始边界取最晚时间，多个结束边界取最早时间；对空基础分组执行 replace 或边界修改时抛错，remove 保持幂等。

### 2.4 `scripts/infer_trading_date_from_calendar_date.py`

新增离线数据维护脚本，只负责从公告日历日生成 `inferred_no_night_trading_date`，生产运行时不得调用该脚本或回写 CSV。

| 函数 | 确定接口 | 主要功能 |
|---|---|---|
| `infer_exchange_holiday_trading_dates` | `infer_exchange_holiday_trading_dates(exchange: str, input_path: str, output_path: str, calendar_dir: str, product: str, product_meta_dir: str) -> None` | 创建不注入规则服务的 `TradingCalendar`，逐行调用 `next_trading_date()` 并写出三列 CSV。 |
| `_validate_input_columns` | `_validate_input_columns(df: pd.DataFrame, path: Path) -> None` | 校验输入至少包含 `no_night_calendar_date` 和 `holiday_str`。 |
| `main` | `main() -> None` | 提供命令行参数解析、错误输出和脚本退出码。 |

脚本默认拒绝静默覆盖已有输出文件；需要覆盖时使用显式 `--overwrite` 参数。

### 2.5 新增测试模块

仓库当前没有独立测试目录，本次建立最小但完整的 `tests/` 结构。

| 模块 | 主要覆盖内容 |
|---|---|
| `tests/rules/test_product_meta_session_rules.py` | 新旧 `sessions_history` 兼容解析、schema version、ID 唯一性、日期/时间、scope、action 和错误路径校验。 |
| `tests/rules/test_exchange_holiday_manager.py` | CSV 三列校验、缓存、交易所规范化、重复日期和无夜盘交易日判断。 |
| `tests/rules/test_trading_rule_service.py` | lifecycle、dates、range、holiday、scope、动作优先级、冲突、缓存和 `applied_override_ids`。 |
| `tests/rules/test_realized_session_events.py` | scheduled/realized 隔离、暂停区间扣除、跨多个基础 interval 的切分和非法异常区间。 |
| `tests/calendar/test_calendar_contract_sessions.py` | 合约级 session ID、offset、反向时间戳、标准时间轴以及未注入规则服务时的异常。 |
| `tests/integration/test_session_override_pipeline.py` | `TradingRuleService -> TradingCalendar -> BarAggregator/DataEnricher` 的合约级集成调用。 |
| `tests/scripts/test_infer_trading_date_from_calendar_date.py` | 公告日历日到目标交易日的离线推导及覆盖保护。 |

## 三、修改已有模块

### 3.1 `data/rules/product_meta_manager.py`

1. 删除当前依赖 `day_sessions` 位置语义的 `Session` 数据类，改为从 `session_models.py` 导入 `BaseSessions` 和 `SessionOverrideRules`。
2. 新增 `get_base_sessions(product: str, trading_date: date) -> BaseSessions`，按 `effective_date` 返回显式 morning、afternoon、night 三组基础时段。
3. 新增 `get_session_override_rules(product: str) -> SessionOverrideRules`，解析、校验并缓存 `schema_version: 1` 的三类 override。
4. 暂时保留 `get_sessions(product, trading_date)` 作为 `get_base_sessions()` 的兼容别名，并发出弃用警告；任何真实合约时段查询不得再使用该入口。
5. `_load()` 增加 YAML 顶层映射校验、YAML 解析错误包装和带品种/文件路径的异常信息。
6. 新增私有解析器，分别解析 base sessions、scope、source、action、lifecycle rule、scheduled override 和 realized event。
7. 加载阶段校验 schema version、规则 ID 唯一性、枚举、ISO 日期/时间、闭区间顺序和字段组合；跨 Tier 的“显式合约是否属于品种”校验交给 `TradingRuleService`。
8. 兼容旧 `day_sessions` 输入时仅按“最后一个 interval 为 afternoon”转换，并发出迁移警告；新数据必须使用显式字段。
9. 所有 interval 输出规范为不可变 tuple，并统一采用 `[start, end)` 语义。

### 3.2 `data/rules/instrument_registry_manager.py`

1. 公共接口 `get_instrument_registry(instrument)` 保持不变。
2. 将返回对象中的 `product` 统一转为大写，确保与 `ProductMetaManager` 查询键一致。
3. 补充注册表必要字段和空值校验，使 `list_date`、`last_trade_date`、`product`、`exchange` 可安全用于 lifecycle 匹配。
4. 保留合约全局唯一约束，并让错误信息包含数据文件目录和冲突合约。

### 3.3 `data/calendar/calendar.py`

1. 构造接口调整为 `TradingCalendar(product, meta_manager, calendar_dir, trading_rule_service=None)`；基础日期方法仍可在未注入规则服务时使用。
2. 新增 `_require_trading_rule_service() -> TradingRuleService`，所有真实时段方法通过它取得服务，未注入时抛出明确 `RuntimeError`。
3. `get_session_id` 改为 `get_session_id(instrument, calendar_timestamp, view="scheduled") -> int`。
4. `get_time_offset` 改为 `get_time_offset(instrument, calendar_timestamp, view="scheduled") -> pd.Timedelta`。
5. `get_minute_offset` 改为 `get_minute_offset(instrument, calendar_timestamp, view="scheduled") -> int`。
6. `get_session_ids` 改为 `get_session_ids(instruments, calendar_timestamps, view="scheduled") -> np.ndarray`。
7. `get_minute_offsets` 改为 `get_minute_offsets(instruments, calendar_timestamps, view="scheduled") -> np.ndarray`。
8. `get_calendar_timestamp_from_date_session_offset` 增加 `instrument` 和 `view` 参数，并改用显式 session group。
9. `get_calendar_timestamps_from_date_session_offset` 增加 `instruments` 和 `view` 参数，逐行按合约反向翻译。
10. `get_standard_timestamps` 改为 `get_standard_timestamps(instrument, start_date, end_date, freq, view="scheduled") -> np.ndarray`。
11. `get_calendar_timestamp_from_trading_date_and_time` 增加 `instrument` 和 `view` 参数，并校验输入时间确实属于返回时段。
12. `get_calendar_timestamps_from_trading_date_and_time` 增加 `instruments` 和 `view` 参数。
13. 删除所有 `day_sessions[:-1]`、`day_sessions[-1]` 和“至少两个日盘 interval”的位置假设，直接使用 morning、afternoon、night。
14. 所有区间判断统一改为左闭右开，修正当前部分代码使用 `<= end` 导致收市边界被判为可交易的问题。
15. `is_trading_date()`、`next_trading_date()`、`get_trading_date()` 和前后交易日映射不依赖 `TradingRuleService`，接口保持不变。

### 3.4 `data/feed/aggregator.py`

1. `aggregate()` 从 `Dominant_Contract` 列取得每行合约，并将合约序列传给 `get_session_ids()`、`get_minute_offsets()` 和反向时间戳接口。
2. 分组键继续使用 `trading_date + session_id + group_id`，但标准化时间戳必须按该行实际 `Dominant_Contract` 生成。
3. `align_to_standard_timestamps()` 不再以单一品种时段生成整个区间；改为按 DataFrame 中每个 `(trading_date, Dominant_Contract)` 组合分别获取标准时间轴后合并。
4. 同一交易日若因换月处理包含两个物理合约，必须分别生成标准时间轴，不能用其中一个合约覆盖另一个合约的最后交易日特殊时段。
5. 默认使用 `view="scheduled"`；realized 对齐只有在调用方未来明确要求时才开放，避免把临时停牌误判为行情缺失。

### 3.5 `data/cleaning/cleaning.py`

1. `DataEnricherGenerateCalendarTimestamp.apply()` 从原始 `InstrumentID` 取得合约序列，并传给合约级批量时间戳转换接口。
2. 在转换前校验 `InstrumentID`、`TradingDay`、`UpdateTime`、`UpdateMillisec` 长度一致且合约代码非空。
3. 默认请求 scheduled 视图；原始行情时间戳生成不因 realized 事件被截断。
4. 更新 `TYPE_CHECKING` 中过时的 `data.metadata` 导入路径，统一指向现有 `data.rules` 模块。

### 3.6 `data/factory.py`

1. 导入并实例化 `InstrumentRegistryManager`、`ExchangeHolidayManager` 和 `TradingRuleService`。
2. 在构造生产 `TradingCalendar` 时注入 `TradingRuleService`。
3. 增加内部组装函数 `_create_trading_rule_components(env_config)`，统一创建三个 Manager 和规则服务，避免 research/simulation 两套装配逻辑漂移。
4. `create_research_feed()` 和 `create_simulation_feed()` 均使用上述组装结果。
5. 清洗管道中的 `DataEnricherGenerateCalendarTimestamp` 自动获得已注入规则服务的 calendar。

### 3.7 `data/config.py`

1. 在 `EnvironmentConfig` 增加 `exchange_holiday_dir: str = "./db/reference/exchange_holidays"`。
2. 更新类文档，明确该目录是交易所级节假日无夜盘安排，不属于品种 YAML 或交易日历目录。

### 3.8 `data/facade.py`

1. 增加规则服务的惰性缓存，避免外部调用方直接装配底层 Manager。
2. 新增 `get_trading_sessions(instrument: str, trading_date: date, view: SessionView = "scheduled") -> TradingSessions`，作为数据引擎对外查询完整合约时段的统一入口。
3. feed 的现有公开接口保持不变。

### 3.9 `data/feed/research_feed.py`

1. 保留现有主流程，但确保传入 `BarAggregator` 的数据始终包含规范化后的 `Dominant_Contract`。
2. 修正 `FileLocator.get_1min_file_path()` 调用缺少 `product` 参数的现有问题，否则新增合约级对齐测试无法完整运行。
3. 更新文档，说明标准时间轴按每个交易日实际主力合约的 scheduled sessions 生成。

### 3.10 `data/feed/simulation_feed.py`

1. 主流程接口不变，但清洗流水线中的时间戳转换将自动按每条 TICK 的合约查询 scheduled sessions。
2. 对换月双合约流保留各自合约代码，禁止在时间戳转换时只使用 `SimulationFeed.product`。
3. 增加集成测试，覆盖同一交易日两个合约时段不同的情况。

### 3.11 `data/rules/README_CodeX.md`

1. 增加 `TradingRuleService` 是下游唯一 session 解释入口的约定。
2. 记录查询键为 `(instrument, trading_date, view)`，并说明 `exchange` 仍由注册表和品种元数据校验。
3. 记录 ProductMeta、Registry、Holiday Manager 和 Rule Service 的职责边界。
4. 记录 scheduled 与 realized 的区别及禁止从行情缺口自动生成 realized 事件的原则。

### 3.12 `db/reference/product_meta/RB_mock.yaml`

1. 将 `sessions_history.day_sessions` 迁移为 `morning_sessions` 和 `afternoon_sessions`。
2. 保留 `night_sessions`，并补充顶层 `session_override_rules.schema_version: 1`。
3. 增加三个必备空列表，随后仅录入已有可靠证据支持的示例事件。
4. 不加入“进入交割月统一取消夜盘”规则。

## 四、新增和调整的数据目录

新增：

```text
db/reference/exchange_holidays/
├── SHFE.csv
├── INE.csv
├── DCE.csv
└── CZCE.csv
```

每个 CSV 固定为：

```text
no_night_calendar_date,holiday_str,inferred_no_night_trading_date
```

首轮提交可以只建立带表头的空文件和测试 fixture；正式历史数据应在官方公告核验完成后单独录入，不能为了通过代码测试写入猜测日期。

## 五、兼容与迁移策略

1. `ProductMetaManager.get_sessions(product, trading_date)` 只作为基础时段兼容别名保留一个迁移周期，不返回合约真实时段。
2. 现有 YAML 的 `day_sessions` 暂时可读，但加载时发出弃用警告；仓库内样例和后续新增 YAML 全部使用显式三分组。
3. `TradingCalendar` 的真实时段接口是破坏性签名变更，所有仓库内调用点必须在同一批修改中完成，不提供缺省合约回退。
4. 未注入 `TradingRuleService` 的 calendar 只允许执行交易日期相关方法；禁止静默退回基础时段。
5. 默认查询视图统一为 `scheduled`，只有明确进行历史真实可交易区间研究时才传 `view="realized"`。
6. 缓存键统一为 `(instrument.upper(), trading_date, view)`；YAML 或 CSV 维护后通过重建对象或 `clear_cache()` 刷新。

## 六、建议实施顺序

1. 新增 `session_models.py`，修改 `ProductMetaManager`，完成新旧 YAML 解析和校验测试。
2. 新增 `ExchangeHolidayManager`、交易所 CSV 骨架和离线推导脚本，完成日期数据链测试。
3. 新增 `TradingRuleService`，先完成 scheduled 视图、动作归并和 lifecycle/holiday/date selector 测试。
4. 补充 realized 视图和异常区间扣除测试。
5. 修改 `TradingCalendar` 的依赖注入和合约级接口，完成边界、跨零点和最后交易日测试。
6. 修改 `BarAggregator`、清洗时间戳生成器、ResearchFeed、SimulationFeed 和工厂装配，完成端到端测试。
7. 修改 `DataFacade`、配置、README 和 `RB_mock.yaml`，最后运行全量测试和静态语法检查。

## 七、完成标准

1. 任意已注册合约可通过 `get_trading_sessions(instrument, trading_date, view)` 获得三分组时段和可追踪的 override ID。
2. 同品种同交易日的不同合约可以返回不同 session，并被 `TradingCalendar` 和 `BarAggregator` 正确消费。
3. 节假日无夜盘日期只从交易所 CSV 读取，生产代码不临时推导或写回日期。
4. scheduled 与 realized 结果互不覆盖，基础 `sessions_history` 始终保持计划基准。
5. 非法 schema、规则冲突、错误时间、错误 scope 和缺失依赖均明确报错，不静默忽略或回退。
6. 新增单元测试和集成测试全部通过，现有 research/simulation feed 的公开入口保持可用。
