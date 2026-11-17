"""预处理——因子间正交化组件。"""
from __future__ import annotations
from collections.abc import Sequence
import pandas as pd
import numpy as np
from typing import Literal, Any, Self
from dataclasses import dataclass, field
import logging
from factors.common import walk_forward_split
from factors.library.others.future_native._utils import WarningMixin
import copy
from functools import reduce

logger = logging.getLogger(__name__)

NAN_RETURN_ERROR_CODE = {
    1: 'N<K',
    2: 'exists all NaN factor',
    3: 'N<K after dropping NaN under policy drop_if_missing_any',
    4: 'exists zero std fator'
}

@dataclass(frozen=True)
class OrthogonalizationResult:
    values: tuple[pd.Series,...]
    warnings: tuple[str,...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

def qr_core(F: np.ndarray, tol: float = 1e-8) -> tuple[np.ndarray, np.ndarray]:
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
                         f'Residual std is {s} < {tol}.')
    #G[:, s < tol] = 0   
    return G, s  

def _has_zero_std(F: np.ndarray) -> bool:
    return (F.std(axis=0) == 0.0).any()

def seq_orth_qr_with_nan(
        F: np.ndarray, 
        tol: float = 1e-8, 
        policy: Literal['drop_if_missing_any','fill_with_mean'] = 'fill_with_mean'
) -> tuple[np.ndarray, np.ndarray, Literal[0, 1, 2, 3, 4]]:
    nan_re = np.full_like(F, np.nan), np.full(F.shape[1], np.nan)
    N, K = F.shape
    if N < K:
        return nan_re[0], nan_re[1],  1
    F = np.where(np.isinf(F), np.nan, F) # 后面的代码没有处理 inf，所以这里防一手，先把 inf 置 NaN
    if policy == 'fill_with_mean':
        miss = np.isnan(F) 
        mean = np.nanmean(F, axis=0)
        if np.isnan(mean).any(): # 若某一列为全NaN，导致均值为NaN，此时无法填充
            return nan_re[0], nan_re[1], 2
        F = np.where(miss, mean, F)
        if _has_zero_std(F):
            return nan_re[0], nan_re[1], 4
        G, resid_std = qr_core(F, tol)
        G[miss] = np.nan
    else:
        keep = (~np.isnan(F)).all(axis=1)
        F_valid = F[keep]
        N_valid = F_valid.shape[0]
        if N_valid < K:
            return nan_re[0], nan_re[1], 3
        if _has_zero_std(F_valid):
            return nan_re[0], nan_re[1], 4
        G_keep, resid_std = qr_core(F_valid, tol)
        G = np.empty_like(F, dtype=float)
        G[keep] = G_keep
        G[~keep] = np.nan
    return G, resid_std, 0

def seq_lstsq_beta_with_nan(
        F: np.ndarray,
        rcond = None, 
        intercept: bool = False,
        policy: Literal['common', 'own'] = 'own'
) -> list[np.ndarray]:
    K_input = F.shape[1]
    if intercept:
        ones = np.ones((F.shape[0], 1))
        F = np.hstack([ones, F])
    
    N, K = F.shape
    nan_betas = [np.asarray([np.nan]*i) for i in range(K)]

    if N < K: # 如果样本数不足，直接返回全 nan beta
        nan_betas_re = nan_betas if not intercept else nan_betas[1:]
        assert len(nan_betas_re) == K_input # post-condition 
        return nan_betas_re

    valid = np.isfinite(F).all(axis=1)
    betas: list[np.ndarray] = []
    if not intercept:
        betas.append(np.asarray([]))
    for k in range(1, K):
        valid = valid if policy=='common' else np.isfinite(F[:, :k+1]).all(axis=1)
        if valid.sum() < k: # 由于越往后面的因子走，k会越来越大，而有效样本只可能减小或不变，所以后续的回归可以直接不用做了，直接用nan beta
            betas.extend(nan_betas[k:])
            break
        X = F[valid, :k]
        y = F[valid, k]
        beta, _, rank, _= np.linalg.lstsq(X, y, rcond=rcond)
        if rank < k: # 证明存在 rank deificiency(共线性)
            betas.append(nan_betas[k])
        else:
            betas.append(beta)
    # post-condition
    assert len(betas) == K_input # betas的长度一定等于输入的矩阵的列数
    return betas

def seq_res_with_fitted_betas(
        F: np.ndarray,
        betas: Sequence[np.ndarray],
        intercept: bool
) -> np.ndarray:
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
            qr_tol: float = 1e-8
    ):
        if method == 'lstsq':
            raise NotImplementedError('lstsq not implemented in V1.')
        self._method = method
        self._qr_missing_policy: Literal[
            'drop_if_missing_any', 'fill_with_mean']  = qr_missing_policy
        self._qr_tol= qr_tol

    def transform(
            self, 
            *factors: pd.Series, 
            eligible: pd.Series[bool]
    ) -> OrthogonalizationResult:
        self._reset_warnings()

        factor_df = pd.concat(factors, axis=1, join='outer') # MultiIndex[timestamp, product], Column[factor]
        factor_df = factor_df.loc[eligible]
        factor_df = factor_df.astype(float)

        grouped = factor_df.groupby(level='timestamp')
        keys: list[pd.Timestamp] = []
        resid_std_l: list[np.ndarray] = []
        G_l: list[np.ndarray] = []
        indices: list[pd.Index] = []
        for ts, df in grouped:
            keys.append(ts) # pyright: ignore[reportArgumentType]
            indices.append(df.index)
            F = df.to_numpy()
            G, resid_std, code = seq_orth_qr_with_nan(F, self._qr_tol, self._qr_missing_policy)
            assert G.shape == df.shape # 正交化后的因子 matrix 的 N（品种数量）应该和输入一致
            assert resid_std.shape[0] == df.shape[1] # 残差标准差 array 的长度应该和因子数量一致
            if code!= 0:
                self._warn(f'{ts}: NAN return, reason is {NAN_RETURN_ERROR_CODE[code]}')
            G_l.append(G)
            resid_std_l.append(resid_std)

        idx = indices[0].append(indices[1:])
        orth_factor_df = pd.DataFrame(
            np.vstack(G_l), columns=factor_df.columns, index=idx)
        orth_series_tup = tuple(orth_factor_df[col] for col in orth_factor_df)
        resid_std_df = pd.DataFrame(
            np.vstack(resid_std_l), index=keys, columns=factor_df.columns)

        self._log_warnings(logger=logger, factor_name=f'mulfi-factor: {factor_df.columns}')

        return OrthogonalizationResult(
            values=orth_series_tup,
            warnings=tuple(self.warnings),
            metadata={'residual_variation_ratio': resid_std_df}
        )



class TSGramSchmidtOrthogonalizer(WarningMixin):

    def __init__(
            self, 
            fit_intercept: bool = False,
            rcond: float | None = None,
            sample_policy: Literal['common', 'own'] = 'own'
    ):
        self._intercept = fit_intercept
        self._rcond = rcond
        self._policy: Literal['common', 'own'] = sample_policy
        self._betas_by_product: dict[str, list[np.ndarray]] | None = None
        self._fit_factor_names: list[str] | None

    def fit(
            self, 
            *factors: pd.Series, 
            eligible: pd.Series[bool]
    ) -> Self:
        self._reset_warnings()
        self._fit_factor_names = [f.name if isinstance(f.name, str) else f'factor{i}_unnamed' 
                             for i, f in enumerate(factors)]
        factor_df = pd.concat(factors, axis=1, join='outer') # MultiIndex[timestamp, product], Column[factor]
        factor_df = factor_df.loc[eligible]
        factor_df = factor_df.astype(float)

        grouped = factor_df.groupby(level='product')
        betas_by_prod: dict[str, list[np.ndarray]] = {}
        for prod, df in grouped:
            assert isinstance(prod, str)
            F = df.to_numpy()
            betas = seq_lstsq_beta_with_nan(F, self._rcond, self._intercept, self._policy)
            betas_by_prod[prod] = betas
        self._betas_by_product = betas_by_prod
        self._log_warnings(logger, f'multi-factor TS orthogonalization: fit phase')
        return self

    def _check_is_fitted(self, ):
        if self._betas_by_product is None:
            raise AttributeError(f'Not fitted. Fit first please.')

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
                R = seq_res_with_fitted_betas(F, betas, self._intercept) 
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
        
        self._log_warnings(logger=logger, factor_name='multi-factor orthogonalization: transform phase')
        return OrthogonalizationResult(
            values=tuple(transed_factor_df[col] for col in transed_factor_df),
            warnings=tuple(self.warnings),
            metadata={'betas': copy.deepcopy(self._betas_by_product)} # pyright: ignore[reportOptionalMemberAccess]
        )

def walk_foward_orthogonalizer(
        *factors: pd.Series,
        eligible: pd.Series,
        ts_orthogonalizer: TSGramSchmidtOrthogonalizer, # 以后可扩展
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




        
    


        
            



