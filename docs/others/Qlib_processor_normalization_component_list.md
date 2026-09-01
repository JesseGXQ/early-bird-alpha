## User

去网络上帮我查一下，几个著名的量化研究框架，例如Qlib等，其中是否有预处理组件，例如 RankNormalizer, QuantileNormalizer等。告诉我其参数，以及每个参数所对应的具体方法。

之所以这么问，是因为我想让我的项目的预处理链的组件名称、参数，和标准实践框架做对齐。

---

## Assistant

**Tool: webfetch**

**Tool: bash**

---

## Assistant

**Tool: webfetch**

---

## Assistant

**Tool: webfetch**

---

## Assistant

我已查阅 Qlib 官方文档与源码 `qlib/data/dataset/processor.py`（main 分支），下面给出其中与归一化/标准化相关的 Processor 组件清单。**Qlib 的预处理组件实际只覆盖你既定 V1 名单的一小部分**——它没有 `QuantileNormalizer`（normal quantile 变换），也没有独立的截面 Winsorizer；最接近"rank normalization"的是 `CSRankNorm`，但做法是**线性把百分位 rank 缩放到 std=1**（`rank(pct=True) - 0.5`，再 ×3.46），并非按分位数映射到正态分布。这点对齐时要特别注意。其他主流框架（如 WorldQuant Brain 公式库、Alphalens）要么没有同名参数化的组件，要么把预处理留给上层，所以以下以 Qlib 为准绳并辅以补充。

## Qlib 的归一化类与参数

源码位置：`qlib/data/dataset/processor.py`、辅助函数在 `qlib/utils/data.py`。

| 类名 | 范畴 | 构造参数 | 参数对应的方法/语义 |
|---|---|---|---|
| `MinMaxNorm` | 时序 | `fit_start_time`, `fit_end_time` | 用 fit 时间窗内的全样本（按列，axis=0）算 min、max；`(x - min) / (max - min)`。fit 仅一次、再 transform 全期，典型防止测试集泄漏的设计 |
| | | `fields_group=None` | 作用列组（feature/label/None = 全部） |
| `ZScoreNorm` | 时序 | `fit_start_time`, `fit_end_time` | fit 窗内按列算 `mean`、`std`（`np.nanmean` / `np.nanstd`），`(x - mean) / std`；std==0 的列置 `std=1, mean=0` 跳过 |
| | | `fields_group=None` | 同上 |
| `RobustZScoreNorm` | 时序 | `fit_start_time`, `fit_end_time` | fit 窗内按列算 `median` 和 `MAD = median(|x - median|)`；`std = MAD * 1.4826`（正态一致尺度因子）。对 NaN 用 `np.nanmedian` |
| | | `fields_group=None` | 同上 |
| | | `clip_outlier=True` | 变换后用 `np.clip(X, -3, 3)`。**这是 Qlib 唯一的 winsorize 出口**——它把稳健 z-score 与 clip 绑死在一个开关里 |
| `CSZScoreNorm` | 横截面 | `fields_group=None` | 按 `groupby("datetime").apply(zscore_func)` 截面内标准化 |
| | | `method="zscore"` | 选 `qlib.utils.data.zscore`：`(x - x.mean()) / x.std()` |
| | | `method="robust"` | 选 `qlib.utils.data.robust_zscore`：median + MAD·1.4826，再 `np.clip(±3)`，可选再走一次普通 zscore（但 `CSZScoreNorm` 内部传参只有 `x`，所以走的是不带二次 zscore 的分支） |
| `CSRankNorm` | 横截面 | `fields_group=None` | 截面内 `rank(pct=True)`（0~1 均匀），再减 0.5 中心化、再 ×3.46 缩到 ≈std=1。文档解释：均匀分布的 rank 均值≈0.5、std≈1/3.46，故乘 3.46。**不是 inverse-normal-transform** |
| `CSZFillna` | 横截面 | `fields_group=None` | 截面内按 `datetime` groupby 后用截面均值填 NaN |
| `Fillna` | 通用 | `fields_group=None`, `fill_value=0` | 用常数填 NaN |
| `ProcessInf` | 通用 | （无） | 每个 datetime 分组内，把 ±inf 替换为该列非 inf 的均值 |
| `TanhProcess` | 通用 | （无） | 对非 LABEL 列做 `x - 1` 后 `np.tanh(x)`——一种非线性压缩、抗尖峰 |

> Qlib 在 `qlib/contrib/data/processor.py` 还有一个 `ConfigSectionProcessor`，但那是跟随 Alpha158 配置按"section"应用上述 processor 的元工具，不属于归一化原语本身。

## 关于 `zscore` / `robust_zscore` 辅助函数

`qlib/utils/data.py:zscore(x)`：`(x - x.mean()) / x.std()`，对 Series 与 DataFrame 通用。
`qlib/utils/data.py:robust_zscore(x, zscore=False)`：`x = x - x.median()`，`mad = x.abs().median()`，`x = clip(x / mad / 1.4826, -3, 3)`；若 `zscore=True` 再做一次普通 zscore。**注意：Qlib 的 robust z-score 把 clip(-3, 3) 写死在函数里**——这与 `RobustZScoreNorm.clip_outlier` 参数形成重复 clip（无害但说明设计是再 clip 过的）。

## 与你项目 V1 预处理器清单的对齐建议

| 你 V1 既定名 | Qlib 最近对应 | 命名/参数对齐建议 |
|---|---|---|
| `DropNonFinite` | `DropnaProcessor` + `ProcessInf` | 可保留 `DropNonFinite`，但 Qlib 把 NaN 与 ±inf 拆成两个 processor。可设参数 `non_finite: Literal["nan","inf","both"]`，对齐 Qlib 习惯 |
| `CrossSectionalWinsorizer` | 无独立类；`RobustZScoreNorm` 的 `clip_outlier=True` | 建议暴露与 Qlib 一致的 `clip_outlier: bool` 与 `clip_limit: tuple[float, float] = (-3.0, 3.0)`；同时区分"先 robust z 再 clip"（Qlib 行为）vs "纯分位 winsorize"（如 1%/99%），后者 Qlib 无现成类 |
| `RollingTimeSeriesWinsorizer` | 无 | Qlib 时序归一化是基于一次性 fit 窗，不是滚动 winsorize；本项目走滚动，需自定参数 `window`、`quantile` |
| `CrossSectionalZScore` | `CSZScoreNorm(method="zscore")` | **可直接对齐**：类名 `CSZScoreNorm`、参数 `method="zscore"`，内部调 `zscore` |
| `RobustZScore` | `CSZScoreNorm(method="robust")` 或 `RobustZScoreNorm` | 注意 Qlib 有两个不同形态：截面版 `CSZScoreNorm(method="robust")`、时序版 `RobustZScoreNorm`。建议用 `scope: Literal["cs","ts"]` 把两者合并，参数名用 `clip_outlier`、`clip_limit` 与 Qlib 对齐 |
| `RankNormalizer` | `CSRankNorm` | 命名可对齐 `CSRankNorm`，参数 `fields_group` 暂时改为你既有的列组语义；**Qlib 公式是 `(rank_pct - 0.5) * 3.46`**，不映射到正态分位——建议增加参数 `method: Literal["uniform","normal"]`：`uniform` 跟 Qlib 一致，`normal` 走 `scipy.stats.norm.ppf((rank-0.5)/N)` |
| `QuantileNormalizer` | **无同义类** | Qlib 没有 inverse-normal-transform 的 processor；稳健性参考可取自 alphalens 的 `quantile_transform`（sklearn `QuantileTransformer`）。建议命名上区分：`RankNormalizer(method="normal")` 即 inverse-normal，`RankNormalizer(method="uniform")` 即 Qlib 的 `CSRankNorm`；不要把 `QuantileNormalizer` 与 `RankNormalizer` 拆成两个独立类——这是与 Qlib 体系最大的差异 |
| `ExposureNeutralizer` | 无独立类 | Qlib 中性化是靠自定义 processor 接入，没有现成类；建议按照其 fit/transform 模式自洽命名，参数化用 `exposures: list[str]` |
| `RollingSmoother` / `EwmSmoother` | 无 | Qlib 不内置；纯本地设计，命名可参考 pandas 习惯（`RollingSmoother(window=, center=False)`，`EwmSmoother(span=, halflife=)`） |
| `PreprocessingPipeline` | `DataHandlerLP` 的 `infer_processors`/`learn_processors` 列表 | Qlib 用 YAML list 而非显式 Pipeline 类；你可保留显式类，但**每个组件构造参数都要支持 dict 反序列化**（Qlib 风格）以便配置注入 |

## 几点需要决策的对齐点

1. **是否接受 Qlib 的 fit/transform 双阶段范式**：Qlib 的 `MinMaxNorm`、`ZScoreNorm`、`RobustZScoreNorm` 都是 fit-once-transform-many（用 `fit_start_time`/`fit_end_time` 控制泄漏），而 `CSZScoreNorm`、`CSRankNorm`、`CSZFillna` 是无状态的逐截面变换。你的 V1 既定规则"可拟合变换器只能在训练集 fit、然后对验证集和样本外 transform"完全对应 Qlib 这套设计。**建议把所有 V1 预处理器统一拆成有状态/无状态两类，类名上与 Qlib 保持前缀区分**（如 `CS*` 表示 cross-sectional 无状态 fit-free，其余为时序有状态需 fit）。

2. **Qlib 的 `CSRankNorm` 不等于 normal-quantile normalization**：这是 Qlib 命名上的小误导。若想让 `QuantileNormalizer` 拥有标准实践背书，需要引用 sklearn `QuantileTransformer(output_distribution="normal")` 或 WorldQuant Brain 的 `rank_norm` 函数（其内部确实用 inverse normal CDF）。在没有权威框架同名同参实现支撑的情况下，建议参数上提供 `method` 开关，让两种语义共存，并明确文档。

3. **`clip_outlier` 的语义冲突**：Qlib 在 `robust_zscore` 函数里**写死** clip 到 [-3,3]，又在 `RobustZScoreNorm` 提供 `clip_outlier` 参数，造成同一处理被两次 clip（结果不变但语义冗余）。你的 `CrossSectionalWinsorizer` 与 `RobustZScore` 是两个独立组件，**不要按 Qlib 这种"绑死"的写法**——应把 winsorize 显式独立，让 RobustZScore 不内含 clip，由 pipeline 顺序决定先后。

4. **框架覆盖差异**：WorldQuant Brain 在公式层提供 `rank()`、`zscore()`、`winsorize()`、`normalize()`（这里 normalize 即 rank→normal 的归一）等内联操作符，不是组件化类，参数化极弱；Alphalens 不做预处理，要求用户自带干净因子。**Qlib 是目前唯一提供参数化 Processor 组件、且代码可直接作为命名/参数对齐参考的主流开源框架**，以上的对齐建议主要以 Qlib 为准。

需要我据此起草一份 V1 预处理组件命名与参数的最终对齐方案（含每个组件的构造签名草稿）吗？若需要，请确认是否按上面的"分类前置 + `method` 开关双语义 + winsorize 独立"思路落地。

---