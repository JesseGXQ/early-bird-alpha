# session_override_rules 数据存储设计_修改建议2

这份数据存储修改建议，是在我们已经完成了第一版的 trading session rule 变量（sessions_history + session_override_rules）入库后发现的。

以下是CodeX给我的回答。


你的反思很有道理，而且我觉得这是一个很关键的建模修正。

更准确地说：`sessions_history` 很适合放在 product level；但 `session_override_rules` 不完全适合放在 product level。它里面混合了几类不同作用域的规则，其中相当一部分天然是市场级、交易所级或产品集合级。

补充完善一下：

**1. 基础 session 和 override 的维度不同**

`BaseSessions` 是品种属性：

- RB 的夜盘几点收；
- IF 的日盘历史上何时从 09:15 改到 09:30；
- 某品种是否有夜盘；
- 某品种夜盘永久缩短。

这些都很自然地放在 `{PRODUCT}.yaml`。

但很多 override 是事件或制度规则，不是单个品种自己的属性：

- 疫情期间商品期货统一暂停夜盘；
- 2019-12-25 晚多个交易所延迟夜盘开市；
- 法定节假日后首个交易日无夜盘；
- 中金所国债期货最后交易日仅上午交易；
- 股指期货熔断事件。

这些如果复制到几十个品种 YAML，确实会造成维护负担、审核负担和一致性风险。

**2. 更合理的当前结构是 market-level only**

基于目前已经确认的事实规则，更简洁的结构不是先做两层 hybrid，而是只建立一份市场级 override rules 文件：

```text
product_meta/{PRODUCT}.yaml
  sessions_history

market_session_override_rules.yaml
  market / exchange / product-group / instrument-scope rules
```

也就是说：

- 品种 YAML 保留基础时段。
- 市场级 override 文件承接目前全部已确认的 override 规则。
- 暂时不建立产品级 override YAML，也不在产品 YAML 中保留空的 override 字段。
- 产品级 override 只作为未来扩展点：只有当出现无法用 market/exchange/product-group/instrument scope 表达的真实品种个案时，再新增对应实体。

这个选择符合“如非必要勿增实体”。截至目前，所有已确认的 override 都是跨品种、跨交易所、产品集合或合约集合规则，没有一条必须贴在单个 `{PRODUCT}.yaml` 上。

例如市场级规则可以长这样：

```yaml
- rule_id: CFFEX_TREASURY_LAST_TRADE_DATE_REMOVE_AFTERNOON
  scope:
    exchange: CFFEX
    products: [TS, TF, T, TL]
  trigger:
    type: instrument_last_trade_date
  actions:
    - operation: remove_session_group
      session_group: afternoon
```

2019 延迟开市可以是：

```yaml
- event_id: SHFE_INE_DCE_CZCE_20191226_DELAYED_NIGHT_OPEN
  scope:
    exchanges: [SHFE, INE, DCE, CZCE]
    product_filter:
      has_night_session_on_trading_date: true
  date_selector:
    type: dates
    dates: ["2019-12-26"]
  actions:
    - operation: set_first_interval_start
      session_group: night
      time: "22:30"
```

这样未来再发生一次交易所级事件，只改一条规则，而不是批量改几十个 YAML。

截至目前可以全部迁移到市场级的规则包括：

- 国债最后交易日仅上午交易：`CFFEX + [TF, T, TS, TL]`。
- 股指期货历史最后交易日提前收盘：`CFFEX + [IF, IH, IC]`。
- 法定节假日后首个交易日无夜盘：`[SHFE, INE, DCE, CZCE] + has_night_session_on_trading_date`。
- 疫情期间暂停商品期货夜盘：`[SHFE, INE, DCE, CZCE] + has_night_session_on_trading_date`。
- 2019-12-26 夜盘延迟开市：`[SHFE, INE, DCE, CZCE] + has_night_session_on_trading_date`。
- 股指期货熔断 realized events：`CFFEX + [IF, IH, IC]`。

**3. performance 问题可以靠索引解决，不必靠复制规则解决**

你的 performance 担心也是对的：如果每次查一个合约 session，都全表扫描所有市场级 override，肯定不优雅。

但解决办法不是把规则复制回产品级，而是在加载时编译索引。

例如 `SessionRuleResolver` 初始化时把规则预处理成：

```text
rules_by_exchange[CZCE]
rules_by_product[RB]
rules_by_date[2019-12-26]
rules_by_trigger[instrument_last_trade_date]
realized_events_by_date[2016-01-04]
```

查询时流程可以是：

1. 先根据合约拿到 `exchange/product/list_date/last_trade_date`。
2. 取 product 的 `sessions_history`。
3. 从市场级索引中只取候选规则：
   - 当前 exchange 的规则；
   - 当前 product 的规则；
   - 当前 date 的 dated override；
   - 当前 trigger 类型相关规则。
4. 合并 actions。
5. 按 `(instrument, trading_date, view)` 缓存结果。

这样不会每次扫描全市场规则，性能上是可控的。

若未来真的新增产品级 override 实体，也应作为第二阶段候选规则源叠加进来；但在当前规则事实下，没有必要为了一个空扩展点增加读取分支和存储实体。

**4. 维护成本方面，市场级更优**

你说的“维度负担”非常准确。product-level 存 override 最大的问题不是机器能不能处理，而是人类审计很痛苦：

- 同一事件复制 50 次，怎么确认没有漏一个？
- 同一条规则的 `source`、`date_selector`、`actions` 是否完全一致？
- 后续发现证据修订，要改几十处。
- AI 批量改也会增加 review 面积。

市场级规则能把事实保持为“一条事实一条记录”，这是更符合规则数据库设计的。

**5. 我会建议下一步调整 plan 和数据结构**

最终我建议的结论是：

- `sessions_history`：继续 product-level。
- `session_override_rules`：当前只建立 market-level 主表。
- 不建立产品级 override YAML，也不在产品 YAML 中保留空的 product-level override 字段。
- 产品级 override 只是未来扩展点；只有出现真实、无法由市场级 scope 表达的个案时再新增。
- 当前已经生成到 product YAML 的 override，可以视为第一版可执行结果，但不是理想最终形态，应迁移到市场级。
- 下一步应迁移：
  - 国债最后交易日规则 -> market-level。
  - 股指最后交易日历史提前收盘 -> market-level。
  - 疫情暂停夜盘 -> market-level。
  - 2019 延迟开市 -> market-level。
  - 节假日无夜盘 -> market/exchange-level。
  - 股指熔断 realized events -> market-level dated realized events。
- product YAML 里只保留 `sessions_history`。




