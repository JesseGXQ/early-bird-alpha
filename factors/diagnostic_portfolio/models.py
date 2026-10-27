"""

组合实验层 — 核心领域对象。

实现《20260924 组合实验层组件开发方案》第 6 章的领域对象：

    - DiagnosticSignal           输入的单列信号
    - RebalanceConfig            调仓日程配置
    - RiskConfig                 时序配方风险参数
    - ConstraintConfig           权重约束
    - CostConfig                 单边成本代理
    - DiagnosticPortfolioSpec    一次组合实验的完整配置
    - DiagnosticPortfolioResult  组合实验的完整结果容器

所有配置与结果对象均为 frozen dataclass。冻结只能防止属性重新绑定，
不能让 pandas 对象真正不可变；调用方必须遵守“不原地修改输入”契约
（由测试保护）。

"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import numpy as np
import pandas as pd

from factors.models import INDEX_NAMES, HarvestProfile


# ============================================================
# 模块级常量
# ============================================================

# V1 可执行研究配方对应的 HarvestProfile 名称（开发方案 §7.2）。
# DiagnosticPortfolioSpec 只接受这三个范式，其余范式明确拒绝，
# 不得静默降级成语义不同的权重规则。
SUPPORTED_PROFILE_NAMES: tuple[str, ...] = (
    'ts_inv_vol_gross_one',
    'ts_inv_vol_target_vol',
    'market_rank_ls',
)

# 信号来源阶段。
_SOURCE_STAGES: tuple[str, ...] = ('raw', 'preprocessed', 'composite')

# DiagnosticSignal.warnings 允许的阶段键。
_WARNING_STAGES: tuple[str, ...] = (
    'factor_computation', 'preprocess', 'combination'
)


# ============================================================
# 内部校验辅助函数
# ============================================================

def _require_non_empty_str(value: Any, field_name: str) -> None:
    """
    校验字段为非空字符串。

    Parameters
    ----------
    value : Any
        待校验值。
    field_name : str
        字段名称，用于错误信息。

    Raises
    ------
    ValueError
        当值不是字符串或为空字符串时抛出。

    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} 必须是非空字符串，收到 {value!r}')


def _require_positive_int(value: Any, field_name: str) -> None:
    """
    校验字段为严格正整数（排除 bool）。

    Parameters
    ----------
    value : Any
        待校验值。
    field_name : str
        字段名称，用于错误信息。

    Raises
    ------
    ValueError
        当值不是正整数时抛出。

    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{field_name} 必须是正整数，收到 {value!r}')


def _require_finite_number(value: Any, field_name: str) -> None:
    """
    校验字段为有限实数（排除 bool）。

    Parameters
    ----------
    value : Any
        待校验值。
    field_name : str
        字段名称，用于错误信息。

    Raises
    ------
    ValueError
        当值不是有限实数时抛出。

    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field_name} 必须是有限实数，收到 {value!r}')
    if not math.isfinite(value):
        raise ValueError(f'{field_name} 必须是有限实数，收到 {value!r}')


def _require_positive_number(value: Any, field_name: str) -> None:
    """
    校验字段为严格正的有限实数（排除 bool）。

    Parameters
    ----------
    value : Any
        待校验值。
    field_name : str
        字段名称，用于错误信息。

    Raises
    ------
    ValueError
        当值不是严格正的有限实数时抛出。

    """
    _require_finite_number(value, field_name)
    if value <= 0:
        raise ValueError(f'{field_name} 必须严格大于 0，收到 {value!r}')


# ============================================================
# 6.1 DiagnosticSignal
# ============================================================

@dataclass(frozen=True)
class DiagnosticSignal:
    """
    进入组合实验的单列信号。

    表示一个已经明确的预测变量，由原始因子或完成预处理后的单列结果
    包装而成。V1 不接受多列 DataFrame；复合因子以后也必须先收敛为
    一列明确的预测变量，再扩展 source_stage 枚举。

    Parameters
    ----------
    name : str
        信号稳定名称，非空。
    values : pd.Series
        单列数值信号，索引为 MultiIndex[timestamp, product]。
    source_stage : {'raw', 'preprocessed', 'composite'}
        信号来源阶段。
    factor_name : str
        来源因子名称，非空。
    recipe : Mapping[str, Any]
        因子参数、预处理配方、合成算子配置的可序列化快照，不允许放入策略、账户或
        优化器实例。
    warnings : Mapping[str, Any], optional
        按来源阶段分别汇总的上游警告，默认空字典。键为阶段名称，取值
        仅允许：

        - ``'factor_computation'``：来自“因子计算”阶段的警告。
        - ``'preprocess'``：来自“因子预处理”阶段的警告。
        - ``'combination'``: 来自“因子合成”阶段的警告。

        不同阶段警告分开保存，而不是合并进同一个元组。

    Notes
    -----
    索引层级必须严格命名为 ``timestamp`` 和 ``product``，且必须唯一。
    允许 NaN（按缺失信号政策处理），但不允许正负无穷。

    """

    name: str
    values: pd.Series = field(repr=False)
    source_stage: Literal['raw', 'preprocessed', 'composite']
    factor_name: str
    recipe: Mapping[str, Any] = field(default_factory=dict)
    warnings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 不允许信号为全 NaN、或仅有单值
        if self.values.isna().all():
            raise ValueError('信号为全 NaN。')
        if self.values.nunique() == 1:
            raise ValueError(f'信号只有单值: {self.values.unique()}')
        
        _require_non_empty_str(self.name, 'DiagnosticSignal.name')
        _require_non_empty_str(self.factor_name, 'DiagnosticSignal.factor_name')
        if self.source_stage not in _SOURCE_STAGES:
            raise ValueError(
                f"DiagnosticSignal.source_stage 必须是 {_SOURCE_STAGES} 之一，"
                f'收到 {self.source_stage!r}'
            )
        if not isinstance(self.values, pd.Series):
            raise ValueError(
                f'DiagnosticSignal.values 必须是 pd.Series，'
                f'收到 {type(self.values).__name__}'
            )
        if pd.api.types.is_bool_dtype(self.values.dtype) or \
                not pd.api.types.is_numeric_dtype(self.values.dtype):
            raise ValueError(
                f'DiagnosticSignal.values 必须是数值型 Series，'
                f'收到 dtype={self.values.dtype}'
            )

        index = self.values.index
        if not isinstance(index, pd.MultiIndex):
            raise ValueError(
                'DiagnosticSignal.values 的索引必须是 '
                f'MultiIndex{INDEX_NAMES}'
            )
        if tuple(index.names) != INDEX_NAMES:
            raise ValueError(
                f'DiagnosticSignal.values 的索引层级名称必须严格为 '
                f'{INDEX_NAMES}，收到 {tuple(index.names)}'
            )
        if not index.is_unique:
            raise ValueError('DiagnosticSignal.values 的索引必须唯一')

        numeric = self.values.to_numpy(dtype='float64', na_value=np.nan)
        if np.isinf(numeric).any():
            raise ValueError('DiagnosticSignal.values 不允许包含正负无穷')

        if not isinstance(self.recipe, Mapping):
            raise ValueError(
                f'DiagnosticSignal.recipe 必须是 Mapping，'
                f'收到 {type(self.recipe).__name__}'
            )
        for key in self.recipe:
            if not isinstance(key, str):
                raise ValueError(
                    f'DiagnosticSignal.recipe 的键必须是字符串，收到 {key!r}'
                )
        if not isinstance(self.warnings, Mapping):
            raise ValueError(
                f'DiagnosticSignal.warnings 必须是 Mapping，'
                f'收到 {type(self.warnings).__name__}'
            )
        unknown_stages = set(self.warnings) - set(_WARNING_STAGES)
        if unknown_stages:
            raise ValueError(
                f'DiagnosticSignal.warnings 的键必须是 {_WARNING_STAGES} '
                f'之一，收到非法键 {sorted(unknown_stages)}'
            )


# ============================================================
# 6.2 RebalanceConfig
# ============================================================

@dataclass(frozen=True)
class RebalanceConfig:
    """
    全局固定间隔调仓规则。

    Parameters
    ----------
    interval : int
        正整数，表示每隔多少个全局研究时点调仓一次。
    anchor : date or pd.Timestamp or None, optional
        调仓锚点；None 表示全局时间轴首个时间戳。anchor 不在时间轴上
        时，使用大于等于 anchor 的首个时间戳作为实际锚点，默认 None。

    Notes
    -----
    anchor 与调仓日相对全局时间轴的位置在运行期确定；本对象不持有
    时间轴，因此不在此处校验“anchor 晚于倒数第二个时间戳”。

    """

    interval: int
    anchor: date | pd.Timestamp | None = None

    def __post_init__(self) -> None:
        _require_positive_int(self.interval, 'RebalanceConfig.interval')
        if self.anchor is not None and not isinstance(self.anchor, date):
            raise ValueError(
                'RebalanceConfig.anchor 必须是 date / pd.Timestamp / None，'
                f'收到 {type(self.anchor).__name__}'
            )


# ============================================================
# 6.3 RiskConfig
# ============================================================

@dataclass(frozen=True)
class RiskConfig:
    """
    时序组合风险参数。

    Parameters
    ----------
    volatility_lookback : int
        单品种事前波动率回看窗口长度（全局研究时点数），正整数。
    volatility_min_periods : int
        单品种波动率所需最少有效收益数，至少为 2 且不大于
        volatility_lookback。
    covariance_lookback : int or None, optional
        滚动样本协方差回看窗口长度；目标波动率配方必填，默认 None。
    covariance_min_periods : int or None, optional
        协方差每个品种所需最少有效观测数；与 covariance_lookback 必须
        同时提供或同时为 None（目标波动率配方必填），默认 None。
    target_volatility : float or None, optional
        年化目标波动率，目标波动率配方必填且为正数，默认 None。

    Notes
    -----
    所有经济参数都显式配置，不为波动率窗口、协方差窗口和目标波动率
    设置隐藏默认值。滚动标准差使用 ddof=1。

    """

    volatility_lookback: int
    volatility_min_periods: int
    covariance_lookback: int | None = None
    covariance_min_periods: int | None = None
    target_volatility: float | None = None

    def __post_init__(self) -> None:
        _require_positive_int(
            self.volatility_lookback, 'RiskConfig.volatility_lookback'
        )
        if isinstance(self.volatility_min_periods, bool) or \
                not isinstance(self.volatility_min_periods, int) or \
                self.volatility_min_periods < 2:
            raise ValueError(
                'RiskConfig.volatility_min_periods 必须是至少为 2 的整数，'
                f'收到 {self.volatility_min_periods!r}'
            )
        if self.volatility_min_periods > self.volatility_lookback:
            raise ValueError(
                'RiskConfig.volatility_min_periods 不能大于 '
                'volatility_lookback，收到 '
                f'{self.volatility_min_periods} > {self.volatility_lookback}'
            )

        has_lookback = self.covariance_lookback is not None
        has_min_periods = self.covariance_min_periods is not None
        if has_lookback != has_min_periods:
            raise ValueError(
                'RiskConfig.covariance_lookback 与 covariance_min_periods '
                '必须同时提供或同时为 None'
            )
        if has_lookback:
            _require_positive_int(
                self.covariance_lookback, 'RiskConfig.covariance_lookback'
            )
            if isinstance(self.covariance_min_periods, bool) or \
                    not isinstance(self.covariance_min_periods, int) or \
                    self.covariance_min_periods < 2:
                raise ValueError(
                    'RiskConfig.covariance_min_periods 必须是至少为 2 的整数，'
                    f'收到 {self.covariance_min_periods!r}'
                )
            if self.covariance_min_periods > self.covariance_lookback: # pyright: ignore[reportOperatorIssue]
                raise ValueError(
                    'RiskConfig.covariance_min_periods 不能大于 '
                    'covariance_lookback，收到 '
                    f'{self.covariance_min_periods} > '
                    f'{self.covariance_lookback}'
                )

        if self.target_volatility is not None:
            _require_positive_number(
                self.target_volatility, 'RiskConfig.target_volatility'
            )


# ============================================================
# 6.4 ConstraintConfig
# ============================================================

@dataclass(frozen=True)
class ConstraintConfig:
    """
    最终权重约束。

    Parameters
    ----------
    max_gross : float, optional
        最终 gross 上限（绝对权重之和），正数。
        当 HarvestProfile.portfolio_target != 'gross_one' 时适用。
    max_product_weight : float or None, optional
        单品种最终绝对权重上限，取值 (0, +inf)；None 表示不设上限，
        默认 None。
    max_volatility_scaler : float or None, optional
        目标波动率模型允许采用的最大缩放倍数，正数；None 表示不设上限，
        默认 None。该字段限制的是风险模型对基础权重的放大倍数，不表示
        组合最终的 gross 名义敞口。

    Notes
    -----
    max_volatility_scaler 只约束目标波动率缩放倍数，防止过低或不稳定的
    事前风险估计造成异常放大；max_gross 约束最终绝对权重之和，是研究层
    的 gross 名义敞口上限。**权重缩放前，若生成基础权重时采用 gross=1 ，
    则组合最终的 gross <= min(max_volatility_scalar，max_gross)**。
    例如 max_volatility_scalar = 2, max_gross = 3，则组合最终 gross 不会
    大于2。

    另一方面， max_volatility_scalr 和 max_gross 分别表示
    “风险模型保护”和“组合敞口保护”，不能混用。

    """

    max_gross: float | None = None
    max_product_weight: float | None = None
    max_volatility_scaler: float | None = None

    def __post_init__(self) -> None:
        if self.max_gross is not None:
            _require_positive_number(
                self.max_gross, 'ConstraintConfig.max_gross'
            )
        if self.max_product_weight is not None:
            _require_positive_number(
                self.max_product_weight, 'ConstraintConfig.max_product_weight'
            )
        if self.max_volatility_scaler is not None:
            _require_positive_number(
                self.max_volatility_scaler,
                'ConstraintConfig.max_volatility_scaler',
            )


# ============================================================
# 6.5 CostConfig
# ============================================================

@dataclass(frozen=True)
class CostConfig:
    """
    单边单位权重成本代理。

    Parameters
    ----------
    rate : float or Mapping[str, float]
        统一成本率，或按品种的单边单位权重成本率。Mapping 的品种代码
        在构造时规范化为大写。

    Notes
    -----
    所有成本率必须有限且非负。Mapping 必须覆盖
    DatasetSpec.products 中全部品种，且不允许出现多余键，避免拼写
    错误被忽略；该检查需要品种列表，故由
    :meth:`validate_product_coverage` 在运行期执行。V1 不支持日期维度
    成本面板。

    """

    rate: float | Mapping[str, float]

    def __post_init__(self) -> None:
        if isinstance(self.rate, Mapping):
            normalized: dict[str, float] = {}
            for key, value in self.rate.items():
                if not isinstance(key, str) or not key.strip():
                    raise ValueError(
                        f'CostConfig.rate 的键必须是非空字符串，收到 {key!r}'
                    )
                code = key.upper()
                if code in normalized:
                    raise ValueError(
                        f'CostConfig.rate 规范化后出现重复品种 {code!r}'
                    )
                _require_finite_number(
                    value, f'CostConfig.rate[{code!r}]'
                )
                if value < 0:
                    raise ValueError(
                        f'CostConfig.rate[{code!r}] 必须非负，收到 {value!r}'
                    )
                normalized[code] = float(value)
            object.__setattr__(self, 'rate', normalized)
        else:
            _require_finite_number(self.rate, 'CostConfig.rate')
            if self.rate < 0:
                raise ValueError(
                    f'CostConfig.rate 必须非负，收到 {self.rate!r}'
                )

    @property
    def is_uniform(self) -> bool:
        """
        成本率是否为统一标量。

        Returns
        -------
        bool
            统一成本率返回 True，按品种成本率返回 False。

        """
        return not isinstance(self.rate, Mapping)

    def rate_for(self, product: str) -> float:
        """
        返回指定品种的单边成本率。

        Parameters
        ----------
        product : str
            品种代码，大小写不敏感。

        Returns
        -------
        float
            统一成本率或该品种的按品种成本率。

        Raises
        ------
        KeyError
            按品种成本率未覆盖该品种时抛出。

        """
        if isinstance(self.rate, Mapping):
            return self.rate[product.upper()]
        return float(self.rate)

    def validate_product_coverage(self, products: tuple[str, ...]) -> None:
        """
        校验按品种成本率恰好覆盖给定品种集合。

        Parameters
        ----------
        products : tuple of str
            目标品种集合，通常来自 DatasetSpec.products。

        Returns
        -------
        None

        Raises
        ------
        ValueError
            缺少品种或多出品种时抛出。

        """
        if not isinstance(self.rate, Mapping):
            return
        expected = {product.upper() for product in products}
        provided = set(self.rate)
        missing = sorted(expected - provided)
        extra = sorted(provided - expected)
        if missing or extra:
            raise ValueError(
                'CostConfig.rate 的品种集合与 DatasetSpec.products 不一致，'
                f'缺少 {missing}，多余 {extra}'
            )


# ============================================================
# 6.6 DiagnosticPortfolioSpec
# ============================================================

@dataclass(frozen=True)
class DiagnosticPortfolioSpec:
    """
    一次组合实验的完整不可变配置。

    Parameters
    ----------
    name : str
        本次组合实验名称，非空。
    harvest_profile : HarvestProfile
        收获范式，必须属于 V1 支持的研究配方。
    rebalance : RebalanceConfig
        调仓规则。
    constraints : ConstraintConfig
        权重约束。
    costs : CostConfig
        成本代理。
    realized_volatility_lookback : int or None, optional
        输出组合滚动实现波动率的窗口长度，正整数。
    risk : RiskConfig or None, optional
        风险参数，默认 None。
    clip_bound : float or None, optional
        clip 的正数边界，默认 None。时序模式下可选（为 None 表示不 clip），
        截面模式必须为 None。
        - 该参数仅接受常数，上游需保证各品种之间的因子值可比，例如先做标准化。
        - 注意：不可由全样本分位数计算而来(Look-Ahead bias)！
    min_cross_section_size : int or None, optional
        截面排名所需最少有效品种数，至少为 2，默认 None。
    annualization_factor : int, optional
        年化因子，V1 日频默认 252，必须为正整数。
    sector_attribution_col: str or None, optional
        按板块分类做收益贡献归因时，所用的板块列名称。
    missing_return_policy: {'raise', 'default_to_zero_and_warning'}, optional
        缺失收益率政策（对齐后的品种权重不为0，但收益率缺失时如何处理）。
        默认为 'raise'。
        当为 'raise' 时抛出异常，直接中止实验；当为
        'default_to_zero_and_warning' 时，将缺失的收益率用0填充。

    Notes
    -----
    配置的适用性：

    - ``ts_inv_vol_gross_one``：需要 risk 中的单品种波动率参数；
      clip_bound 可选，为 None 时不截断信号；covariance 与
      target_volatility、max_volatility_scaler、min_cross_section_size
      均必须为 None。
    - ``ts_inv_vol_target_vol``：需要完整 RiskConfig；
      clip_bound 可选，为 None 时不截断信号；
      max_volatility_scaler 必须提供；min_cross_section_size 必须为 None。
    - ``market_rank_ls``：需要 min_cross_section_size；risk、
      clip_bound 与 max_volatility_scaler 必须为 None。

    出现不适用字段时直接报错，不允许“接受后忽略”互相矛盾的配置，
    以保证配置哈希与实际行为一致。

    """

    name: str
    harvest_profile: HarvestProfile
    rebalance: RebalanceConfig
    constraint: ConstraintConfig
    cost: CostConfig
    realized_volatility_lookback: int | None = None
    risk: RiskConfig | None = None
    clip_bound: float | None = None
    min_cross_section_size: int | None = None
    annualization_factor: int = 252
    sector_attribution_col: str | None = None
    missing_return_policy: Literal['raise', 'default_to_zero_and_warning'] =\
    'raise'

    def __post_init__(self) -> None:
        _require_non_empty_str(self.name, 'DiagnosticPortfolioSpec.name')
        if not isinstance(self.harvest_profile, HarvestProfile):
            raise ValueError(
                'DiagnosticPortfolioSpec.harvest_profile 必须是 '
                f'HarvestProfile，收到 {type(self.harvest_profile).__name__}'
            )
        if not isinstance(self.rebalance, RebalanceConfig):
            raise ValueError(
                'DiagnosticPortfolioSpec.rebalance 必须是 RebalanceConfig，'
                f'收到 {type(self.rebalance).__name__}'
            )
        if not isinstance(self.constraint, ConstraintConfig):
            raise ValueError(
                'DiagnosticPortfolioSpec.constraints 必须是 ConstraintConfig，'
                f'收到 {type(self.constraint).__name__}'
            )
        if not isinstance(self.cost, CostConfig):
            raise ValueError(
                'DiagnosticPortfolioSpec.costs 必须是 CostConfig，'
                f'收到 {type(self.cost).__name__}'
            )
        if self.realized_volatility_lookback is not None:
            _require_positive_int(
                self.realized_volatility_lookback,
                'DiagnosticPortfolioSpec.realized_volatility_lookback',
            )
        _require_positive_int(
            self.annualization_factor,
            'DiagnosticPortfolioSpec.annualization_factor',
        )

        profile_name = self.harvest_profile.name
        if profile_name == 'ts_inv_vol_gross_one':
            self._validate_ts_gross_one()
        elif profile_name == 'ts_inv_vol_target_vol':
            self._validate_ts_target_vol()
        elif profile_name == 'market_rank_ls':
            self._validate_market_rank_ls()
        else:
            raise ValueError(
                f'不支持的 HarvestProfile：{profile_name!r}；'
                f'V1 仅支持 {SUPPORTED_PROFILE_NAMES}'
            )

    def _validate_ts_gross_one(self) -> None:
        """
        校验 ts_inv_vol_gross_one 的配置适用性。

        Returns
        -------
        None

        Raises
        ------
        ValueError
            配置字段冲突时抛出。

        """
        if self.risk is None:
            raise ValueError('ts_inv_vol_gross_one 必须提供 RiskConfig')
        if self.risk.covariance_lookback is not None or \
                self.risk.covariance_min_periods is not None or \
                self.risk.target_volatility is not None:
            raise ValueError(
                'ts_inv_vol_gross_one 要求 covariance_lookback、'
                'covariance_min_periods 和 target_volatility 均为 None'
            )
        if self.clip_bound is not None:
            _require_positive_number(
                self.clip_bound, 'DiagnosticPortfolioSpec.clip_bound'
            )
        if self.min_cross_section_size is not None:
            raise ValueError(
                'ts_inv_vol_gross_one 不接受 min_cross_section_size'
            )
        if self.constraint.max_volatility_scaler is not None:
            raise ValueError(
                'ts_inv_vol_gross_one 不接受 '
                'constraints.max_volatility_scaler'
            )
        self._check_max_gross()

    def _validate_ts_target_vol(self) -> None:
        """
        校验 ts_inv_vol_target_vol 的配置适用性。

        Returns
        -------
        None

        Raises
        ------
        ValueError
            配置字段冲突时抛出。

        """
        if self.risk is None:
            raise ValueError('ts_inv_vol_target_vol 必须提供 RiskConfig')
        if self.risk.covariance_lookback is None or \
                self.risk.covariance_min_periods is None:
            raise ValueError(
                'ts_inv_vol_target_vol 要求提供 covariance_lookback 和 '
                'covariance_min_periods'
            )
        if self.risk.target_volatility is None:
            raise ValueError(
                'ts_inv_vol_target_vol 要求提供 target_volatility'
            )
        if self.clip_bound is not None:
            _require_positive_number(
                self.clip_bound, 'DiagnosticPortfolioSpec.clip_bound'
            )
        if self.constraint.max_volatility_scaler is None:
            raise ValueError(
                'ts_inv_vol_target_vol 要求在 ConstraintConfig 中提供 '
                'max_volatility_scaler'
            )
        if self.min_cross_section_size is not None:
            raise ValueError(
                'ts_inv_vol_target_vol 不接受 min_cross_section_size'
            )

    def _validate_market_rank_ls(self) -> None:
        """
        校验 market_rank_ls 的配置适用性。

        Returns
        -------
        None

        Raises
        ------
        ValueError
            配置字段冲突时抛出。

        """
        if self.risk is not None:
            raise ValueError('market_rank_ls 不接受 RiskConfig')
        if self.clip_bound is not None:
            raise ValueError('market_rank_ls 不接受 clip_bound')
        if self.min_cross_section_size is None:
            raise ValueError(
                'market_rank_ls 必须提供 min_cross_section_size'
            )
        if isinstance(self.min_cross_section_size, bool) or \
                not isinstance(self.min_cross_section_size, int) or \
                self.min_cross_section_size < 2:
            raise ValueError(
                'DiagnosticPortfolioSpec.min_cross_section_size 必须是'
                f'至少为 2 的整数，收到 {self.min_cross_section_size!r}'
            )
        if self.constraint.max_volatility_scaler is not None:
            raise ValueError(
                'market_rank_ls 不接受 constraints.max_volatility_scaler'
            )
        self._check_max_gross()
        
    def _check_max_gross(self) -> None:
        if self.harvest_profile.portfolio_target == 'gross_one':
            if self.constraint.max_gross is not None:
                raise ValueError(
                    f'在 HarvestProfile.portfolio_target 为 "gross_one" 的情况下，'
                    'DiagnosticPortfolioSpec.constraints.max_gross 必须为 None 。'
                )


# ============================================================
# 6.7 DiagnosticPortfolioResult
# ============================================================

@dataclass(frozen=True)
class DiagnosticPortfolioResult:
    """
    组合实验的完整结果容器。

    所有中间表使用稳定排序。输入列顺序不同不得改变最终产品列顺序；
    产品列统一按品种代码升序排列。

    Parameters
    ----------
    signal_name : str
        输入信号名称。
    spec : DiagnosticPortfolioSpec
        完整不可变配置。
    target_weights : pd.DataFrame or pd.Series
        目标权重。
        若为 DataFrame：index=决策时间，columns=product；
        若为 Series： Index[timestamp, product]。
    rebalance_flags : pd.Series
        全局决策时间轴上的调仓标志，布尔。
    weight_changes : pd.DataFrame or pd.Series
        当期有效权重减前一期有效权重。
    turnover : pd.Series
        决策时间索引上的单边换手。
    decision_cost : pd.Series
        决策时间索引上的成本。V1 即换手成本。
    product_contributions : pd.DataFrame
        分品种收益贡献，index=收益实现时间，columns=product。
    portfolio_returns : pd.DataFrame
        gross_return、cost、proxy_net_return。
    nav : pd.DataFrame
        gross_nav、proxy_net_nav。
    exposures : pd.DataFrame
        gross、net、long、short、active_count。
    risk : pd.DataFrame or None, optional
        ex_ante_volatility、realized_volatility、scaling_factor。
    product_attribution : pd.DataFrame
        分品种汇总贡献和占比。
    direction_attribution : pd.DataFrame
        long、short 汇总贡献。
    performance : pd.DataFrame
        index=gross/proxy_net，列为核心绩效：
        total_return、annualized_arithmetic_return、cagr、
        annualized_volatility、sharpe、max_drawdown、n_periods。
    status : pd.DataFrame or None
        每个决策时点的有效数、约束状态和降级原因。
    metadata : Mapping[str, Any]
        输入范围、时间约定、公式口径和版本信息。
    sector_attribution : pd.DataFrame or pd.Series or None, optional
        有分类数据时输出板块归因（Index[timestamp, sector_col] 的
        Series 或等价的 DataFrame），否则为 None。
    warnings : tuple of str, optional
        汇总警告，默认空元组。

    Notes
    -----
    报告只能消费本对象，不得在绘图阶段重新计算另一套权重或收益。

    """

    signal_name: str
    spec: DiagnosticPortfolioSpec
    target_weights: pd.DataFrame | pd.Series = field(repr=False)
    rebalance_flags: pd.Series = field(repr=False)
    weight_changes: pd.DataFrame | pd.Series = field(repr=False)
    turnover: pd.Series = field(repr=False)
    decision_cost: pd.Series = field(repr=False)
    product_contributions: pd.DataFrame = field(repr=False)
    portfolio_returns: pd.DataFrame = field(repr=False)
    nav: pd.DataFrame = field(repr=False)
    exposures: pd.DataFrame = field(repr=False)
    risk: pd.DataFrame | None = field(repr=False)
    product_attribution: pd.DataFrame = field(repr=False)
    direction_attribution: pd.DataFrame = field(repr=False)
    sector_attribution: pd.DataFrame | pd.Series | None = field(repr=False)
    performance: pd.DataFrame = field(repr=False)
    status: pd.DataFrame | None = field(repr=False)
    warnings: tuple[str, ...]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_non_empty_str(self.signal_name, 'signal_name')
        if not isinstance(self.spec, DiagnosticPortfolioSpec):
            raise ValueError(
                'DiagnosticPortfolioResult.spec 必须是 '
                f'DiagnosticPortfolioSpec，收到 {type(self.spec).__name__}'
            )
        if self.sector_attribution is not None and \
                not isinstance(self.sector_attribution,
                               (pd.DataFrame, pd.Series)):
            raise ValueError(
                'DiagnosticPortfolioResult.sector_attribution 必须是 '
                'pd.DataFrame、pd.Series 或 None'
            )
        if not isinstance(self.warnings, tuple):
            raise ValueError('DiagnosticPortfolioResult.warnings 必须是 tuple')
        if not isinstance(self.metadata, Mapping):
            raise ValueError('DiagnosticPortfolioResult.metadata 必须是 Mapping')


__all__ = [
    'SUPPORTED_PROFILE_NAMES',
    'DiagnosticSignal',
    'RebalanceConfig',
    'RiskConfig',
    'ConstraintConfig',
    'CostConfig',
    'DiagnosticPortfolioSpec',
    'DiagnosticPortfolioResult',
]
