"""预处理——因子间正交化组件。"""
from __future__ import annotations
from collections.abc import Sequence
import pandas as pd
import numpy as np
from typing import Literal, Any, Self
from dataclasses import dataclass, field
import logging
from factors.common import WarningMixin, walk_forward_split
from factors._utils import validate_unique_names
import copy
from functools import reduce
import warnings

logger = logging.getLogger(__name__)

QR_REASON_CODES = {
    0: 'success',
    1: 'degenerate',
    2: 'all missing',
    3: 'collinear',
    4: 'N<K of factor matrix input',
    5: 'N<K after policy drop_if_missing_any'

}

LSTSQ_REASON_CODES = {
    0: 'success', 
    5: 'dropped',
    4: 'N<K'
}

@dataclass(frozen=True)
class OrthogonalizationResult:
    values: tuple[pd.Series,...] = field(repr=False)
    warnings: tuple[str,...] = field(default=(), repr=False)
    metadata: dict[str, Any] = field(
        default_factory=dict, repr=False)

def _qr_core(F: np.ndarray, tol: float = 1e-8) -> tuple[np.ndarray, np.ndarray]:
    """`F`: N*K 因子矩阵，列顺序即优先级。
    This (core) function requires（When any one of the following is violated, 
    an exception will be raised)：
    - N >= K
    - 不允许有非法值（missing或inf)，
    - 不允许有退化列（std=0)
    - 不允许有共线性

    返回`(G, s)`, 其中 `G` 的每列 `g_j` 为正交化后的因子向量，满足总体方差为1，均值为0。
    `s` 为残差标准差，也为剩余波动比例。
    
    Notes
    ------
    - 返回 `(G, s)`，其中`G`的每一列是 ``g_j = sqrt(N)* u_j / ||u_j||``，即残差的方向；
    `s` 的每个元素为 ``s_j = ||u_j|| / sqrt(N)``，是残差的尺度（RMS）。由于函数内部对 F 
    做每列取均值，故``s_j^2 = 1/N * \\sum_i e_{ij}^2 = Var_{ddof=0}(e_j)``，
    即 `s` 是因子的残差的标准差。同时由于做了标准化，使每列总体标准差为 1 ，因而 `s` 还可以
    解释为剩余波动比例。
    - 若检测到多重共线性（QR返回的s的任一值小于tol），（目前）直接报错。
    """
    assert np.isfinite(F).all().all()

    N, K = F.shape
    assert N >= K 

    mean = F.mean(axis=0)
    std = F.std(axis=0)
    assert (std != 0.0).all()
    F = (F - mean) / std
    
    Q, R = np.linalg.qr(F)    # Householder qr,数值稳定  ，Q为N*K，各列两两正交且长度为1，R为K*K上三角矩阵
    d = np.diag(R)
    G = Q * np.sign(d) * np.sqrt(N)  
    s = np.abs(d) / np.sqrt(N)
    if (s < tol).any():
        pos = np.argmax(s < tol) # Find the first True value of (s<tol). Numpy treats False as 0, True as 1, so find the first position of True.
        raise ValueError(f'Multicolliearity detected: column {pos}.'
                         f'Residual std ratio is {s} < {tol}.')
    #G[:, s < tol] = 0   
    return G, s  

def _qr_find_degenerate_and_all_missing_cols(
        F: np.ndarray
) -> tuple[list[int], list[int]]:
    """Give the positions for degenerate and all-missing columns in F."""
    # assertion: colums of F are eigher "all-NaN" or "all-valid"
    all_nan_col_mask = np.isnan(F).all(axis=0)
    all_nonnan_col_mask = (~np.isnan(F)).all(axis=0)
    assert (all_nan_col_mask | all_nonnan_col_mask).all()

    col_pos_all_nan = list(np.asarray(range(F.shape[1]))[all_nan_col_mask])
    # degenerate column: std == 0.0, we do not consider very small std to be "dengerate" here.
    # If F has only one row, then `std` will give 0.0, which also satisfies "degenerate"
    deg_col_mask_rel = np.std(F[:, all_nonnan_col_mask], axis=0) == 0.0
    col_pos_all_nonnan = np.asarray(range(F.shape[1]))[all_nonnan_col_mask]
    col_pos_deg = list(col_pos_all_nonnan[deg_col_mask_rel])
    return col_pos_deg, col_pos_all_nan

def _qr_find_collinear_cols(
        F: np.ndarray, 
        tol: float, 
) -> list[int]:
    """Give the collinear column positions in F."""
    # To use this function, F must have no degenerate columns.
    assert not (np.std(F, axis=0) == 0.0).any()
    assert (~np.isnan(F)).all().all()

    col_pos_collinear: list[int] = []
    K = F.shape[1]

    # Scale F before check colliearity
    # F has no degenerate or all-NaN columns, so this operation is safe.
    F_norm: np.ndarray = (F - F.mean(axis=0)) / F.std(axis=0) 
    valid_col_pos_comb = [0]
    for k in range(1, K):
        pos_comb_check = valid_col_pos_comb + [k]
        F_norm_sub = F_norm[:, pos_comb_check]
        if np.linalg.matrix_rank(F_norm_sub, tol) < len(pos_comb_check): 
            # no full rank, means collinearity
            col_pos_collinear.append(k)
        else:
            # full rank => no collinearity
            valid_col_pos_comb = pos_comb_check
    return col_pos_collinear




def _seq_orth_qr_safe(
        F: np.ndarray, 
        tol: float = 1e-8,
        policy: Literal['drop_if_missing_any', 'fill_with_mean'] = 'fill_with_mean'
) -> tuple[np.ndarray, np.ndarray, list[int]]: # Q, s, list of code
    """
    处理F的边界条件：
    生成一个 F_qr 供 core 函数使用，剔除全NaN列，degenerate列（std 严格为0），共线列。
    随后，本函数返回的G中，“正常列”由 core 函数返回值提供，全NaN列保持全NaN，degenerate列为全0，
    共线列为全0。同时，将G中输入的F中为缺失值的地方置为NaN。
    
    对于s，正常列由 core 函数返回值提供，全NaN列为NaN，degenerate和共线列为0。

    错误码：0为成功，其他为失败。
    """
    nan_Q = np.full_like(F, np.nan)
    nan_s = np.full(F.shape[1], np.nan)
    reason_codes = [0]*F.shape[1] # indicating whether each column of F is processed successfully or not and why

    F = np.where(np.isinf(F), np.nan, F) # 把inf直接当做NaN处理
    F_input_missing_mask = np.isnan(F)
    N_input, K_input = F.shape

    if N_input < K_input: # 检查一次N、K大小关系
        return nan_Q, nan_s, [4]*K_input

    # 处理缺失值政策
    if policy == 'drop_if_missing_any':
        valid_row_mask = (~np.isnan(F)).all(axis=1)
        F_policy = F[valid_row_mask]
        N, K = F_policy.shape
        if N < K: # here we are deleting rows, so need to check this
            return nan_Q, nan_s, [5]*K_input
    else:
        # 用 context manager 消除可能存在的全NaN列导致的 nanmean 警告
        with warnings.catch_warnings():
            warnings.filterwarnings(
                action='ignore', message='Mean of empty slice',
                category=RuntimeWarning
            )
            mean = np.nanmean(F, axis=0) # 全NaN列会得到 NaN
        F_policy = np.where(np.isnan(F), mean, F)
    
    # Note: F_policy's row count will be smaller or equal to F(input).
    # And have the same column count with F(input).

    col_pos_deg, col_pos_all_nan = _qr_find_degenerate_and_all_missing_cols(F_policy) # 进这个函数的，其列要么全NaN，要么全非NaN。
    col_pos_deg: list[int]; col_pos_all_nan: list[int]
    for pos in col_pos_deg: 
        reason_codes[pos] = 1
    for pos in col_pos_all_nan:
        reason_codes[pos] = 2

    valid_col_mask = [False if k in (col_pos_deg+col_pos_all_nan) else True
                       for k in range(K_input)]
    F_deg_allnan_col_dropped = F_policy[:, valid_col_mask] # here we are deleting columns, so K will become smaller, no need to check whether N<K
    if F_deg_allnan_col_dropped.size == 0: # all columns are either degenerate or all-missing
        # for degenerate columns, set Q and s to zero.
        Q_out = np.where(col_pos_deg, 0.0, nan_Q)
        s_out = np.where(col_pos_deg, 0.0, nan_s)
        return nan_Q, nan_s, reason_codes
    
    if F_deg_allnan_col_dropped.shape[1] > 1:
        relative_col_pos_collinear:list[int] = _qr_find_collinear_cols(F_deg_allnan_col_dropped, tol=tol) # check this `tol` parameter, suitable to be passed through here?
        remaining_col_pos_after_dropping_deg_allnan = [pos for pos in range(K_input) 
                             if pos not in (col_pos_deg+col_pos_all_nan)] 
        col_pos_collinear = [remaining_col_pos_after_dropping_deg_allnan[pos] for pos in relative_col_pos_collinear]
        for pos in col_pos_collinear:
            reason_codes[pos] = 3
        relative_valid_col_mask = [False if k in relative_col_pos_collinear else True 
                                   for k in range(F_deg_allnan_col_dropped.shape[1])]
        F_qr = F_deg_allnan_col_dropped[:, relative_valid_col_mask]
    else: # F_valid has exactly one column
        col_pos_collinear = []
        F_qr = F_deg_allnan_col_dropped

    G_core, s_core = _qr_core(F_qr, tol=tol)
    
    G = np.full((N_input, K_input), fill_value=np.nan)
    s = np.full(K_input, fill_value=np.nan)
    
    into_core_col_mask = [False if k in (col_pos_deg+col_pos_all_nan+col_pos_collinear)
                     else True for k in range(K_input)] # 指示送进 qr_core 函数的是哪些列
    # Remember, under 'drop_if_missing_any',
    # we have use `valid_row_mask` to select only "valid" rows.
    # So here we need to fill only those "valid" rows, and keep "invalid" place
    # as is(NaN).
    if policy == 'drop_if_missing_any': 
        G[np.ix_(valid_row_mask, into_core_col_mask)] = G_core # pyright: ignore[reportPossiblyUnboundVariable]
    else: # Under 'fill_with_mean', we have never deleted rows. So number of rows of G_core and G must be the same.
        G[:, into_core_col_mask] = G_core

    s[into_core_col_mask] = s_core

    # 剩余的列，degenerate 列和共线列用 0填充，all-NaN 列填NaN（原本就是NaN初始化，直接不管）
    deg_collinear_col_mask = [True if k in (col_pos_deg+col_pos_collinear) else False
                             for k in range(K_input)]
    G[:, deg_collinear_col_mask] = 0
    s[deg_collinear_col_mask] = 0

    # 将输入矩阵中原本是inf或缺失的地方，置回缺失值
    G[F_input_missing_mask] = np.nan
    
    return G, s, reason_codes


# [Implementation Note]
# What are the problems that we are going to solve?
# Essentially there are two goals: 
# First, we need to assure that the design matrix send into np.linalg.lstsq is of
# "full-rank". Second, we need to assure that the detection of design matrix rank 
# and the least-square process use the same numerical condition for the rank determination process.
# That is, if we use some type of "scaling" and some tolerance level 
# for the rank determination procedure, then for the regression part, 
# we should use the same "scaling", else there will be disparity 
# between regression and rank determination. 
type FitOks = list[bool | None]
type Keeps = list[list[int]]
type Scales = list[np.ndarray]
type Gammas = list[np.ndarray]
type ReasonCodes = list[list[int]]
type Ratios = list[float]

def _lstsq_beta_smart(
        X: np.ndarray, 
        y: np.ndarray, 
        tol: float
) -> tuple[np.ndarray, list[int], np.ndarray, np.ndarray]:
    """
    （1）先对 X 每列做 MaxAbs 缩放。
    （2）从 X 中筛选出一个满秩的 design matrix，然后进行回归。
    （3）返回 `(safe_scale, keep, gamma, e)`，
    gamma 为回归系数，对应的是被 scale 过的 X。
    
    """
    scale: np.ndarray = np.max(np.abs(X), axis=0)
    is_zero = scale == 0.0
    safe_scale = np.where(is_zero, 1.0, scale) 
    Z = X / safe_scale
    # 首先根据X选择出合法列作为 design matrix
    keep: list[int] = []
    rhs = np.zeros_like(Z)
    for j in range(Z.shape[1]):
        if is_zero[j]:
            continue
        trail = keep + [j]
        _, _, rank, _ = np.linalg.lstsq(Z[:, trail], rhs, rcond=tol)
        if rank == len(trail):
            keep = trail
    # 若有效基底为空，此时残差为目标变量y本身
    if not keep:
        gamma = np.empty(0)
        e = y.copy() 
    # 若不为空，则执行 least-square 算法
    else:
        A = Z[:, keep]
        gamma, _, rank, _ = np.linalg.lstsq(A, y, rcond=tol)
        if rank != A.shape[1]:
            raise ValueError(f'least-square 算法仍然不满秩。请检查数值条件等问题。')
        e = y - A @ gamma
    return safe_scale, keep, gamma, e

def _seq_lstsq_beta_safe(
        F: np.ndarray,
        tol: float, 
        intercept: bool = False,
        policy: Literal['common', 'own'] = 'own'
) -> tuple[FitOks, Keeps, Scales, Gammas, ReasonCodes, Ratios]:
    """
    Use least-square method for sequentially orthogonalization.
    
    """
    K_input = F.shape[1]

    def post_conditions(fit_oks, keeps, scales, betas, reason_codes, ratios):
        assert len(fit_oks) == K_input
        assert len(keeps) == K_input
        assert len(scales) == K_input
        assert len(betas) == K_input
        assert len(reason_codes) == K_input
        for k, codes in enumerate(reason_codes):
            assert len(codes) == k + int(intercept) 
        assert len(ratios) == K_input

    if intercept:
        ones = np.ones((F.shape[0], 1))
        F = np.hstack([ones, F])
    
    N, K = F.shape
    nan_gammas = [np.asarray([np.nan]*i) for i in range(K)]

    if N < K: # 样本不足，直接短路返回
        fit_oks = [False] * K_input
        keeps = [[] for k in range(K_input)]
        empty_scales = [np.asarray([])] * K_input
        nan_betas_re = nan_gammas if not intercept else nan_gammas[1:]
        nan_reason_codes = [[4]*(k+int(intercept)) for k in range(K_input)]
        nan_ratios = [np.nan]*K_input
        post_conditions(fit_oks, keeps, empty_scales, nan_betas_re, nan_reason_codes, nan_ratios)
        return (fit_oks, keeps, empty_scales, nan_betas_re, nan_reason_codes, nan_ratios)

    valid = np.isfinite(F).all(axis=1) # 1-d boolean, indicating "row validity"
    fit_oks: list[bool|None] = []
    keeps: list[list[int]] = []
    scales: list[np.ndarray] = []
    gammas: list[np.ndarray] = []
    reason_codes: list[list[int]] = []
    ratios: list[float] = []

    if not intercept:
        fit_oks.append(None)
        keeps.append([])
        scales.append(np.asarray([]))
        gammas.append(np.asarray([]))
        reason_codes.append([])
        ratios.append(np.nan)
    
    for k in range(1, K):
        valid = valid if policy=='common' else np.isfinite(F[:, :k+1]).all(axis=1)
        if valid.sum() < k: # 由于越往后面的因子走，k会越来越大，而有效样本只可能减小或不变，所以后续的回归可以直接不用做了，直接用nan beta
            fit_oks.extend([False] * (K-k))
            keeps.extend([[] for _ in range(k, K)])
            scales.extend([np.asarray([])] *(K-k))
            gammas.extend(nan_gammas[k:])
            reason_codes.extend([[4]*j for j in range(k, K)])
            ratios.extend([np.nan]*(K-k))
            break
        X = F[valid, :k]
        y = F[valid, k]
        safe_scale, keep, gamma, e = _lstsq_beta_smart(X, y, tol)
        # 记录结果
        fit_oks.append(True)
        keeps.append(keep)
        scales.append(safe_scale)
        gammas.append(gamma)
        codes = [0 if j in keep else 5 for j in range(k)]
        reason_codes.append(codes)
        # compute std(e)/std(y), need to be used as metadata
        std_e = float(np.std(e))
        std_y = float(np.std(y))
        ratios.append(std_e/std_y if std_y!=0.0 else np.nan)

    post_conditions(fit_oks, keeps, scales, gammas, reason_codes, ratios)
    return fit_oks, keeps, scales, gammas, reason_codes, ratios

def _seq_res_with_fitted_betas(
        F: np.ndarray,
        fit_oks: FitOks,
        keeps: Keeps,
        scales: Scales,
        gammas: Gammas,
        intercept: bool = False,
) -> np.ndarray:
    """
    """
    assert len(gammas) == F.shape[1]
    
    if intercept:
        F = np.hstack([np.ones((F.shape[0], 1)), F])
        fit_oks = [None] + fit_oks
        keeps = [[]] + keeps
        scales = [np.asarray([])] + scales
        gammas = [np.asarray([])] + list(gammas)
    N, K = F.shape
    R = np.empty_like(F)
    R[:, 0] = F[:, 0]
    for k in range(1, K):
        y = F[:, k]
        ok, keep, scale, g = fit_oks[k], keeps[k], scales[k], gammas[k]
        assert ok is not None
        if not ok:
            R[:, k] = np.nan
        else:
            if not keep:
                R[:, k] = y.copy()
            else:
                X = F[:, :k]
                Z = X / scale
                A = Z[:, keep]
                R[:, k] = y - A @ g
    return R if not intercept else R[:, 1:]


class CSGramSchmidtOrthogonalizer(WarningMixin):
    """"
    截面模式下的 Gram-Schmidt 序贯正交化组件。

    Parameters
    ------
    method: {'lstsq', 'qr'}, default 'qr'
        正交化方法，目前仅支持  'qr'。
    qr_missing_policy: {'drop_if_missing_any', 'fill_with_mean'}, default 'fill_with_mean'
        qr 方法下的缺失值政策。若为 'drop_if_missing_any'，对某一截面而言，则对某一行若任一
        因子有缺失值，则剔除整行（品种），即后续因子缺失会额外删除影响前面的样本。
        若某一个因子全部为缺失值，会导致该截面正交化整体失败。
        若为 'fill_with_mean'，则会先对截面上因子值缺失的品种用其余品种的均值填充。
        此时若某个因子全缺失，该因子会被剔除，残差为NaN，留下的因子正常做序贯正交化。
        - 注意：'fill_with_mean' 模式下，返回的残差化因子会根据输入中的缺失值重新置回缺失值，因此
        无法保证残差依然正交，因为此时“正交”的语义是对填充了缺失值后的因子做正交。
    qr_tol: float, default 1e-8
        qr 模式下的秩判定阈值，会送入 `np.linalg.matrix_rank`，秩判定时，内部会先做zscore化
        以改善数值条件。
    warning_limit: int or None, default None
        `WarningMixin` 类的内部缓存警告条数上限。为 None 时采用该类内部设定的默认值。
    
    Methods
    ------
    transform(*factors, eligible)
        对每个截面做序贯正交化变换，得到残差化因子。
        
        
    """

    def __init__(
            self, 
            method: Literal['qr', 'lstsq'] = 'qr',
            qr_missing_policy: Literal[
                'drop_if_missing_any', 
                'fill_with_mean'] = 'fill_with_mean', 
            qr_tol: float = 1e-8,
            *,
            warning_limit: int | None = None,
    ):
        if method == 'lstsq':
            raise NotImplementedError('lstsq not implemented in V1.')
        self._method = method
        self._qr_missing_policy: Literal[
            'drop_if_missing_any', 'fill_with_mean']  = qr_missing_policy
        self._qr_tol= qr_tol
        self._init_warnings(warning_limit)

    def transform(
            self, 
            *factors: pd.Series, 
            eligible: pd.Series[bool]
    ) -> OrthogonalizationResult:
        """
        对每个截面做序贯正交化变换。

        Parameters
        *factors: pd.Series
            多个待做正交化的因子。
        eligible: pd.Series
            品种池准入掩码。
        
        Returns
        ------
        OrthogonalizationResult
            正交化结果。包含 `values: pd.Series`, `warnings: tuple[str]`, 
            `metadata: dict[str, Any]`。
        
        """
        self._reset_warnings()

        names = [f.name if isinstance(f.name, str) else f'factor{i}_unnamed' 
                for i, f in enumerate(factors)]
        validate_unique_names(names) 

        factor_df = pd.concat(factors, axis=1, join='outer') # MultiIndex[timestamp, product], Column[factor]
        factor_df.columns = names
        # TODO： 是否改为类似`apply_eligible_mask` 专用方法？
        # Acutally here directly `.loc` where drop the products
        # outside universe. And the returned "Orthogonalized Series"
        # will have indices that are different from their inputs.
        # Also, whether we need to restore the input index of each factor 
        # is an issue that has yet to be determined.
        factor_df = factor_df.loc[eligible] 
        factor_df = factor_df.astype(float)

        grouped = factor_df.groupby(level='timestamp')
        timestamp_l: list[pd.Timestamp] = []
        resid_std_l: list[np.ndarray] = []
        G_l: list[np.ndarray] = []
        indices: list[pd.Index] = []
        reason_codes_l: list[list[int]] = []
        for ts, df in grouped:
            timestamp_l.append(ts) # pyright: ignore[reportArgumentType]
            indices.append(df.index)
            F = df.to_numpy()
            G, resid_std, codes = _seq_orth_qr_safe(F, self._qr_tol, self._qr_missing_policy)
            assert G.shape == df.shape # 正交化后的因子 matrix 的 N（品种数量）应该和输入一致
            assert resid_std.shape[0] == df.shape[1] # 残差标准差 array 的长度应该和因子数量一致
            n_failed = sum(c!=0 for c in codes)
            if n_failed != 0:
                self._warn(f'{ts}: operation failed for {n_failed} factors.')
            G_l.append(G)
            resid_std_l.append(resid_std)
            reason_codes_l.append(codes)

        idx = indices[0].append(indices[1:])
        orth_factor_df = pd.DataFrame(
            np.vstack(G_l), columns=factor_df.columns, index=idx)
        orth_series_tup = tuple(orth_factor_df[col] for col in orth_factor_df)
        resid_std_df = pd.DataFrame(
            np.vstack(resid_std_l), index=timestamp_l, columns=factor_df.columns)
        failed_reason_df = pd.DataFrame(
            reason_codes_l, columns=factor_df.columns,
            index=timestamp_l).replace(QR_REASON_CODES).astype('category')

        self._log_warnings(logger=logger, factor_name=f'mulfi-factor: {factor_df.columns}')

        return OrthogonalizationResult(
            values=orth_series_tup,
            warnings=tuple(self.warnings),
            metadata={
                'residual_variation_ratio': resid_std_df,
                'failed_reasons': failed_reason_df
            }
        )



class TSGramSchmidtOrthogonalizer(WarningMixin):
    """
    时序模式下的 Gram-Schmidt 正交化组件。

    Parameters
    ------
    fit_intercept: bool, optional, default False
        正交化（回归）时，是否包含截距。
    rcond_tol: float or None, optional, default None
        送入 `np.linalg.lstsq` 的 rcond 参数取值，用于秩判定，默认为 None 时，
        内部会设置为1e-10。
    sample_policy: {'common', 'own'}, default 'own'
        序贯正交化时，如何选择样本，为 'common' 时，会取所有因子的非缺失样本（任一
        因子有缺失值即删除整行，后面的因子缺失会影响前面的样本）。若某一因子整体缺失，会
        导致正交化失败。
        为 'own' 时，目标因子作为被解释变量，只会删除该目标因子
        和前序因子的缺失行，后面因子的缺失值没有影响。若某一因子整体缺失，则该因子之前的
        所有因子可以正常正交化，从该因子直到最后一个无法得到残差。

    warning_limit: int or None, optional, default None
        `WarningMixin` 类的内部缓存警告条数上限。为 None 时采用该类内部设定的默认值。
    
    Methods
    ------
    fit(*factors, eligible)
        对训练期样本做正交化拟合，得到回归系数等。
    transform(*factors, eligible)
        对变换期的样本做正交（残差化）处理，内部利用训练期的回归系数等。
    """

    def __init__(
            self, 
            fit_intercept: bool = False,
            rond_tol: float | None = None,
            sample_policy: Literal['common', 'own'] = 'own',
            *,
            warning_limit: int | None = None,
    ):
        self._init_warnings(warning_limit)

        self._intercept = fit_intercept
        self._tol = rond_tol or 1e-10
        self._policy: Literal['common', 'own'] = sample_policy

        self._fit_factor_names: list[str] | None

        self._fit_oks_by_product: dict[str, FitOks] | None = None
        self._keeps_by_product: dict[str, Keeps] | None = None
        self._scales_by_product: dict[str, Scales] | None = None
        self._gammas_by_product: dict[str, Gammas] | None = None
        self._reason_codes_by_product: dict[str, ReasonCodes] | None = None
        self._ratios_by_product: dict[str, Ratios] | None = None
        

    def fit(
            self, 
            *factors: pd.Series, 
            eligible: pd.Series[bool]
    ) -> Self:
        """
        利用训练期因子数据做拟合，得到回归结果。
        回归结果包含：
        -（对于每个目标变量）是否回归成功
        - (对于每个目标变量) 保留了哪些因子列
        - (对于每个目标变量) 对每个因子如何做缩放，用于改善秩判定的数值条件
        - (对于每个目标变量) 回归系数
        - (对于每个目标变量) 每个前序因子是否包含在回归中以及为什么的原因码
        - 每个因子的残差标准差比例，表达正交化后还剩多少波动。

        Parameters
        ------
        *factors: pd.Series
            待正交化的多个因子，从左到右做序贯正交。
        eligible: pd.Series
            品种池准入掩码。

        Returns
        ------
        Self
            返回自身，内部保留回归结果作为“状态”。
        
        """
        self._reset_warnings()

        names = [f.name if isinstance(f.name, str) else f'factor{i}_unnamed' 
                             for i, f in enumerate(factors)]
        validate_unique_names(names)
        self._fit_factor_names = names

        factor_df = pd.concat(factors, axis=1, join='outer') # MultiIndex[timestamp, product], Column[factor]
        factor_df = factor_df.loc[eligible]
        factor_df = factor_df.astype(float)
        grouped = factor_df.groupby(level='product')

        fit_oks_by_product: dict[str, FitOks] = {}
        keeps_by_product: dict[str, Keeps] = {}
        scales_by_product: dict[str, Scales] = {}
        gammas_by_product: dict[str, Gammas] = {}
        reason_codes_by_product: dict[str, ReasonCodes] = {}
        ratios_by_product: dict[str, Ratios] = {}

        for prod, df in grouped:
            assert isinstance(prod, str)
            F = df.to_numpy()
            fit_oks, keeps, scales, gammas, codes, ratios = _seq_lstsq_beta_safe( 
                F, self._tol, self._intercept, self._policy)
            # ------------------------------------
            # 记录结果
            fit_oks_by_product[prod] = fit_oks
            keeps_by_product[prod] = keeps
            scales_by_product[prod] = scales
            gammas_by_product[prod] = gammas
            reason_codes_by_product[prod] = codes
            ratios_by_product[prod] = ratios
            # ------------------------------------
            n_fit_failed = sum([1 if x == False else 0 for x in fit_oks])
            if n_fit_failed != 0:
                self._warn(f'{prod}: Orthogonalization failed for {n_fit_failed} factors.')
        
        # publish results
        self._fit_oks_by_product = fit_oks_by_product
        self._keeps_by_product = keeps_by_product
        self._scales_by_product = scales_by_product
        self._gammas_by_product = gammas_by_product
        self._reason_codes_by_product = reason_codes_by_product
        self._ratios_by_product = ratios_by_product

        self._log_warnings(logger, f'Multifactor TS orthogonalization: fit phase')
        return self

    def _check_is_fitted(self, ):
        if self._gammas_by_product is None:
            raise ValueError(f'Not fitted. Fit first please.')
        
    # TODO: 增加fit-transform 窗口不重叠校验
    def transform(
            self,
            *factors: pd.Series,
            eligible: pd.Series[bool] | None
    ) -> OrthogonalizationResult:
        """
        利用训练期拟合的“状态”，对变换期因子数据做序贯正交化。

        Parameters
        ------
        *factors: pd.Series
            变换期待做序贯正交化的多个因子，输入因子的顺序和名称必须和训练期对齐。
        eligible: pd.Series
            品种池准入掩码。
        
        Returns
        ------
        OrthogonalizationResult
            正交化结果。包含 `values: pd.Series`, `warnings: tuple[str]`, 
            `metadata: dict[str, Any]`。

        Notes
        ------
        1. 注意，不保证变换期的各因子在数值上得到相互正交属性。

        
        """
        self._check_is_fitted()

        trans_factor_names = [f.name if isinstance(f.name, str) else f'factor{i}_unnamed' 
                             for i, f in enumerate(factors)]
        if trans_factor_names != self._fit_factor_names:
            raise ValueError(f'fit factor names: {self._fit_factor_names}, '
                             f'transform factor names: {trans_factor_names}, '
                             'they are not equal.')
        factor_df = pd.concat(factors, axis=1, join='outer') # MultiIndex[timestamp, product], Column[factor]
        if eligible is not None:
            factor_df = factor_df.loc[eligible]
        factor_df = factor_df.astype(float)

        grouped = factor_df.groupby(level='product')
        Rs: list[np.ndarray] = []
        indices: list[pd.Index] = []

        for prod, df in grouped:
            assert isinstance(prod, str)
            F = df.to_numpy()
            fit_oks = self._fit_oks_by_product.get(prod, None) # pyright: ignore[reportOptionalMemberAccess]
            keeps = self._keeps_by_product.get(prod, None) # pyright: ignore[reportOptionalMemberAccess]
            scales = self._scales_by_product.get(prod, None) # pyright: ignore[reportOptionalMemberAccess]
            gammas = self._gammas_by_product.get(prod, None)  # pyright: ignore[reportOptionalMemberAccess]
            if gammas is not None:
                R = _seq_res_with_fitted_betas(F, fit_oks, keeps, scales, gammas, self._intercept)  # pyright: ignore[reportArgumentType]
            else: 
                R = np.full_like(F, fill_value=np.nan)
                self._warn(f'transform phase, product {prod} fitted betas not found(fit data did not contain this product).')
            Rs.append(R)
            indices.append(df.index)

        transed_factor_df = pd.DataFrame(
            np.vstack(Rs), 
            index=indices[0].append(indices[1:]), 
            columns=factor_df.columns
        )
        transed_factor_df = transed_factor_df.sort_index()
        
        self._log_warnings(logger=logger, factor_name='Multifactor orthogonalization: transform phase')

        return OrthogonalizationResult(
            values=tuple(transed_factor_df[col] for col in transed_factor_df),
            warnings=tuple(self.warnings),
            metadata={
                'fit_oks': self._build_df_from_dict('_fit_oks_by_product'),
                'fit_keeps': self._build_df_from_dict('_keeps_by_product'),
                'fit_scales': self._build_df_from_dict('_scales_by_product'),
                'gammas': self._build_df_from_dict('_gammas_by_product'),
                'fit_residual_std_ratios': self._build_df_from_dict('_ratios_by_product'),
                'fit_reason_codes': (self._build_df_from_dict('_reason_codes_by_product'))
            }
        )

    def _build_df_from_dict(self, attr_name: str) -> pd.DataFrame:
        data = getattr(self, attr_name)
        assert isinstance(data, dict)
        df = pd.DataFrame.from_dict(
            data, orient='index', 
            columns=self._fit_factor_names # pyright: ignore[reportArgumentType]
        )  
        return df
        

def walk_forward_orthogonalize(
        *factors: pd.Series,
        eligible: pd.Series,
        ts_orthogonalizer: TSGramSchmidtOrthogonalizer, # 以后可扩展， 此处采用 duck type，必须拥有 fit, transform 方法， transform 方法返回 OrthogonalizationResult 类
        fit_size: int | pd.DateOffset,
        transform_size: int | pd.DateOffset,
        mode: Literal['rolling', 'expanding'] = 'rolling'
) -> OrthogonalizationResult:
    """
    时序模式下的“推进拟合->变换”的正交化。

    Parameters
    ------
    * factors: pd.Series
        待做序贯正交化的多个因子，顺序从左到右。
    eligible: pd.Series
        品种池准入掩码。
    ts_orthogonalizer
        时序模式下支持 `fit` 和 `transform` 方法的正交化组件。
        - 目前仅支持 :class:`TSGramSchmidtOrthogonalizer`
    fit_size: int or pd.DateOffset
        拟合窗口大小。
    transform_size: int or pd.DateOffset
        变换窗口大小。拟合和变换窗口要么都是 `int`，要么都是 `DateOffset`。
    mode: {'rolling', 'expanding'}, optional, default 'rolling'
        窗口向前推进模式，滚动或扩展，默认为滚动。
    
    Returns
    ------
    OrthogonalizationResult
        正交化结果，包含 `values: pd.Series`, `warnings: tuple[str]`, `metadata: dict[str, Any]`。

    """
    orth = ts_orthogonalizer
    joint_index = reduce(lambda idx_a, idx_b: idx_a.union(idx_b), 
                         [f.index for f in factors])
    timeline = joint_index.get_level_values('timestamp').unique().sort_values()
    assert isinstance(timeline, pd.DatetimeIndex)

    splits = walk_forward_split(
        timeline, 
        train_size=fit_size,
        validation_size=None,
        test_size=transform_size,
        mode=mode,
        embargo=0,
    )

    warnings: list[str] = []
    metadata: dict[str, Any] = {}
    _orth_values_folds: list[tuple[pd.Series, ...]] = []
    for i, fold in enumerate(splits):
        fit_factors = [f.loc[fold.train[0]:fold.train[-1]] for f in factors]
        trans_factors = [f.loc[fold.test[0]:fold.test[-1]] for f in factors]
        orth.fit(*fit_factors, eligible=eligible)
        rst = orth.transform(*trans_factors, eligible=eligible)
        _orth_values_folds.append(rst.values)
        warnings.extend(f'fold{i}: {msg}' for msg in rst.warnings)
        metadata[f'fold{i}'] = rst.metadata

    orth_factors: list[pd.Series] = []
    for sub_series_tup in zip(*_orth_values_folds):
        sub_series_tup: tuple[pd.Series]
        orth_factors.append(pd.concat(sub_series_tup, axis=0))
    
    return OrthogonalizationResult(
        values=tuple(orth_factors),
        warnings=tuple(warnings),
        metadata=metadata
    )




# ======================================
#  inline test
# ======================================
if __name__ == '__main__':
    # nobs = 1
    # rng = np.random.default_rng(42)
    # f1 = rng.standard_normal((nobs, 1))
    # f2 = np.full_like(f1, np.nan)
    # f3 = np.full_like(f1, 0.5)
    # f4 = rng.standard_normal((nobs, 1))
    # F = np.hstack([f1, f2, f3, f4])
    # print(F)
    # rst = _find_degenerate_and_all_missing_cols(F)  
    # print(rst)
    
    # # -------------------------- test of `seq_orth_qr_safe` --------------------
    # # case 1, 5 factors, the 1st, and 4th are collinear
    # nobs = 10
    # rng = np.random.default_rng(42)
    # f1 = rng.standard_normal((nobs, 1))
    # f2 = f1.copy()
    # f3 = rng.standard_normal((nobs,1))
    # f4 = 0.5 * f1 + 0.5 * f2 + rng.standard_normal((nobs,1))/1e10
    # f5 = rng.standard_normal((nobs,1))
    # F = np.hstack([f1, f2, f3, f4, f5])
    # # cols = _find_collinear_cols(F, tol=1e-8)
    # # print(f'collinear columns detected are:{cols}, should be [1, 3]')

    # Q, s, codes = seq_orth_qr_safe(F, policy='drop_if_missing_any')
    # print(f'Q is :\n{Q}')
    # print(f's is :\n{s}')
    # print(f'codes are :\n{codes}')

    # # case 2, f2 is collinear, f3 is degenerate, f4 is all-nan
    # # policy = 'drop_if_missing_any'
    # nobs = 20
    # rng = np.random.default_rng(42)
    # f1 = rng.standard_normal((nobs, 1))
    # f2 = f1.copy()
    # f3 = np.full_like(f1, fill_value=0.5)
    # f4 = np.full_like(f1, fill_value=np.nan)
    # f5 = rng.standard_normal((nobs,1))
    # F = np.hstack([f1, f2, f3, f4, f5])
    # Q, s, codes = seq_orth_qr_safe(F, policy='drop_if_missing_any')
    # print('-'*20)
    # print(f'policy is "drop_if_missing_any"')
    # print(f'Q is :\n{Q}')
    # print(f's is :\n{s}')
    # print(f'codes are :\n{codes}')

    # # policy = 'fill_with_mean'
    # Q, s, codes = seq_orth_qr_safe(F, policy='fill_with_mean')
    # print('-'*20)
    # print(f'policy is "fill_with_mean"')
    # print(f'Q is :\n{Q}')
    # print(f's is :\n{s}')
    # print(f'codes are :\n{codes}')   

    # case 3, same as case 2, only that f1 and f2's first element is NaN
    # nobs = 20
    # rng = np.random.default_rng(42)
    # f1 = rng.standard_normal((nobs, 1))
    # f1[0] = np.nan
    # f2 = f1.copy()
    # f3 = np.full_like(f1, fill_value=0.5)
    # f4 = np.full_like(f1, fill_value=np.nan)
    # f5 = rng.standard_normal((nobs,1))
    # F = np.hstack([f1, f2, f3, f4, f5])
    # print(F)
    
    # Q, s, codes = seq_orth_qr_safe(F, policy='fill_with_mean')
    # print('-'*20)
    # print(f'policy is "fill_with_mean"')
    # print(f'Q is :\n{Q}')
    # print(f's is :\n{s}')
    # print(f'codes are :\n{codes}')  

    # --------------------------------- test of TS mode --------------------
    import numpy as np
    from factors.preprocess.orthogonalize import _seq_lstsq_beta_safe, _seq_res_with_fitted_betas
    rng = np.random.default_rng(42)
    f1 = rng.standard_normal((10, 1))
    f2 = f1.copy() + rng.standard_normal((10, 1))/1e25
    f3 = rng.standard_normal((10, 1))
    f4 = np.full((10, 1), 1)
    f5 = rng.standard_normal((10, 1))

    F = np.hstack([f1, f2, f3, f4, f5])
    print(f'F is :\n{F}')



    






    

    


        
            



