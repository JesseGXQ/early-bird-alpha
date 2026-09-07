# 因子模块：因子评估 grouped forward returns 开发帮助

> 本文服务于因子研究模块 V1 开发方案的 Step 8，目标是帮助开发 `factors/evaluation/grouped.py`。
>
> 本文以当前已经实现的 `coverage.py` 和 `ic.py` 为实际代码风格参考。原始架构文档和代码写作方案用于说明业务目标；当两者与当前代码组织不一致时，以本文明确记录的开发口径为准。

## 1. 本阶段要解决什么问题

给定同一研究面板上的：

- 因子值 `factor`；
- 某个预测期限的未来收益标签 `label`；
- 当期品种池 `universe`；
- 评价模式、分组数、分组方向和缺失值政策；

分组收益模块需要回答：

1. 因子值从低到高时，未来收益是否呈现单调变化？
2. 预测关系是普遍存在于全部因子区间，还是只集中在极端状态？
3. 高因子组和低因子组的未来收益差是多少？
4. 横截面模式下，不同 timestamp 的分组关系是否稳定？
5. 时序模式下，不同 product 的分组关系是否一致？
6. 时序因子按自身完整历史分组时，条件收益关系怎样？
7. 时序因子只使用 point-in-time 历史分组时，条件收益关系是否仍然存在？
8. 重复因子值、缺失值、样本不足和空组应怎样记录？

本阶段的代码职责为：

```text
factors/evaluation/grouped.py
    ├── 选择每个评价单元的合资格因子样本
    ├── 构造分位数组归属
    ├── 计算各组平均未来收益和有效标签数
    ├── 计算高因子组减低因子组
    ├── 汇总 timestamp 级或 product 级结果
    └── 保存组归属、失效原因和结果元数据
```

## 2. 模块边界

### 2.1 模块负责

- 横截面模式下，每个 timestamp 跨 product 分组；
- 时序模式下，每个 product 沿 timestamp 分组；
- 默认 5 组，可配置；
- 等权计算各组平均未来收益；
- 输出每组有效标签数；
- 输出高因子组平均收益减低因子组平均收益；
- 保存组归属，供后续换手诊断复用；
- 处理 `universe`、缺失值、ties、样本不足和分组失败；
- 为时序模式提供 full-sample、expanding PIT 和 rolling PIT 分组。

### 2.2 模块不负责

- 将分组结果转换为最终目标仓位；
- 确定组合 gross exposure、风险预算或目标波动率；
- 处理手续费、滑点、容量、保证金和成交限制；
- 构造可自融资的多期持有组合；
- 把重叠的多期 forward returns 直接连乘为策略净值；
- 对分组收益执行 Newey-West、bootstrap 或多重检验；
- 自动判定因子是否有效。

结果必须命名为 `grouped_forward_returns`，不得命名为 `strategy_returns`、`portfolio_pnl` 等真实策略结果。

## 3. 已确定的开发口径

### 3.1 输入格式

沿用当前 `coverage.py` 和 `ic.py` 的代码风格，核心函数接收已经组装好的 DataFrame：

```text
pd.DataFrame
index = MultiIndex[timestamp, product]
columns = [factor, label, universe]
```

其中：

- `factor` 是时刻 t 已可见的因子值；
- `label` 是索引为 t 的未来收益标签；
- `universe` 是布尔品种池标记；
- 三列共享完全相同的研究面板索引。

低层 `grouped.py` 不再处理字符串 `universe="all"`。该配置应由上游评价器规范化后写入 DataFrame 的 `universe` 列。

### 3.2 默认配置

```python
mode: Literal["cross_sectional", "time_series"]
n_groups: int = 5
grouping_direction: Literal["ascending", "descending"] = "ascending"
min_observations: int = 10
nan_policy: Literal["drop", "strict"] = "drop"
ts_grouping_method: Literal[
    "full_sample",
    "expanding",
    "rolling",
] = "full_sample"
rolling_window: int | None = None
```

### 3.3 因子决定组归属，标签不决定组边界

组归属只使用：

```text
universe=True
且 factor 有效
```

标签仅用于组归属完成后计算组平均未来收益。

不能先删除标签缺失行，再根据剩余因子值计算分位数。否则未来标签是否可用会改变当期分组边界。

### 3.4 等权组收益

每个组的 future return 使用有效标签的简单算术平均：

```python
group_mean = group["label"].mean()
```

V1 不支持市值、成交量、波动率或风险预算加权。加权分组属于更具体的收获方式，应在后续评价任务或诊断组合中显式实现。

### 3.5 不增加 `min_group_observations`

V1 只保留 `min_observations`，不新增每组最低标签数参数。

如果因子分组已经成功，但某个组没有有效标签：

- 该组平均未来收益为 NaN；
- 其他有有效标签的组继续输出；
- 若高因子组或低因子组为 NaN，则 `high_minus_low` 为 NaN；
- `group_counts` 和元数据负责暴露该情况；
- 不抹掉其他组的有效结果。

### 3.6 ties 不做任意拆分

直接对因子值执行分位数分组。重复值导致无法形成目标数量的不同分位数组时：

- 不使用 `rank(method="first")` 按行顺序强行拆分；
- 不自动减少实际组数；
- 不抛出整个实验；
- 当前评价单元的组归属返回 NA；
- 当前评价单元的分组结果返回 NaN；
- 记录稳定失效码和原因。

### 3.7 组号和高低方向分离

`grouping_direction` 只控制组号：

- `ascending`：低因子值为第 1 组，高因子值为第 `n_groups` 组；
- `descending`：高因子值为第 1 组，低因子值为第 `n_groups` 组。

无论组号如何，价差始终定义为：

```text
高因子组平均未来收益 - 低因子组平均未来收益
```

### 3.8 只对收益类标签使用本模块

底层 DataFrame 只有 `label` 数值列，无法自行识别标签类型。因此调用契约为：

> `FactorEvaluator` 只在标签属于 forward-return 类收益标签时调用 `grouped_forward_returns()`。

未来波动率、方向和事件标签若需要分箱诊断，应使用更通用的 grouped label statistics，不能把其组均值命名为收益。

## 4. 评价轴的严格对称关系

横截面和时序模式的区别，不在于因子最初怎样计算，而在于预测关系沿哪个轴估计。

| 模式 | 外层评价单元 | 单元内分组轴 | 明细结果索引 | 二次汇总轴 |
|---|---|---|---|---|
| `cross_sectional` | timestamp | product | timestamp | timestamp |
| `time_series` | product | timestamp | product | product |

### 4.1 横截面模式

对每个 timestamp：

```text
取得当期品种
→ 按当期因子值分组
→ 计算各组未来收益
→ 计算当期 high-minus-low
```

因此每个 timestamp 产生一行分组结果。

### 4.2 时序模式

对每个 product：

```text
取得该品种历史观测
→ 按该品种自身因子分布分组
→ 计算各历史状态的未来收益
→ 计算该品种 high-minus-low
```

因此每个 product 产生一行分组结果。

### 4.3 跨品种汇总不会改变评价模式

时序模式先得到每个品种的 TS_HML_i，随后对有效品种等权平均。这只是对品种级结果进行描述汇总，和 `ts_ic` 先按 product 计算、再汇总品种分布相同。

不能把所有 product 的全部历史行直接 pooled 后计算一个 HML，否则历史更长的品种会获得更高权重，并掩盖品种间异质性。

## 5. 评价样本

### 5.1 基础掩码

对任意 `(t, i)`，定义：

```text
E[t,i] = universe 是否为 True
F[t,i] = factor 是否有效
L[t,i] = label 是否有效
```

用于形成分组边界的样本为：

```text
B = E & F
```

用于计算组平均收益的样本为：

```text
R = E & F & L
```

关键代码：

```python
eligible = group.loc[group["universe"]]
factor_sample = eligible.loc[eligible["factor"].notna()]
return_sample = factor_sample.loc[factor_sample["label"].notna()]
```

### 5.2 为什么标签不能参与分组边界

假设某个 timestamp 有 20 个合资格品种，其中两个未来标签缺失。

正确流程为：

```text
使用 20 个因子值形成组归属
→ 两个标签缺失品种仍保留组号
→ 计算组均值时剔除其标签
```

错误流程会先删除两个标签缺失品种，再使用剩余 18 个因子值重新计算分位数。这样会让未来结果的可用性改变当时的分组边界，也会使相同因子截面在不同标签 horizon 下产生不同组归属。

### 5.3 最低样本要求

形成 `n_groups` 个组至少需要：

```python
n_factor_valid >= max(min_observations, n_groups)
```

即使数量满足要求，ties 仍可能使分位数边界重复。

## 6. 分位数组定义

### 6.1 核心代码

```python
def _assign_quantile_groups(
    factor: pd.Series,
    n_groups: int,
    grouping_direction: Literal["ascending", "descending"],
) -> pd.Series:
    """按因子值构造目标数量的分位数组。"""

    ascending_groups = pd.qcut(
        factor,
        q=n_groups,
        labels=range(1, n_groups + 1),
        duplicates="raise",
    ).astype("Int64")

    if grouping_direction == "ascending":
        return ascending_groups

    return (n_groups + 1 - ascending_groups).astype("Int64")
```

### 6.2 为什么不使用 `duplicates="drop"`

`duplicates="drop"` 会在不同评价单元产生不同的实际组数。例如目标为 5 组，有的 timestamp 形成 5 组，有的只形成 3 组。此时“第 3 组”不再代表稳定分位区间，也无法稳定识别最高组和最低组。

### 6.3 为什么不使用 `rank(method="first")`

完全相同的两个因子值不应仅因为输入行顺序不同而进入不同组。V1 宁可将该评价单元标记为无法形成目标组数，也不制造不存在的经济排序。

## 7. 横截面分组收益

### 7.1 组归属

对 timestamp `t`，在合资格且因子有效的品种集合中进行分组：

```text
g_cs[t,i] = QuantileGroup_i(factor[t,i])
```

关键分派代码：

```python
selected = (
    df.groupby(level="timestamp")
    .apply(
        _select_and_group_one_unit,
        n_groups=n_groups,
        grouping_direction=grouping_direction,
        min_observations=min_observations,
        nan_policy=nan_policy,
    )
)
```

每个 timestamp 独立计算边界。其他 timestamp 的因子分布不能影响当期组归属。

### 7.2 各组平均未来收益

对 timestamp `t` 的第 `q` 组：

```text
R_cs[t,q]
= mean(label[t,i] | group[t,i] == q 且 label 有效)
```

它回答：

> 在同一个 timestamp，因子处于相同截面分位组的品种，其未来收益平均是多少？

### 7.3 横截面高低组差

```text
CS_HML[t] = R_cs[t,high] - R_cs[t,low]
```

每个 timestamp 产生一个 `high_minus_low`。随后对有效 timestamp 等权汇总：

```text
mean_CS_HML = mean_t(CS_HML[t])
```

## 8. 时序 full-sample 分组

### 8.1 定义

对 product `i`，使用其完整合资格因子历史：

```text
H_full[i] = {factor[s,i] | universe[s,i] 且 factor 有效}
```

每个时点的组归属为：

```text
g_full[t,i] = QuantileGroup(factor[t,i]; H_full[i])
```

关键分派代码：

```python
selected = (
    df.groupby(level="product")
    .apply(
        _select_and_group_one_unit,
        n_groups=n_groups,
        grouping_direction=grouping_direction,
        min_observations=min_observations,
        nan_policy=nan_policy,
    )
)
```

### 8.2 意义

full-sample 分组回答：

> 在完整研究样本中，该品种因子处于自身历史高分位时，其未来收益是否高于自身历史低分位时？

它是 ex-post 条件分布诊断。由于完整样本分位数使用了 `t` 之后的因子分布，它不能被解释为时刻 `t` 当时可以获得的组归属。

### 8.3 每个 product 的组收益

```text
R_ts[i,q]
= mean(label[t,i] | group[t,i] == q 且 label 有效)
```

每个 product 产生一行组收益。

### 8.4 每个 product 的高低组差

```text
TS_HML[i] = R_ts[i,high] - R_ts[i,low]
```

它是同一品种两个历史因子状态的条件未来收益差。

## 9. 时序 point-in-time 分组

### 9.1 为什么需要 PIT

full-sample 分组可以描述完整样本的条件关系，但无法回答历史当时可否形成相同组归属。

PIT 分组要求：

> 修改 timestamp `t` 之后的因子数据，不得改变 `t` 及以前的时序组归属。

PIT 只改变组归属的生成方式。最终结果仍然是每个 product 的组平均未来收益和 `TS_HML[i]`，不会改成按 timestamp 跨品种比较。

### 9.2 Expanding PIT

对 product `i`、时点 `t`：

```text
H_expanding[i,t]
= {factor[s,i] | s <= t 且 universe[s,i] 且 factor 有效}
```

当前组归属为：

```text
g_expanding[t,i]
= QuantileGroup(factor[t,i]; H_expanding[i,t])
```

历史包含当前因子 `factor[t,i]`，因为它在形成时刻 `t` 的研究信号时已经可见。

至少积累：

```python
max(min_observations, n_groups)
```

个合资格有效因子值后，才尝试形成 PIT 组归属。此前属于 PIT 暖机期，组号为 NA。

### 9.3 Rolling PIT

rolling 模式先取得最近 `rolling_window` 个时间行，再在窗口内应用 `universe` 和因子有效性过滤。

窗口表示最近 W 个时间行，不是最近 W 个有效因子值。否则缺失较多时，一个名为“60 日”的窗口可能实际跨越数百日。

rolling 是一种状态化因子使用方式，已经隐含策略侧怎样解释因子。因此：

- 不提供默认窗口；
- 用户必须显式传入 `rolling_window`；
- 窗口必须进入实验配置和结果元数据；
- `rolling_window` 至少要使形成目标组数在理论上可能。

### 9.4 参数校验

```python
def _validate_ts_grouping_config(
    mode: str,
    ts_grouping_method: str,
    rolling_window: int | None,
    n_groups: int,
    min_observations: int,
) -> None:
    """校验时序分组方法和 rolling 窗口。"""

    allowed = {"full_sample", "expanding", "rolling"}
    if ts_grouping_method not in allowed:
        raise ValueError(
            f"非法 ts_grouping_method: {ts_grouping_method!r}"
        )

    if mode == "cross_sectional":
        if ts_grouping_method != "full_sample":
            raise ValueError("横截面模式不能配置时序 PIT 分组")
        if rolling_window is not None:
            raise ValueError("横截面模式不能配置 rolling_window")
        return

    if ts_grouping_method == "rolling":
        if rolling_window is None:
            raise ValueError(
                "rolling 时序分组必须显式提供 rolling_window"
            )
        minimum = max(min_observations, n_groups)
        if rolling_window < minimum:
            raise ValueError(
                "rolling_window 不能小于形成分组所需的最低样本数"
            )
    elif rolling_window is not None:
        raise ValueError(
            "只有 rolling 时序分组可以提供 rolling_window"
        )
```

### 9.5 PIT 核心代码提示

下面代码展示算法重点，而不是要求生产实现逐字复制：

```python
def _pit_assignments_one_product(
    product_frame: pd.DataFrame,
    method: Literal["expanding", "rolling"],
    n_groups: int,
    grouping_direction: Literal["ascending", "descending"],
    min_observations: int,
    rolling_window: int | None,
) -> tuple[pd.Series, pd.DataFrame]:
    """为单个 product 构造 PIT 组归属和逐时点元数据。"""

    product_frame = product_frame.sort_index(level="timestamp")
    assignments = pd.Series(
        pd.NA,
        index=product_frame.index,
        dtype="Int64",
        name="factor_group",
    )
    metadata_rows: list[dict[str, object]] = []
    minimum = max(min_observations, n_groups)

    for position, (row_index, row) in enumerate(
        product_frame.iterrows()
    ):
        if not row["universe"]:
            metadata_rows.append({
                "status": "out_of_universe",
                "n_factor_valid": 0,
                "invalid_code": None,
            })
            continue

        if pd.isna(row["factor"]):
            metadata_rows.append({
                "status": "factor_missing",
                "n_factor_valid": 0,
                "invalid_code": None,
            })
            continue

        if method == "expanding":
            history = product_frame.iloc[: position + 1]
        else:
            assert rolling_window is not None
            start = max(0, position - rolling_window + 1)
            history = product_frame.iloc[start : position + 1]

        history_factor = history.loc[
            history["universe"] & history["factor"].notna(),
            "factor",
        ]

        if len(history_factor) < minimum:
            metadata_rows.append({
                "status": "insufficient_history",
                "n_factor_valid": len(history_factor),
                "invalid_code": 2,
            })
            continue

        if history_factor.nunique(dropna=True) < n_groups:
            metadata_rows.append({
                "status": "insufficient_unique_values",
                "n_factor_valid": len(history_factor),
                "invalid_code": 3,
            })
            continue

        try:
            history_groups = _assign_quantile_groups(
                history_factor,
                n_groups=n_groups,
                grouping_direction=grouping_direction,
            )
        except ValueError:
            metadata_rows.append({
                "status": "duplicate_quantile_edges",
                "n_factor_valid": len(history_factor),
                "invalid_code": 4,
            })
            continue

        assignments.loc[row_index] = history_groups.loc[row_index]
        metadata_rows.append({
            "status": "valid",
            "n_factor_valid": len(history_factor),
            "invalid_code": None,
        })

    metadata = pd.DataFrame(
        metadata_rows,
        index=product_frame.index,
    )
    return assignments, metadata
```

逐行都写入一条 metadata，可以直接复用 `product_frame.index`，从而保持 `MultiIndex[timestamp, product]`，避免把索引元组降级为普通 object index。`out_of_universe` 和 `factor_missing` 是赋值状态，不代表整个 product 的分组评价失败。

### 9.6 PIT 结果如何计算

PIT 组归属完成后，仍按 product 汇总：

```text
R_pit[i,q]
= mean(label[t,i] | g_pit[t,i] == q 且 label 有效)

TS_HML_pit[i]
= R_pit[i,high] - R_pit[i,low]
```

不能在组归属完成后按 timestamp 跨品种重新形成主要分组统计。那会变成“时序方式生成状态、截面方式聚合收益”的混合诊断组合，不属于本模块的基础时序分组结果。

## 10. 分组平均未来收益

### 10.1 通用计算

无论组归属来自横截面、时序 full-sample 还是 PIT，后续收益统计均可复用：

```python
def _grouped_returns_and_counts(
    df: pd.DataFrame,
    assignments: pd.Series,
    outer_axis: Literal["timestamp", "product"],
    n_groups: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """计算各外层评价单元的分组平均未来收益和标签数。"""

    evaluation = df[["label"]].copy()
    evaluation["factor_group"] = assignments
    evaluation = evaluation.reset_index()

    valid = evaluation.dropna(
        subset=["factor_group", "label"]
    )

    grouped = valid.groupby(
        [outer_axis, "factor_group"],
        sort=True,
    )["label"]

    means = grouped.mean().unstack("factor_group")
    counts = grouped.count().unstack("factor_group")

    expected_groups = range(1, n_groups + 1)
    means = means.reindex(columns=expected_groups)
    counts = counts.reindex(
        columns=expected_groups,
        fill_value=0,
    ).fillna(0).astype(int)

    means.columns.name = "factor_group"
    counts.columns.name = "factor_group"
    return means, counts
```

### 10.2 组归属与标签缺失

在默认 `drop` 模式下：

- 因子有效但标签缺失的观测仍保存组号；
- 该观测不进入组收益均值；
- `group_counts` 只统计有效标签数；
- 可另在元数据中记录每组 assigned count 和 label-valid count 的差。

### 10.3 不能把空组填成 0 收益

某组没有有效标签时：

```text
grouped_forward_return = NaN
group_count = 0
```

0 表示该组平均收益确实为零；NaN 表示没有样本定义平均收益。两者不能混用。

## 11. 高因子组减低因子组

### 11.1 方向映射

```python
def _extreme_group_numbers(
    n_groups: int,
    grouping_direction: Literal["ascending", "descending"],
) -> tuple[int, int]:
    """返回高因子组号和低因子组号。"""

    if grouping_direction == "ascending":
        return n_groups, 1
    return 1, n_groups
```

### 11.2 核心代码

```python
high_group, low_group = _extreme_group_numbers(
    n_groups,
    grouping_direction,
)

high_minus_low = (
    grouped_forward_returns[high_group]
    - grouped_forward_returns[low_group]
).rename("high_minus_low")
```

### 11.3 杠杆尺度

`high_minus_low = mean(high) - mean(low)` 等价于多头组内权重和为 1、空头组内权重和为 -1，即 gross exposure 为 2。

V1 不乘以 0.5。结果名称明确为 `high_minus_low`，不能暗示已经完成 gross normalization。

### 11.4 缺失传播

高因子组或低因子组任一侧没有有效标签时，`high_minus_low` 为 NaN。中间组缺失不会直接使高低组差失效，但必须在 `group_counts` 中可见。

## 12. 描述汇总

### 12.1 分组均值汇总

对每个组，先在每个外层评价单元中计算组均值，再对外层评价单元等权平均：

```python
mean_by_group = grouped_forward_returns.mean(axis=0)
```

横截面模式中每个有效 timestamp 权重相同；时序模式中每个有效 product 权重相同。这不是将所有原始观察 pooled 后求均值。

### 12.2 高低组差汇总

```python
def _summarize_high_minus_low(
    high_minus_low: pd.Series,
) -> GroupedSpreadSummary:
    valid = high_minus_low.dropna()
    n_valid = len(valid)

    return GroupedSpreadSummary(
        n_valid=n_valid,
        mean=valid.mean() if n_valid > 0 else np.nan,
        std=valid.std(ddof=1) if n_valid > 1 else np.nan,
        positive_ratio=(valid > 0).mean()
        if n_valid > 0 else np.nan,
    )
```

其中外层单位在横截面模式表示 timestamp，在时序模式表示 product。

### 12.3 V1 不做稳健推断

本阶段不在 `grouped.py` 中重复实现 Newey-West、block bootstrap、p-value 和多重检验。这些属于后续 `robustness.py`。

## 13. 分组条件差与策略收益的区别

### 13.1 横截面模式

当标签是一期未来收益、每期按因子重新分组时，`CS_HML[t]` 可以近似解释为忽略成本、容量和成交限制的等权多空诊断收益。

但当 horizon 大于 1 时，相邻 forward return 通常重叠，不能直接把每日 `CS_HML[t]` 连乘成一条自融资策略净值，除非另行定义持有期、调仓频率和重叠 cohort。

### 13.2 时序模式

品种级：

```text
TS_HML[i]
= E[label_i | high] - E[label_i | low]
```

是两个历史因子状态的条件收益差，不是逐期持仓收益序列。

若真实状态仓位为高组 `+1`、低组 `-1`、中间组 `0`，逐期收益代理是 `position[t,i] * label[t,i]`。其均值还依赖高低状态出现频率和仓位归一方式，一般不等于 `TS_HML[i]`。

因此：

- `TS_HML[i]` 是时序分组预测诊断；
- 它支持“高状态做多、低状态做空”的策略假设；
- 真正逐期状态持仓收益属于后续 `DiagnosticPortfolioExperiment`。

## 14. 缺失值政策

### 14.1 `drop`

默认政策：

- `universe=False`：不属于评价样本，不视为缺失；
- 因子缺失：不形成组归属；
- 标签缺失：保留已经形成的组归属，但不进入组收益均值；
- 删除后因子样本不足：当前评价单元分组失败。

### 14.2 `strict`

对外层评价单元：

- 横截面模式：一个 timestamp 是一个严格检查单元；
- 时序模式：一个 product 是一个严格检查单元。

只要 `universe=True` 范围内存在因子或标签缺失，整个评价单元失效。

PIT 时序模式下，为保持最终 product 级结果语义一致，`strict` 同样按整个 product 检查，而不是对每个滚动窗口分别给出不同 strict 状态。默认仍为 `drop`。

### 14.3 `universe=False` 不触发 strict

```python
eligible = group.loc[group["universe"], ["factor", "label"]]
has_missing = eligible.isna().any(axis=None)
```

品种池外行不属于研究样本，即使因子和标签都是 NaN，也不能使评价单元失效。

## 15. 失效条件和原因码

### 15.1 建议原因码

```python
GROUPED_INVALID_CODES = {
    1: "has_missing_nan_policy_strict",
    2: "insufficient_factor_observations",
    3: "insufficient_unique_factor_values",
    4: "duplicate_quantile_edges",
    9: "empty_universe",
}
```

数字码属于 `grouped.py` 模块内命名空间。不能假设它和 `ic.py` 中相同数字具有相同含义。

### 15.2 空品种池

外层评价单元没有任何 `universe=True` 行：

```text
invalid_code = 9
group assignments = NA
group returns = NaN
```

### 15.3 因子样本不足

```python
if n_factor_valid < max(min_observations, n_groups):
    invalid_code = 2
```

### 15.4 唯一因子值不足

```python
if factor.nunique(dropna=True) < n_groups:
    invalid_code = 3
```

即使唯一值数量不少于 `n_groups`，经验分位数边界仍可能因大量 ties 重复。因此还需要捕获 `pd.qcut` 的分位数边界异常。

### 15.5 重复分位数边界

```python
try:
    groups = _assign_quantile_groups(...)
except ValueError:
    invalid_code = 4
```

分组失败只影响当前 timestamp、product 或 PIT 当前赋值，不应抛出并终止整个实验。

### 15.6 标签常数不是失效条件

如果某个评价单元内所有有效标签都相同，各组收益相同、`high_minus_low=0` 是合法结果。

因此不能复用 `ic.py::_select_group_sample()` 中的 `label_single_value` 失效检查。`grouped.py` 应使用自己的样本选择逻辑。

## 16. 结果数据结构

### 16.1 分组价差汇总

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class GroupedSpreadSummary:
    n_valid: int
    mean: float
    std: float
    positive_ratio: float
```

### 16.2 完整结果

```python
@dataclass(frozen=True)
class GroupedEvaluationResults:
    mode: Literal["cross_sectional", "time_series"]
    n_groups: int
    grouping_direction: Literal["ascending", "descending"]
    nan_policy: Literal["drop", "strict"]

    ts_grouping_method: Literal[
        "full_sample",
        "expanding",
        "rolling",
    ] | None
    rolling_window: int | None

    group_assignments: pd.Series
    group_metadata: pd.DataFrame
    grouped_forward_returns: pd.DataFrame
    group_counts: pd.DataFrame
    high_minus_low: pd.Series
    high_minus_low_summary: GroupedSpreadSummary
```

### 16.3 各字段含义

#### `group_assignments`

- 与原研究面板相同的 `MultiIndex[timestamp, product]`；
- dtype 使用 pandas nullable `Int64`；
- 合法值为 `1..n_groups`；
- 品种池外、因子缺失、暖机期和分组失败为 `pd.NA`；
- 后续横截面组成员换手应直接复用该字段，不能重新分组。

#### `group_metadata`

至少保存：

```text
n_eligible
n_factor_valid
n_factor_missing
n_label_valid
n_label_missing
n_groups_formed
status
invalid_code
```

索引语义：

- 横截面：timestamp；
- 时序 full-sample：product；
- 时序 PIT：赋值元数据可以使用 `(timestamp, product)`，最终 product 级统计由 returns/counts 表表达。

#### `grouped_forward_returns`

```text
cross_sectional:
    index = timestamp
    columns = 1..n_groups

time_series:
    index = product
    columns = 1..n_groups
```

#### `group_counts`

与 `grouped_forward_returns` 相同形状，值为每组有效标签数。

#### `high_minus_low`

```text
cross_sectional: index = timestamp
time_series:      index = product
```

### 16.4 为什么保存组归属

Step 9 的横截面换手需要比较相邻 timestamp 的高因子组和低因子组成员变化。

如果 Step 8 不保存 `group_assignments`，后续模块只能重新运行分组。重新分组可能因代码、参数或数据变化得到另一套成员，破坏结果一致性。

## 17. 核心实现流程

### 17.1 外层流程

```text
校验 DataFrame 和配置
    ↓
复制输入，保证不原地修改
    ↓
按 mode 和 ts_grouping_method 生成 group_assignments
    ↓
生成 group_metadata
    ↓
将 assignments 与 label 对齐
    ↓
按 timestamp 或 product 计算组均值和组计数
    ↓
计算 high_minus_low
    ↓
等权汇总外层评价单元
    ↓
返回 GroupedEvaluationResults
```

### 17.2 单个 full-sample 评价单元

```python
@dataclass(frozen=True)
class SelectedGroupedUnit:
    data: pd.DataFrame | None
    assignments: pd.Series | None
    n_eligible: int
    n_factor_valid: int
    n_label_valid: int
    invalid_code: int | None
```

```python
def _select_and_group_one_unit(
    group: pd.DataFrame,
    n_groups: int,
    grouping_direction: Literal["ascending", "descending"],
    min_observations: int,
    nan_policy: Literal["drop", "strict"],
) -> SelectedGroupedUnit:
    """选择一个评价单元并生成分位数组归属。"""

    eligible = group.loc[group["universe"]].copy()
    n_eligible = len(eligible)

    if n_eligible == 0:
        return SelectedGroupedUnit(
            data=None,
            assignments=None,
            n_eligible=0,
            n_factor_valid=0,
            n_label_valid=0,
            invalid_code=9,
        )

    if nan_policy == "strict" and eligible[
        ["factor", "label"]
    ].isna().any(axis=None):
        return SelectedGroupedUnit(
            data=None,
            assignments=None,
            n_eligible=n_eligible,
            n_factor_valid=int(eligible["factor"].notna().sum()),
            n_label_valid=int(eligible["label"].notna().sum()),
            invalid_code=1,
        )

    factor_sample = eligible.loc[eligible["factor"].notna()]
    n_factor_valid = len(factor_sample)
    n_label_valid = int(factor_sample["label"].notna().sum())

    minimum = max(min_observations, n_groups)
    if n_factor_valid < minimum:
        return SelectedGroupedUnit(
            data=None,
            assignments=None,
            n_eligible=n_eligible,
            n_factor_valid=n_factor_valid,
            n_label_valid=n_label_valid,
            invalid_code=2,
        )

    if factor_sample["factor"].nunique() < n_groups:
        return SelectedGroupedUnit(
            data=None,
            assignments=None,
            n_eligible=n_eligible,
            n_factor_valid=n_factor_valid,
            n_label_valid=n_label_valid,
            invalid_code=3,
        )

    try:
        assignments = _assign_quantile_groups(
            factor_sample["factor"],
            n_groups=n_groups,
            grouping_direction=grouping_direction,
        )
    except ValueError:
        return SelectedGroupedUnit(
            data=None,
            assignments=None,
            n_eligible=n_eligible,
            n_factor_valid=n_factor_valid,
            n_label_valid=n_label_valid,
            invalid_code=4,
        )

    return SelectedGroupedUnit(
        data=factor_sample,
        assignments=assignments,
        n_eligible=n_eligible,
        n_factor_valid=n_factor_valid,
        n_label_valid=n_label_valid,
        invalid_code=None,
    )
```

生产实现可以抽取重复的无效结果构造，但不应删除这些业务分支。

### 17.3 公共函数骨架

```python
def grouped_forward_returns(
    df: pd.DataFrame,
    mode: Literal["cross_sectional", "time_series"],
    n_groups: int = 5,
    grouping_direction: Literal["ascending", "descending"] = "ascending",
    min_observations: int = 10,
    nan_policy: Literal["drop", "strict"] = "drop",
    ts_grouping_method: Literal[
        "full_sample",
        "expanding",
        "rolling",
    ] = "full_sample",
    rolling_window: int | None = None,
) -> GroupedEvaluationResults:
    """计算横截面或时序模式下的分组未来收益。"""
    ...
```

## 18. 输入验证

### 18.1 DataFrame

- 必须是 `pd.DataFrame`；
- 必须包含 `factor`、`label`、`universe`；
- 索引必须是 `MultiIndex[timestamp, product]`；
- 索引名称和顺序必须严格正确；
- 不能有重复索引；
- `factor` 和 `label` 必须是数值型；
- `universe` 必须是无缺失的布尔列；
- 正负无穷应在评价前拒绝或显式转为 NaN。

### 18.2 配置

```python
if mode not in {"cross_sectional", "time_series"}:
    raise ValueError(...)

if n_groups < 2:
    raise ValueError(...)

if min_observations < 1:
    raise ValueError(...)

if grouping_direction not in {"ascending", "descending"}:
    raise ValueError(...)

if nan_policy not in {"drop", "strict"}:
    raise ValueError(...)
```

### 18.3 不修改输入

公共函数入口应先：

```python
df = df.copy()
```

所有排序、增加 `factor_group` 列和重设索引的操作都只能作用于副本。

## 19. 与现有模型的衔接

### 19.1 `EvaluationConfig`

现有字段可以继续使用：

```text
mode
min_observations
n_groups
grouping_direction
nan_policy
```

时序 PIT 需要增加或由独立配置承载：

```text
ts_grouping_method
rolling_window
```

rolling window 必须进入序列化配置、实验哈希和结果元数据。

### 19.2 `FactorEvaluationResult`

当前只有：

```python
grouped_returns: pd.DataFrame | None
```

该字段不足以保存组归属、各组标签数、高低组差、PIT 配置和分组失败元数据。

进入统一 `FactorEvaluator` 开发时，建议将其改为：

```python
grouped_returns: GroupedEvaluationResults | None
```

或增加独立字段。使用结果 dataclass 更符合当前 `ic.py` 的实际风格。

## 20. 最小人工算例

### 20.1 横截面单调正向

一个 timestamp、10 个品种、分 5 组：

```text
factor = [1,2,3,4,5,6,7,8,9,10]
label  = [1,1,2,2,3,3,4,4,5,5]
```

ascending 分组下：

```text
group_1 mean = 1
group_2 mean = 2
group_3 mean = 3
group_4 mean = 4
group_5 mean = 5
high_minus_low = 4
```

### 20.2 `grouping_direction` 翻转

相同数据改为 descending：

```text
group_1 = 高因子组
group_5 = 低因子组
```

组号对应列发生翻转，但 `high_minus_low = 5 - 1 = 4`，不应因组号方向改变符号。

### 20.3 标签缺失不改变组归属

若最高因子品种的 label 为 NaN：

- 分组仍使用全部有效因子；
- 该品种仍属于最高因子组；
- 最高组均值只使用该组其他有效标签；
- `group_counts` 比 assigned count 少 1。

### 20.4 某组标签全缺失

- 该组收益为 NaN；
- 该组 count 为 0；
- 其他组继续保留；
- 若该组是高组或低组，则 `high_minus_low` 为 NaN。

### 20.5 ties 导致分组失败

```text
factor = [1,1,1,1,1,2,2,2,2,2]
n_groups = 5
```

不能按行顺序任意拆成 5 组。结果应为组归属 NA，并记录 `insufficient_unique_factor_values`。

### 20.6 `universe=False` 不触发 strict

```text
product C:
    factor = NaN
    label = NaN
    universe = False
```

C 不进入严格缺失检查，也不影响分组边界。

### 20.7 时序 full-sample

对单个 product 的 20 个历史观测分成 5 组，输出一行：

```text
index = product
columns = group_1 ... group_5
```

`high_minus_low` 是该品种历史高因子状态与低因子状态的条件收益差。

### 20.8 Expanding PIT

设：

```text
n_groups = 5
min_observations = 10
```

前 9 个合资格有效因子值均属于暖机期，组号为 NA。从第 10 个开始，每个当前组号只能由截至当前的历史决定。

修改第 15 个时点之后的因子值，不能改变第 10 至第 15 个时点已经形成的组号。

### 20.9 Rolling PIT

```text
ts_grouping_method = "rolling"
rolling_window = 60
```

第 100 个时点的组归属只允许使用第 41 至第 100 个时间行中的合资格有效因子。

## 21. pytest 测试清单

建议新增：

```text
tests/factors/evaluation/test_grouped.py
```

### 21.1 输入与配置

- 缺少必需列时抛出异常；
- 索引不是 `MultiIndex[timestamp, product]` 时抛出异常；
- 重复索引时抛出异常；
- `universe` 非布尔或包含 NA 时抛出异常；
- 非法 mode、direction、nan policy 和 group method 抛出异常；
- `n_groups < 2` 时抛出异常；
- rolling 未提供 window 时抛出异常；
- 非 rolling 模式提供 window 时抛出异常；
- rolling window 太小时抛出异常；
- 输入 DataFrame 未被修改。

### 21.2 横截面分组

- 每个 timestamp 独立分组；
- 可手算 5 组均值；
- 每组标签 count 正确；
- high-minus-low 可手算；
- descending 只翻转组号，不改变 high-minus-low；
- 不同 timestamp 的样本不相互影响；
- 单个 timestamp 样本不足返回失效码；
- ties 不使用行顺序拆分；
- 无法形成目标组数时只使当前 timestamp 失效。

### 21.3 时序 full-sample

- 每个 product 独立分组；
- 每个 product 输出自己的组均值；
- 每个 product 输出自己的 high-minus-low；
- 跨 product 汇总使用等权，不按历史长度加权；
- 一个 product 分组失败不影响其他 product。

### 21.4 PIT

- expanding 只使用截至当前的历史；
- rolling 只使用最近 W 个时间行；
- rolling 窗口内再应用 universe 和缺失过滤；
- 当前因子包含在历史分位数样本中；
- 暖机期组号为 NA；
- 修改未来因子不改变过去组号；
- 标签变化不改变任何组号；
- PIT 最终仍按 product 汇总组收益；
- PIT 不按 timestamp 跨品种重新形成主要分组结果；
- PIT 配置和窗口进入结果元数据。

### 21.5 缺失值

- `drop` 下因子缺失不形成组号；
- `drop` 下标签缺失不改变组号；
- 标签缺失不参与组均值；
- 某组标签 count 为 0 时组收益为 NaN；
- 不增加或依赖 `min_group_observations`；
- `strict` 下合资格行存在缺失使外层评价单元失效；
- `universe=False` 的缺失不触发 strict。

### 21.6 结果结构

- `group_assignments` 与输入索引完全一致；
- assignment dtype 为 nullable `Int64`；
- 组收益列严格为 `1..n_groups`；
- counts 和 returns 形状相同；
- 横截面结果索引为 timestamp；
- 时序结果索引为 product；
- high-minus-low 的索引与外层评价单元一致；
- 标签常数时结果合法且 high-minus-low 为 0。

测试从仓库根目录运行：

```powershell
python -m pytest tests/factors/evaluation/test_grouped.py -q
```

## 22. 开发时最容易犯的错误

1. 先删除标签缺失行，再计算因子分位数组。
2. 让不同标签 horizon 产生不同因子组归属。
3. 使用 `rank(method="first")` 任意拆开相同因子值。
4. 使用 `duplicates="drop"` 静默减少实际组数。
5. 因某组没有标签而把组收益填成 0。
6. 把 `group_n - group_1` 固定当成高因子减低因子，忽略 descending。
7. 改变 `grouping_direction` 后错误地翻转 high-minus-low 符号。
8. 把标签常数当作分组失败。
9. 把全部 product 历史 pooled 后作为时序汇总。
10. 在时序 PIT 分组后按 timestamp 跨品种重新形成基础评价结果。
11. 为 rolling window 设置研究者不知情的静默默认值。
12. rolling 使用最近 W 个有效值，导致实际时间跨度不受控制。
13. PIT 计算中使用未来因子值。
14. 使用标签决定 PIT 组边界。
15. 不保存 group assignments，迫使后续换手模块重新分组。
16. 将 horizon 大于 1 的重叠 forward returns 直接连乘成策略净值。
17. 把 `TS_HML_i` 当作逐期状态持仓收益。
18. 把 high-minus-low 命名为真实 `strategy_returns` 或 `portfolio_pnl`。
19. 原地向输入 DataFrame 添加分组列。

## 23. Step 8 完成标准

达到以下条件，才能认为分组收益初版开发完成：

- `grouped.py` 接收 `DataFrame[factor, label, universe]`；
- 横截面模式按 timestamp 跨 product 分组；
- 时序模式按 product 沿 timestamp 分组；
- 默认 5 组且组数可配置；
- 组边界只使用 universe 和 factor；
- 标签缺失不改变组归属；
- 默认 `nan_policy="drop"`；
- ties 不被任意拆分；
- 无法形成目标组数时当前评价单元返回 NA/NaN 和原因码；
- 输出各组平均未来收益和有效标签数；
- high-minus-low 始终表示高因子组减低因子组；
- changing direction 只改变组号，不改变价差经济方向；
- 不增加 `min_group_observations`；
- 保存与原面板对齐的 `group_assignments`；
- 时序 full-sample 被明确标记为 ex-post 描述性诊断；
- 时序 expanding 和 rolling PIT 不使用未来因子；
- rolling window 必须由用户显式输入；
- PIT 最终按 product 输出条件收益差，再跨 product 等权汇总；
- 不把 PIT 结果改成 timestamp 截面比较；
- 区分条件收益差和逐期策略持仓收益；
- 结果使用 `grouped_forward_returns`，不冒充真实策略 PnL；
- 输入不被修改；
- 所有核心统计量和边界行为都有可手算 pytest。

## 24. 与后续模块的边界

### 24.1 Step 9 换手

横截面最高组和最低组成员变化应直接使用本模块保存的 `group_assignments`。

### 24.2 衰减

不同 horizon 的 grouped forward returns 由 `FactorEvaluator` 分别计算，再由 diagnostics 汇总。分组模块自身一次只处理一个标签列。

### 24.3 稳健推断

high-minus-low 的 Newey-West、bootstrap 和多重检验属于 `robustness.py`，不在 `grouped.py` 重复实现。

### 24.4 诊断组合

如果研究目标是构造逐期状态仓位：

```text
高历史分位 → +1
低历史分位 → -1
中间分位   → 0
```

并进一步处理跨品种权重、gross normalization、风险缩放和成本代理，应进入 `DiagnosticPortfolioExperiment`。

Step 8 的核心原则可以概括为：

> 因子和 universe 决定组归属，标签只用于评价；横截面在 timestamp 内比较品种，时序在 product 内比较历史状态；PIT 改变历史分组信息集，但不改变评价轴；分组条件收益差是预测诊断，不自动等于真实策略收益。
