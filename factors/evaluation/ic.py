"""IC, RankIC 和相关统计量。"""
from __future__ import annotations

from typing import Literal, Any
from dataclasses import dataclass
from math import ceil
import pandas as pd
import numpy as np
import statsmodels.api as sm
from arch.bootstrap import \
    CircularBlockBootstrap as cb_bootstrap

SELECTED_GROUP_INVALID_CODES = {
    1: 'has_missing_nan_policy_strict',
    9: 'empty_universe',
    3: 'insufficient_valid_obs',
    4: 'factor_single_value',
    5: 'label_single_value',
}

@dataclass(frozen=True)
class SelectedGroup:
    nan_policy: Literal['strict', 'drop']
    min_observations: int
    data: pd.DataFrame | None
    n_eligible: int # 品种池内样本数，未考虑是否合格（是否有缺失值）
    n_valid: int | None 
    n_dropped: int | None 
    invalid_code: int | None

@dataclass(frozen=True)
class ICSummary:
    n_valid: int
    mean: float
    std: float
    positive_ratio: float

@dataclass(frozen=True)
class NeweyWestMeanTestResults:
    max_lags: int
    mean: float
    standard_error: float
    t_stat: float
    p_value: float
    n_obs: int

@dataclass(frozen=True)
class BootstrapConfidenceIntervals:
    block_size: int
    seed: int
    reps: int
    confidence_level: float
    confidence_intervals: tuple[float, float]

@dataclass(frozen=True)
class CSICEvaluationResults:
    rank: bool
    group_metadata: pd.DataFrame
    ic: pd.Series
    ic_summary: ICSummary
    icir: float
    icir_annual: float
    newey_mean_test_results: \
        NeweyWestMeanTestResults | None
    bootstrap_confidence_intervals: \
        BootstrapConfidenceIntervals | None

@dataclass(frozen=True)
class TSICEvaluationResults:
    rank: bool
    group_metadata: pd.DataFrame
    ic: pd.Series
    ic_summary: ICSummary

def _group_to_series(
        g: SelectedGroup
    )-> pd.Series:
    d = {
        'n_eligible': g.n_eligible,
        'n_valid': g.n_valid,
        'n_dropped': g.n_dropped,
        'invalid_code': g.invalid_code
    }
    return pd.Series(d)

def _single_value(
        values: pd.Series
    ) -> bool:
    if values.nunique(dropna=True) == 1:
        return True
    return False

def _select_group_sample(
        group: pd.DataFrame,
        min_observations: int,
        nan_policy: Literal['strict', 'drop'],
) -> SelectedGroup:
    # 首先根据品种池做筛选
    eligible = group.loc[
        group['universe'], ['factor', 'label']
    ]
    n_eligible = len(eligible)
    if n_eligible == 0: # 空品种池
        return SelectedGroup(
            nan_policy = nan_policy,
            min_observations = min_observations,
            data = None,
            n_eligible=0,
            n_valid = None,
            n_dropped = None,
            invalid_code=9
        )
    
    has_missing = eligible.isna().any(axis=None)

    if nan_policy == 'strict' and has_missing:
        return SelectedGroup(
            nan_policy = nan_policy,
            min_observations=min_observations,
            data = None,
            n_eligible=n_eligible,
            n_valid=None,
            n_dropped=None,
            invalid_code=1
        )
    if nan_policy == 'strict' and not has_missing:
        if n_eligible >= min_observations:
            factor_single = _single_value(eligible['factor'])
            label_single = _single_value(eligible['label'])
            if factor_single:
                return SelectedGroup(
                    nan_policy=nan_policy,
                    min_observations=min_observations,
                    data=None,
                    n_eligible=n_eligible,
                    n_valid=n_eligible,
                    n_dropped=None,
                    invalid_code=4
                )
            elif label_single:
                return SelectedGroup(
                    nan_policy=nan_policy,
                    min_observations=min_observations,
                    data=None,
                    n_eligible=n_eligible,
                    n_valid=n_eligible,
                    n_dropped=None,
                    invalid_code=5
                )
            else: # factor not single, label not single
                return SelectedGroup(
                    nan_policy=nan_policy,
                    min_observations=min_observations,
                    data=eligible,
                    n_eligible=n_eligible,
                    n_valid=n_eligible,
                    n_dropped=None,
                    invalid_code=None
                )
        else: # n_eligible < min_observations
            return SelectedGroup(
                nan_policy=nan_policy,
                min_observations=min_observations,
                data=None,
                n_eligible=n_eligible,
                n_valid=n_eligible,
                n_dropped=None,
                invalid_code=3
            )
    else: # nan_policy = 'drop'
        valid = eligible.dropna(
        subset=['factor', 'label'],
        how='any'
    )
        n_valid = len(valid)
        n_dropped = n_eligible - n_valid
        if n_valid >= min_observations:
            factor_single = _single_value(valid['factor'])
            label_single = _single_value(valid['label'])
            if factor_single:
                return SelectedGroup(
                    nan_policy=nan_policy,
                    min_observations=min_observations,
                    data=None, 
                    n_eligible=n_eligible,
                    n_valid=n_valid,
                    n_dropped=n_dropped,
                    invalid_code=4
                )
            elif label_single:
                return SelectedGroup(
                    nan_policy=nan_policy,
                    min_observations=min_observations,
                    data=None,
                    n_eligible=n_eligible,
                    n_valid=n_valid,
                    n_dropped=n_dropped,
                    invalid_code=5
                )
            else: # factor not single, label not single
                return SelectedGroup(
                    nan_policy=nan_policy,
                    min_observations=min_observations,
                    data=valid,
                    n_eligible=n_eligible,
                    n_valid=n_valid,
                    n_dropped=n_dropped,
                    invalid_code=None
                )
        else:
            return SelectedGroup(
                nan_policy=nan_policy,
                min_observations=min_observations,
                data=None,
                n_eligible=n_eligible,
                n_valid=n_valid,
                n_dropped=n_dropped,
                invalid_code=3
            )

def _ic_valid_group(
        data: pd.DataFrame,
        rank: bool = False
) -> float:
    if not rank:
        return data['factor'] \
            .corr(data['label'])
    return data['factor'].rank() \
    .corr(data['label'].rank())

def _ic_group(
        group: SelectedGroup,
        rank: bool = False
) -> float:
    if group.invalid_code is None: # valid group
        assert group.data is not None
        return _ic_valid_group(group.data, rank)
    return np.nan

def _icir(
        valid_ic: pd.Series,
        annual: bool,
        annualization_factor: int | None = None,
        adjust_method: Literal[
            'triangle_ACF', 'se_HAC'] | None = None,
        h: int | None = None,
        se_HAC: float | None = None,
    ) -> float:
    if adjust_method not in {
        'triangle_ACF', 'se_HAC', None}:
        raise ValueError(f'非法 adjust_method: {adjust_method}。')
    assert not valid_ic.isna().any()
    mean = valid_ic.mean()
    std = valid_ic.std()
    icir = mean / std if std > 0 else np.nan
    if not annual:
        return icir
    else: # annualize=True
        assert annualization_factor is not None
        if adjust_method is None:
            return icir * np.sqrt(annualization_factor)
        if adjust_method == 'triangle_ACF':
            assert h is not None
            return icir * np.sqrt(annualization_factor/h)
        else: # adjust_method = se_HAC
            assert se_HAC is not None
            return mean / se_HAC  \
                * np.sqrt(annualization_factor/len(valid_ic))

def _summarize(
        ic: pd.Series,
    )-> ICSummary:
    valid_ic = ic.dropna()
    n_valid = len(valid_ic)
    mean = valid_ic.mean() if n_valid > 0 else np.nan
    std = valid_ic.std(ddof=1) if n_valid > 1 else np.nan
    positive_ratio = (valid_ic > 0).mean() \
    if n_valid > 0 else np.nan
    return ICSummary(
        n_valid = n_valid,
        mean = mean,
        std = std,
        positive_ratio = positive_ratio
    )

def _newey_west_mean_test(
        valid_ic: pd.Series, # values of ic
        max_lags: int
) -> NeweyWestMeanTestResults:
    """
    IC均值检验。 H0: mean(IC) = 0.
    Use Newey-West HAC standard error.
    """
    sample = valid_ic.to_numpy(dtype=float)
    if max_lags < 0 or max_lags > sample.size-2:
        raise ValueError(f'max_lags 必须满足\
        0 <= max_lags < 合格样本数-2, 此处合格样本数\
        为 {sample.size}, max_lags 为 {max_lags}。')
    
    ones = np.ones((sample.size, 1), dtype=float)
    res = sm.OLS(sample, ones).fit(
        cov_type='HAC',
        cov_kwds={
            'maxlags': max_lags,
            'use_correction': True
        }
    )
    return NeweyWestMeanTestResults(
        max_lags = max_lags,
        mean = res.params[0],
        standard_error = res.bse[0],
        t_stat = res.tvalues[0],
        p_value = res.pvalues[0],
        n_obs = int(sample.size)
    )

def _bootstrap_ci(
        valid_ic: pd.Series,
        block_size: int,
        seed: int,
        reps: int,
        confidence_level: float 
) -> BootstrapConfidenceIntervals:
    """用 circular block bootstrap 估计 IC 的置信区间。"""
    sample = valid_ic.to_numpy(dtype=float)

    if not (1 <= block_size <= sample.size):
        raise ValueError(f"block_size must be between 1 and {sample.size}, got {block_size}")
    if reps <= 0:
        raise ValueError(f"reps must be positive, got {reps}")
    if not (0 <= confidence_level <= 1):
        raise ValueError(f"confidence_level must be in [0, 1], got {confidence_level}")

    bs = cb_bootstrap(block_size, sample, seed=seed)
    bootstrap_means = bs.apply(
        lambda s: s.mean(), reps=reps
    )
    alpha = 1.0 - confidence_level
    lower, upper = np.quantile(
        bootstrap_means,
        [alpha / 2.0, 1.0 - alpha / 2.0],
    )
    return BootstrapConfidenceIntervals(
        block_size = block_size,
        seed = seed,
        reps = reps,
        confidence_level = confidence_level,
        confidence_intervals = (lower, upper)
    )

def _cs_ic_details(
        rank: bool,
        df: pd.DataFrame,
        label_horizon: int,
        min_observations: int,
        annualization_factor: int,
        adjust_method: Literal[
            'triangle_ACF', 'se_HAC'] | None = None,
        nan_policy: Literal['strict', 'drop'] = 'drop',
        nw_max_lags: int | None = None,
        bs_block_size: int | None = None,
        bs_seed: int = 42,
        bs_reps: int = 500,
        bs_confidence_level: float = 0.95,
) -> CSICEvaluationResults:    
    # 1. 根据 nan_policy 和 min_observations 挑选每组数据
    selected: pd.Series = \
        df.groupby(level='timestamp') \
        .apply(_select_group_sample,
               min_observations=min_observations,
               nan_policy=nan_policy)  # type: ignore
    group_meta: pd.DataFrame = selected \
        .apply(_group_to_series)

    # 2. 计算 IC 或 RankIC
    ic = selected.transform(
        _ic_group, rank=rank)

    # 3. 汇总统计
    ic_summary = _summarize(ic)

    # 4. 均值检验 + circular block bootstrap 置信区间
    ic_valid = ic.dropna()

    if nw_max_lags is None:
        nw_max_lags = label_horizon - 1  # 采用默认，L=h-1
    nw_max_lags = min(
        nw_max_lags, len(ic_valid)-2)  # clamp到理论上限
    def get_block_size(n_obs: int):
        size = max(label_horizon,
                    ceil(n_obs ** (1 / 3)))
        size = min(size, n_obs)
        return size
    
    if ic_valid.size < 2: # 全空或为1。全空无法执行。为1时，nw_max_lags会小于0，且bootstrap 区间无意义。
        ic_mean_test_res = None
        bs_ci = None
    else:        
        # 均值检验
        ic_mean_test_res = _newey_west_mean_test(
                ic_valid, nw_max_lags)
        # cbs置信区间
        size_cs = get_block_size(ic_valid.size) \
            if bs_block_size is None else bs_block_size
        bs_ci = _bootstrap_ci(
            valid_ic=ic_valid,
            block_size=size_cs,
            seed=bs_seed,
            reps=bs_reps,
            confidence_level=bs_confidence_level
        )       

    # 5. ICIR
    icir = _icir(valid_ic=ic_valid, annual=False)

    if adjust_method in {'triangle_ACF', None}:
        icir_annual = _icir(
            valid_ic=ic_valid,
            annual=True,
            annualization_factor=annualization_factor,
            adjust_method=adjust_method,
            h=label_horizon
        )
    else:
        if ic_mean_test_res is not None:
            se_HAC = ic_mean_test_res.standard_error
            icir_annual = _icir(
                valid_ic=ic_valid,
                annual=True,
                annualization_factor=annualization_factor,
                adjust_method=adjust_method,
                se_HAC=se_HAC
            )
        else:
            icir_annual = np.nan

    return CSICEvaluationResults(
        rank=rank,
        group_metadata= group_meta,
        ic = ic,
        ic_summary= ic_summary,
        icir = icir, 
        icir_annual = icir_annual,
        newey_mean_test_results = ic_mean_test_res,
        bootstrap_confidence_intervals = bs_ci
    )

def _ts_ic_details(
        rank: bool,
        df: pd.DataFrame,
        min_observations: int,
        nan_policy: Literal['strict', 'drop'] = 'drop',
) -> TSICEvaluationResults:
    selected: pd.Series = \
        df.groupby(level='product') \
        .apply(_select_group_sample,
               min_observations=min_observations,
               nan_policy=nan_policy)  # type: ignore
    group_meta = selected \
        .apply(_group_to_series)

    ic = selected.transform(
        _ic_group, rank=rank
    )

    ic_summary = _summarize(ic)

    return TSICEvaluationResults(
        rank = rank,
        group_metadata = group_meta,
        ic = ic,
        ic_summary= ic_summary
    )

def ic_details(
        df: pd.DataFrame,
        mode: Literal['cross_sectional', 'time_series'],
        label_horizon: int,
        min_observations: int,
        annualization_factor: int,
        adjust_method: Literal[
            'triangle_ACF', 'se_HAC'] | None = None,
        nan_policy: Literal['strict', 'drop'] = 'drop',
        nw_max_lags: int | None = None,
        bs_block_size: int | None = None,
        bs_seed: int = 42,
        bs_reps: int = 500,
        bs_confidence_level: float = 0.95
) -> tuple[CSICEvaluationResults, CSICEvaluationResults] \
    | tuple[TSICEvaluationResults, TSICEvaluationResults]: 
    """计算截面（cross_sectional）或时序（time_series）模式下的 IC 与 RankIC
    评估结果。

    对每个分组（截面模式下为每个时间截面，时序模式下为每个品种），先依据品种池
    与缺失值策略筛选合格样本，再计算该组的 Pearson IC（rank=False）或 RankIC
    （rank=True），最后产出汇总统计、ICIR、年化 ICIR、Newey-West IC 均值检验
    与 circular block bootstrap 置信区间。均值检验、置信区间与年化 ICIR 仅
    在截面模式下计算，时序模式只输出分组元数据、IC 序列与汇总统计。

    Parameters
    ----------
    df : pd.DataFrame
        因子评估数据。MultiIndex 索引须包含 ``timestamp`` 与 ``product`` 两个
        层级；数据列须包含 ``factor``（因子值）、``label``（标签，通常为
        label_horizon 期前瞻收益）与 ``universe``（布尔型品种池标记）。
    mode : {'cross_sectional', 'time_series'}
        计算模式。cross_sectional 按时间截面计算各品种在截面上的相关性；
        time_series 按品种计算其因子与标签在整个历史区间上的相关性。
    label_horizon : int
        标签的前瞻期数，必须为大于等于 1 的整数。用于 Newey-West 检验默认滞后
        阶数（label_horizon - 1）、bootstrap 默认块长，以及 triangle_ACF 方法
        下 ICIR 年化的重叠矫正。该参数仅在 cross_sectional 模式下生效。
    min_observations : int
        判定分组有效所需的最小样本数，必须大于等于 2。
    annualization_factor : int
        年化因子，即一年内包含的期数（如日频数据取 252），必须大于 0。仅在
        cross_sectional 模式下用于 ICIR 年化。
    adjust_method : {'triangle_ACF', 'se_HAC', None}, optional
        ICIR 年化的重叠矫正方法，仅在 cross_sectional 模式下生效。None 表示
        不做矫正，直接乘以 sqrt(annualization_factor)；'triangle_ACF' 假设
        重叠标签使 IC 自相关呈三角衰减（近似 MA(h-1)），按
        sqrt(annualization_factor / label_horizon) 收缩；'se_HAC' 用
        Newey-West 标准误替换样本标准差，并按 sqrt(annualization_factor /
        有效样本数) 缩放。默认 None。
    nan_policy : {'strict', 'drop'}, optional
        缺失值处理策略。strict 表示分组内任一 factor/label 缺失即整组判为无效
        （invalid_code=1）；drop 表示剔除缺失样本后再判断是否达到
        min_observations。默认 'drop'。
    nw_max_lags : int, optional
        Newey-West 均值检验的滞后阶数。默认取 label_horizon - 1，并自动收敛到
        有效样本数减 2 以内。仅 cross_sectional 模式生效。
    bs_block_size : int, optional
        bootstrap 块长。默认取 max(label_horizon, 有效样本数 ** (1/3))，
        并收敛到有效样本数以内。仅 cross_sectional 模式生效。
    bs_seed : int, optional
        bootstrap 随机种子，保证结果可复现。默认 42。
    bs_reps : int, optional
        bootstrap 重采样次数。默认 500。
    bs_confidence_level : float, optional
        bootstrap 置信区间的置信水平，取值须位于 [0, 1]。默认 0.95。

    Returns
    -------
    tuple[CSICEvaluationResults, CSICEvaluationResults] 或
    tuple[TSICEvaluationResults, TSICEvaluationResults]
        元组首个元素为 IC（rank=False）的评估结果，第二个为 RankIC
        （rank=True）的评估结果，各元素内的 ``rank`` 字段亦标识自身种类。
        截面模式返回两个 CSICEvaluationResults，字段包括分组元数据
        （group_metadata）、IC 序列（ic）、汇总统计（ic_summary）、ICIR
        （icir）、年化 ICIR（icir_annual）、Newey-West 均值检验结果
        （newey_mean_test_results）与 bootstrap 置信区间
        （bootstrap_confidence_intervals）；样本不足时后两者为 None。
        时序模式返回两个 TSICEvaluationResults，仅含分组元数据（group_metadata）、
        IC 序列（ic）与汇总统计（ic_summary）。字段定义见各数据类。
    """
    if mode not in {'cross_sectional','time_series'}:
        raise ValueError(f'mode 必须为 cross_sectional 或 time_series, 现在为 {mode}。')
    if nan_policy not in {"drop", "strict"}:
            raise ValueError(f"不支持的 nan_policy: {nan_policy}")
    if min_observations < 2:
        raise ValueError(f'min_observations 必须大于等于2，现在为 {min_observations}')
    if adjust_method not in {'triangle_ACF', 'se_HAC', None}:
        raise ValueError(f'非法的 adjust_method: {adjust_method}。')
    if label_horizon < 1:
        raise ValueError(f'非法的 label_horizon: {label_horizon}。')
    if annualization_factor <=0 :
        raise ValueError(f'非法的 annualization_factor: {annualization_factor}。')

    results = []
    if mode == 'cross_sectional':
        for rank in [False, True]:
            res = _cs_ic_details(
            rank=rank,
            df=df,
            label_horizon=label_horizon,
            min_observations=min_observations,
            annualization_factor=annualization_factor,
            adjust_method=adjust_method,
            nan_policy=nan_policy,
            nw_max_lags=nw_max_lags,
            bs_block_size=bs_block_size,
            bs_seed=bs_seed,
            bs_reps=bs_reps,
            bs_confidence_level=bs_confidence_level
        )
            results.append(res)
    else: # mode = 'time_series' 
        for rank in [False, True]:
            res = _ts_ic_details(
                rank=rank,
                df=df, 
                min_observations=min_observations,
                nan_policy=nan_policy
            )
            results.append(res)
    return results[0], results[1]
        