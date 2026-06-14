# session_override_rules 数据存储设计修改建议

## 命名修改和事件归类调整

### 命名修改

将复杂的中国期货市场交易时段历史规则映射到数据库中，本质是一个“分类+整理”问题。因此需要更直观的字段命名，分别对主要的分类字段做如下重命名：

* `recurring_rules` -> `recurring_instrument_life_cycle_rules`，强调仅涵盖合约生命周期类交易时段调整。
* `dated_overrides` -> `expected_dated_overrides`，强调该字段下保存可以“提前预期到的”日期级别的交易时段覆盖规则。
* `realized_session_events` -> `unexpected_session_events`，强调是“未能预期到的”交易时段突然变更。

请修改相关文本。

### 归类调整

将“结算系统故障导致夜盘延迟开市”调整到`unexpected_session_events`，强调其未能被提前预期到的“突发”性质。

## 基础交易时段的数据结构

请标注：第一版中，取消`opening_auction`。

## 关于 `sources` 部分

请标注：第一版中，`source`字段存储时，仅包括 `url`和`note`这两个字段。但保留第九节有关证据来源的原始设计，提醒我们后面可以据此完善。

## 规则匹配与应用顺序

在设计方案的第十节——规则匹配与应用顺序中，前3个步骤没有问题。

步骤4、5、7应改为单独的一个小节——动作记录和执行。目前的设计如下：

1. 记录`recurring_instrument_life_cycle_rules`和`expected_dated_overrides`的动作。
2. 若请求时的`view=realized`，记录`unexpected_session_events`。

以下为伪代码：
```
对 night, morning, afternnon: 
    if view == realized & exception_intervals:
        implement it
        skip all else
    else:
        if exists(remove_session_group):
            implement it
            skip all else
        else:
            if exists(replace_session_group):
                implement it
                skip all else
            else:
                取所有 set_first_interval_start 最晚的
                取所有 set_last_interval_end 最早的
                implement them
```

有了这个应用顺序，我们应该完全删去 10.1 冲突处理 这一节。

## 加载校验

作为个人项目，且是初版代码构建，应尽量删除所有非必要的防御性编程。第一版中，仅保留你觉得绝对不可忽略的校验。

这一节不要删除，请分为规则校验设计——完整版，规则校验设计——第一版。




