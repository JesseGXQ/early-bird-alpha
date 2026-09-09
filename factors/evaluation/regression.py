"""单因子回归预测关系诊断。"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Literal

import numpy as np
import pandas as pd
import statsmodels.api as sm
from arch.bootstrap import CircularBlockBootstrap
from scipy import stats
from statsmodels.regression.linear_model import RegressionResultsWrapper

FE_ABSORPTION_TOLERANCE = 1e-10
FE_ABSORPTION_MAX_ITERATIONS = 10_000

_GROUP_COLUMNS = ["n_eligible", "n_valid", "n_dropped"]
_REGRESSION_COLUMNS = ["invalid_code", "dof_residual", "r_squared",
                       "adjusted_r_squared", "design_rank",
                       "n_observations", "n_parameters"]
_POOLED_INDEX = [*_REGRESSION_COLUMNS, "n_products", "n_timestamps",
                 "n_product_singletons", "n_timestamp_singletons",
                 "cluster_use_correction", "cluster_dof_correction",
                 "reference_distribution", "inference_dof"]


@dataclass(frozen=True)
class RegressionBetaSummary:
    n_valid: int
    mean: float
    median: float
    std: float
    positive_ratio: float


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
    inference_dof: float
    use_correction: bool
    requested_max_lags: int | None 
    effective_max_lags: int | None


@dataclass(frozen=True)
class FactorBetaMeanTestResults(HACRegressionResults):
    """Fama–MacBeth factor beta 均值的 HAC 检验结果。"""


@dataclass(frozen=True)
class RegressionBootstrapConfidenceIntervals:
    block_size: int
    seed: int
    reps: int
    confidence_level: float
    confidence_intervals: tuple[float, float]
    n_observations: int


@dataclass(frozen=True)
class CSRegressionEvaluationResults:
    """截面回归评估结果。
    
    Parameters(some of them)
    ------
    factor_coefficients: pd.Series
        每个时点上因子的回归系数。
        Index: [timestamp]
    group_metadata: pd.DataFrame
        每个时点作为一组的筛选元数据。
        - Index[timestamp]
        - columns: [n_eligible, n_valid, n_dropped]
    fit_intercept: bool
        逐时点截面回归是否拟合截距。
    regression_metadata: pd.DataFrame
        - Index[timestamp]
        - columns: invalid_code, dof_residual,
        r_squared, adjusted_r_squared,
        design_rank, n_observations, n_parameters.
            - ``invalid_code``: one of ``REGRESSION_INVALID_CODES``.
    
    """
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
    factor_beta_bootstrap_ci: RegressionBootstrapConfidenceIntervals | None


@dataclass(frozen=True)
class PooledRegressionResults:
    """时序模式下的多品种固定效应回归结果。

    Parameters(some of them)
    ------
    regression_metadata: pd.Series
        混合回归的元数据。
        - Index: invalid_code, dof_residual, r_squared,
        adjusted_r_squared, design_rank, n_observations, n_parameters,
        n_products, n_timestamps, n_product_singletons,
        n_timestamp_singletons, cluster_use_correction,
        cluster_dof_correction, reference_distribution,
        inference_dof.
    """
    cov_type: Literal["two_way_cluster"]
    cluster_cov_vars: Literal["timestamp_product"]
    fixed_effect_vars: Literal["product", "product_timestamp"]
    estimate: float
    standard_error: float
    t_stat: float
    p_value: float
    confidence_interval: tuple[float, float]
    n_observations: int
    regression_metadata: pd.Series


@dataclass(frozen=True)
class TSRegressionEvaluationResults:
    """时序回归评估结果。

    Parameters(some of them)
    ------
    per_product_regression_metadata: pd.DataFrame
        每个品种的回归元数据：
        - Index[product]
        - columns: invalid_code, dof_residual, r_squared,
        adjusted_r_squared, design_rank, n_observations, n_parameters.

    """
    mode: Literal["time_series"]
    label_horizon: int
    min_observations: int
    nan_policy: Literal["drop", "strict"]
    control_vars: tuple[str, ...]
    fit_intercept: bool
    group_metadata: pd.DataFrame
    per_product_results: dict[str, HACRegressionResults]
    per_product_regression_metadata: pd.DataFrame
    factor_beta_summary: RegressionBetaSummary
    pooled_product_fe: PooledRegressionResults
    pooled_product_timestamp_fe: PooledRegressionResults


REGRESSION_INVALID_CODES = {
    1: "has_missing_nan_policy_strict", 2: "insufficient_observations",
    3: "constant_label", 4: "rank_deficient_design",
    5: "insufficient_clusters", 6: "fixed_effect_absorption_not_converged",
    7: "invalid_cluster_covariance", 9: "empty_universe",
}


@dataclass(frozen=True)
class _SelectedGroup:
    data: pd.DataFrame | None
    n_eligible: int
    n_valid: int
    n_dropped: int
    invalid_code: int | None


def _is_int(value: object) -> bool:
    """判断输入是否为整数标量，并明确排除布尔值。"""
    return isinstance(value, (int, np.integer)) and not isinstance(
        value, (bool, np.bool_))


def _validate_inputs(df: pd.DataFrame, mode: str, label_horizon: int,
                     min_observations: int, nan_policy: str,
                     control_columns: tuple[str, ...], fit_intercept: bool,
                     nw_max_lags: int | None, bs_block_size: int | None,
                     bs_seed: int, bs_reps: int,
                     confidence_level: float) -> None:
    """校验回归数据契约与全部公共配置参数。

    对结构或配置错误立即抛出异常，样本层面的局部失效留给后续步骤处理。
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df 必须是 pandas DataFrame。")
    if df.empty:
        raise ValueError("df 不能为空。")
    if not isinstance(df.index, pd.MultiIndex):
        raise ValueError("df.index 必须是 MultiIndex。")
    if list(df.index.names) != ["timestamp", "product"]:
        raise ValueError("df.index 名称和顺序必须为 ['timestamp', 'product']。")
    if not df.index.is_unique:
        raise ValueError("df.index 必须唯一。")
    timestamps = df.index.get_level_values("timestamp")
    if not isinstance(timestamps, pd.DatetimeIndex):
        raise TypeError("timestamp 索引层必须是 DatetimeIndex。")
    if not timestamps.is_monotonic_increasing:
        raise ValueError("timestamp 索引层必须单调递增。")
    if not df.columns.is_unique:
        raise ValueError("df 的列名必须唯一。")
    if mode not in {"cross_sectional", "time_series"}:
        raise ValueError(f"非法的 mode: {mode}。")
    if not _is_int(label_horizon) or label_horizon < 1:
        raise ValueError("label_horizon 必须是正整数，且不能是 bool。")
    if not _is_int(min_observations) or min_observations < 2:
        raise ValueError("min_observations 必须是大于等于 2 的整数，且不能是 bool。")
    if nan_policy not in {"drop", "strict"}:
        raise ValueError(f"非法的 nan_policy: {nan_policy}。")
    if not isinstance(control_columns, tuple) or not all(
            isinstance(column, str) for column in control_columns):
        raise TypeError("control_columns 必须是字符串元组。")
    if len(set(control_columns)) != len(control_columns):
        raise ValueError("control_columns 不允许包含重复列名。")
    overlap = {"factor", "label", "universe"}.intersection(control_columns)
    if overlap:
        raise ValueError(f"control_columns 不允许包含保留列: {sorted(overlap)}。")
    if not isinstance(fit_intercept, bool):
        raise TypeError("fit_intercept 必须是 bool。")
    if nw_max_lags is not None and (not _is_int(nw_max_lags) or nw_max_lags < 0):
        raise ValueError("nw_max_lags 必须是非负整数或 None。")
    if bs_block_size is not None and (not _is_int(bs_block_size) or bs_block_size < 1):
        raise ValueError("bs_block_size 必须是正整数或 None。")
    if not _is_int(bs_seed) or bs_seed < 0:
        raise ValueError("bs_seed 必须是非负整数，且不能是 bool。")
    if not _is_int(bs_reps) or bs_reps < 1:
        raise ValueError("bs_reps 必须是正整数，且不能是 bool。")
    if (isinstance(confidence_level, (bool, np.bool_))
            or not isinstance(confidence_level, (int, float, np.number))
            or not 0 < float(confidence_level) < 1):
        raise ValueError("confidence_level 必须严格位于 (0, 1)。")
    if mode == "time_series" and (bs_block_size is not None or bs_seed != 42
            or bs_reps != 500):
        raise ValueError("time_series 模式不使用 bootstrap，不能传入非默认配置。")

    required = ["factor", "label", "universe", *control_columns]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"df 缺少必需列: {missing}。")
    if not pd.api.types.is_bool_dtype(df["universe"].dtype):
        raise TypeError("universe 必须是布尔列。")
    if df["universe"].isna().any():
        raise ValueError("universe 不能包含缺失值。")
    for column in ["factor", "label", *control_columns]:
        dtype = df[column].dtype
        if pd.api.types.is_bool_dtype(dtype) or not pd.api.types.is_numeric_dtype(dtype):
            raise TypeError(f"列 {column!r} 必须是数值型。")
        if not np.isfinite(df[column].dropna().to_numpy(dtype=float)).all():
            raise ValueError(f"列 {column!r} 不能包含正负无穷。")


def _select_group(group: pd.DataFrame, columns: list[str],
                  nan_policy: Literal["drop", "strict"]) -> _SelectedGroup:
    """按品种池和缺失值策略筛选单个评价分组的回归样本。"""
    eligible = group.loc[group["universe"], columns]
    if len(eligible) == 0:
        return _SelectedGroup(None, 0, 0, 0, 9)
    complete = eligible.dropna(subset=columns)
    n_dropped = len(eligible) - len(complete)
    if nan_policy == "strict" and n_dropped:
        return _SelectedGroup(None, len(eligible), len(complete), n_dropped, 1)
    return _SelectedGroup(complete, len(eligible), len(complete), n_dropped, None)


def _design(data: pd.DataFrame, 
            controls: tuple[str, ...], 
            intercept: bool) -> np.ndarray:
    """构造以 factor 为核心、包含可选控制变量和截距的设计矩阵。"""
    explanatory = data[["factor", *controls]].to_numpy(dtype=float)
    return np.column_stack([np.ones(len(data)), explanatory]) \
        if intercept else explanatory


def _empty_metadata(invalid_code: int, n_obs: int, n_params: int, 
                    rank: float = np.nan, dof: float = np.nan) \
                        -> dict[str, None | float | int]:
    """为无法完成估计的分组构造带失效码的回归元数据。"""
    return {"invalid_code": invalid_code, "dof_residual": dof, 
            "r_squared": np.nan,
            "adjusted_r_squared": np.nan, "design_rank": rank,
            "n_observations": n_obs, "n_parameters": n_params}


def _fit_group(selected: _SelectedGroup, controls: tuple[str, ...],
               intercept: bool, min_observations: int) \
                -> tuple[RegressionResultsWrapper | None, 
                    dict[str, None | float | int]]:
    """检查单组样本的可识别性并拟合普通最小二乘回归。"""
    n_params = 1 + len(controls) + int(intercept)
    if selected.invalid_code is not None:
        return None, _empty_metadata(selected.invalid_code, 
                                     selected.n_valid, n_params)
    assert selected.data is not None
    if len(selected.data) < max(min_observations, n_params + 1):
        return None, _empty_metadata(2, len(selected.data), n_params)
    y = selected.data["label"].to_numpy(dtype=float)
    x = _design(selected.data, controls, intercept)
    rank, dof = int(np.linalg.matrix_rank(x)), float(len(y) - n_params)
    if intercept and np.ptp(y) == 0:
        return None, _empty_metadata(3, len(y), n_params, rank, dof)
    if rank < n_params: # OLS估计，必须满秩，即 rank = n_params
        return None, _empty_metadata(4, len(y), n_params, rank, dof)
    result = sm.OLS(y, x).fit()
    return result, {"invalid_code": None, "dof_residual": float(result.df_resid),
                    "r_squared": float(result.rsquared),
                    "adjusted_r_squared": float(result.rsquared_adj),
                    "design_rank": rank, "n_observations": int(result.nobs),
                    "n_parameters": n_params}


def _metadata_frame(records: list[dict[str, object]], 
                    index: pd.Index) -> pd.DataFrame:
    """将逐组元数据记录整理为具有稳定列顺序的 DataFrame。"""
    frame = pd.DataFrame(records, index=index, columns=_REGRESSION_COLUMNS)
    # 混合 None 和 整数的 invalid_codes会被 pandas 自动该为 float 类型， None 变成 
    # np.nan，需要从 records 重建，恢复 None
    frame["invalid_code"] = pd.Series(
        [r["invalid_code"] for r in records], index=index, dtype=object)
    return frame


def _summary(values: object) -> RegressionBetaSummary:
    """对有效 factor beta 计算等权描述性汇总。"""
    sample = pd.Series(values, dtype=float).dropna()
    n = len(sample)
    return RegressionBetaSummary(
        n, 
        float(sample.mean()) if n else np.nan,
        float(sample.median()) if n else np.nan,
        float(sample.std(ddof=1)) if n > 1 else np.nan,
        float((sample > 0).mean()) if n else np.nan
    )


def _inference(estimate: float, variance: float, dof: float,
               confidence: float
               ) -> tuple[float, float, float, tuple[float, float]]:
    """根据估计值、方差和有限样本自由度计算 t 推断统计量。"""
    if not np.isfinite(variance) or variance < 0:
        return np.nan, np.nan, np.nan, (np.nan, np.nan)
    se = float(np.sqrt(variance))
    if se == 0: # 完全拟合
        t_stat, p_value = np.nan, np.nan
    else:
        t_stat = float(estimate / se)
        p_value = float(2 * stats.t.sf(abs(t_stat), dof))
    critical = float(stats.t.ppf((1 + confidence) / 2, dof))
    ci = (float(estimate - critical * se), float(estimate + critical * se))
    return se, t_stat, p_value, ci


def _hac_result(result: RegressionResultsWrapper, factor_index: int,
                requested_lags: int,
                confidence_level: float,
                result_type: type[HACRegressionResults] = HACRegressionResults
                ) -> HACRegressionResults:
    """为 OLS 中的 factor 系数计算有限样本 HAC 稳健推断。"""
    n = int(result.nobs)
    effective_lags = min(requested_lags, n - 2)
    robust = result.get_robustcov_results(
        cov_type="HAC", maxlags=effective_lags,
        use_correction=True, use_t=True)
    estimate = float(result.params[factor_index])
    variance = float(np.asarray(robust.cov_params())[factor_index, factor_index])
    inference_dof = float(result.df_resid)
    se, t_stat, p_value, ci = _inference(
        estimate, variance, inference_dof, confidence_level)
    return result_type(
        estimate, se, t_stat, p_value, confidence_level, ci, n, "t",
        inference_dof, True, requested_lags, effective_lags)


def _mean_hac(beta: pd.Series, requested_lags: int,
              confidence_level: float) \
    -> FactorBetaMeanTestResults | None:
    """对有效的逐期 factor beta 序列执行 HAC 均值检验。"""
    sample = beta.dropna().to_numpy(dtype=float)
    if len(sample) < 2:
        return None
    result = sm.OLS(sample, np.ones((len(sample), 1))).fit()
    return _hac_result(
        result, 0, requested_lags, confidence_level,
        FactorBetaMeanTestResults)  # type: ignore[return-value]


def _bootstrap(beta: pd.Series, horizon: int, block_size: int | None,
               seed: int, reps: int,
               confidence: float) -> RegressionBootstrapConfidenceIntervals | None:
    """使用循环块 bootstrap 估计 factor beta 均值的置信区间。"""
    sample = beta.dropna().to_numpy(dtype=float)
    n = len(sample)
    if n < 2:
        return None
    if block_size is None:
        block_size = min(max(horizon, ceil(n ** (1 / 3))), n)
    elif block_size > n:
        raise ValueError(f"bs_block_size 不能超过有效 factor beta 的数量 {n}。")
    bootstrap = CircularBlockBootstrap(block_size, sample, seed=seed)
    means = bootstrap.apply(lambda values: values.mean(), reps=reps)
    alpha = 1 - confidence
    lower, upper = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return RegressionBootstrapConfidenceIntervals(
        block_size, seed, reps, confidence, (float(lower), float(upper)), n)


def _cs_regression_details(df: pd.DataFrame, label_horizon: int,
                           min_observations: int,
                           nan_policy: Literal["drop", "strict"],
                           controls: tuple[str, ...], intercept: bool,
                           nw_max_lags: int | None,
                           bs_block_size: int | None,
                           bs_seed: int, bs_reps: int,
                           confidence_level: float) -> CSRegressionEvaluationResults:
    """按 timestamp 执行截面回归，并汇总 beta 及稳健推断。"""
    columns = ["factor", "label", *controls]
    keys, group_rows, meta_rows, betas = [], [], [], []
    for key, group in df.groupby(level="timestamp", sort=False):
        selected = _select_group(group, columns, nan_policy)
        result, metadata = _fit_group(selected, controls, intercept, min_observations)
        keys.append(key)
        group_rows.append({"n_eligible": selected.n_eligible,
                           "n_valid": selected.n_valid,
                           "n_dropped": selected.n_dropped})
        meta_rows.append(metadata)
        factor_index = 1 if intercept else 0
        betas.append(float(result.params[factor_index]) \
                     if result is not None else np.nan)
    index = pd.DatetimeIndex(keys, name="timestamp")
    group_metadata = pd.DataFrame(group_rows, index=index, columns=_GROUP_COLUMNS)
    coefficients = pd.Series(betas, index=index, name="factor_beta", dtype=float)
    requested_lags = label_horizon - 1 if nw_max_lags is None else nw_max_lags
    return CSRegressionEvaluationResults(
        "cross_sectional", label_horizon, min_observations, nan_policy,
        group_metadata, controls, intercept, coefficients,
        _metadata_frame(meta_rows, index), _summary(coefficients),
        _mean_hac(coefficients, requested_lags, confidence_level),
        _bootstrap(coefficients, label_horizon, bs_block_size,
                   bs_seed, bs_reps, confidence_level))


def _alternating_projection(values: np.ndarray, products: pd.Index,
                            timestamps: pd.Index) -> tuple[np.ndarray, bool]:
    """通过交替去均值吸收非平衡面板的 product 和 timestamp 固定效应。"""
    transformed = np.asarray(values, dtype=float).copy()
    product_codes, _ = pd.factorize(products, sort=False)
    timestamp_codes, _ = pd.factorize(timestamps, sort=False)
    for _ in range(FE_ABSORPTION_MAX_ITERATIONS):
        previous = transformed.copy()
        for codes in (product_codes, timestamp_codes):
            counts = np.bincount(codes)
            for column in range(transformed.shape[1]):
                sums = np.bincount(codes, weights=transformed[:, column])
                transformed[:, column] -= sums[codes] / counts[codes]
        if np.max(np.abs(transformed - previous)) < FE_ABSORPTION_TOLERANCE:
            return transformed, True
    return transformed, False


def _pooled_metadata(code: int | None, n_obs: int, n_params: int, 
                     n_products: int,n_timestamps: int, 
                     product_singletons: int,
                     timestamp_singletons: int, dof: float = np.nan,
                     r2: float = np.nan, adjusted_r2: float = np.nan,
                     rank: float = np.nan, inference_dof: float = np.nan) \
                        -> pd.Series:
    """按照稳定索引契约构造 pooled 回归元数据。"""
    values = {"invalid_code": code, "dof_residual": dof,
              "r_squared": r2, "adjusted_r_squared": adjusted_r2,
              "design_rank": rank, "n_observations": n_obs, 
              "n_parameters": n_params,
              "n_products": n_products, "n_timestamps": n_timestamps,
              "n_product_singletons": product_singletons,
              "n_timestamp_singletons": timestamp_singletons,
              "cluster_use_correction": True, "cluster_dof_correction": True,
              "reference_distribution": "t", "inference_dof": inference_dof}
    return pd.Series(values, index=_POOLED_INDEX, dtype=object)


def _invalid_pooled(fixed_effects: Literal["product", "product_timestamp"],
                    metadata: pd.Series, estimate: float = np.nan
                    ) -> PooledRegressionResults:
    """构造失败的 pooled 结果，并按约定保留可能有效的点估计。"""
    return PooledRegressionResults(
        "two_way_cluster", "timestamp_product", fixed_effects, estimate,
        np.nan, np.nan, np.nan, (np.nan, np.nan),
        int(metadata["n_observations"]), metadata)


def _fit_pooled(data: pd.DataFrame, controls: tuple[str, ...], 
                min_observations: int,
                fixed_effects: Literal["product", "product_timestamp"],
                confidence_level: float,
                empty_universe: bool = False,
                ) -> PooledRegressionResults:
    """拟合指定固定效应规格，并执行 product × timestamp 双向聚类推断。"""
    n_obs, n_params = len(data), 1 + len(controls)
    products = data.index.get_level_values("product")
    timestamps = data.index.get_level_values("timestamp")
    product_counts = pd.Series(products).value_counts() # 每个品种上有多少个时点
    timestamp_counts = pd.Series(timestamps).value_counts() # 每个时点上有多少个品种
    common = (n_obs, n_params, len(product_counts), len(timestamp_counts),
              int((product_counts == 1).sum()),
              int((timestamp_counts == 1).sum()))
    if empty_universe:
        return _invalid_pooled(fixed_effects, _pooled_metadata(9, *common))
    if n_obs < max(min_observations, n_params + 1): # 观测值不足
        return _invalid_pooled(fixed_effects, _pooled_metadata(2, *common))
    raw = data[["label", "factor", *controls]].to_numpy(dtype=float)
    if np.ptp(raw[:, 0]) == 0:  # label 没有 variation
        return _invalid_pooled(fixed_effects, _pooled_metadata(3, *common))
    if common[2] < 2 or common[3] < 2: # pooled 样本的品种总数或时间点总数小于2
        return _invalid_pooled(
            fixed_effects, _pooled_metadata(5, *common))
    if fixed_effects == "product":
        frame = pd.DataFrame(raw, index=data.index)
        transformed = (frame - frame.groupby(level="product", sort=False)
                       .transform("mean")).to_numpy(dtype=float)
        converged = True
    else:
        transformed, converged = _alternating_projection(raw, products, timestamps)
    if not converged:
        return _invalid_pooled(fixed_effects, _pooled_metadata(6, *common))

    y, design = transformed[:, 0], transformed[:, 1:]
    rank, dof = int(np.linalg.matrix_rank(design)), float(n_obs - n_params)
    absorbed = np.any(np.max(np.abs(design), axis=0) <= FE_ABSORPTION_TOLERANCE)
    if absorbed or rank < n_params:
        return _invalid_pooled(
            fixed_effects, _pooled_metadata(4, *common, dof=dof, rank=rank))
    if np.max(np.abs(y)) <= FE_ABSORPTION_TOLERANCE:
        return _invalid_pooled(
            fixed_effects, _pooled_metadata(3, *common, dof=dof, rank=rank))
    result = sm.OLS(y, design).fit()
    estimate = float(result.params[0])
    inference_dof = float(min(common[2], common[3]) - 1)
    metadata_args = dict(
        dof=float(result.df_resid), r2=float(result.rsquared),
        adjusted_r2=float(result.rsquared_adj), rank=rank,
        inference_dof=inference_dof)
    product_codes, _ = pd.factorize(products, sort=False)
    timestamp_codes, _ = pd.factorize(timestamps, sort=False)
    try:
        robust = result.get_robustcov_results(
            cov_type="cluster",
            groups=np.column_stack([product_codes, timestamp_codes]),
            use_correction=True, df_correction=True, use_t=True)
        variance = float(np.asarray(robust.cov_params())[0, 0])
    except (ValueError, ZeroDivisionError, FloatingPointError,
            np.linalg.LinAlgError):
        variance = np.nan
    if not np.isfinite(variance) or variance < 0:
        return _invalid_pooled(
            fixed_effects,
            _pooled_metadata(7, *common, **metadata_args), estimate)
    se, t_stat, p_value, ci = _inference(
        estimate, variance, inference_dof, confidence_level)
    return PooledRegressionResults(
        "two_way_cluster", "timestamp_product", fixed_effects,
        estimate, se, t_stat, p_value, ci, n_obs,
        _pooled_metadata(None, *common, **metadata_args))


def _pooled_sample(df: pd.DataFrame, columns: list[str],
                   nan_policy: Literal["drop", "strict"]) -> pd.DataFrame:
    """根据缺失值策略构造两个 pooled 规格共用的完整样本。"""
    eligible = df.loc[df["universe"], columns]
    if nan_policy == "drop":
        return eligible.dropna(subset=columns)
    missing_rows = eligible[columns].isna().any(axis=1)
    invalid_products = eligible.loc[missing_rows].index.get_level_values(
        "product").unique()
    mask_valid = ~eligible.index.get_level_values("product").isin(invalid_products)
    return eligible.loc[mask_valid].dropna(subset=columns)


def _ts_regression_details(df: pd.DataFrame, label_horizon: int,
                           min_observations: int,
                           nan_policy: Literal["drop", "strict"],
                           controls: tuple[str, ...], intercept: bool,
                           nw_max_lags: int | None,
                           confidence_level: float) -> TSRegressionEvaluationResults:
    """按 product 执行时序回归，并同时计算两个 pooled 固定效应规格。"""
    columns = ["factor", "label", *controls]
    keys, group_rows, meta_rows, betas = [], [], [], []
    product_results: dict[str, HACRegressionResults] = {}
    requested = label_horizon - 1 if nw_max_lags is None else nw_max_lags
    for key, group in df.groupby(level="product", sort=False):
        selected = _select_group(group, columns, nan_policy)
        result, metadata = _fit_group(selected, controls, intercept, min_observations)
        keys.append(key)
        group_rows.append({"n_eligible": selected.n_eligible,
                           "n_valid": selected.n_valid,
                           "n_dropped": selected.n_dropped})
        meta_rows.append(metadata)
        if result is None:
            betas.append(np.nan)
        else:
            hac = _hac_result(
                result, 1 if intercept else 0, requested,
                confidence_level)
            product_results[str(key)] = hac
            betas.append(hac.estimate)
    index = pd.Index(keys, name="product")
    group_metadata = pd.DataFrame(group_rows, index=index, columns=_GROUP_COLUMNS)
    pooled = _pooled_sample(df, columns, nan_policy)
    empty_universe = not bool(df["universe"].any())
    return TSRegressionEvaluationResults(
        "time_series", label_horizon, min_observations, nan_policy,
        controls, intercept, group_metadata, product_results,
        _metadata_frame(meta_rows, index), _summary(betas),
        _fit_pooled(pooled, controls, min_observations, "product",
                    confidence_level, empty_universe),
        _fit_pooled(pooled, controls, min_observations,
                    "product_timestamp", confidence_level, empty_universe)
    )


def regression_details(
    df: pd.DataFrame,
    mode: Literal["cross_sectional", "time_series"],
    label_horizon: int,
    min_observations: int,
    nan_policy: Literal["strict", "drop"] = "drop",
    control_columns: tuple[str, ...] = (),
    fit_intercept: bool = True,
    nw_max_lags: int | None = None,
    bs_block_size: int | None = None,
    bs_seed: int = 42,
    bs_reps: int = 500,
    confidence_level: float = 0.95,
) -> CSRegressionEvaluationResults | TSRegressionEvaluationResults:
    """计算截面或时序模式下的单因子回归预测诊断。

    对同一研究面板上的一个因子和一个未来标签执行回归评估。截面模式按
    timestamp 估计 Fama–MacBeth 式 factor beta，并对 beta 序列执行 HAC
    均值检验和循环块 bootstrap；时序模式按 product 估计 HAC 回归，同时
    返回 product 固定效应与 product + timestamp 固定效应的 pooled 结果。

    Parameters
    ----------
    df : pd.DataFrame
        待评估面板。索引必须是名称和顺序严格为 ``timestamp``、``product``
        的唯一 MultiIndex，且 timestamp 层为单调递增的 DatetimeIndex。
        必需列为 ``factor``、``label`` 和无缺失的布尔列 ``universe``；还须
        包含 ``control_columns`` 显式声明的所有数值控制变量。
    mode : {'cross_sectional', 'time_series'}
        评估模式。``cross_sectional`` 按 timestamp 独立回归；
        ``time_series`` 按 product 独立回归并计算两个 pooled 规格。
    label_horizon : int
        未来标签的期数，必须为正整数。用于确定默认 HAC 滞后阶数和截面
        bootstrap 块长；标签必须已由上游完成前瞻对齐。
    min_observations : int
        单个回归单元所需的最低有效观测数，必须大于等于 2。实际门槛同时
        不低于参数数量加一，以保证正的残差自由度。
    nan_policy : {'strict', 'drop'}, default 'drop'
        缺失值策略。``drop`` 删除回归变量不完整的合资格行；``strict`` 使
        含缺失值的 timestamp 或 product 整体失效。时序 pooled 回归会整体
        排除 strict 失效的 product。
    control_columns : tuple[str, ...], default ()
        显式参与条件化的控制变量列名，顺序会原样保存在结果中。不得重复或
        包含 ``factor``、``label``、``universe``，其系数不会写入结果。
    fit_intercept : bool, default True
        是否为逐 timestamp 和逐 product 回归加入截距。pooled 回归始终通过
        固定效应吸收处理组均值，不受此参数影响。
    nw_max_lags : int or None, default None
        HAC 协方差请求的最大滞后阶数。None 表示使用
        ``label_horizon - 1``；实际滞后会根据各评价单元的有效样本数收敛。
    bs_block_size : int or None, default None
        截面 beta 循环块 bootstrap 的块长。None 时根据标签期数和有效 beta
        数自动确定。仅适用于截面模式。
    bs_seed : int, default 42
        截面 bootstrap 的非负随机种子，仅适用于截面模式。
    bs_reps : int, default 500
        截面 bootstrap 的正整数重复次数，仅适用于截面模式。
    confidence_level : float, default 0.95
        所有统计推断使用的统一置信水平，必须严格位于 ``(0, 1)``。该值同时
        用于截面 beta 均值 HAC、循环块 bootstrap、逐品种 HAC，以及 pooled
        双向聚类置信区间。

    Returns
    -------
    CSRegressionEvaluationResults or TSRegressionEvaluationResults
        截面模式返回逐 timestamp 的 factor beta、样本与回归元数据、beta
        描述汇总、HAC 均值检验和 bootstrap 区间；时序模式返回逐 product
        的 HAC 结果与元数据、跨品种 beta 汇总，以及两个 pooled 固定效应
        回归结果。局部无效的评价单元通过 NaN 和稳定失效码表达。

    Notes
    -----
    ``universe`` 只决定样本资格，不进入设计矩阵。函数不会重新移动标签、
    修改输入 DataFrame，或返回截距、控制变量和固定效应的系数。时序模式不
    使用 bootstrap，因此只接受 bootstrap 参数的默认配置。
    """
    _validate_inputs(df, mode, label_horizon, min_observations, nan_policy,
                     control_columns, fit_intercept, nw_max_lags,
                     bs_block_size, bs_seed, bs_reps, confidence_level)
    working = df.copy()
    if mode == "cross_sectional":
        return _cs_regression_details(
            working, label_horizon, min_observations, nan_policy,
            control_columns, fit_intercept, nw_max_lags, bs_block_size,
            bs_seed, bs_reps, confidence_level)
    return _ts_regression_details(
        working, label_horizon, min_observations, nan_policy,
        control_columns, fit_intercept, nw_max_lags, confidence_level)
