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
    3: 'collinear',
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

def _find_degenerate_and_all_missing_cols(
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

def _find_collinear_cols(F: np.ndarray, tol: float) -> list[int]:
    """Give the collinear column positions in F."""
    col_pos_collinear: list[int] = []
    K = F.shape[1]
    # Here F has no degenerate or all-NaN columns, so this operation is safe.
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

    col_pos_deg, col_pos_all_nan = _find_degenerate_and_all_missing_cols(F_policy) # 进这个函数的，其列要么全NaN，要么全非NaN。
    col_pos_deg: list[int]; col_pos_all_nan: list[int]
    for pos in col_pos_deg: 
        reason_codes[pos] = 1
    for pos in col_pos_all_nan:
        reason_codes[pos] = 2

    valid_col_mask = [False if k in (col_pos_deg+col_pos_all_nan) else True
                       for k in range(K_input)]
    F_deg_allnan_col_dropped = F_policy[:, valid_col_mask] # here we are deleting columns, so K will become smaller, no need to check whether N<K
    if F_deg_allnan_col_dropped.size == 0: # all columns are either degenerate or all-missing
        return nan_Q, nan_s, reason_codes
    
    if F_deg_allnan_col_dropped.shape[1] > 1:
        relative_col_pos_collinear:list[int] = _find_collinear_cols(F_deg_allnan_col_dropped, tol=tol) # check this `tol` parameter, suitable to be passed through here?
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
# sequentially, if one factor is collinear with previous ones,
# then we should ignore it in the sequentially orthogonalization process.
def _seq_lstsq_beta_with_nan(
        F: np.ndarray,
        rcond: float | None = None, 
        intercept: bool = False,
        policy: Literal['common', 'own'] = 'own'
) -> tuple[list[np.ndarray], list[None | int], list[float]]:
    """
    Use least-square method for sequentially orthogonalization.

    returns `betas, reason_codes, ratios.`

    """
    K_input = F.shape[1]
    if intercept:
        ones = np.ones((F.shape[0], 1))
        F = np.hstack([ones, F])
    
    N, K = F.shape
    nan_betas = [np.asarray([np.nan]*i) for i in range(K)]

    if N < K: # 如果样本数不足，直接返回全 nan beta
        nan_betas_re = nan_betas if not intercept else nan_betas[1:]
        assert len(nan_betas_re) == K_input # post-condition 
        return nan_betas_re, [4]*K_input, [np.nan]*K_input

    valid = np.isfinite(F).all(axis=1) # 1-d boolean, indicating "row validity"
    betas: list[np.ndarray] = []
    col_pos_collinear: list[int] = [] 
    reason_codes: list[None | int] = []
    ratios: list[float] = []

    if not intercept:
        betas.append(np.asarray([]))
        reason_codes.append(None)
        ratios.append(np.nan)
    
    for k in range(1, K):
        valid = valid if policy=='common' else np.isfinite(F[:, :k+1]).all(axis=1)
        if valid.sum() < k: # 由于越往后面的因子走，k会越来越大，而有效样本只可能减小或不变，所以后续的回归可以直接不用做了，直接用nan beta
            betas.extend(nan_betas[k:])
            reason_codes.extend([4]*(K-k))
            ratios.extend([np.nan]*(K-k))
            break
        # Note that here `_pos` are deleting columns, so no need
        # to check N, K relationship again(there will be less columns, making 
        # the N, K relationship safer.)
        _pos = [j for j in range(k) if j not in col_pos_collinear]
        X = F[np.ix_(valid, _pos)] # use np.ix_ to construct a grid(or say, mesh) for fancy indexing
        y = F[valid, k]
        beta, ss_res, rank, _= np.linalg.lstsq(X, y, rcond=rcond)
        # Note that we are doing least-square sequentially, 
        # and we have used col_pos_non_collinear for selecting columns,
        # so all columns before k are "valid(non-collinear)", then
        # rank deficiency must mean the latest-added column k-1 is "collinear"
        # We must delete the last column and do least square again, since
        # np.linalg.lstsq doesn't drop the collinear column automatically.
        # That is, we must drop the last column manually and fit again.
        if rank < X.shape[1]: 
            reason_codes.append(3)
            col_pos_collinear.append(k-1)
            beta, ss_res, rank_, _ = np.linalg.lstsq(X[:, :-1], y, rcond=rcond)
            assert rank_ == X[:, :-1].shape[1] 
            ss_res = ss_res[0]
            e = y - X[:, :-1] @ beta
        else: # full rank
            reason_codes.append(0)
            e = y - X @ beta
        # ---------------------------------------------------------------#
        # Here the beta coefficients corresponds to columns 
        # in F that are not "collinear"
        # (because here we have fitted only on "non-collinear" columns).
        _gen = (b for b in beta)
        beta = np.asarray(
            [next(_gen) if j not in col_pos_collinear else np.nan 
            for j in range(k)]
        ) 
        # ---------------------------------------------------------------#
        betas.append(beta)
        # compute std(e)/std(y), need to be used as metadata
        std_e = float(np.std(e))
        std_y = float(np.std(y))
        ratios.append(std_e/std_y if std_y!=0.0 else np.nan)

    # post-condition
    assert len(betas) == K_input # betas的长度一定等于输入的矩阵的列数
    assert len(reason_codes) == K_input
    assert len(ratios) == K_input
    return betas, reason_codes, ratios

def _seq_res_with_fitted_betas(
        F: np.ndarray,
        betas: Sequence[np.ndarray],
        intercept: bool = False,
) -> np.ndarray:
    """
    根据betas中的回归系数序列，依次计算F中每一列的残差(第1列不动，第2列取第1列*beta的残差，
    第3列取第[1,2]列*beta的残差，依次类推。

    注意，由于存在可能的共线，即 beta 中可能有 np.nan，此时需要把F中对应列剔除掉。
    例如 F = [f1, f2, f3, f4, f5]，f2和f1共线，f3独立，f4和[f1, f3]共线，f5独立。
    那么
    - f1的残差，是f1本身；
    - f2的残差，是f2对f1的残差；
    - f3的残差，是f3是对f1的残差(r3 = f3 - f1*b1)；
    - f4的残差，是f4对 [f1, f3] 的残差；
    - f5的残差，是f5对 [f1, f3] 的残差。

    """
    assert len(betas) == F.shape[1]
    
    if intercept:
        X = np.hstack([np.ones((F.shape[0], 1)), F])
        bs = [np.asarray([])] + list(betas)
    else:
        X = F
        bs = betas
    N, K = X.shape
    R = np.full_like(X, fill_value=np.nan)
    R[:, 0] = X[:, 0]
    for k in range(1, K):
        b = bs[k]
        A = X[:, :k]
        # Note that b could have NaN entries(meaning collinear columns 
        # during the fit phase.)
        # we need to de-select those columns. Otherwise, we would have 
        # all-NaN A@b thus all-NaN residuals.
        A = A[:, ~np.isnan(b)]
        b = b[~np.isnan(b)]
        y = X[:, k]
        r = y - A @ b
        R[:, k] = r
    return R if not intercept else R[:, 1:]


class CSGramSchmidtOrthogonalizer(WarningMixin):


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

    def __init__(
            self, 
            fit_intercept: bool = False,
            rcond: float | None = None,
            sample_policy: Literal['common', 'own'] = 'own',
            *,
            warning_limit: int | None = None,
    ):
        self._intercept = fit_intercept
        self._rcond = rcond
        self._policy: Literal['common', 'own'] = sample_policy
        self._fit_factor_names: list[str] | None
        self._betas_by_product: dict[str, list[np.ndarray]] | None = None
        self._res_std_ratios_by_product: dict[str, list[float]] | None = None
        self._reason_codes_by_product: dict[str, list[None|int]] | None = None
        self._init_warnings(warning_limit)

    def fit(
            self, 
            *factors: pd.Series, 
            eligible: pd.Series[bool]
    ) -> Self:
        self._reset_warnings()

        names = [f.name if isinstance(f.name, str) else f'factor{i}_unnamed' 
                             for i, f in enumerate(factors)]
        validate_unique_names(names)
        self._fit_factor_names = names

        factor_df = pd.concat(factors, axis=1, join='outer') # MultiIndex[timestamp, product], Column[factor]
        factor_df = factor_df.loc[eligible]
        factor_df = factor_df.astype(float)

        grouped = factor_df.groupby(level='product')
        betas_by_prod: dict[str, list[np.ndarray]] = {}
        codes_by_prod: dict[str, list[None|int]] = {}
        ratios_by_prod: dict[str, list[float]] = {}
        for prod, df in grouped:
            assert isinstance(prod, str)
            F = df.to_numpy()
            betas, reason_codes, ratios = _seq_lstsq_beta_with_nan( # CoA refers to "coefficient of alienation"
                F, self._rcond, self._intercept, self._policy)
            # (1) record betas
            betas_by_prod[prod] = betas
            # (2) record reason_codes and possible warnings.
            codes_by_prod[prod] = reason_codes
            n_failed = sum([c not in {None, 0} for c in reason_codes])
            if n_failed != 0:
                self._warn(f'{prod}: operation failed for {n_failed} factors.')
            # (3) record ratios
            ratios_by_prod[prod] = ratios
        
        # publish results
        self._betas_by_product = betas_by_prod
        self._reason_codes_by_product = codes_by_prod
        self._res_std_ratios_by_product = ratios_by_prod

        self._log_warnings(logger, f'Multifactor TS orthogonalization: fit phase')
        return self

    def _check_is_fitted(self, ):
        if self._betas_by_product is None:
            raise ValueError(f'Not fitted. Fit first please.')
    # TODO: 增加fit-transform 窗口不重叠校验
    def transform(
            self,
            *factors: pd.Series,
            eligible: pd.Series[bool] | None
    ) -> OrthogonalizationResult:
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

        assert self._betas_by_product is not None
        for prod, df in grouped:
            assert isinstance(prod, str)
            F = df.to_numpy()
            betas = self._betas_by_product.get(prod, None) 
            if betas is not None:
                R = _seq_res_with_fitted_betas(F, betas, self._intercept) 
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
                'betas': self._build_df_from_dict('_betas_by_product'),
                'residual_std_ratios_fit': self._build_df_from_dict('_res_std_ratios_by_product'),
                'failed_reasons_fit': (self._build_df_from_dict('_reason_codes_by_product')
                                       .replace(LSTSQ_REASON_CODES).astype('category'))
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
):
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
    from factors.preprocess.orthogonalize import _seq_lstsq_beta_with_nan, _seq_res_with_fitted_betas
    rng = np.random.default_rng(42)
    f1 = rng.standard_normal((10, 1))
    f2 = f1.copy() + rng.standard_normal((10, 1))/1e25
    f3 = rng.standard_normal((10, 1))
    f4 = np.full((10, 1), 1)
    f5 = rng.standard_normal((10, 1))

    F = np.hstack([f1, f2, f3, f4, f5])
    print(f'F is :\n{F}')


    rst  = _seq_lstsq_beta_with_nan(F, )
    print(rst)

    






    

    


        
            



