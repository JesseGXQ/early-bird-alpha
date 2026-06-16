对于个人项目而言，目前的代码写作计划有些地方过于复杂，现在请你将其简化：

1. 不要新增一个session_models模块，并添加那么多的类：仅保留`BaseSessions`, `SessionOverrideRules`、`TradingSessions`。

* `BaseSessions`就是原本的`Session`改个名，用来表示基础交易时段规则。
* `SessionOverrideRules`：`SessionOverrideRules(schema_version, recurring_instrument_lifecycle_rules, scheduled_dated_overrides, realized_session_events)`
其中recurring_instrument_lifecycle_rules, scheduled_dated_overrides, realized_session_events 用普通的 Python 列表和字典结构承载即可。

注意：本次简化是减少“数据类数量”和“抽象层级”，不是减少类型标注。代码中应尽可能保留清晰的类型提示，包括 `Literal`、类型别名、方法参数类型和返回值类型。复杂 YAML 子结构可以继续用 `dict/list` 表示，但需要通过类型标注和运行时校验保证可读性与正确性。

2. TradingRuleService 未来要覆盖所有“合约 + 交易日 -> 完整交易规则”的查询（不仅是交易时段，可能还包括保证金，最小变动价位等等），不应该塞满 session override 的私有方法。

改为这样组织：
```text
data/rules/
├── models.py <- 把所有的原有的（product_meta.py中的那些）和计划新建的数据类放里面
├── product_meta_manager.py
├── instrument_registry_manager.py
├── instrument_daily_status_manager.py
├── exchange_holiday_manager.py
├── trading_rule_service.py 
├── session_rule_resolver.py <- 新增SessionRuleResolver
```

`TradingRuleService` = 对外门面

各 Resolver = 具体规则解释器

* `TradingRuleService`下的 `get_trading_sessions`方法和接口保持不变。内部调用`meta_manager.get_base_sessions`(原来的 `get_sessions` 方法), `meta_manager.get_session_override_rules`，分别获取 `BaseSessions` 和 `SessionOverrideRules`。然后调用 `SessionRuleResolver.resolve_rules`，获得 `TradingSessions`。

```python
TradingRuleService(
    meta_manager,
    registry_manager,
    holiday_manager=None,
    daily_status_manager=None,
)
```

3. `SessionRuleResolver`的设计

```python
SessionRuleResolver(
    registry_manager: InstrumentRegistryManager,
    meta_manager: ProductMetaManager,
    holiday_manager: ExchangeHolidayManager | None = None,
)
```

其下方法：

* `resolve_rules(self, instrument, trading_date, base_sessions: BaseSessions, session_override_rules: SessionOverrideRules, ) -> TradingSessions`
    - 内部流程：
        1. collect recurring lif ecycle rules actions 
        2. collect scheduled dated overrides actions
        3. 拼接 actions
        4. collect (if exists) realized session events exception exception_intervals
        5. apply actions
        6. apply exception intervals

* `_collect_recurring_lifecycle_rules_actions(self, instrument, trading_date, recurring_instrument_lifecycle_rules: dict) -> list[dict] #收集第一步的actions`

* `_collect_scheduled_dated_overrides_actions(self, instrument, trading_date, scheduled_dated_overrides: dict) -> list[dict] # 收集第二步的actions`

* `_collect_realized_session_events_exception_intervals(self, instrument, trading_date, realized_session_events: dict) -> list[dict] # 收集第三步的 exception_intervals`

* `_apply_actions(base_sessions: BaseSessions, actions: list[dict]) -> TradingSessions #执行动作（遵循先后顺序规则）`

* `_apply_exception_intervals(trading_session: TradingSessions, exception_intervals: list[dict]) -> TradingSessions # 执行特殊区间安排`







