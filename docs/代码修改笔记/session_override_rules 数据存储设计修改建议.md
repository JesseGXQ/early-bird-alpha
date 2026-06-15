# session_override_rules 数据存储设计修改建议

## 命名修改和事件归类调整

### 命名修改

将复杂的中国期货市场交易时段历史规则映射到数据库中，本质是一个“分类+整理”问题。因此需要更直观的字段命名，分别对主要的分类字段做如下重命名：

* `recurring_rules` -> `recurring_instrument_lifecycle_rules`，强调仅涵盖合约生命周期类交易时段调整。
* `dated_overrides` -> `scheduled_dated_overrides`，强调该字段下保存可以“提前已规划的”日期级别的交易时段覆盖规则。

## 基础交易时段的数据结构

请标注：第一版中，取消`opening_auction`及相关代码设计。同时写一个说明，提醒我们后续考虑完善。

## 关于 `sources` 部分

请标注：第一版中，`source`字段存储时，仅包括 `url`和`note`这两个字段。但保留第九节有关证据来源的原始设计，提醒我们后面可以据此完善。

## 规则匹配与应用顺序

在设计方案的第十节——规则匹配与应用顺序中，前3个步骤没有问题。

步骤4、5、7应改为单独的一个小节——动作的收集、归并和执行。目前的设计如下：

1. 记录`recurring_instrument_life_cycle_rules`和`scheduled_dated_overrides`的中的动作。
2. 若请求时的`view=realized`，记录`realized_session_events`中的`exception_intervals`。

代码逻辑：
对夜盘，早盘，晚盘分别执行动作。
* 动作执行优先级：remove > replace > 边界收缩。
* 当有 remove 时，执行 remove，跳过后两个；当没有remove但有replace时，执行replace,跳过边界收缩；当仅有边界收缩时，执行边界收缩。
    - 边界收缩：取最晚开盘，最早收盘。
* 若 `view==realized`且该session没有被 remove时，执行 `exception_intervals` 的替换。

以下为伪代码：
```
for session in  (night, morning, afternnon): 
    if exists(remove_session_group):
        implement it
    else:
        if exists(replace_session_group):
            implement it
        else:
            取所有 set_first_interval_start 最晚的
            取所有 set_last_interval_end 最早的
            implement them
    
    if view == realized & session not removed:
    implement exception_intervals
```

目前的10.1节 冲突处理过于 general，将其改为“有效性检验”

动作归并如上。有效性检验采用下述最基本的几个：
1. 多个不同的 `replace_session_group` 报错。
2. 合并后开始时间不得晚于结束时间。
3. 不得修改原本不存在的交易小节。

## 加载校验

作为个人项目，且是初版代码构建，应尽量删除所有非必要的防御性校验。第一版中，仅保留最基础的校验。

这一节（第十四节）不要删除，请修改成数据校验（第一版）。

计划保留的校验：
1、2、3、4、5、6、7和YAML加载失败时的处理。


## 关于节假日相关交易时段调整

交易时段特殊规则中，有一条“法定节假日夜盘关闭”。你在 6.3 `date_selector` 中设定用 `type: "dates"`，并设立一个 `dates` 列表来列出所有的法定节假日。交易所级节假日安排重复写入每个品种 YAML，会产生冗余和数据不一致风险。

拆出交易所级节假日安排文件，例如：
```text
db/reference/exchange_holidays/
├── SHFE.csv
├── INE.csv
├── DCE.csv
└── CZCE.csv
```
每个CSV文件仅保存两列：no_night_calendar_date, holiday_str（无夜盘日历日，节假日名称）。

* 第一列设定为`no_night_calendar_date`，是为了和交易所的公告保持完全对齐。代码逻辑上，需要调用`TradingCalendar`将查询这个日历日的下一个交易日（因为取消掉夜盘归属下一个交易日）。

写一个轻量级的`ExchangeHolidayManager`，内部读取相应CSV文件，并提供查询功能，结合`session_override_rules`，供`TradingRuleServie`使用。

相应需要修改 6.2和6.3节：标明当 `event_type==holiday`时，`date_selector`此时不能只保存一个静态的节假日列表。此时的逻辑是查询`ExchangeHolidayManager`得到一个节假日列表。

## 返回对象

为控制个人项目复杂度，以及保证第一版代码的简洁，将目前的返回对象`TradingSessions`中的`realized_exception_intervals`属性删去。这是因为理论上而言，所有的时间段缺口或缩减，都可以通过`applied_overrride_ids`进行追踪。

但同时写一个说明，表明后续可能需要补充该属性，供行情缺口监测或审计流程等使用。

## 6.4 交易所节假日安排 相关性修改

每个CSV保存3列：`no_night_calendar_date`,`holiday_str`, `inferred_no_night_trading_date`（无夜盘日历日，节假日名称，推断出的无夜盘交易日）。

* `inferred_no_night_trading_date`由一个离线脚本infer_trading_date_from_calendar_date.py 计算生成。
    - 该脚本和生产环境完全分离。内部调用`TradingCalendar`的`next_trading_date`方法。
* `ExchangeHolidayManager`实例化时进行校验，必须已存在`inferred_no_night_trading_date`列。

* 为解决可能的循环依赖，采用如下设计：`TradingCalendar`可选持有`TradingRuleService`的实例[^1]。离线脚本中，调用的`TradingCalendar`不持有`TradingRuleService`的实例。

[^1]: 原因是`TradingCalendar`只有那些需要获取交易时段的方法需要实际依赖`TradingRuleService`。一些更基础的方法，例如`is_trading_date`, `next_trading_date`则不需要。
















