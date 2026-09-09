# 因子模块：因子评估 regression 开发帮助

> 本文服务于因子研究模块 V1 的预测关系诊断开发，目标是帮助实现 `factors/evaluation/regression.py`。
>
> 本文以当前已经实现的 `coverage.py`、`ic.py` 和 `grouped.py` 为实际代码风格参考。`regression.py` 定位为单因子检验：控制变量可以参与条件化，但结果只保存被评价 factor 的系数与推断。回归结果只属于因子对未来标签的统计预测诊断，不是因子合成权重、诊断组合收益或真实策略回测结果。

## 1. 本阶段要解决什么问题

给定同一研究面板上的：

- 时点 `t` 已经可见的因子值 `factor`；
- 索引仍为 `t`、数值表示未来 `h` 期结果的标签 `label`；
- 当期动态品种池 `universe`；
- 可选控制变量；
- 评价模式、最低样本数、缺失值政策和稳健推断配置；

回归评估模块需要回答：

1. 截面模式下，每个 timestamp 的因子斜率是多少？
2. 逐期截面斜率的平均值是否在考虑时间相关后仍具有统计证据？
3. 控制其他已知暴露后，因子是否仍具有增量截面解释力？
4. 时序模式下，各 product 的因子斜率、拟合度和稳健显著性如何？
5. 时序预测关系是否广泛存在于多个品种，而不是由少数品种驱动？
6. 将多品种样本合并后，共同因子斜率是多少？
7. 控制品种固定效应后，以及进一步控制日期固定效应后，结论是否不同？
8. 重叠标签、品种内序列相关和同日共同冲击如何进入稳健推断？
9. 缺失、样本不足、共线、固定效应吸收失败或协方差异常时如何保留可审计结果？

本阶段代码职责为：

```text
factors/evaluation/regression.py
    ├── 逐 timestamp 的 Fama–MacBeth 式截面回归
    ├── 截面 factor beta 序列的描述汇总与 HAC 均值检验
    ├── 截面 factor beta 序列的循环块 bootstrap 置信区间
    ├── 逐 product 的时序预测回归与 HAC 推断
    ├── product 固定效应 pooled 回归
    ├── product + timestamp 双向固定效应 pooled 回归
    ├── timestamp × product 双向聚类推断
    └── 只保存 factor beta、回归元数据、样本元数据和失效原因
```

## 2. 模块边界

### 2.1 模块负责

- 对一个因子和一个标签执行回归诊断；
- 接收零个或多个显式控制变量；
- 将控制变量视为 nuisance regressors，不保存其系数、标准误或协方差；
- 横截面模式按 timestamp 独立估计回归；
- 时序模式按 product 独立估计回归；
- 时序模式同时估计两种 pooled 固定效应规格；
- 对重叠标签使用 HAC 或双向聚类稳健协方差；
- CS 输出逐期 factor beta，并对其均值输出 HAC 推断和 bootstrap 区间；
- TS 分品种与 pooled 结果只输出 factor beta 及其稳健推断；
- 保留样本筛选、有效滞后、聚类数和固定效应吸收信息；
- 对局部失败返回 NaN 与稳定原因码，不因单个评价单元失败而终止全部实验。

### 2.2 模块不负责

- 对因子或标签执行标准化、缩尾、平滑或中性化；
- 自动挑选控制变量；
- 自动删除共线控制变量以“修复”模型；
- 估计第一阶段资产 beta 的传统两步资产定价模型；
- 使用标签拟合多个因子的合成权重；
- 保存截距、控制变量系数、完整参数协方差矩阵或所有解释变量的推断结果；
- 生成预测值对应的目标仓位；
- 计算 signal PnL、手续费、滑点、保证金或真实策略净值；
- 做滚动或 expanding 参数估计；
- 自动判定因子是否有效。

### 2.3 与另外三类回归的区别

项目中“回归”可能指向不同业务概念，必须分开：

| 位置 | 被解释变量 | 目的 | 标签使用规则 |
|---|---|---|---|
| `evaluation/regression.py` | 未来标签 | 评价因子的预测关系 | 标签只作为评价对象 |
| 预处理残差化 | 因子值 | 从因子中剔除已有暴露 | 不应访问未来标签 |
| 回归型因子合成 | 训练期标签 | 拟合多个因子的合成权重 | 只能在训练窗口访问标签 |

三者可以复用低层纯数值工具，但不得共享同一个高层业务结果对象。

## 3. 已确定的开发口径

### 3.1 输入格式

低层公共函数接收已经组装好的 DataFrame：

```text
pd.DataFrame
index = MultiIndex[timestamp, product]
required columns = [factor, label, universe]
optional columns = 显式声明的控制变量
```

其中：

- `factor` 是时刻 `t` 已经可见的因子值；
- `label` 是索引为 `t` 的未来标签；
- `universe` 是无缺失的布尔品种池标记；
- 控制变量也必须在 `t` 时刻已经可见；
- 所有列共享完全相同的研究面板索引。

标签已经由 `LabelBuilder` 完成前瞻对齐。评价组件不能再次执行 `shift`。

### 3.2 控制变量接口

控制变量通过以下参数显式声明：

```python
control_columns: tuple[str, ...] = ()
```

规则如下：

- 空元组表示只评价 `factor`；
- 列顺序必须保留，并进入结果元数据；
- 不允许重复列名；
- 不允许包含保留列 `factor`、`label` 或 `universe`；
- 声明的列必须存在且为数值型；
- DataFrame 中未声明的额外列必须忽略；
- 不自动对控制变量做标准化、去均值或缺失填充。
- 结果对象以 `control_vars` 保存本次实际使用的控制变量名称，但不保存控制变量系数。

### 3.3 截距

```python
fit_intercept: bool = True
```

- 该参数只控制逐 timestamp 截面回归和逐 product 时序回归；
- `True` 时模型加入截距，但截距估计不进入结果对象；
- pooled 回归始终吸收指定固定效应，不额外输出截距；
- pooled 模型的固定效应语义不随 `fit_intercept` 改变。

### 3.4 三类估计结果必须同时保留

`cross_sectional` 模式返回逐期截面结果。

`time_series` 模式必须同时返回：

1. 分品种时序回归；
2. product 固定效应 pooled 回归；
3. product + timestamp 双向固定效应 pooled 回归。

两个 pooled 规格不能通过配置只默认保留其中一个。日期固定效应会删除同日共同方向，两个规格回答不同问题，必须并列展示。

### 3.5 稳健推断统一采用有限样本 t 分布

- Fama–MacBeth 平均斜率 HAC 检验使用有限样本 t 分布；
- 分品种 HAC 回归使用回归残差自由度；
- 双向聚类推断自由度为：

$$
df_{cluster}=\min(G_{product},G_{timestamp})-1.
$$

- pooled 协方差启用 `use_correction=True` 和 `df_correction=True`；
- 结果必须记录参考分布、实际自由度和小样本修正开关。

## 4. 评价样本与执行顺序

### 4.1 回归所需列

定义：

```python
regression_columns = (
    "factor",
    "label",
    *control_columns,
)
```

`universe` 只决定样本资格，不进入设计矩阵。

### 4.2 固定执行顺序

```text
校验 DataFrame、索引和配置
    ↓
复制输入，禁止原地修改
    ↓
应用 universe
    ↓
按评价单元应用 drop 或 strict
    ↓
检查有效样本数和残差自由度
    ↓
构建设计矩阵与可选截距
    ↓
检查标签常数和设计矩阵满秩
    ↓
估计 OLS 点估计
    ↓
按模式计算 HAC 或双向聚类协方差
    ↓
生成 factor beta、回归元数据和原因码
    ↓
汇总 factor beta
```

### 4.3 `drop` 政策

对当前评价单元：

```python
eligible = group.loc[group["universe"], regression_columns]
valid = eligible.dropna(subset=regression_columns)
```

任一回归变量缺失的行均不进入回归。必须记录：

```text
n_eligible
n_valid
n_dropped
```

### 4.4 `strict` 政策

- 截面模式：`universe=True` 范围内任一回归变量缺失，使当前 timestamp 失效；
- 时序模式：任一回归变量缺失，使当前 product 的分品种回归失效；
- pooled 回归中，strict 失效的 product 整体排除；
- 其他没有缺失的 product 仍可进入 pooled 样本；
- `universe=False` 行的缺失不触发 strict。

### 4.5 pooled 共同样本

两个 pooled 规格必须使用同一个样本：

- `drop`：全部 `universe=True` 且所有回归变量完整的行；
- `strict`：先排除 strict 失效的整个 product，再使用其余 product 的完整合资格行；
- 不允许 product FE 与 product + timestamp FE 各自静默形成不同样本；
- 共同样本的行数、product 数和 timestamp 数必须进入两个结果对象。

### 4.6 最低样本要求

设设计矩阵列数（包括可选截距）为 `n_parameters`，则单个回归单元至少需要：

```python
minimum_required = max(min_observations, n_parameters + 1)
```

`n_parameters + 1` 保证至少一个正的残差自由度。满足数量门槛仍不代表模型可识别，随后必须检查矩阵秩。

## 5. 截面模式：Fama–MacBeth 式逐期回归

### 5.1 定义

对每个 timestamp `t` 估计：

$$
y_{i,t}^{(h)}
=a_t+\beta_t f_{i,t}+\gamma_t^{\mathsf T}x_{i,t}
+\varepsilon_{i,t}^{(h)}.
$$

其中 `x` 是 `control_columns` 对应的控制变量。

这里的“Fama–MacBeth 式”准确指：

> 每个 timestamp 执行一次截面回归，再研究逐期 factor beta 序列的均值、稳定性和显著性。

它不包含传统资产定价检验中先估计资产风险 beta 的另一阶段。

### 5.2 单期结果

每个有效 timestamp 保存：

- `factor` 的系数点估计；
- 样本数和参数数；
- 设计矩阵秩；
- 普通 OLS 的 $R^2$ 和调整后 $R^2$；
- 残差自由度；
- 回归失效原因码。

逐期 factor beta 统一保存在 `factor_coefficients: pd.Series`，索引为完整的
`timestamp`；失效 timestamp 对应值为 NaN。CS 不保存截距或控制变量系数，也
不输出单期 `standard_error`、`t_stat`、`p_value` 或置信区间。单期普通 OLS
推断不是 Fama–MacBeth 的主要检验对象，也不能处理 factor beta 序列的跨期
相关。主要推断对象是按 timestamp 排列的 factor beta 均值，其 HAC 标准误、
t 值、p 值和置信区间统一保存在 `factor_beta_mean_test`。

### 5.3 factor beta 汇总

设有效逐期斜率为 $\widehat\beta_t$：

$$
\bar\beta=\frac{1}{T}\sum_{t=1}^{T}\widehat\beta_t.
$$

至少输出：

```text
n_valid
mean
median
std
positive_ratio
```

汇总对有效 timestamp 等权，不按单期 `n_valid` 加权。

`positive_ratio` 只把严格大于 0 的 factor beta 计为正；0 不计入正值。

### 5.4 控制变量含义

有控制变量时，`factor` 系数回答：

> 在同一 timestamp、给定控制变量后，因子增加一个单位时，未来标签条件均值变化多少？

组件不替研究者判断控制变量是否应当进入模型，也不把控制后斜率等同于因果效应。
控制变量和可选截距只用于识别 factor 的条件斜率，其自身估计结果不进入
`CSRegressionEvaluationResults`。

## 6. Fama–MacBeth 平均斜率的 HAC 推断

### 6.1 为什么需要 HAC

当标签 horizon 大于 1 时，相邻标签通常共享未来收益区间，因此 $\widehat\beta_t$ 序列可能具有序列相关。普通均值标准误会低估不确定性。

把斜率序列写成截距模型：

$$
\widehat\beta_t=\mu_\beta+u_t.
$$

对截距 $\mu_\beta$ 使用 Newey–West/HAC 协方差。

### 6.2 滞后阶数

```python
requested_lags = (
    label_horizon - 1
    if nw_max_lags is None
    else nw_max_lags
)
effective_lags = min(requested_lags, n_valid_periods - 2)
```

规则：

- `nw_max_lags` 必须是非负整数；
- 默认值为 `label_horizon - 1`；
- 因样本长度收敛时同时记录请求值和实际值；
- 有效 period 少于 2 时不执行均值推断，结果为 None；
- 不允许负的实际滞后进入 statsmodels。

### 6.3 推断结果

至少保存：

```text
estimate
standard_error
t_stat
p_value
confidence_interval
n_observations
requested_max_lags
effective_max_lags
use_correction
reference_distribution
inference_df
```

使用有限样本 t 分布，`inference_df = n_valid_periods - 1`。

## 7. factor beta 的循环块 bootstrap

### 7.1 估计对象

bootstrap 对按 timestamp 排序的有效 factor beta 序列重采样，每次计算平均斜率。不能独立抽取单个 timestamp 而破坏相邻依赖。

### 7.2 默认块长

```python
default_block_size = max(
    label_horizon,
    ceil(n_valid_periods ** (1 / 3)),
)
effective_block_size = min(
    default_block_size,
    n_valid_periods,
)
```

若用户显式提供 `bs_block_size`，则必须满足：

```text
1 <= bs_block_size <= n_valid_periods
```

### 7.3 可复现配置

默认：

```text
bs_seed = 42
bs_reps = 500
bs_confidence_intervals = 0.95
```

结果保存块长、随机种子、重复次数、置信水平和上下界。有效 beta 少于 2 时不执行 bootstrap，返回 None。

## 8. 时序模式：分品种预测回归

### 8.1 定义

对每个 product `i` 估计：

$$
y_{i,t}^{(h)}
=\alpha_i+\beta_i f_{i,t}+\gamma_i^{\mathsf T}x_{i,t}
+\varepsilon_{i,t}^{(h)}.
$$

`factor` 系数表示同一个品种自身因子增加一个单位时，未来标签条件均值的变化。

### 8.2 HAC 标准误

每个 product 的 factor beta 使用 HAC 协方差：

```python
result = sm.OLS(y, design).fit(
    cov_type="HAC",
    cov_kwds={
        "maxlags": effective_max_lags,
        "use_correction": True,
    },
    use_t=True,
)
```

每个 product 分别根据自身有效样本数收敛滞后阶数，并记录实际值。有限样本 t 推断使用该回归的残差自由度。

### 8.3 分品种输出

每个成功得到 factor beta 点估计的 product，在
`per_product_results[product]` 中保存一个 `HACRegressionResults`：

```text
estimate
standard_error
t_stat
p_value
confidence_level
confidence_interval
n_observations
reference_distribution
inference_df
use_correction
requested_max_lags
effective_max_lags
```

这些字段全部对应 factor beta；不保存截距、控制变量系数或完整协方差矩阵。

`group_metadata` 以 product 为索引保存：

```text
n_eligible
n_valid
n_dropped
```

`per_product_regression_metadata` 同样以 product 为索引，保存：

```text
invalid_code
dof_residual
r_squared
adjusted_r_squared
n_parameters
design_rank
n_observations
```

上述两个 DataFrame 保留完整 product 索引；无效 product 的相应统计量为 NaN，
并由 `invalid_code` 说明原因。`per_product_results` 只包含成功得到 factor beta
点估计的 product。

### 8.4 跨品种 factor beta 汇总

对有效的 $\widehat\beta_i$ 输出：

```text
n_valid
mean
median
std
positive_ratio
```

每个 product 等权。不能按历史长度对 product 隐式加权。

该汇总描述品种异质性，不对按 product 排列的 beta 向量使用 HAC，也不将其年化。

## 9. pooled 面板回归

### 9.1 product 固定效应规格

$$
y_{i,t}^{(h)}
=\alpha_i+\beta f_{i,t}+\gamma^{\mathsf T}x_{i,t}
+\varepsilon_{i,t}^{(h)}.
$$

它控制每个品种不随时间变化的平均水平，但保留同一天多个品种共享的收益方向。

规格名称固定为：

```text
product_fe
```

### 9.2 product + timestamp 固定效应规格

$$
y_{i,t}^{(h)}
=\alpha_i+\delta_t+\beta f_{i,t}
+\gamma^{\mathsf T}x_{i,t}
+\varepsilon_{i,t}^{(h)}.
$$

日期固定效应删除同一天所有品种共享的标签方向，更接近控制共同市场冲击后的相对预测关系。

规格名称固定为：

```text
product_timestamp_fe
```

### 9.3 为什么两个规格必须并列

- `product_fe` 保留 CTA 可能主动收获的共同趋势；
- `product_timestamp_fe` 只利用同日品种之间的相对变化；
- 两者斜率不同不是实现错误，而是估计对象不同；
- 不能只展示统计上更显著的一个规格。

### 9.4 pooled 不等于跨品种广度

pooled 回归按有效面板行估计共同斜率，历史更长的品种自然贡献更多观测。它不能替代分品种 beta 分布。

报告 pooled 结果时必须同时展示：

- 分品种 factor beta 汇总；
- pooled 的 product 数和 timestamp 数；
- 两个固定效应规格的斜率；
- 双向聚类稳健推断。

## 10. 固定效应吸收

### 10.1 不构造大规模日期哑变量

日频长面板可能包含数千个 timestamp。直接执行：

```python
pd.get_dummies(timestamp)
```

会产生巨大的稠密设计矩阵。生产实现不得依赖大规模日期哑变量。

小型 pytest 可以用显式哑变量回归作为黄金对照，但生产路径必须使用固定效应吸收。

### 10.2 product 固定效应去均值

对标签和每个解释变量按 product 去均值：

$$
\widetilde z_{i,t}
=z_{i,t}-\bar z_i.
$$

在变换后的标签和解释变量上执行无截距 OLS。结果只提取 factor beta，固定
效应、截距和控制变量估计均不进入结果对象。

### 10.3 非平衡面板的双向吸收

简单执行：

```text
原值 - product 均值 - timestamp 均值 + 总均值
```

只对平衡面板严格成立。研究面板允许品种上市时间不同、动态品种池变化和缺失，因此双向固定效应必须使用交替投影：

```text
初始化 residualized = 原变量
    ↓
按 product 减去当前组均值
    ↓
按 timestamp 减去当前组均值
    ↓
重复，直到最大绝对改变量小于 tolerance
```

标签和全部解释变量必须使用相同样本、相同分组和相同收敛规则分别残差化。

建议内部常量：

```text
FE_ABSORPTION_TOLERANCE = 1e-10
FE_ABSORPTION_MAX_ITERATIONS = 10_000
```

它们不进入首版公共函数和结果数据契约。达到最大迭代次数仍未收敛时，
`regression_metadata['invalid_code']` 记录
`fixed_effect_absorption_not_converged`；不额外扩张 metadata 索引。

### 10.4 吸收后的识别检查

固定效应吸收后必须重新检查：

- 每个解释变量是否仍有有限且非零的变异；
- 变换后的设计矩阵是否满列秩；
- 有效观察是否仍满足参数数和残差自由度要求。

若因子只随 timestamp 变化，则会被 timestamp 固定效应完全吸收。此时 `product_timestamp_fe` 应返回秩亏失效，而不是静默删除因子。

### 10.5 singleton 处理

V1 不做迭代 singleton pruning。单成员固定效应组保留在共同样本中，并由吸收、秩检查和聚类数检查决定模型是否可估计。结果元数据应额外报告 product 和 timestamp 的 singleton 数量，提醒研究者解释有限样本结果。

## 11. pooled 双向聚类推断

### 11.1 需要同时处理的依赖

CTA 面板至少存在两类误差依赖：

- 同一 product 内沿时间的序列相关和重叠标签；
- 同一 timestamp 内跨 product 的共同冲击。

因此 pooled 斜率统一使用 product × timestamp 双向聚类，不提供普通 iid 标准误作为主要结果。

### 11.2 statsmodels 实现口径

当前 Conda base 环境为 `statsmodels 0.14.2`，可使用：

```python
groups = np.column_stack([
    product_codes,
    timestamp_codes,
])

robust = ols_result.get_robustcov_results(
    cov_type="cluster",
    groups=groups,
    use_correction=True,
    df_correction=True,
    use_t=True,
)
```

statsmodels 的双向聚类协方差为：

$$
\widehat V_{two-way}
=\widehat V_{product}
+\widehat V_{timestamp}
-\widehat V_{intersection}.
$$

### 11.3 最低聚类要求

两个维度都至少需要两个不同聚类：

```text
n_products >= 2
n_timestamps >= 2
```

否则 pooled 点估计不作为有效 pooled 回归结果，返回 `insufficient_clusters`。

### 11.4 推断自由度

```python
inference_dof = min(n_products, n_timestamps) - 1
```

所有 pooled t 值、p 值和置信区间使用该自由度。结果中不能只保存
`dof_residual` 而遗漏真正用于稳健推断的 `inference_dof`。

### 11.5 非法协方差

双向聚类在聚类数较少或样本结构特殊时可能得到：

- 非有限协方差元素；
- 对角线为负；
- 无法开方的标准误。

处理规则：

1. 保留已经成功估计的 OLS 点估计；
2. 检查 factor beta 对应的协方差对角元素；
3. 非有限或负方差时，将 factor beta 的标准误、t 值、p 值和区间设为 NaN；
4. `regression_metadata['invalid_code']` 记录 `invalid_cluster_covariance`；
5. 不用绝对值、截断到零或普通 OLS 协方差静默替代。

## 12. 拟合统计的口径

### 12.1 逐期和分品种回归

保存普通 OLS：

```text
r_squared
adjusted_r_squared
```

标签常数时模型失效，不输出误导性的 $R^2$。

### 12.2 pooled 回归

pooled 只报告固定效应吸收后的：

```text
r_squared
adjusted_r_squared
```

这两个字段定义为固定效应吸收后、变换数据上的无截距回归拟合度，语义上属于
within $R^2$。当前数据契约沿用 `r_squared` 和 `adjusted_r_squared` 字段名，
不能将其解释为 overall、between 或 inclusive $R^2$。

### 12.3 点估计与显著性必须分离

- `estimate` 表示斜率点估计；
- `standard_error` 表示指定稳健协方差下的估计误差；
- t 值和 p 值依赖稳健协方差与参考分布；
- $R^2$ 衡量样本内拟合度；
- 低 $R^2$ 不自动否定金融收益预测关系；
- 大 pooled t 值不证明每个 product 都存在预测力。

## 13. 失效条件和原因码

### 13.1 建议原因码

```python
REGRESSION_INVALID_CODES = {
    1: "has_missing_nan_policy_strict",
    2: "insufficient_observations",
    3: "constant_label",
    4: "rank_deficient_design",
    5: "insufficient_clusters",
    6: "fixed_effect_absorption_not_converged",
    7: "invalid_cluster_covariance",
    9: "empty_universe",
}
```

数字码属于 `regression.py` 自己的命名空间，不与 `ic.py` 或 `grouped.py` 共享数字含义。

### 13.2 有效性表达

当前数据契约不单独保存 `status` 字段，有效性由点估计、推断字段和
`invalid_code` 联合表达：

- `invalid_code is None`：要求的点估计和推断均成功；
- `invalid_code == 7`：factor beta 点估计可以保留，但双向聚类推断字段为 NaN；
- 其他失效码：当前回归无法定义，factor beta 及相应推断字段为 NaN。

### 13.3 设计矩阵秩亏

以下情况均属于 `rank_deficient_design`：

- 启用截距时因子为常数；
- 控制变量与因子完全共线；
- 控制变量之间完全共线；
- 固定效应吸收后某个解释变量没有剩余变异；
- 设计矩阵列数大于矩阵秩。

组件不能自动删除某一列后继续拟合，因为这会使不同 timestamp 或 product 实际估计不同模型。

### 13.4 标签常数

在启用截距或固定效应的模型中，标签常数没有有效的预测变异，应返回 `constant_label`。不能将零斜率和完美拟合解释为有效统计证据。

### 13.5 局部失败隔离

- 一个 timestamp 失败不影响其他 timestamp；
- 一个 product 失败不影响其他 product；
- 一个 pooled 规格吸收失败不抹掉另一个 pooled 规格或分品种结果；
- metadata DataFrame 仍保留失败评价单元的索引；
- 无效系数为 NaN，不填 0。

## 14. 结果数据结构

### 14.1 factor beta 描述汇总

```python
@dataclass(frozen=True)
class RegressionBetaSummary:
    n_valid: int
    mean: float
    median: float
    std: float
    positive_ratio: float
```

CS 使用它汇总逐 timestamp factor beta，TS 使用它汇总逐 product factor beta。

### 14.2 HAC 回归结果

```python
@dataclass(frozen=True)
class HACRegressionResults:
    estimate: float
    standard_error: float
    t_stat: float
    p_value: float
    confidence_level: float
    confidence_interval: tuple[float, float]
    n_observations: int
    reference_distribution: Literal["t"]
    inference_df: float
    use_correction: bool
    requested_max_lags: int | None = None
    effective_max_lags: int | None = None
```

该对象只保存一个 factor beta 或 factor beta 均值的 HAC 结果，不保存截距、
控制变量系数或完整协方差矩阵。

Fama–MacBeth 均值检验使用语义更具体的子类：

```python
@dataclass(frozen=True)
class FactorBetaMeanTestResults(HACRegressionResults):
    """Fama–MacBeth factor beta 均值的 HAC 检验结果。"""
```

它是独立 dataclass 子类，不是 `HACRegressionResults` 的赋值别名。

循环块 bootstrap 使用独立结果类型，避免让 `regression.py` 依赖 `ic.py` 的高层结果对象：

```python
@dataclass(frozen=True)
class RegressionBootstrapConfidenceIntervals:
    block_size: int
    seed: int
    reps: int
    confidence_level: float
    confidence_intervals: tuple[float, float]
    n_observations: int
```

### 14.3 pooled 结果

```python
@dataclass(frozen=True)
class PooledRegressionResults:
    cov_type: Literal["two_way_cluster"]
    cluster_cov_vars: Literal["timestamp_product"]
    fixed_effect_vars: Literal[
        "product",
        "product_timestamp",
    ]
    estimate: float
    standard_error: float
    t_stat: float
    p_value: float
    confidence_interval: tuple[float, float]
    n_observations: int
    regression_metadata: pd.Series
```

所有标量估计与推断字段只对应 factor beta。两个固定效应规格分别由：

```text
fixed_effect_vars = "product"
fixed_effect_vars = "product_timestamp"
```

识别。`regression_metadata` 的索引固定为：

```text
invalid_code
dof_residual
r_squared
adjusted_r_squared
design_rank
n_observations
n_parameters
n_products
n_timestamps
n_product_singletons
n_timestamp_singletons
cluster_use_correction
cluster_dof_correction
reference_distribution
inference_dof
```

`n_observations` 同时作为顶层字段保留，便于直接读取 factor beta 推断的样本数；
metadata 中的同名值必须与它严格一致。

### 14.4 截面完整结果

```python
@dataclass(frozen=True)
class CSRegressionEvaluationResults:
    mode: Literal["cross_sectional"]
    label_horizon: int
    min_observations: int
    nan_policy: Literal["drop", "strict"]
    group_metadata: pd.DataFrame
    control_vars: tuple[str, ...]
    fit_intercept: bool
    factor_coefficients: pd.Series
    regression_metadata: pd.DataFrame
    factor_beta_summary: RegressionBetaSummary
    factor_beta_mean_test: FactorBetaMeanTestResults | None
    factor_beta_bootstrap_ci: (
        RegressionBootstrapConfidenceIntervals | None
    )
```

索引语义：

```text
factor_coefficients:
    Index[timestamp]
    name = "factor_beta"
group_metadata:
    Index[timestamp]
    columns:
        - n_eligible
        - n_valid
        - n_dropped
regression_metadata:
    Index[timestamp]
    columns:
        - invalid_code
        - dof_residual
        - r_squared
        - adjusted_r_squared
        - design_rank
        - n_observations
        - n_parameters
```

`factor_coefficients` 保留全部 timestamp；无效 timestamp 为 NaN。CS 不保存
逐期截距、控制变量系数、单期标准误、t 值、p 值或置信区间。上述推断统计量
只属于 `factor_beta_mean_test` 对 factor beta 时间序列均值所做的 HAC 检验。

### 14.5 时序完整结果

```python
@dataclass(frozen=True)
class TSRegressionEvaluationResults:
    mode: Literal["time_series"]
    label_horizon: int
    min_observations: int
    nan_policy: Literal["drop", "strict"]
    control_vars: tuple[str, ...]
    fit_intercept: bool
    # per-product
    group_metadata: pd.DataFrame
    per_product_results: dict[str, HACRegressionResults]
    per_product_regression_metadata: pd.DataFrame
    factor_beta_summary: RegressionBetaSummary
    # pooled
    pooled_product_fe: PooledRegressionResults
    pooled_product_timestamp_fe: PooledRegressionResults
```

索引语义：

```text
group_metadata:
    Index[product]
    columns = [n_eligible, n_valid, n_dropped]
per_product_regression_metadata:
    Index[product]
    columns = [
        invalid_code,
        dof_residual,
        r_squared,
        adjusted_r_squared,
        design_rank,
        n_observations,
        n_parameters,
    ]
```

`per_product_results` 的键为 product，值只保存该 product 的 factor beta HAC
结果。无效 product 不进入该 dict，但必须保留在两个 metadata DataFrame 中。

### 14.6 单因子输出不变量

- `factor_coefficients`、`HACRegressionResults.estimate` 和
  `PooledRegressionResults.estimate` 始终只表示 factor beta；
- `control_vars` 只记录模型使用的控制变量名称；
- 结果不保存截距、控制变量系数、完整系数向量或参数协方差矩阵；
- 若未来增加多因子联合检验，应重新设计新的结果契约，不扩张这些字段的隐含语义。

## 15. 推荐公共接口

```python
def regression_details(
    df: pd.DataFrame,
    mode: Literal["cross_sectional", "time_series"],
    label_horizon: int,
    min_observations: int,
    nan_policy: Literal["drop", "strict"] = "drop",
    control_columns: tuple[str, ...] = (),
    fit_intercept: bool = True,
    nw_max_lags: int | None = None,
    bs_block_size: int | None = None,
    bs_seed: int = 42,
    bs_reps: int = 500,
    bs_confidence_intervals: float = 0.95,
) -> CSRegressionEvaluationResults | TSRegressionEvaluationResults:
    """计算截面或时序模式下的回归预测诊断。"""
    ...
```

### 15.1 参数适用范围

| 参数 | 截面模式 | 时序模式 |
|---|---|---|
| `label_horizon` | HAC 与 bootstrap 默认依赖范围 | 每个 product 的 HAC 默认滞后 |
| `min_observations` | 每个 timestamp | 每个 product，并用于 pooled 总样本门槛 |
| `nan_policy` | timestamp 级 | product 级及 pooled 样本 |
| `control_columns` | 每期截面控制；结果记录为 `control_vars` | 分品种和两个 pooled 规格共同使用；结果记录为 `control_vars` |
| `fit_intercept` | 生效 | 只对分品种回归生效 |
| `nw_max_lags` | beta 均值 HAC | 每个 product 的 HAC |
| bootstrap 参数 | beta 均值区间 | 不使用 |

时序模式收到非默认 bootstrap 参数时应抛出 ValueError，避免用户误以为执行了 bootstrap。默认值可以保留但不产生结果。

### 15.2 不修改输入

公共入口必须先复制输入：

```python
working = df.copy()
```

排序、添加截距、编码聚类、固定效应吸收和构造结果列均只能作用于新对象。

## 16. 输入验证

### 16.1 DataFrame

- 必须是非空 `pd.DataFrame`；
- 必须包含 `factor`、`label`、`universe`；
- 索引必须是 `MultiIndex`；
- 索引名称与顺序严格为 `['timestamp', 'product']`；
- 索引必须唯一；
- timestamp 层必须是 `DatetimeIndex`；
- 时间戳层必须单调递增；
- `factor`、`label` 和控制变量必须是数值型；
- 有效值中不能包含正负无穷；
- `universe` 必须是无缺失的布尔列。

### 16.2 配置

- `mode` 只能为 `cross_sectional` 或 `time_series`；
- `label_horizon` 必须为正整数，bool 不视为整数；
- `min_observations` 必须大于等于 2，bool 不视为整数；
- `nan_policy` 只能为 `drop` 或 `strict`；
- `control_columns` 必须为字符串元组；
- `fit_intercept` 必须为 bool；
- `nw_max_lags` 若非空必须为非负整数；
- `bs_block_size` 若非空必须为正整数；
- `bs_reps` 必须为正整数；
- `bs_confidence_intervals` 必须严格位于 `(0, 1)`；
- 时序模式不能接收非默认 bootstrap 配置。

### 16.3 全局错误与局部失效

输入类型、索引、列、配置错误应立即抛出异常。

由具体样本产生的空品种池、缺失、样本不足、秩亏和协方差问题属于业务结果，应返回原因码，不应抛出并终止整个实验。

## 17. 核心实现流程

### 17.1 截面模式

```text
按 timestamp 分组
    ↓
逐组选择合资格完整样本
    ↓
检查门槛、标签常数和矩阵秩
    ↓
估计普通 OLS，并提取 factor beta 与回归元数据
    ↓
拼接 factor_coefficients 和 metadata
    ↓
提取有效 factor beta 时间序列
    ↓
描述汇总 + HAC 均值检验 + 循环块 bootstrap
```

### 17.2 时序模式

```text
按 product 分组
    ↓
逐组选择样本并估计 HAC 回归
    ↓
将有效 product 的 factor beta HAC 结果写入 per_product_results
    ↓
等权汇总各 product 的 factor beta
    ↓
构造两个 pooled 规格的共同样本
    ↓
分别吸收一向、双向固定效应
    ↓
检查收敛、剩余变异和矩阵秩
    ↓
无截距 OLS 点估计
    ↓
product × timestamp 双向聚类推断
```

### 17.3 不建议复用 `common.select_group_sample`

现有公共选择器固定围绕 `factor` 和 `label` 两列，并包含 IC 所需的常数检查。回归还需要：

- 动态数量的控制变量；
- 参数数与残差自由度门槛；
- 设计矩阵秩检查；
- pooled 共同样本；
- 标签常数和解释变量共线的独立语义。

因此 `regression.py` 应定义自己的样本选择数据类和函数，不应为强行复用而改变现有 IC/grouped 行为。

## 18. 与现有模型的衔接

### 18.1 `EvaluationConfig`

现有字段可以直接提供：

```text
mode
label_horizon
min_observations
nan_policy
```

回归新增配置包括：

```text
control_columns
fit_intercept
nw_max_lags
bootstrap 配置
```

在统一 `FactorEvaluator` 开发前，这些参数先由 `regression_details()` 明确接收，不在本轮提前修改 `EvaluationConfig`。

### 18.2 `FactorEvaluationResult`

当前模型没有结构化回归字段。未来统一编排时建议增加：

```python
regression: (
    CSRegressionEvaluationResults
    | TSRegressionEvaluationResults
    | None
) = None
```

不应把回归结果塞入 `ic_summary`、`robustness` 或普通字典中。

### 18.3 `robustness.py`

现有 `ic.py` 已包含部分 HAC 与 bootstrap 实现，`robustness.py` 目前提供多重检验。首版 regression 可以在模块内部实现回归专属稳健推断。

未来若抽取公共稳健统计工具，要求：

- 不改变已有 IC 结果语义；
- 公共函数只处理数值统计，不依赖具体评价结果 dataclass；
- regression 仍负责决定 HAC、双向聚类和自由度的业务口径。

## 19. 最小人工算例

### 19.1 单期截面斜率

一个 timestamp、三个 product：

| product | factor | label | universe |
|---|---:|---:|---|
| A | 1 | 3 | True |
| B | 2 | 5 | True |
| C | 3 | 7 | True |

带截距回归的数学真值和对外结果：

```text
内部 intercept = 1，但结果不保存
factor beta = 2
r_squared = 1
n_valid = 3
```

### 19.2 Fama–MacBeth 平均斜率

三个 timestamp 的 factor beta 为：

```text
[1.0, 2.0, 3.0]
```

描述汇总：

```text
mean = 2.0
median = 2.0
std = 1.0
positive_ratio = 1.0
n_valid = 3
```

HAC 推断结果应与对该序列执行 statsmodels 截距回归一致。

### 19.3 控制变量改变 factor beta

构造：

```text
label = 2 * factor + 3 * control
```

并让 `factor` 与 `control` 相关。测试要求：

- 不提供 control 时 factor beta 含遗漏变量影响；
- 提供 control 后恢复 `factor beta = 2`；
- `control_vars` 记录使用了 `control`；
- 结果不保存 `control beta = 3`，因为控制变量只是条件化变量。

### 19.4 分品种回归互不污染

构造两个 product：

```text
A: label = 1 + 2 * factor
B: label = -3 - 4 * factor
```

结果应分别恢复：

```text
A factor beta = 2
B factor beta = -4
```

不能把两个 product pooled 后冒充分品种结果。

### 19.5 product 固定效应

构造两个 product，具有不同截距但共同斜率：

```text
A: label = 10 + 2 * factor
B: label = -5 + 2 * factor
```

product FE 应恢复共同 `factor beta = 2`，且不输出 A/B 固定效应系数。

### 19.6 日期固定效应删除共同冲击

构造：

```text
label[t,i] = 2 * factor[t,i] + common_shock[t] + product_effect[i]
```

`product_timestamp_fe` 应在吸收共同日期冲击后恢复 `factor beta = 2`。

另构造 factor 只随 timestamp 变化、同日所有 product 相同的面板。该因子应被 timestamp FE 完全吸收，模型返回 `rank_deficient_design`。

### 19.7 非平衡面板

让 product B 晚于 A 才进入样本，并使若干 timestamp 缺少某个 product。交替投影得到的双向 FE factor beta 应与在同一小样本上显式加入 product/timestamp 哑变量的回归一致。

### 19.8 `universe=False` 不触发 strict

若 C 行为：

```text
factor = NaN
label = NaN
universe = False
```

C 不进入回归变量缺失检查，也不使其 timestamp 或 product 在 strict 模式失效。

## 20. pytest 测试清单

建议未来新增：

```text
tests/factors/evaluation/test_regression.py
```

### 20.1 输入与配置

- 缺少必需列时抛出异常；
- 控制列不存在、重复或使用保留名时抛出异常；
- 索引类型、名称、顺序、唯一性或 timestamp 类型错误时抛出异常；
- `universe` 非布尔或包含 NA 时抛出异常；
- 非数值因子、标签或控制变量时抛出异常；
- 正负无穷被拒绝；
- 非法 mode、horizon、样本数、nan policy 和稳健配置被拒绝；
- 时序模式非默认 bootstrap 配置被拒绝；
- 输入 DataFrame 未被修改。

### 20.2 Fama–MacBeth 式截面回归

- 每个 timestamp 独立估计；
- 带截距和不带截距结果可手算；
- 加入多个控制变量后 factor beta 正确；
- 结果只保存 factor beta，不保存截距或控制变量系数；
- `control_vars` 与输入的 `control_columns` 完全一致；
- 逐期 $R^2$ 和调整后 $R^2$ 正确；
- factor beta 使用 timestamp 等权汇总；
- 一个 timestamp 失效不影响其他 timestamp；
- 不再次 shift 标签。

### 20.3 HAC 与 bootstrap

- Fama–MacBeth 均值 HAC 结果与 statsmodels 直接结果一致；
- 分品种 HAC 结果与 statsmodels 直接结果一致；
- 默认滞后为 `label_horizon - 1`；
- 请求滞后超过样本允许范围时正确收敛并记录；
- 有限样本 t 自由度正确；
- bootstrap 固定 seed 可复现；
- 默认块长和显式块长正确；
- 样本不足时推断结果为 None，而非伪造零值。

### 20.4 分品种时序回归

- 每个 product 恢复各自斜率；
- product 之间的数据互不污染；
- 每个 product 独立应用缺失和滞后政策；
- factor beta 跨 product 等权汇总；
- 跨 product beta 分布不被命名为 ICIR 或年化指标；
- 一个 product 失败不影响其他 product。

### 20.5 固定效应

- product 去均值结果与小面板 product 哑变量回归等价；
- 双向交替投影与非平衡小面板双向哑变量回归等价；
- 两个 pooled 规格使用完全相同的原始观察样本；
- 日期固定效应删除共同日期冲击；
- 只随 product 变化的控制变量被 product FE 吸收后触发秩亏；
- 只随 timestamp 变化的因子被双向 FE 吸收后触发秩亏；
- 人工强制不收敛时记录 `fixed_effect_absorption_not_converged`，且只使对应 pooled 规格失效；
- 不在结果 metadata 中增加契约之外的吸收迭代字段。

### 20.6 双向聚类

- 协方差与 `statsmodels 0.14.2` 双向 cluster 结果一致；
- `use_correction=True` 和 `df_correction=True`；
- `inference_dof = min(n_products, n_timestamps) - 1`；
- t 值、p 值和置信区间使用有限样本 t 分布；
- 任一维聚类少于 2 时返回 `insufficient_clusters`；
- 非法协方差保留点估计并将推断字段置为 NaN；
- 不回退到普通 OLS 标准误。

### 20.7 缺失、样本与共线

- `drop` 删除所有回归变量中的不完整行；
- `strict` 截面按 timestamp 失效；
- `strict` 时序按 product 失效；
- strict 失效 product 整体排除出 pooled 样本；
- `universe=False` 缺失不触发 strict；
- 样本数满足 `max(min_observations, n_parameters + 1)`；
- 常数标签返回 NaN 和稳定原因码；
- 因子与控制变量共线时不自动删列；
- 无效系数不填 0。

### 20.8 结果结构

- CS `factor_coefficients` 索引为完整 timestamp，失效项为 NaN；
- 截面 HAC 均值推断使用 `FactorBetaMeanTestResults`，且统计量对应 factor beta 的时间序列均值；
- `FactorBetaMeanTestResults` 是 `HACRegressionResults` 的独立 dataclass 子类；
- TS `per_product_results` 只保存有效 product 的 factor beta HAC 结果；
- TS 的两个 metadata DataFrame 保留完整 product 索引；
- pooled 的标量估计与推断字段只对应 factor beta；
- pooled metadata 使用契约规定的稳定索引名；
- pooled 的 `r_squared` 与 `adjusted_r_squared` 具有 within 拟合度语义；
- 所有结果都不输出截距、控制变量系数、固定效应系数或完整协方差矩阵；
- 配置、HAC 滞后和 pooled 聚类元数据符合 dataclass 契约。

测试必须在仓库根目录、Conda base 环境运行：

```powershell
conda run -n base python -m pytest tests/factors/evaluation/test_regression.py -q
```

## 21. 开发时最容易犯的错误

1. 因子和标签已经对齐后再次 shift 标签。
2. 自动把 DataFrame 所有额外列当作控制变量。
3. 在不同 timestamp 或 product 中静默删除不同的共线控制变量。
4. 用普通 iid 标准误处理重叠标签。
5. 对按 product 排列的 beta 向量执行 Newey–West 时间序列检验。
6. 只报告 pooled 显著性，不报告分品种 beta 分布。
7. 只保留带日期固定效应的规格，删除 CTA 可能收获的共同趋势。
8. 只保留不带日期固定效应的规格，忽略共同冲击解释。
9. 让两个 pooled 规格使用不同完整样本。
10. 用平衡面板双重去均值公式处理非平衡面板。
11. 构造数千列稠密日期哑变量作为生产实现。
12. 固定效应吸收后不重新检查变量变异和矩阵秩。
13. 保存截距、控制变量或固定效应系数，偏离单因子检验的数据契约。
14. 双向聚类只按日期或只按品种，遗漏另一维依赖。
15. 使用正态分布而没有记录，偏离已确定的有限样本 t 口径。
16. 只保存 OLS `dof_residual`，遗漏双向聚类真正使用的 `inference_dof`。
17. 对负的聚类方差取绝对值后开方。
18. 协方差异常时静默回退到普通 OLS 标准误。
19. 把低 $R^2$ 自动解释为因子无效。
20. 把 pooled 大 t 值解释为每个品种都显著。
21. 把截面斜率或时序斜率解释为因果效应。
22. 把回归预测值直接命名为仓位或策略收益。
23. 原地向输入 DataFrame 添加截距、残差化列或聚类编码。

## 22. 初版完成标准

达到以下条件，才能认为回归评估初版开发完成：

- `regression.py` 接收规范 `DataFrame[factor, label, universe, controls...]`；
- 控制变量只能显式声明；
- 支持带截距和不带截距的逐组回归；
- 横截面模式按 timestamp 估计 Fama–MacBeth 式回归；
- `factor_coefficients` 只保存逐期 factor beta 点估计；
- CS 单期系数不输出普通 OLS 标准误、t 值、p 值或置信区间；
- factor beta 按 timestamp 等权汇总；
- factor beta 均值使用 `FactorBetaMeanTestResults` 保存 HAC 有限样本 t 推断；
- factor beta 均值支持可复现循环块 bootstrap；
- 时序模式按 product 估计 HAC 预测回归；
- 每个有效 product 只保存 factor beta 的 HAC 稳健推断；
- factor beta 按 product 等权汇总；
- 同时输出 product FE 和 product + timestamp FE pooled 结果；
- 两个 pooled 规格使用同一完整样本；
- 非平衡面板使用交替投影吸收双向固定效应；
- pooled 不构造大规模日期哑变量，不输出固定效应系数；
- pooled 只保存 factor beta 的点估计和双向聚类推断；
- pooled 使用 product × timestamp 双向聚类；
- 双向聚类启用小样本修正和有限样本 t 推断；
- 推断自由度使用较小聚类数减一；
- 样本不足、常数标签、秩亏、聚类不足、吸收失败和协方差异常均有稳定原因码；
- 不静默删除共线变量或回退协方差类型；
- 不再次移动标签，不修改输入；
- 控制变量只参与条件化，所有结果均不保存其系数、标准误或协方差；
- 所有 factor beta、固定效应吸收与稳健推断都有人工面板 pytest。

## 23. 与后续组件的边界

### 23.1 多重检验

单次回归保存原始 p 值。跨多个因子、参数、控制规格和 horizon 的检验族定义与 Benjamini–Hochberg 等校正，由实验编排器调用 `robustness.adjust_pvalues()` 统一完成。

### 23.2 衰减

不同 `label_horizon` 的 factor beta 由评价器分别计算，再由 diagnostics 汇总成回归斜率衰减。`regression.py` 一次只处理一个标签列。

### 23.3 归因与冗余

控制变量回归可以提供增量解释力证据，但不能替代完整的分品种、分时期、分板块归因，也不能替代多因子相关性和正交化诊断。

### 23.4 signal PnL

回归斜率描述条件均值变化，不定义仓位映射。若要把因子转换为 sign、连续或 deadband 权重，并进一步处理风险缩放、换手和成本，应进入后续 `DiagnosticPortfolioExperiment`。

### 23.5 真实策略回测

回归组件不处理真实合约映射、移仓、张数、保证金和成交限制。统计显著的斜率不等于可交易或可实现收益。

本组件的核心原则可以概括为：

> 截面回归先逐期估计、再对斜率序列做时间稳健推断；时序回归先展示每个品种的异质性、再用两个固定效应 pooled 规格研究共同规律；所有稳健误差必须匹配重叠标签和面板依赖，所有失败都保留点估计边界、样本元数据与可审计原因。
