# 因子模块：因子评估 coverage、IC 与 Rank IC 开发帮助

> 本文服务于因子研究模块 V1 开发方案的 Step 7，目标是帮助开发 `FactorEvaluator` 的覆盖率、IC、Rank IC 和相关稳健统计组件。
>
> 本文不是完整的因子有效性判定规则，也不是策略回测说明。IC、Rank IC 和分组标签只属于预测能力诊断，不能解释为真实可交易收益。

## 1. 本阶段要解决什么问题

给定同一研究面板上的：

- 因子值 `factor_values`；
- 某个预测期限的标签 `labels`；
- 当期品种池 `universe`；
- 评价模式和最小样本数等配置；

Step 7 需要回答：

1. 因子和标签在合资格样本中分别覆盖多少观测？
2. 两者共同可用于评价的样本有多少？
3. 因子与未来标签是否存在线性预测关系？
4. 因子与未来标签是否存在单调排序关系？
5. 上述关系在不同 timestamp 或 product 上是否稳定？
6. IC 均值在存在时间相关性时是否仍具有统计证据？
7. 同时检验多个因子、参数和标签期限时，如何控制多重检验问题？

对应代码职责为：

```text
factors/evaluation/
    coverage.py       覆盖率、有效样本数量和直接可观察的缺失分解
    ic.py             cs_ic、cs_rank_ic、ts_ic、ts_rank_ic 及描述性汇总
    robustness.py     Newey-West、block bootstrap 和多重检验
    evaluator.py      后续统一编排上述组件
```

这与原始写作方案存在一处有意调整：覆盖率不再放入 `diagnostics.py`，而由 `coverage.py` 独立负责。`diagnostics.py` 后续保留给衰减、换手等跨结果诊断。

## 2. 已确定的开发口径

### 2.1 数据格式

因子、标签和布尔品种池都使用：

```text
pd.Series
index = MultiIndex[timestamp, product]
```

其中：

- `timestamp` 是因子在研究中可用的时点；
- `product` 是规范化后的期货品种代码；
- 因子值 $f_{t,i}$ 与标签 $y_{t,i}^{(h)}$ 共享同一个 `(timestamp, product)`；
- 标签索引为 $t$，其值表示 $t$ 之后未来 $h$ 期的结果。

因此评价阶段不能再次对标签执行 `shift`。未来标签的时间方向由 `LabelBuilder` 保证。

### 2.2 品种池接口

公共接口允许：

```python
from typing import Literal

import pandas as pd


type UniverseInput = pd.Series | Literal["all"]
```

默认值为：

```python
universe: UniverseInput = "all"
```

`"all"` 的准确含义是：

> 当前输入研究面板中的所有 `(timestamp, product)` 行均为合资格样本，不额外应用品种池筛选。

它不表示查询数据库中的所有期货品种，不补齐缺失品种，也不构造完整的 timestamp × product 笛卡尔积。

进入统计计算前，应将 `"all"` 统一规范化为布尔 Series：

```python
def normalize_universe(
    universe: UniverseInput,
    index: pd.MultiIndex,
) -> pd.Series:
    """将品种池参数规范化为与研究面板对齐的布尔掩码。"""

    if isinstance(universe, str):
        if universe != "all":
            raise ValueError(
                f"universe 字符串只允许为 'all'，实际为 {universe!r}"
            )
        return pd.Series(True, index=index, name="universe", dtype=bool)

    if not isinstance(universe, pd.Series):
        raise TypeError("universe 必须是布尔 Series 或字符串 'all'")

    if not universe.index.equals(index):
        raise ValueError("universe 的索引必须与研究面板索引完全一致")

    if universe.isna().any():
        raise ValueError("universe 不允许包含缺失值")

    if not pd.api.types.is_bool_dtype(universe.dtype):
        raise TypeError("universe 必须是布尔 Series")

    return universe.rename("universe")
```

### 2.3 缺失值政策

`nan_policy` 支持：

```python
Literal["drop", "strict"]
```

默认使用：

```python
nan_policy = "drop"
```

两种政策的定义为：

- `drop`：在每个评价组内，仅使用因子和标签同时有效的成对样本；删除后仍须满足 `min_observations`。
- `strict`：合资格样本内只要存在因子或标签缺失，该组指标就返回 NaN，并记录原因。

必须先应用 `universe`，再应用 `nan_policy`。`universe=False` 表示不属于研究样本，不是缺失观测，不能触发 `strict`。

### 2.4 指标命名

使用对称且显式的命名：

| 评价模式 | Pearson | Rank IC |
|---|---|---|
| 横截面 | `cs_ic` | `cs_rank_ic` |
| 时序 | `ts_ic` | `ts_rank_ic` |

虽然统一使用 IC 命名，仍必须在文档和结果元数据中保留统计轴：

- `cs_ic`：每个 timestamp 跨 product 计算；
- `ts_ic`：每个 product 沿 timestamp 计算。

二者不是可以任意互换的同一个统计量。

### 2.5 ICIR 命名

非年化和年化结果分开：

```text
icir
annualized_icir
```

一个字段不能根据配置悄悄改变是否年化的语义。

## 3. 评价样本与执行顺序

### 3.1 三个基础掩码

设研究面板索引集合为 $\mathcal I$。对任意 $(t,i)\in\mathcal I$，定义：

$$
E_{t,i}=\mathbf 1\{\text{universe}_{t,i}=\text{True}\},
$$

$$
F_{t,i}=\mathbf 1\{f_{t,i}\text{ 有效}\},
$$

$$
L_{t,i}=\mathbf 1\{y_{t,i}^{(h)}\text{ 有效}\}.
$$

联合有效掩码为：

$$
J_{t,i}=E_{t,i}\land F_{t,i}\land L_{t,i}.
$$

关键代码为：

```python
eligible = normalized_universe
factor_valid = factor_values.notna()
label_valid = labels.notna()
joint_valid = eligible & factor_valid & label_valid
```

### 3.2 固定执行顺序

所有 coverage 和 IC 计算都应遵循：

```text
校验输入索引
    ↓
规范化 universe
    ↓
确定合资格样本
    ↓
统计缺失与覆盖率
    ↓
应用 drop 或 strict
    ↓
检查 min_observations
    ↓
检查常数因子/常数标签
    ↓
计算 Pearson IC 和 Rank IC
    ↓
汇总、稳健推断和警告
```

### 3.3 为什么不能先做 inner join

覆盖率计算前不能使用下面的写法缩小索引：

```python
# 错误示意：可能直接隐藏某一侧整行缺失的坐标。
paired = pd.concat(
    [factor_values, labels],
    axis=1,
    join="inner",
)
```

例如标签缺少某个 `(timestamp, product)` 行时，inner join 会把该行直接删除，使标签覆盖率和联合覆盖率被高估。

V1 建议要求：

```python
factor_values.index.equals(labels.index)
```

不满足时报告输入契约错误，而不是静默修复。因子定义和标签构造都应保持研究主面板索引。

## 4. 覆盖率

### 4.1 覆盖率的作用

覆盖率回答的是“后续统计量实际代表哪些样本”。它不能证明因子有效，但可以揭示：

- 因子是否只在少数品种上可计算；
- 标签是否因为预测期限而在样本尾部大量缺失；
- 动态品种池是否显著改变评价样本；
- IC 是否由很小或高度集中的样本支持；
- 不同 timestamp 和 product 的研究证据是否可比。

只报告一个总体百分比是不够的。V1 至少输出总体、分 timestamp 和分 product 三种粒度。

### 4.2 合资格样本数

对任意统计范围 $G$，合资格样本数为：

$$
N_{eligible}(G)=\sum_{(t,i)\in G}E_{t,i}.
$$

它是所有覆盖率的分母。

关键代码：

```python
eligible_count = int(group["universe"].sum())
```

### 4.3 因子覆盖率

定义：

$$
C_f(G)=
\frac{\sum_{(t,i)\in G}E_{t,i}F_{t,i}}
{N_{eligible}(G)}.
$$

意义：衡量合资格样本中因子能够产生有效值的比例，主要反映因子暖机、输入缺失和计算可用性。

关键代码：

```python
factor_valid_count = int(
    (group["universe"] & group["factor"].notna()).sum()
)
factor_coverage = (
    factor_valid_count / eligible_count
    if eligible_count > 0
    else np.nan
)
```

### 4.4 标签覆盖率

定义：

$$
C_y(G)=
\frac{\sum_{(t,i)\in G}E_{t,i}L_{t,i}}
{N_{eligible}(G)}.
$$

意义：衡量合资格样本中未来标签可用的比例。预测期限为 $h$ 时，品种样本末端的标签可用性由 `horizon` 和 `min_valid_obs` 共同决定，因此标签覆盖率必须与这两个参数一起解释。

关键代码：

```python
label_valid_count = int(
    (group["universe"] & group["label"].notna()).sum()
)
label_coverage = (
    label_valid_count / eligible_count
    if eligible_count > 0
    else np.nan
)
```

### 4.5 联合覆盖率

定义：

$$
C_{joint}(G)=
\frac{\sum_{(t,i)\in G}J_{t,i}}
{N_{eligible}(G)}.
$$

意义：这是最终可以进入因子—标签配对评价的样本比例，也是 `FactorEvaluationResult.coverage` 最核心的覆盖率。

关键代码：

```python
joint_valid_count = int(
    (
        group["universe"]
        & group["factor"].notna()
        & group["label"].notna()
    ).sum()
)
joint_coverage = (
    joint_valid_count / eligible_count
    if eligible_count > 0
    else np.nan
)
```

### 4.6 可由评价模块独立完成的缺失分解

评价模块仅凭最终因子、标签和 `universe`，可以准确分解：

#### 仅因子缺失

$$
N_{factor\_only}(G)
=\sum_G E_{t,i}(1-F_{t,i})L_{t,i}.
$$

```python
factor_only_missing = (
    group["universe"]
    & group["factor"].isna()
    & group["label"].notna()
).sum()
```

#### 仅标签缺失

$$
N_{label\_only}(G)
=\sum_G E_{t,i}F_{t,i}(1-L_{t,i}).
$$

```python
label_only_missing = (
    group["universe"]
    & group["factor"].notna()
    & group["label"].isna()
).sum()
```

#### 因子和标签同时缺失

$$
N_{both}(G)
=\sum_G E_{t,i}(1-F_{t,i})(1-L_{t,i}).
$$

```python
both_missing = (
    group["universe"]
    & group["factor"].isna()
    & group["label"].isna()
).sum()
```

三类计数应满足：

$$
N_{eligible}
=N_{joint}
+N_{factor\_only}
+N_{label\_only}
+N_{both}.
$$

这条恒等式应成为单元测试。

品种池外样本数也可以直接观察：

$$
N_{out\_of\_universe}(G)
=\sum_{(t,i)\in G}(1-E_{t,i}).
$$

```python
out_of_universe_count = int((~group["universe"]).sum())
```

它不进入覆盖率分母，也不归类为因子或标签缺失。整个输入范围还应满足：

$$
|G|=N_{eligible}(G)+N_{out\_of\_universe}(G).
$$

### 4.7 依赖上游 data lineage 的缺失原因

评价模块无法仅凭最终 NaN 准确判断因子缺失来自：

- 原始行情缺失；
- 因子暖机期；
- 因子公式自身不可定义；
- 预处理引入缺失；
- 标签 horizon 不完整；
- 其他上游阶段。

这些属于依赖上游 `data lineage` 的跨阶段缺失原因归因。V1 不反向猜测原因，只实现前一节能够局部闭合的缺失分解。

未来可预留通用扩展参数：

```python
from collections.abc import Mapping


reason_masks: Mapping[str, pd.Series] | None = None
```

例如：

```python
reason_masks = {
    "factor_warmup": factor_warmup_mask,
    "raw_data_missing": raw_data_missing_mask,
    "label_horizon_incomplete": label_horizon_incomplete_mask,
}
```

扩展接口只接收通用布尔掩码，不依赖因子、预处理或标签组件的内部类型。若原因掩码允许重叠，其计数不能解释为互斥归因；未来若要求严格加总，需要再定义原因优先级或单一分类字段。

### 4.8 覆盖率核心代码

```python
from __future__ import annotations

import numpy as np
import pandas as pd


def _coverage_stats(group: pd.DataFrame) -> pd.Series:
    """计算一个统计范围内的覆盖率和缺失分解。"""

    eligible = group["universe"]
    factor_valid = group["factor"].notna()
    label_valid = group["label"].notna()

    eligible_count = int(eligible.sum())
    out_of_universe_count = int((~eligible).sum())
    factor_valid_count = int((eligible & factor_valid).sum())
    label_valid_count = int((eligible & label_valid).sum())
    joint_valid_count = int(
        (eligible & factor_valid & label_valid).sum()
    )

    factor_only_missing_count = int(
        (eligible & ~factor_valid & label_valid).sum()
    )
    label_only_missing_count = int(
        (eligible & factor_valid & ~label_valid).sum()
    )
    both_missing_count = int(
        (eligible & ~factor_valid & ~label_valid).sum()
    )

    def rate(numerator: int) -> float:
        if eligible_count == 0:
            return np.nan
        return numerator / eligible_count

    return pd.Series({
        "eligible_count": eligible_count,
        "out_of_universe_count": out_of_universe_count,
        "factor_valid_count": factor_valid_count,
        "label_valid_count": label_valid_count,
        "joint_valid_count": joint_valid_count,
        "factor_coverage": rate(factor_valid_count),
        "label_coverage": rate(label_valid_count),
        "joint_coverage": rate(joint_valid_count),
        "factor_only_missing_count": factor_only_missing_count,
        "label_only_missing_count": label_only_missing_count,
        "both_missing_count": both_missing_count,
    })
```

总体、分 timestamp 和分 product 复用同一函数：

```python
overall = _coverage_stats(frame).to_frame().T

by_timestamp = frame.groupby(
    level="timestamp",
    sort=True,
).apply(_coverage_stats)

by_product = frame.groupby(
    level="product",
    sort=True,
).apply(_coverage_stats)
```

总体覆盖率使用总体计数相除，不是“各 timestamp 覆盖率的简单平均”。当不同 timestamp 的合资格品种数不同时，两种结果并不相等。分 timestamp 和分 product 表负责展示局部分布，总体表负责回答整个联合样本的 pooled 覆盖率。

### 4.9 建议输出字段

如果仍使用一个 `pd.DataFrame` 承载 coverage，建议采用长表：

| 字段 | 含义 |
|---|---|
| `scope` | `overall`、`timestamp` 或 `product` |
| `timestamp` | 分 timestamp 行的键，其他范围为 NaT |
| `product` | 分 product 行的键，其他范围为空 |
| `eligible_count` | 合资格样本数 |
| `out_of_universe_count` | 品种池外样本数，不进入覆盖率分母 |
| `factor_valid_count` | 有效因子数 |
| `label_valid_count` | 有效标签数 |
| `joint_valid_count` | 联合有效数 |
| `factor_coverage` | 因子覆盖率 |
| `label_coverage` | 标签覆盖率 |
| `joint_coverage` | 联合覆盖率 |
| `factor_only_missing_count` | 仅因子缺失数 |
| `label_only_missing_count` | 仅标签缺失数 |
| `both_missing_count` | 两者同时缺失数 |

分母为零时，覆盖率必须返回 NaN，而不是 0，并记录 `no_eligible_observations`。

## 5. Pearson IC

### 5.1 Pearson 相关的定义

对成对样本 $(x_k,y_k), k=1,\ldots,n$，Pearson 相关系数为：

$$
\rho_{x,y}
=
\frac{
\sum_{k=1}^{n}(x_k-\bar x)(y_k-\bar y)
}{
\sqrt{
\sum_{k=1}^{n}(x_k-\bar x)^2
\sum_{k=1}^{n}(y_k-\bar y)^2
}
}.
$$

它衡量标准化后的线性共同变化：

- 接近 1：强正线性关系；
- 接近 -1：强负线性关系；
- 接近 0：没有明显线性关系，但不代表没有非线性关系。

Pearson IC 不表示因果关系，也不包含仓位映射、风险预算、换手和交易成本。

### 5.2 横截面 IC：`cs_ic`

对每个 timestamp $t$，定义合资格且联合有效的品种集合：

$$
S_t=
\left\{
i\mid E_{t,i}=1,\ F_{t,i}=1,\ L_{t,i}=1
\right\}.
$$

横截面 IC 为：

$$
cs\_ic_t
=
\operatorname{Corr}_{i\in S_t}
\left(f_{t,i},y_{t,i}^{(h)}\right).
$$

它回答：

> 在同一个时点，因子值较高的品种，其未来标签是否也倾向于更高？

明细结果按 timestamp 索引。`min_observations` 表示每个 timestamp 至少需要多少个联合有效品种。

关键分组代码：

```python
cs_detail = frame.groupby(
    level="timestamp",
    sort=True,
).apply(
    _calculate_one_group,
    min_observations=min_observations,
    nan_policy=nan_policy,
)
```

单品种截面无法形成横截面相关。即使时间序列很长，只要每个 timestamp 的有效品种数不足，`cs_ic` 仍必须是 NaN，不能用时间维度补足横截面样本。

### 5.3 时序 IC：`ts_ic`

对每个 product $i$，定义合资格且联合有效的时间集合：

$$
T_i=
\left\{
t\mid E_{t,i}=1,\ F_{t,i}=1,\ L_{t,i}=1
\right\}.
$$

时序 IC 为：

$$
ts\_ic_i
=
\operatorname{Corr}_{t\in T_i}
\left(f_{t,i},y_{t,i}^{(h)}\right).
$$

它回答：

> 对同一个品种，该品种自身因子值较高时，其未来标签是否也倾向于更高？

明细结果按 product 索引。`min_observations` 表示每个 product 至少需要多少个联合有效时间观测。

关键分组代码：

```python
ts_detail = frame.groupby(
    level="product",
    sort=True,
).apply(
    _calculate_one_group,
    min_observations=min_observations,
    nan_policy=nan_policy,
)
```

只有一个 timestamp 的品种无法形成时序相关。跨品种数量不能替代单个品种的时间样本。

### 5.4 `drop` 和 `strict` 的组内行为

```python
def _select_group_sample(
    group: pd.DataFrame,
    nan_policy: str,
) -> tuple[pd.DataFrame | None, str | None]:
    """按 universe 和缺失值政策选择一个评价组的配对样本。"""

    eligible = group.loc[group["universe"], ["factor", "label"]]

    if eligible.empty:
        return None, "no_eligible_observations"

    if nan_policy == "strict" and eligible.isna().any(axis=None):
        return None, "missing_observations_strict"

    if nan_policy != "drop" and nan_policy != "strict":
        raise ValueError(f"不支持的 nan_policy: {nan_policy!r}")

    return eligible.dropna(subset=["factor", "label"]), None
```

无论哪种政策，都应同时输出：

```text
n_eligible
n_valid
n_dropped
```

### 5.5 常数因子和常数标签

如果有效样本中的因子或标签没有方差，Pearson 相关分母为零，相关系数未定义。

必须返回 NaN 和原因，不能返回 0：

```python
if valid["factor"].nunique(dropna=True) < 2:
    reason = "constant_factor"
    cs_ic_or_ts_ic = np.nan

if valid["label"].nunique(dropna=True) < 2:
    reason = "constant_label"
    cs_ic_or_ts_ic = np.nan
```

0 表示“相关系数被成功估计且接近零”；NaN 表示“该组无法定义相关系数”。两者不能混用。

### 5.6 Pearson IC 的关键计算代码

```python
pearson_ic = valid["factor"].corr(
    valid["label"],
    method="pearson",
)
```

调用前必须完成 universe、缺失值、样本数量和常数检查。

## 6. Rank IC

### 6.1 定义

Rank IC 是因子秩和标签秩的 Pearson 相关。对一个有效组，定义：

$$
R^f_k=\operatorname{rank}(f_k),
\qquad
R^y_k=\operatorname{rank}(y_k),
$$

则：

$$
RankIC=\operatorname{Corr}(R^f,R^y).
$$

项目中显式采用“先在同组内 rank，再计算 Pearson”的实现，不依赖隐式默认行为。

### 6.2 横截面 Rank IC：`cs_rank_ic`

$$
cs\_rank\_ic_t
=
\operatorname{Corr}_{i\in S_t}
\left(
\operatorname{rank}_i(f_{t,i}),
\operatorname{rank}_i(y_{t,i}^{(h)})
\right).
$$

它回答：

> 在同一 timestamp，因子对品种未来标签的排序是否具有单调一致性？

它对极端数值的具体距离不如 Pearson IC 敏感，但会丢失因子强度间距信息。

### 6.3 时序 Rank IC：`ts_rank_ic`

$$
ts\_rank\_ic_i
=
\operatorname{Corr}_{t\in T_i}
\left(
\operatorname{rank}_t(f_{t,i}),
\operatorname{rank}_t(y_{t,i}^{(h)})
\right).
$$

它回答：

> 对同一个 product，因子在自身历史中的相对高低，是否与未来标签的相对高低单调对应？

### 6.4 重复值政策

rank 必须明确使用平均秩：

```python
factor_rank = valid["factor"].rank(method="average")
label_rank = valid["label"].rank(method="average")
```

例如数值 `[1, 2, 2, 4]` 的秩为 `[1, 2.5, 2.5, 4]`。

重复值本身不是错误；只有整组因子或标签为常数时，Rank IC 才未定义。

### 6.5 Rank IC 的关键计算代码

```python
factor_rank = valid["factor"].rank(method="average")
label_rank = valid["label"].rank(method="average")

rank_ic = factor_rank.corr(
    label_rank,
    method="pearson",
)
```

Rank IC 和 `pd.Series.corr(method="spearman")` 在常规样本中等价，但显式 rank 后再做 Pearson 更直接地实现了项目写作方案，也使重复值政策可见。

## 7. IC 明细的统一实现

### 7.1 单组计算函数

下面代码同时定义 Pearson IC 和 Rank IC 的核心行为：

```python
from __future__ import annotations

import numpy as np
import pandas as pd


def _calculate_one_group(
    group: pd.DataFrame,
    min_observations: int,
    nan_policy: str,
) -> pd.Series:
    """计算一个 timestamp 截面或一个 product 时序的 IC。"""

    if nan_policy not in {"drop", "strict"}:
        raise ValueError(f"不支持的 nan_policy: {nan_policy!r}")

    eligible = group.loc[group["universe"], ["factor", "label"]]
    n_eligible = len(eligible)

    if n_eligible == 0:
        return pd.Series({
            "pearson": np.nan,
            "rank": np.nan,
            "n_eligible": 0,
            "n_valid": 0,
            "n_dropped": 0,
            "reason": "no_eligible_observations",
        })

    has_missing = eligible.isna().any(axis=None)
    valid = eligible.dropna(subset=["factor", "label"])
    n_valid = len(valid)

    if nan_policy == "strict" and has_missing:
        return pd.Series({
            "pearson": np.nan,
            "rank": np.nan,
            "n_eligible": n_eligible,
            "n_valid": n_valid,
            "n_dropped": n_eligible - n_valid,
            "reason": "missing_observations_strict",
        })

    if n_valid < min_observations:
        return pd.Series({
            "pearson": np.nan,
            "rank": np.nan,
            "n_eligible": n_eligible,
            "n_valid": n_valid,
            "n_dropped": n_eligible - n_valid,
            "reason": "insufficient_observations",
        })

    if valid["factor"].nunique(dropna=True) < 2:
        return pd.Series({
            "pearson": np.nan,
            "rank": np.nan,
            "n_eligible": n_eligible,
            "n_valid": n_valid,
            "n_dropped": n_eligible - n_valid,
            "reason": "constant_factor",
        })

    if valid["label"].nunique(dropna=True) < 2:
        return pd.Series({
            "pearson": np.nan,
            "rank": np.nan,
            "n_eligible": n_eligible,
            "n_valid": n_valid,
            "n_dropped": n_eligible - n_valid,
            "reason": "constant_label",
        })

    pearson = valid["factor"].corr(
        valid["label"],
        method="pearson",
    )

    factor_rank = valid["factor"].rank(method="average")
    label_rank = valid["label"].rank(method="average")
    rank = factor_rank.corr(label_rank, method="pearson")

    return pd.Series({
        "pearson": pearson,
        "rank": rank,
        "n_eligible": n_eligible,
        "n_valid": n_valid,
        "n_dropped": n_eligible - n_valid,
        "reason": None,
    })
```

生产实现应减少重复构造 `pd.Series` 的代码，但不应压缩掉这些边界分支。

### 7.2 模式分派和最终列名

```python
if mode == "cross_sectional":
    detail = frame.groupby(level="timestamp", sort=True).apply(
        _calculate_one_group,
        min_observations=min_observations,
        nan_policy=nan_policy,
    )
    detail = detail.rename(columns={
        "pearson": "cs_ic",
        "rank": "cs_rank_ic",
    })

elif mode == "time_series":
    detail = frame.groupby(level="product", sort=True).apply(
        _calculate_one_group,
        min_observations=min_observations,
        nan_policy=nan_policy,
    )
    detail = detail.rename(columns={
        "pearson": "ts_ic",
        "rank": "ts_rank_ic",
    })

else:
    raise ValueError(f"不支持的评价模式: {mode!r}")
```

建议明细列为：

```text
cs_ic 或 ts_ic
cs_rank_ic 或 ts_rank_ic
n_eligible
n_valid
n_dropped
reason
```

## 8. IC 汇总统计

### 8.1 均值

对有效 IC 组 $z_g$，其中 $g$ 是 timestamp 或 product：

$$
\bar z=\frac{1}{G}\sum_{g=1}^{G}z_g.
$$

意义：描述多个评价组中的平均相关方向和强度。

V1 对评价组使用等权汇总：横截面模式下每个有效 timestamp 权重相同，时序模式下每个有效 product 权重相同。不能因为某个组的 `n_valid` 更大就隐式给予更高权重；若以后需要样本数加权汇总，应使用不同的指标名称并记录权重定义。

```python
valid_ic = detail[metric_name].dropna()
mean_ic = valid_ic.mean()
```

### 8.2 标准差

使用样本标准差：

$$
s_z=
\sqrt{
\frac{1}{G-1}
\sum_{g=1}^{G}(z_g-\bar z)^2
}.
$$

```python
std_ic = valid_ic.std(ddof=1)
```

意义：描述 IC 在不同 timestamp 或 product 之间的离散程度。它不是均值估计的标准误。

### 8.3 ICIR

对横截面 IC 时间序列：

$$
ICIR=\frac{\overline{cs\_ic}}{s_{cs\_ic}}.
$$

```python
icir = mean_ic / std_ic if std_ic > 0 else np.nan
```

意义：衡量横截面 IC 均值相对于逐期波动的稳定性。

ICIR 不是 t 统计量：

- ICIR 分母是逐期 IC 的标准差；
- t 统计量分母是均值估计的标准误；
- 存在序列相关时，标准误还必须使用稳健估计。

### 8.4 年化 ICIR

若评价频率的年化因子为 $A$：

$$
Annualized\ ICIR=\sqrt A\times ICIR.
$$

```python
annualized_icir = (
    np.sqrt(annualization_factor) * icir
    if annualization_factor is not None
    else np.nan
)
```

`icir` 始终保存非年化值；`annualized_icir` 单独保存年化值。

重叠的 $h$ 期标签会使相邻 IC 序列相关，因此简单乘以 $\sqrt A$ 只是描述性缩放，不能替代 Newey-West 或 block bootstrap 显著性推断。

### 8.5 正值比例

$$
PositiveRatio=
\frac{\sum_{g=1}^{G}\mathbf 1\{z_g>0\}}{G}.
$$

```python
positive_ratio = (valid_ic > 0).mean()
```

只有严格大于 0 的 IC 计为正值；等于 0 不计入正值。分母只包含有效 IC，不包含 NaN。

意义：描述预测方向在多少评价组内为正，避免均值完全由少数极端组驱动。

### 8.6 有效组数

```python
n_valid_groups = int(valid_ic.size)
```

横截面模式中应同时命名为 `n_valid_periods`；时序模式中应同时命名为 `n_valid_products`，或者在结果元数据中记录 `group_axis`。

### 8.7 为什么 `ts_ic` 不计算 ICIR

`ts_ic_i` 是每个 product 沿时间得到的一个相关系数。所有 `ts_ic_i` 的均值和标准差描述的是跨品种分布，而不是一个按时间排列的 IC 序列。

因此 V1 对 `ts_ic` 和 `ts_rank_ic` 输出：

- 跨品种均值；
- 跨品种标准差；
- 正值品种比例；
- 有效品种数。

但不把“跨品种均值 ÷ 跨品种标准差”命名为 ICIR，也不使用 $\sqrt{252}$ 年化。未来若实现 rolling `ts_ic`，才可针对每个品种的滚动 IC 时间序列讨论 ICIR。

### 8.8 汇总代码

```python
def summarize_ic(
    detail: pd.DataFrame,
    metric_name: str,
    mode: str,
    annualization_factor: int | None,
) -> dict[str, float | int]:
    """汇总一个 IC 指标。"""

    values = detail[metric_name].dropna()
    n_valid_groups = int(values.size)

    mean = values.mean() if n_valid_groups > 0 else np.nan
    std = values.std(ddof=1) if n_valid_groups > 1 else np.nan
    positive_ratio = (
        float((values > 0).mean())
        if n_valid_groups > 0
        else np.nan
    )

    summary: dict[str, float | int] = {
        "mean": mean,
        "std": std,
        "positive_ratio": positive_ratio,
        "n_valid_groups": n_valid_groups,
    }

    if mode == "cross_sectional":
        icir = mean / std if std > 0 else np.nan
        annualized_icir = (
            np.sqrt(annualization_factor) * icir
            if annualization_factor is not None
            else np.nan
        )
        summary["icir"] = icir
        summary["annualized_icir"] = annualized_icir

    return summary
```

`cs_ic` 和 `cs_rank_ic` 分别调用；`ts_ic` 和 `ts_rank_ic` 也分别调用，不能混为一个统计量。

## 9. Newey-West 稳健标准误

### 9.1 为什么普通标准误不够

若横截面 IC 明细为 $z_t$，普通均值标准误隐含各期独立同分布。但因子值通常具有持续性，多期标签还会重叠，使 $z_t$ 存在序列相关。

例如 $h$ 期未来收益标签：

$$
y_t^{(h)}=\prod_{j=1}^{h}(1+r_{t+j})-1,
$$

$y_t^{(h)}$ 与 $y_{t+1}^{(h)}$ 共享未来收益区间，因而相邻 IC 通常不独立。

### 9.2 估计对象

把 IC 序列写成只有截距的模型：

$$
z_t=\mu+\varepsilon_t.
$$

截距估计 $\hat\mu$ 就是平均 IC。Newey-West 使用滞后 $L$ 内的自协方差修正 $\hat\mu$ 的标准误。

Bartlett 权重下，长期方差近似为：

$$
\widehat\Omega
=\hat\gamma_0
+2\sum_{\ell=1}^{L}
\left(1-\frac{\ell}{L+1}\right)
\hat\gamma_\ell.
$$

均值的稳健标准误为：

$$
SE_{NW}(\hat\mu)=\sqrt{\frac{\widehat\Omega}{T}}.
$$

### 9.3 意义

Newey-West 回答的是：

> 考虑 IC 序列在有限滞后内的异方差和自相关后，平均 IC 的估计不确定性有多大？

它不改变 IC 均值，只改变标准误、t 值和 p-value。

### 9.4 关键代码

项目现有环境已使用 `statsmodels`，可采用截距回归：

```python
import numpy as np
import pandas as pd
import statsmodels.api as sm


def newey_west_mean_test(
    values: pd.Series,
    max_lags: int,
) -> dict[str, float | int]:
    """检验时间序列均值，使用 Newey-West/HAC 标准误。"""

    sample = values.dropna().to_numpy(dtype=float)
    if sample.size < 2:
        return {
            "mean": np.nan,
            "nw_standard_error": np.nan,
            "nw_t_stat": np.nan,
            "raw_pvalue": np.nan,
            "n_observations": int(sample.size),
        }

    if max_lags < 0 or max_lags >= sample.size:
        raise ValueError("max_lags 必须满足 0 <= max_lags < 样本数")

    design = np.ones((sample.size, 1), dtype=float)
    result = sm.OLS(sample, design).fit(
        cov_type="HAC",
        cov_kwds={
            "maxlags": max_lags,
            "use_correction": True,
        },
        use_t=False,
    )

    return {
        "mean": float(result.params[0]),
        "nw_standard_error": float(result.bse[0]),
        "nw_t_stat": float(result.tvalues[0]),
        "raw_pvalue": float(result.pvalues[0]),
        "n_observations": int(sample.size),
    }
```

### 9.5 `max_lags` 配置

`max_lags` 不能被隐藏在函数内部。至少记录：

```text
nw_max_lags
label_horizon
evaluation_frequency
```

对重叠 $h$ 期标签，`h - 1` 是必须重点覆盖的依赖范围，但实际因子和市场状态可能产生更长的相关性。V1 可以将默认值设为至少 `h - 1`，同时允许实验配置显式覆盖。

### 9.6 模式适用性

Newey-West 均值检验直接适用于按 timestamp 排列的 `cs_ic` 和 `cs_rank_ic`。

不能把按 product 排列的 `ts_ic` 向量当作时间序列运行 Newey-West。若要对单个 product 的时序预测关系做稳健推断，可使用：

- 对该 product 的配对时间观测做 block bootstrap，并在每次抽样中重新计算相关；
- 或建立带 HAC 标准误的时序预测回归。

后者的回归系数检验与相关系数检验相关，但不是把 `ts_ic` 标量直接放进 Newey-West 均值检验。

## 10. Block bootstrap 置信区间

### 10.1 定义和作用

普通 bootstrap 独立抽取单个 timestamp，会破坏 IC 序列的时间依赖。Block bootstrap 每次抽取连续时间块，在块内保留相邻观测结构。

设有效 IC 序列长度为 $T$，块长度为 $b$。每次重复：

1. 随机抽取若干长度为 $b$ 的连续块；
2. 拼接并截断到长度 $T$；
3. 计算重采样序列的平均 IC；
4. 重复 $B$ 次得到 $\hat\mu_1^*,\ldots,\hat\mu_B^*$；
5. 用经验分位数构造置信区间。

置信水平 $1-\alpha$ 的 percentile 区间为：

$$
\left[
Q_{\alpha/2}(\hat\mu^*),
Q_{1-\alpha/2}(\hat\mu^*)
\right].
$$

### 10.2 关键代码

下面使用 circular moving blocks，使序列尾部也能成为完整块起点：

```python
import math

import numpy as np
import pandas as pd


def block_bootstrap_mean_ci(
    values: pd.Series,
    block_length: int,
    n_resamples: int = 2_000,
    confidence_level: float = 0.95,
    random_seed: int = 0,
) -> dict[str, float | int]:
    """用循环块 bootstrap 估计时间序列均值的置信区间。"""

    sample = values.dropna().to_numpy(dtype=float)
    n_observations = sample.size

    if n_observations == 0:
        return {
            "bootstrap_ci_lower": np.nan,
            "bootstrap_ci_upper": np.nan,
            "n_observations": 0,
        }

    if not 1 <= block_length <= n_observations:
        raise ValueError("block_length 必须位于 [1, 样本数] 内")
    if n_resamples <= 0:
        raise ValueError("n_resamples 必须为正整数")
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level 必须位于 (0, 1) 内")

    rng = np.random.default_rng(random_seed)
    n_blocks = math.ceil(n_observations / block_length)
    bootstrap_means = np.empty(n_resamples, dtype=float)

    offsets = np.arange(block_length)
    for sample_number in range(n_resamples):
        starts = rng.integers(
            0,
            n_observations,
            size=n_blocks,
        )
        positions = (
            starts[:, None] + offsets[None, :]
        ) % n_observations
        resampled = sample[positions.ravel()[:n_observations]]
        bootstrap_means[sample_number] = resampled.mean()

    alpha = 1.0 - confidence_level
    lower, upper = np.quantile(
        bootstrap_means,
        [alpha / 2.0, 1.0 - alpha / 2.0],
    )

    return {
        "bootstrap_ci_lower": float(lower),
        "bootstrap_ci_upper": float(upper),
        "n_observations": int(n_observations),
        "block_length": block_length,
        "n_resamples": n_resamples,
        "confidence_level": confidence_level,
        "random_seed": random_seed,
    }
```

### 10.3 块长度和可复现性

必须记录：

```text
bootstrap_method
block_length
n_resamples
confidence_level
random_seed
```

块太短会破坏依赖，块太长会减少有效重采样变化。对重叠 $h$ 期标签，默认块长度不应短于 $h$；若需要一个 V1 启发式默认值，可以使用：

```python
block_length = max(label_horizon, math.ceil(n_observations ** (1 / 3)))
block_length = min(block_length, n_observations)
```

这只是可复现的工程默认值，不是对所有因子都最优的统计选择，因此必须允许配置覆盖。

### 10.4 时序模式的 bootstrap

对 `ts_ic_i`，不能 bootstrap 已经计算完成的单个相关系数。应对 product $i$ 的有序 `(factor, label)` 配对行按时间块重采样，并在每次重采样后重新计算 Pearson 或 Rank IC。

这是未来可以加入 `robustness.py` 的模式专属路径。初版若只实现横截面 IC 序列的 block bootstrap，应在结果元数据中明确 `supported_mode="cross_sectional"`，不能静默套用到 `ts_ic`。

## 11. 多重检验校正

### 11.1 为什么需要校正

若同时尝试多个：

- 因子；
- 参数组合；
- 标签期限；
- 预处理配置；
- IC 指标；

即使所有因子实际上都没有预测力，也会因为检验数量增加而出现偶然很小的 p-value。

多重检验校正不改变 IC 本身，而是调整“看到至少若干偶然显著结果”的风险。

### 11.2 检验族

校正前必须明确哪些假设属于同一个 family。例如：

```text
同一次实验批次
× 所有候选因子
× 所有参数
× 所有 horizon
× cs_ic 与 cs_rank_ic
```

不能在看到结果后任意拆分 family 来获得更小的校正后 p-value。

### 11.3 Benjamini-Hochberg FDR

V1 可默认采用 Benjamini-Hochberg 方法控制 false discovery rate。

设共有 $m$ 个原始 p-value，排序后为：

$$
p_{(1)}\le p_{(2)}\le\cdots\le p_{(m)}.
$$

第 $k$ 个排序假设的调整值为：

$$
\widetilde p_{(k)}
=
\min_{j\ge k}
\left(
1,\frac{m}{j}p_{(j)}
\right).
$$

它比控制 family-wise error rate 的方法更适合探索性因子筛选，但仍必须保留原始 p-value。

### 11.4 关键代码

```python
import pandas as pd
from statsmodels.stats.multitest import multipletests


def adjust_pvalues(
    tests: pd.DataFrame,
    alpha: float = 0.05,
    method: str = "fdr_bh",
) -> pd.DataFrame:
    """校正有效的原始 p-value，并保留全部检验元数据。"""

    result = tests.copy()
    valid = result["raw_pvalue"].notna()
    n_tests = int(valid.sum())

    result["adjusted_pvalue"] = pd.NA
    result["rejected"] = False
    result["correction_method"] = method
    result["alpha"] = alpha
    result["n_tests"] = n_tests

    if n_tests == 0:
        return result

    rejected, adjusted, _, _ = multipletests(
        result.loc[valid, "raw_pvalue"].to_numpy(),
        alpha=alpha,
        method=method,
    )

    result.loc[valid, "adjusted_pvalue"] = adjusted
    result.loc[valid, "rejected"] = rejected
    return result
```

### 11.5 必须保留的结果

每个假设至少保存：

```text
factor_name
label_name
label_horizon
metric_name
raw_pvalue
correction_method
adjusted_pvalue
alpha
rejected
n_tests
test_family_id
```

原始 p-value、校正方法、校正后 p-value 和检验总数必须同时保留。

## 12. 结果状态与结构化警告

### 12.1 明细原因码

每个 timestamp 或 product 的明细行建议使用稳定原因码：

| 原因码 | 含义 |
|---|---|
| `None` | 计算成功 |
| `no_eligible_observations` | 该组没有 `universe=True` 的观测 |
| `missing_observations_strict` | strict 模式下存在缺失 |
| `insufficient_observations` | 联合有效样本不足 |
| `constant_factor` | 因子为常数 |
| `constant_label` | 标签为常数 |

无效组返回 NaN，不返回 0。

### 12.2 不要为每一行生成一条长文本警告

若一千个 timestamp 样本不足，不应生成一千条重复字符串。建议：

- `ic_detail.reason` 保存逐组原因码；
- `warnings` 保存聚合警告，包括原因、数量和少量示例键。

例如：

```python
{
    "code": "insufficient_observations",
    "count": 18,
    "examples": [
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
    ],
}
```

当前 `FactorEvaluationResult.warnings` 的类型为 `tuple[str, ...]`。V1 可以先把聚合信息序列化为稳定中文字符串；若以后需要机器查询，再将模型升级为结构化 warning 类型。

## 13. 输入验证

### 13.1 必须校验

因子和标签：

- 必须是 `pd.Series`；
- 必须是 `MultiIndex`；
- 索引名称必须严格等于 `['timestamp', 'product']`；
- 不能有重复索引；
- 两者索引必须完全一致；
- 值必须是可用于相关计算的数值类型；
- 正负无穷应视为输入错误，或者在进入评价器前显式转为 NaN，不能静默参与相关计算。

`universe`：

- 只能是 `"all"` 或布尔 Series；
- Series 索引必须与因子和标签完全一致；
- 不能包含 NA。

配置：

- `min_observations >= 2`；
- `nan_policy` 只能是 `drop` 或 `strict`；
- `mode` 只能是 `cross_sectional` 或 `time_series`；
- `annualization_factor` 若非空必须大于 0；
- bootstrap 次数、块长度和置信水平必须在合法范围内。

### 13.2 不修改输入

评价函数不能原地修改：

```python
factor_values
labels
universe
```

需要排序、重命名或构造 DataFrame 时，应创建新对象。

## 14. 推荐公共接口

Step 7 的函数可以先保持轻量：

```python
def calculate_coverage(
    factor_values: pd.Series,
    labels: pd.Series,
    universe: UniverseInput = "all",
) -> pd.DataFrame:
    ...


def calculate_ic(
    factor_values: pd.Series,
    labels: pd.Series,
    *,
    mode: Literal["cross_sectional", "time_series"],
    universe: UniverseInput = "all",
    min_observations: int = 10,
    nan_policy: Literal["drop", "strict"] = "drop",
    annualization_factor: int | None = None,
) -> tuple[pd.DataFrame, dict[str, object], tuple[str, ...]]:
    ...
```

返回值分别为：

- IC 明细；
- IC 汇总；
- 聚合警告。

Newey-West、bootstrap 和多重检验建议放在 `robustness.py`，由后续 `FactorEvaluator` 编排。这样 `ic.py` 只负责定义和描述 IC，不把推断配置全部塞进一个函数。

## 15. 与现有模型的衔接提示

当前 `EvaluationConfig` 已有：

```text
mode
label_horizon
min_observations
n_groups
grouping_direction
nan_policy
annualization_factor
```

进入代码实现时需要调整：

1. 将 `nan_policy` 默认值从 `strict` 改为 `drop`；
2. 将 docstring 中两种缺失政策改成本文的精确定义；
3. 明确 `annualization_factor` 只控制 `annualized_icir`，不改变 `icir`；
4. 后续为稳健统计增加独立配置，而不是依赖隐式常量。

稳健统计至少需要记录：

```text
nw_max_lags
bootstrap_method
bootstrap_block_length
bootstrap_samples
bootstrap_confidence_level
random_seed
multiple_testing_method
multiple_testing_alpha
test_family_id
```

这些字段可以在开发 `robustness.py` 时决定是扩展 `EvaluationConfig`，还是建立独立的稳健性配置类。

## 16. 最小人工算例

### 16.1 完美正向横截面 IC

某个 timestamp：

| product | factor | label | universe |
|---|---:|---:|---|
| A | 1 | 10 | True |
| B | 2 | 20 | True |
| C | 3 | 30 | True |

结果：

```text
cs_ic = 1
cs_rank_ic = 1
n_eligible = 3
n_valid = 3
```

### 16.2 完美反向横截面 IC

| product | factor | label | universe |
|---|---:|---:|---|
| A | 1 | 30 | True |
| B | 2 | 20 | True |
| C | 3 | 10 | True |

结果：

```text
cs_ic = -1
cs_rank_ic = -1
```

### 16.3 Pearson 和 Rank IC 不同

| product | factor | label | universe |
|---|---:|---:|---|
| A | 1 | 1 | True |
| B | 2 | 2 | True |
| C | 3 | 100 | True |

排序完全一致，因此：

```text
cs_rank_ic = 1
```

但数值间距不是严格线性，`cs_ic` 小于 1。该案例说明 Rank IC 检验单调排序，不保留强度间距。

### 16.4 `universe=False` 不触发 strict

| product | factor | label | universe |
|---|---:|---:|---|
| A | 1 | 10 | True |
| B | 2 | 20 | True |
| C | NaN | NaN | False |

结果中：

```text
n_eligible = 2
n_valid = 2
n_dropped = 0
```

即使使用 `strict`，C 也不应使该组失败。

### 16.5 `drop` 与 `strict` 的差异

| product | factor | label | universe |
|---|---:|---:|---|
| A | 1 | 10 | True |
| B | 2 | 20 | True |
| C | NaN | 30 | True |

若 `min_observations=2`：

- `drop`：使用 A、B 计算，`n_dropped=1`；
- `strict`：IC 返回 NaN，原因为 `missing_observations_strict`。

### 16.6 常数截面

| product | factor | label | universe |
|---|---:|---:|---|
| A | 1 | 10 | True |
| B | 1 | 20 | True |
| C | 1 | 30 | True |

结果：

```text
cs_ic = NaN
cs_rank_ic = NaN
reason = constant_factor
```

不能返回 0。

## 17. pytest 测试清单

建议新增：

```text
tests/factors/evaluation/
    conftest.py
    test_coverage.py
    test_ic.py
    test_robustness.py
```

### 17.1 Coverage

- `universe="all"` 与全 True Series 结果完全一致；
- 总体、分 timestamp、分 product 的分母正确；
- `universe=False` 不进入分母；
- 因子、标签、联合覆盖率可手算；
- 三类缺失计数与 `joint_valid_count` 严格加总到 `eligible_count`；
- 合资格样本为零时覆盖率为 NaN；
- 标签尾部缺失会降低标签和联合覆盖率；
- 输入未被修改。

### 17.2 IC 与 Rank IC

- 完美正相关得到 1；
- 完美负相关得到 -1；
- Pearson 与 Rank IC 可出现不同结果；
- 重复值使用平均秩；
- 常数因子返回 NaN 和 `constant_factor`；
- 常数标签返回 NaN 和 `constant_label`；
- 样本不足返回 NaN，不返回 0；
- 单品种不能输出伪 `cs_ic`；
- 单 timestamp 不能输出有效 `ts_ic`；
- `drop` 和 `strict` 行为符合定义；
- `universe=False` 的缺失不触发 strict；
- `cs_ic` 按 timestamp 分组；
- `ts_ic` 按 product 分组；
- 因子与标签索引不一致时抛出异常；
- 输入未被修改。

### 17.3 汇总统计

- 均值可手算；
- 标准差使用 `ddof=1`；
- 正值比例只使用有效 IC，且零不算正；
- 标准差为零时 ICIR 为 NaN；
- `icir` 不随 `annualization_factor` 改变；
- `annualized_icir = sqrt(A) * icir`；
- `ts_ic` 汇总不产生 ICIR 和年化 ICIR。

### 17.4 稳健统计

- Newey-West 结果与直接调用 statsmodels 的人工结果一致；
- `max_lags` 越界时抛出异常；
- block bootstrap 使用固定 seed 可以复现；
- 不同 seed 允许产生不同重采样结果；
- block length 合法性检查正确；
- 多重检验保留原始和校正后 p-value；
- 已知 p-value 向量的 BH 校正可手算；
- NaN p-value 不进入检验总数；
- 输出保留 `n_tests`、方法、alpha 和 family ID。

测试必须从仓库根目录运行：

```powershell
python -m pytest tests/factors/evaluation -q
```

## 18. 开发时最容易犯的错误

1. 在 coverage 前 inner join，导致缺失行消失、覆盖率虚高。
2. 把 `universe=False` 当成数据缺失。
3. `drop` 后不记录实际删除数量。
4. 样本不足或常数序列返回 0，而不是 NaN。
5. 在横截面模式中用时间长度补足品种数量。
6. 在时序模式中用品种数量补足单品种时间样本。
7. 在完整面板上 rank，而不是在每个评价组内 rank。
8. 因子值和未来标签已经对齐后再次 shift 标签。
9. 把 ICIR 当作 t 统计量。
10. 用普通标准误处理重叠标签产生的时间相关性。
11. 对按 product 排列的 `ts_ic` 向量使用 Newey-West 时间序列标准误。
12. 把跨品种 `ts_ic` 的均值/标准差年化成 ICIR。
13. bootstrap 单个时间点，破坏序列依赖。
14. 多重检验只保留调整后的 p-value，丢失原始结果和 family 定义。
15. 根据最终 NaN 猜测上游缺失原因，而没有相应的 `data lineage`。

## 19. Step 7 完成标准

达到以下条件，才能认为 coverage、IC 与 Rank IC 的初版开发完成：

- `coverage.py` 可以输出总体、分 timestamp 和分 product 覆盖率；
- coverage 使用 `universe` 作为分母边界，并支持 `"all"`；
- 因子、标签和联合覆盖率口径清楚；
- 可直接观察的缺失分解可以严格对账；
- 依赖上游 `data lineage` 的原因归因只保留扩展位置，不反向猜测；
- `ic.py` 同时支持 `cross_sectional` 和 `time_series`；
- 输出名称分别为 `cs_ic/cs_rank_ic` 和 `ts_ic/ts_rank_ic`；
- 默认 `nan_policy="drop"`，`strict` 行为明确；
- 最小样本、常数序列和空品种池返回 NaN 与原因；
- 横截面 IC 输出非年化 `icir` 和独立的 `annualized_icir`；
- `ts_ic` 不输出伪 ICIR；
- Newey-West、block bootstrap 和多重检验的配置与结果可复现；
- 原始 p-value、校正方法、校正后 p-value 和检验数量均被保留；
- 所有核心统计量均有可手工复算的测试；
- 评价函数不修改输入。

## 20. 与后续组件的边界

本阶段只建立“因子—标签的基础预测诊断”。它不会回答：

- 因子怎样转换为最终仓位；
- 是否满足板块中性或风险预算；
- 分组多空是否可交易；
- 扣除手续费和滑点后是否盈利；
- 是否应自动成为 CandidateFactor。

后续 Step 8 的分组标签、Step 9 的衰减与归因，以及更完整的 `HarvestProfile`、有效标签对齐和诊断组合，会把基础相关性连接到更具体的下游收获方式。

Step 7 的核心原则可以概括为：

> 先固定合资格样本和缺失政策，再计算相关；先保留逐组明细和失败原因，再做汇总与显著性；所有统计证据都必须能够追溯到明确的 timestamp、product、universe、标签期限和配置。
