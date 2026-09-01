# ResearchFeed 非日频性能瓶颈分析

## 背景

当前 `test_research_feed.py` 中针对 `ResearchFeed` 的 5min 测试：

```text
product = rb
freq = 5min
start_date = 2015-05-13
end_date = 2025-05-16
profile = test_research_feed_5min_rb_20150513-20250516_profile_output_260718_0855.prof
```

cProfile 结果显示，测试总耗时约：

```text
41586734 function calls in 31.750 seconds
```

本轮分析的约束：

- 只接受代码优化。
- 不接受预计算变量。
- 不接受将中间结果落盘缓存。
- 不接受修改原始数据结构并提前保存 `trading_date`、`session_id`、`minute_offset` 等字段。
- 所有过程仍然需要在 `ResearchFeed` 内部完整走过：原始数据 -> 清洗 -> 聚合 -> 复权 -> 最终 dataframe。

## Profile 结论

主要耗时集中在 `BarAggregator.aggregate`，而不是复权、True Return 或清洗。

核心累计耗时如下：

```text
ResearchFeed.get_historical_vector                         30.408s
BarAggregator.aggregate                                    26.347s
ProductCalendar.get_calendar_timestamps_from_date_session_offset
                                                           13.767s
ProductCalendar.get_session_ids_and_minute_offsets          9.142s
ResearchFeed._collect_raw_1min_data                         3.640s
BarAggregator.align_to_standard_timestamps                  3.034s
ResearchFeed._calculate_true_return_intraday                0.203s
ResearchFeed._adjust_price                                  0.045s
CleaningPipeline.process                                    0.136s
```

因此，当前最主要的瓶颈仍然是非日频聚合阶段的日历映射逻辑。

## 瓶颈 1：聚合后反向生成 normalized calendar_time

位置：

```text
data/feed/aggregator.py
data/calendar/product_calendar.py
```

当前逻辑是：

```text
聚合得到 trading_date, session_id, group_id
-> minute_offset = group_id * minutes
-> 调用 get_calendar_timestamps_from_date_session_offset
-> 逐行反查 calendar_time
```

profile 中：

```text
get_calendar_timestamps_from_date_session_offset    13.767s
get_calendar_timestamp_from_date_session_offset     13.402s / 170301 calls
_session_interval_timestamps                        12.602s / 184899 calls
```

也就是说，聚合后的约 17 万根 5min bar，仍然在逐行调用单值翻译器。

单值翻译器内部会反复：

- 获取该合约、该交易日的 trading session。
- 将 session interval 翻译成 calendar timestamp。
- 处理夜盘对应的前一交易日。
- 反复构造 `pd.Timestamp(...)`。
- 反复调用 `_get_prev_trading_date`。

这一步是当前最大的优化空间。

### 优化方案

将反向翻译从逐行模式改为批量 calendar frame 模式。

在 `BarAggregator.aggregate` 内部，针对实际涉及的 `(Dominant_Contract, trading_date)` 动态生成一张临时标准时间轴表：

```text
Dominant_Contract
trading_date
session_id
group_id
minute_offset
calendar_time
```

然后：

```text
resampled_df
  left join / map
calendar_frame
  on Dominant_Contract, trading_date, session_id, group_id
```

这样可以避免 17 万次单值反查。

这不是预计算，也不落盘。它仍然是 `ResearchFeed` 运行过程中动态生成的中间表。

### 预期收益

这一项理论上可以将 13.8s 压缩到约 1s 左右，甚至更低。

### 重构成本

中高。

需要特别保证以下逻辑正确：

- 夜盘归属下一个交易日。
- 夜盘日历时间可能落在前一交易日的自然日。
- 夜盘可能跨午夜。
- 早盘、午盘中间有休息时间，`minute_offset` 不应把休息时间算进去。
- 不同合约在不同交易日可能存在特殊交易时段。

## 瓶颈 2：session_id 和 minute_offset 批量计算仍按交易日分组解析规则

位置：

```text
data/calendar/product_calendar.py
data/rules/session_rule_resolver.py
```

当前 `get_session_ids_and_minute_offsets` 已经避免了逐行调用 `get_session_id` 和 `get_minute_offset`，但仍然按：

```text
(instrument, trading_date)
```

分组处理。

profile 中：

```text
get_session_ids_and_minute_offsets       9.142s
_get_trading_sessions_cached             3.184s / 2433 calls
_get_trading_sessions                    3.183s
TradingRuleService.get_trading_sessions  3.179s
SessionRuleResolver.resolve_rules        3.024s
_collect_scheduled_dated_overrides       2.612s
```

这里的问题不是百万级逐行调用，而是 2433 个 `(contract, trading_date)` 组合每次都要走一遍规则解析。

`_collect_scheduled_dated_overrides` 内部存在重复日期解析：

```text
pd.Timestamp(...)
pd.to_datetime(...)
```

这些操作在 profile 中占据明显时间。

### 优化方案

优化 `SessionRuleResolver` 的规则准备阶段。

将 session override rules 在加载后或首次使用时规范化为更适合运行时判断的数据结构：

```text
range selector:
  start/end 预先转为 datetime.date

dates selector:
  dates 预先转为 set[datetime.date]

event trading_date:
  预先转为 datetime.date
```

运行时只做轻量级判断：

```text
start <= trading_date <= end
trading_date in dates_set
```

避免每次 `resolve_rules` 都进行字符串到日期的转换。

### 预期收益

这一项预计可将 3.1s 左右的 session rule 解析耗时压缩到 0.5s - 1s。

### 重构成本

中等偏低。

该优化主要发生在 rules 层内部，不应改变外部 API。需要注意不要破坏现有规则数据结构的可读性和测试覆盖。

## 瓶颈 3：strict alignment 重复生成标准时间轴

位置：

```text
data/feed/aggregator.py
ProductCalendar.get_standard_timestamps
```

当前 `align_to_standard_timestamps` 会再次按每个交易日调用：

```text
ProductCalendar.get_standard_timestamps
```

profile 中：

```text
BarAggregator.align_to_standard_timestamps   3.034s
ProductCalendar.get_standard_timestamps      2.640s / 2433 calls
```

这和瓶颈 1 中需要的 normalized calendar_time 本质上是同一类数据：标准 5min 交易时间轴。

### 优化方案

将瓶颈 1 的临时 `calendar_frame` 同时用于：

1. 聚合后标准化 `calendar_time`。
2. strict alignment 时补齐缺失 bar。

也就是说，`BarAggregator.aggregate` 内部只生成一次标准时间轴。

生成后的 calendar frame 既可以提供：

```text
(Dominant_Contract, trading_date, session_id, group_id) -> calendar_time
```

也可以提供：

```text
calendar_time index
```

用于 `reindex`。

### 预期收益

这一项预计可以额外减少 2s - 3s。

### 重构成本

中等。

如果与瓶颈 1 一起重构，整体设计会更自然；如果单独优化，收益有限且容易产生两套相似逻辑。

## 次要瓶颈：原始 1min 数据读取

位置：

```text
data/feed/research_feed.py
data/storage/bar_loader.py
```

profile 中：

```text
ResearchFeed._collect_raw_1min_data   3.640s
BarLoader.load_1min_bar               2.729s / 61 calls
pd.read_csv                           1.766s / 38 calls
pd.to_datetime                        0.818s
ProductCalendar.get_trading_dates     0.543s
```

这部分不是当前第一瓶颈，但仍有优化空间。

### 优化方案

可考虑：

- `pd.read_csv(usecols=...)` 只读取必要列。
- 在 `read_csv` 阶段使用 `parse_dates=['Datetime']`。
- 对稳定字段指定 dtype，减少 pandas 类型推断。
- 减少重复 `pd.to_datetime(df.index)`。
- 如果已经命中 `BarLoader` 内存缓存，尽量避免不必要的深拷贝。

### 预期收益

预计收益约 0.5s - 1.5s。

### 重构成本

低。

但由于不是主要瓶颈，优先级应低于 calendar 映射重构。

## 不建议优先优化的部分

以下部分当前耗时很低：

```text
ResearchFeed._adjust_price                    0.045s
ResearchFeed._calculate_true_return_intraday  0.203s
CleaningPipeline.process                      0.136s
```

因此不建议在下一轮优先处理复权、True Return 或清洗逻辑。

## 最终性能预估

在不做外部预计算、不落盘缓存、不修改原始数据结构的前提下：

### 保守目标

```text
10s - 12s
```

完成反向 timestamp 批量化后，应该可以达到。

### 较理想目标

```text
6s - 8s
```

需要同时完成：

- 反向 timestamp 批量化。
- 标准时间轴与 strict alignment 复用。
- session rule resolver 日期规则规范化。

### 激进目标

```text
5s 以内
```

有可能，但不确定。

限制因素包括：

- CSV 读取与 datetime parse 的物理成本。
- pandas groupby/agg 本身的成本。
- pytest 与 cProfile 的额外开销。
- 10 年 1min 数据量本身较大。

因此，5s 以内不应作为第一阶段硬目标。更合理的路线是先把 30s 压到 10s 以内，再根据新 profile 判断剩余瓶颈。

## 推荐实施顺序

1. 重构 `get_calendar_timestamps_from_date_session_offset` 的使用方式，避免聚合后 17 万次单值反查。
2. 将 normalized timestamp 生成和 strict alignment 合并为一次动态 calendar frame 生成。
3. 优化 `SessionRuleResolver`，将规则中的日期字符串预先规范化。
4. 最后再优化 `BarLoader` 的 CSV 读取和 datetime parse。

## 核心判断

当前 `ResearchFeed` 非日频路径的主要性能瓶颈已经非常集中：

```text
不是 IO，
不是复权，
不是收益率计算，
而是 BarAggregator 内部对 ProductCalendar 的高频调用方式。
```

下一轮优化应围绕“运行时动态 calendar frame”展开。这个方向不违反“不接受预计算变量”的约束，因为它不保存、不复用跨调用状态，只是在单次 `ResearchFeed.get_historical_vector` 运行过程中，将重复单值日历翻译改成批量向量化映射。
