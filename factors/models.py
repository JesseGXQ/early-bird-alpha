"""

因子研究模块 — 核心领域对象

V1 定义因子研究全链路所需的不可变数据模型，包括：
    - 数据集规格、品种池准入条件、数据质量摘要
    - 因子计算状态与结果
    - 标签定义与计算结果
    - 评价配置与结果汇总
    - 实验身份追踪与候选因子交付

所有 DataFrame/Series 字段使用 repr=False 避免日志输出巨型面板。
冻结 dataclass 只能防止属性重新赋值，不能使 DataFrame 真正不可变；
组件必须遵守"不原地修改输入"契约（由测试保护）。

"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal, Mapping

if TYPE_CHECKING:
    import pandas as pd


# ============================================================
# 模块级常量
# ============================================================

# 研究面板 MultiIndex 的两个层级名称（架构文档 §5.1）
INDEX_NAMES: tuple[str, str] = ('timestamp', 'product')

# 研究面板必需字段（架构文档 §5.1）
REQUIRED_COLUMNS: tuple[str, ...] = (
    'trading_date',
    'Dominant_Contract',
    'Adj_Open', 'Adj_High', 'Adj_Low', 'Adj_Close',
    'Raw_Open', 'Raw_High', 'Raw_Low', 'Raw_Close',
    'Volume',
    'OpenInterest',
    'True_Return',
    'is_roll',
)

# 可选期限结构字段（架构文档 §5.1）
OPTIONAL_COLUMNS: tuple[str, ...] = (
    'Near_Contract', 'Near_Price', 'Near_DaysToExpiry',
    'Far_Contract',   'Far_Price',   'Far_DaysToExpiry',
)


# ============================================================
# 1. 数据集模型（DatasetSpec / UniverseSpec / DataQualitySummary / ResearchDataset）
# ============================================================

@dataclass(frozen=True)
class UniverseSpec:
    """
    动态品种池的准入条件设定。

    所有过滤条件在历史时点 t 只能使用其判定时点已经可见的数据。
    品种池按品种维度计算，不因主力合约换月重置。

    Parameters
    ----------
    min_listing_days : int
        品种上市后至少经历多少日历日才可纳入。
    liquidity_lookback : int
        成交量/流动性回看窗口长度（交易日数）。
    min_daily_volume : int
        单日最低成交量（手）；为 0 则表示不启用该条件。
    min_average_volume : int
        回看窗口内滚动平均最低成交量（手）；为 0 则表示不启用。
    min_open_interest : int
        单日最低持仓量（手）；为 0 则表示不启用。
     min_data_coverage : float
        品种有效数据最低覆盖率阈值，取值范围 [0, 1]。

    """

    min_listing_days: int = 126
    liquidity_lookback: int = 20
    min_daily_volume: int = 0
    min_average_volume: int = 0
    min_open_interest: int = 0
    min_data_coverage: float = 0.5


@dataclass(frozen=True)
class DatasetSpec:
    """
    研究数据请求的完整描述。

    聚合了品种列表、日期范围、频率、视图模式和品种池准入条件，
    是一个实验的"数据配置"单件。

    Parameters
    ----------
    products : tuple of str
        品种代码，例如 ('RB', 'MA', 'FG')。
    start_date : date or None, optional, default None
        数据起始日期（闭区间），None 表示最早可用。
    end_date : date or None, optional, default None
        数据结束日期（闭区间），None 表示最近可用。
    freq : Literal['1d']
        K 线频率。V1 仅支持日频，其他频率直接报错。
    view : {'scheduled', 'realized'}
        计划视图 or 实际视图。
    universe : UniverseSpec
        动态品种池准入条件。

    Raises
    ------
    ValueError
        若 freq 不是 '1d'（日内研究不在 V1 架构内，参见架构文档 §9.1）。

    """

    products: tuple[str, ...]
    start_date: date | None = None
    end_date: date | None = None
    freq: Literal['1d'] = '1d'
    view: Literal['scheduled', 'realized'] = 'scheduled'
    universe: UniverseSpec = field(default_factory=UniverseSpec)

    def __post_init__(self):
        if self.freq != '1d':
            raise ValueError(
                f"V1 仅支持日频（freq='1d'），收到 freq='{self.freq}'。"
                f" 日内因子研究不在当前系统架构内。"
            )


@dataclass(frozen=True)
class DataQualitySummary:
    """
    数据集校验后的质量摘要。

    不包含原始数据，只保存校验指标、警告和错误列表。
    is_valid=False 时 errors 非空，说明数据不可用于因子研究。

    Parameters
    ----------
    is_valid : bool
        整体校验是否通过。
    total_rows : int
        研究面板总行数。
    product_sample_counts : dict[str, int] or None
        每个品种的样本数（按产品代码分组计数）。
    missing_rate_by_column : dict[str, float]
        每列的缺失比例（0 ~ 1）。
    warnings : tuple of str
        非致命问题的结构化警告列表。
    errors : tuple of str
        导致 is_valid=False 的致命错误列表。

    """

    is_valid: bool
    total_rows: int
    product_sample_counts: Mapping[str, int] | None
    missing_rate_by_column: Mapping[str, float]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ResearchDataset:
    """
    统一的多品种日频研究面板。

    这是因子研究链路的唯一数据入口。包含：
    - 长表格式的行情与收益数据（MultiIndex[timestamp, product]）
    - 请求时的数据规格
    - 数据质量摘要
    - 逐时点品种准入掩码（由 DynamicUniverse 生成，之后注入）

    DataFrame 本身可变，但组件必须以"不原地修改输入"为契约。

    Parameters
    ----------
    df : pd.DataFrame
        长表格式研究面板，索引为 MultiIndex[timestamp, product]。
        必需列参见 REQUIRED_COLUMNS。
    spec : DatasetSpec
        构建该数据集时使用的数据请求规格。
    quality : DataQualitySummary
        数据质量校验结果。
    universe : pd.Series
        与 df 同索引的布尔准入表，True 表示该行品种在该时点可研究。
        初始可以为空 ，在 DynamicUniverse.build() 后注入。

    """

    df: pd.DataFrame = field(repr=False)
    spec: DatasetSpec
    quality: DataQualitySummary
    universe: pd.Series = field(repr=False)


# ============================================================
# 2. 因子模型（FactorState / IncrementalFactorResult / FactorResult）
# ============================================================

class FactorState:
    """
    因子增量计算的运行状态基类。

    具体因子（如 TimeSeriesMomentumFactor）继承此类，保存滚动窗口缓存、
    累计统计量等。状态必须支持重置（reset）和序列化（to_dict/from_dict），
    以便实验恢复和策略热启动。

    架构不变量（架构文档 §6.2）：
    - 不得保存标签、策略权重、目标仓位或账户状态。
    - 不得引用完整 ResearchDataset。
    - 不得在状态中引用未来数据。

    Attributes
    ----------
    _name : str
        所属因子名称，用于序列化后恢复归属。

    Methods
    -------
    reset()
        将所有内部状态重置为初始值。
    to_dict()
        将状态序列化为普通字典。
    from_dict(d)
        从字典恢复状态实例（类方法）。

    """

    __slots__ = ('_name',)

    def __init__(self, name: str = ''):
        self._name = name

    def reset(self) -> None:
        """
        将因子运行状态重置为初始值。

        子类必须覆盖此方法并清除所有滚动缓存和累计量。
        基类只保留 _name。
        """
        pass

    def to_dict(self) -> dict[str, Any]:
        """
        将状态序列化为可 JSON 兼容的字典。
        子类必须覆盖以导出滚动缓存等内容。

        Returns
        -------
        dict[str, Any]
            包含 name 及所有子类状态的字典。
        """
        return {'name': self._name}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'FactorState':
        """
        从序列化字典恢复状态。

        子类必须覆盖此方法以重建自身的具体状态类型。

        Parameters
        ----------
        d : dict[str, Any]
            to_dict() 输出的字典。

        Returns
        -------
        FactorState
            恢复后的状态实例。
        """
        return cls(name=d.get('name', ''))


@dataclass(frozen=True)
class IncrementalFactorResult:
    """
    一次增量计算的返回封装。

    在逐 Bar 遍历时，每个时间戳返回：
    - 该戳下所有品种的因子值。
    - 更新后的因子状态（供下一步使用）。
    - 当前时间戳以供校验。

    Parameters
    ----------
    values : pd.Series
        当前时间戳各品种的因子值，索引为 product。
    state : FactorState
        计算完成后的最新因子状态。
    timestamp : Any
        当前 Bar 的时间戳，与面板索引对齐。

    """

    values: 'pd.Series' = field(repr=False)
    state: FactorState
    timestamp: Any


@dataclass(frozen=True)
class FactorResult:
    """
    因子计算完整结果。

    聚合了因子名称、类型、参数、因子值 Series 和质量基础信息。
    values 为以研究面板索引对齐的完整时序因子值（跨品种、跨时间）。

    Parameters
    ----------
    name : str
        因子唯一名称，例如 'ts_momentum_20'。
    values : pd.Series
        因子值，索引与输入面板完全一致，Index: MultiIndex[timestamp, product]。
    parameters : Mapping[str, Any]
        本次计算的参数快照（如 lookback=20）。
    required_columns : tuple of str
        因子声明的必需输入列。
    min_history : int
        因子计算所需的最短历史窗口（期数）。
    compute_time_seconds : float
        计算耗时（秒）。
    coverage_rate : float
        因子有效值覆盖率（非 NaN 比例）。
    effective_start : Any
        第一个有效因子值所在的时间戳。
    effective_end : Any
        最后一个有效因子值所在的时间戳。
    warnings : tuple of str
        计算过程中产生的警告信息。

    """

    name: str
    values: 'pd.Series' = field(repr=False)
    parameters: Mapping[str, Any]
    required_columns: tuple[str, ...]
    min_history: int
    compute_time_seconds: float
    coverage_rate: float
    effective_start: Any = None
    effective_end: Any = None
    warnings: tuple[str, ...] = ()


# ============================================================
# 3. 标签模型（LabelSpec / LabelResult）
# ============================================================

@dataclass(frozen=True)
class LabelSpec:
    """
    单条标签构造规格。

    通过 method 区分标签类型，具体计算逻辑由 labels.py 中的
    LabelBuilder 根据 method 分发。

    Parameters
    ----------
    name : str
        标签唯一名称，例如 'fwd_return_5d'。
    method : {'forward_return', 'direction', 'future_volatility', 'residual_return', 'event'}
        标签类型：
        - 'forward_return'：未来 h 期复合收益。
        - 'direction'：未来收益方向（+1 / -1）。
        - 'future_volatility'：未来 h 期已实现波动率。
        - 'residual_return'：基准或风险暴露调整后的残差收益。
        - 'event'：可配置阈值触发的事件标签（如涨跌停、换月等）。
    horizon : int
        预测期限（K 线期数），例如 5 表示未来 5 期。
    source_column : str
        用于构造标签的数据列，默认 'True_Return'。
    min_valid_obs : int
        构造标签需要的最少有效观测数。不足时该标签为 NaN。
    additional_metadata : Mapping[str, Any] or None
        标签类型专属的可选额外参数（为 None 表示不适用）：
        - 'residual_return' 需提供基准列和拟合窗口。
        - 'event' 需提供触发条件表达式或阈值。

    """

    name: str
    method: Literal[
        'forward_return',
        'direction',
        'future_volatility',
        'residual_return',
        'event',
    ]
    horizon: int
    source_column: str = 'True_Return'
    min_valid_obs: int = 1
    additional_metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class LabelResult:
    """
    标签计算结果容器，可承载多列标签。例如未来5日收益率，未来10日收益率，未来5日residual收益率等。

    包含全部已构造标签的 DataFrame 及其构造规格。
    labels 与研究面板同索引，每列对应一个标签定义。

    Parameters
    ----------
    labels : pd.DataFrame
        标签值，索引: MultiIndex[timestamp, product]，列名 = LabelSpec.name。
    specs : tuple of LabelSpec
        参与构造的全部标签规格。
    metadata : Mapping[str, Any] or None
        构造过程的元数据（构造时间、警告等），为 None 表示未记录。

    """

    labels: pd.DataFrame = field(repr=False)
    specs: tuple['LabelSpec', ...]
    metadata: Mapping[str, Any] | None = None


# ============================================================
# 4. 评价模型（EvaluationConfig / FactorEvaluationResult）
# ============================================================

@dataclass(frozen=True)
class EvaluationConfig:
    """
    单因子单标签的评价配置。

    指定评价模式、最小样本、分组参数和缺失值处理政策。
    不同的因子和标签可以共用同一配置，也可以在实验中各自独立指定。

    Parameters
    ----------
    mode : {'cross_sectional', 'time_series'}
        - 'cross_sectional'：每个时间戳跨品种计算指标（IC、分组等），
           然后沿时间序列汇总。
        - 'time_series'：每个品种沿时间计算指标，适用于因子对单一品种自身
           未来收益的时序预测关系评估。
    label_horizon : int
        参与本次评价的标签期限。
        由于 LabelResult 可能包含多个 horizon 的标签列，此字段指定具体使用哪一期。
        该字段是一个交叉引用键，用于告诉 FactorEvaluator 从 LabelResult.labels 中的多列中选择哪一列。
    min_observations : int
        IC 计算的最少有效品种（或观测）数。不足则返回 NaN 并记录警告。
    n_groups : int
        分组收益的组数，默认 5。
    grouping_direction : {'ascending', 'descending'}
        按因子值分组的方向：
        - 'ascending'：因子值最小的品种为第 1 组。
        - 'descending'：因子值最大的品种为第 1 组。
    nan_policy : {'strict', 'drop'}
        - 'strict'：字段缺失时保留 NaN 并记录警告。
        - 'drop'：计算前剔除缺失值。
    annualization_factor : int or None
        年化因子。例如日频数据设为 252。为 None 则不年化。

    """

    mode: Literal['cross_sectional', 'time_series']
    label_horizon: int
    min_observations: int = 10
    n_groups: int = 5
    grouping_direction: Literal['ascending', 'descending'] = 'ascending'
    nan_policy: Literal['strict', 'drop'] = 'strict'
    annualization_factor: int | None = None


@dataclass(frozen=True)
class FactorEvaluationResult:
    """
    一个因子对一个标签的完整评价结果。

    这是 FactorEvaluator 编排输出的汇总容器。各子结果可以独立为
    DataFrame / dict，具体列名和结构由对应评价子模块定义。

    Parameters
    ----------
    factor_name : str
        被评价因子名称。
    label_spec : LabelSpec
        参与本次评价的标签规格，完整记录标签名称、方法、期限与构造参数。
    config : EvaluationConfig
        本次评价使用的配置。
    coverage : pd.DataFrame or None
        覆盖率明细（总体、分时点、分品种），此处的覆盖率指的是 "因子非空 ∩ 标签非空"。
    ic_detail : pd.DataFrame or None
        IC / Rank IC 逐期明细。
    ic_summary : dict or None
        IC 汇总统计（均值、标准差、ICIR、正值比例等）。
    grouped_returns : pd.DataFrame or None
        分组未来收益（各组平均标签、最高组减最低组等）。
    turnover : pd.DataFrame or None
        因子换手明细。
    attribution : pd.DataFrame or None
        归因结果（分品种、分时期、分品种组、分市场状态等）。
    robustness : dict or None
        稳健性结果（样本内外对比、参数敏感性、bootstrap、多重检验等）。
    redundancy : dict or None
        因子冗余分析结果（相关性、正交化前后对比、边际 IC 等）。
    warnings : tuple of str
        评价过程中产生的结构化警告。

    """

    factor_name: str
    label_spec: LabelSpec
    config: EvaluationConfig
    coverage: pd.DataFrame | None = None
    ic_detail: pd.DataFrame | None = None
    ic_summary: dict[str, Any] | None = None
    grouped_returns: pd.DataFrame | None = None
    turnover: pd.DataFrame | None = None
    attribution: pd.DataFrame | None = None
    robustness: dict[str, Any] | None = None
    redundancy: dict[str, Any] | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecayResult:
    """
    单因子在多个预测期限上的预测能力衰减分析结果。

    将同一因子对不同 horizon 标签的 IC、Rank IC 和多空组收益
    汇总为衰减表，展示预测力随期限的消退速度。

    在实验流程中，Runner 先对每个 horizon 调用 FactorEvaluator，
    再将各 horizon 的结果汇总为 DecayResult：

        for each horizon:
            FactorEvaluator.evaluate(factor, label[h]) → FactorEvaluationResult
        ── 汇总 ──
        DecayResult(factor_name, [label_1, label_5, label_10, label_20], ...)

    Parameters
    ----------
    factor_name : str
        被评价因子名称。
    label_specs : tuple of LabelSpec
        参与衰减分析的标签规格列表（按 horizon 升序排列）。
    ic_summary_by_horizon : pd.DataFrame or None
        各 horizon 的 IC 汇总统计，行 = horizon，列 = (mean, std, ICIR, positive_ratio, ...)。
    rank_ic_summary_by_horizon : pd.DataFrame or None
        各 horizon 的 Rank IC 汇总统计。
    long_short_by_horizon : pd.DataFrame or None
        各 horizon 的多空组（最高组 - 最低组）平均收益。

    """

    factor_name: str
    label_specs: tuple[LabelSpec, ...]
    ic_summary_by_horizon: pd.DataFrame | None = None
    rank_ic_summary_by_horizon: pd.DataFrame | None = None
    long_short_by_horizon: pd.DataFrame | None = None


# ============================================================
# 5. 实验模型（ExperimentIdentity / CandidateFactor）
# ============================================================

@dataclass(frozen=True)
class ExperimentIdentity:
    """
    一次因子研究实验的不可变身份标识。

    用于复现、缓存和结果追溯。包含了数据指纹、代码版本和配置哈希，
    确保相同输入产生相同结果（架构不变量之一）。

    Parameters
    ----------
    experiment_id : str
        实验唯一 ID（由 Runner 生成）。
    config_hash : str
        完整实验配置的哈希值。
    data_fingerprint : str
        输入数据的指纹（如数据起止日期、品种列表、缺失率等）。
    git_commit : str or None
        运行时的 Git commit hash。
    python_version : str or None
        Python 版本字符串。
    created_at : datetime or None
        实验创建时间。

    """

    experiment_id: str
    config_hash: str
    data_fingerprint: str
    git_commit: str | None = None
    python_version: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class CandidateFactor:
    """
    研究者认可的可交付候选因子。

    这是研究层向策略层的形式化交付物。CandidateFactor 不是交易信号，
    不自动包含目标仓位或权重。创建必须由研究者显式调用，
    不设"IC 大于某值自动批准"规则（架构文档 §6.7）。

    Parameters
    ----------
    name : str
        因子稳定名称。
    factor_type : {'time_series', 'cross_sectional'}
        因子计算模式。
    parameters : Mapping[str, Any]
        因子参数的可序列化快照。
    required_columns : tuple of str
        因子声明依赖的输入字段。
    min_history : int
        因子计算所需的最短历史窗口（期数）。
    research_evidence_ref : str or None
        支持该因子的研究证据引用（如实验 ID 或报告路径）。
    researcher_notes : str or None
        研究者备注。
    effective_scope : Mapping[str, Any] or None
        研究者基于归因事实手动填写的结构化适用范围建议（非自动导出）：
        - recommended_sectors：推荐适用的品种组（如 ["black_metals", "nonferrous"]）。
        - excluded_sectors：建议排除的品种组。
        - volatility_regime：推荐的市场波动状态（如 "medium"）。
        - evidence：指向完整归因报告的实验 ID。
        为 None 表示研究者尚未填写。

    """

    name: str
    factor_type: Literal['time_series', 'cross_sectional']
    parameters: Mapping[str, Any]
    required_columns: tuple[str, ...]
    min_history: int
    research_evidence_ref: str | None = None
    researcher_notes: str | None = None
    effective_scope: Mapping[str, Any] | None = None
