from __future__ import annotations
from dataclasses import asdict, dataclass
import pandas as pd
import numpy as np

from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from factors.diagnostic_portfolio.models import RiskConfig

@dataclass(frozen=True)
class CovarianceEstimationResult:
    cov_mat: pd.DataFrame # Index[product], Column[product]
    metadata: pd.Series # Index: sample_start, sample_end, valid_prod_count, pairwise_missing, min_eigen, clipped_eigen_count

@dataclass(frozen=True)
class ExAntePortfolioVolatility:
    value: float
    cov_mat_estimation_meta: pd.Series

class DiagnosticPortfolioRiskManager:

    def __init__(self, risk_config: RiskConfig):
        self._config = risk_config
        self._return_history: list[pd.Series] = []

    def append(self, ret: pd.Series) -> None:
        """将本期各品种的收益率合并到内部历史记录中。"""
        self._return_history.append(ret)

    def ex_ante_volatility(self, ) -> pd.Series:
        """对所有品种计算事前波动率。"""
        ret_series = self._return_to_series(
            lag = self._config.volatility_lookback
        ) # MultiIndex[timestamp, product]
        return ret_series.groupby(level='product')\
            .apply(self._ex_ante_vol_one_product)

    def _ex_ante_vol_one_product(self, returns: pd.Series) -> float:
        """对单个品种，计算事前波动率。"""
        if len(returns.dropna()) < self._config.volatility_min_periods:
            return np.nan
        if returns.dropna().nunique() == 1:
            return np.nan
        return returns.std(ddof=1)

    def ex_ante_portfolio_volatility_annualized(
            self, 
            weights: pd.Series,
            annualization_factor: int
    ) -> ExAntePortfolioVolatility:
        """计算组合的年化事前波动率。"""
        ret_series = self._return_to_series(
            lag=self._config.covariance_lookback # pyright: ignore[reportArgumentType]
        )
        products = weights.index.tolist()
        ret_wide = ret_series.rename('return').reset_index().pivot(
            index='timestamp', columns='product', values='return'
        ) # DataFrame, Index[timestamp], columns are products, values are returns
        ret_wide = ret_wide[products]
        # 计算样本协方差
        result = self._covariance_matrix_pairwise(ret_wide)
        cov_mat = result.cov_mat
        assert cov_mat.index.equals(weights.index)
        vol = np.sqrt(annualization_factor * (weights @ cov_mat @ weights))
        return ExAntePortfolioVolatility(
            value=float(vol),
            cov_mat_estimation_meta=result.metadata
        )



    def _covariance_matrix_pairwise(
            self, ret_wide: pd.DataFrame,
    ) -> CovarianceEstimationResult:
        """使用 pairwise complete observations 计算样本协方差，并进行数值修复。

        Parameters
        ----------
        ret_wide : pd.DataFrame
            收益宽表，Index[timestamp]，columns 为品种代码，取值 True_Return；
            允许含 NaN（品种池动态进出与局部缺失）。

        Returns
        -------
        CovarianceEstimationResult
            ``cov_mat`` 为 Index/columns 均为品种代码的协方差矩阵，
            列顺序与 ``ret_wide.columns`` 一致；``metadata`` 记录窗口起止、
            有效品种数、缺失配对数和特征值修复明细。

        Raises
        ------
        ValueError
            当某个品种在窗口内的有效观测数少于 ``covariance_min_periods`` 时
            抛出。该情形本应在调用链上游被剔除，见 Notes。

        Notes
        -----
        口径对齐开发方案 §13.2：逐个元素使用成对完整观测估计，再按固定顺序
        修复——① 对角线用各品种自身样本方差；② 非对角缺失协方差填零；
        ③ 强制对称；④ 特征值分解并将负特征值截断为零；⑤ 重建半正定矩阵。

        前置契约：目标波动率链路（``weights.py``）必须先用
        :meth:`get_invalid_products` 剔除回看窗口内观测不足的品种，再对本函数
        传入剩余品种。因此 ``ret_wide`` 的每一列都应满足有效观测数
        ≥ ``covariance_min_periods``。若仍出现观测不足的品种，说明调用链被
        绕过，属于契约破坏；此时直接报错，而不是静默返回 NaN 。

        对角线刻意用各品种自身样本方差（而不是成对口径），保证方差只由该
        品种自身的观测决定。成对重叠观测不足的产品对（每个品种自身观测都
        达标）仍按 §13.2 步骤②填零并计数。
        """
        if self._config.covariance_min_periods is None:
            raise ValueError(
                '未配置 RiskConfig.covariance_min_periods，无法估计协方差。'
            )
        min_periods = self._config.covariance_min_periods
        products = ret_wide.columns
        n_products = len(products)

        # 前置契约校验：调用方已通过 get_invalid_products 剔除观测不足的品种。
        valid_obs = ret_wide.notna().sum(axis=0)
        insufficient = valid_obs[valid_obs < min_periods]
        if not insufficient.empty:
            raise ValueError(
                '协方差窗口内存在有效观测数不足 covariance_min_periods 的品种，'
                '应由 get_invalid_products 在调用本函数前先行剔除：\n'
                f'{insufficient.to_dict()}（covariance_min_periods='
                f'{min_periods}）'
            )

        # ① 对角线使用各品种自身样本方差（ddof=1）。
        variances = ret_wide.var(ddof=1)
        cov = ret_wide.cov(min_periods=min_periods)
        cov = cov.reindex(index=products, columns=products)
        values = cov.to_numpy(dtype=float, copy=True)
        np.fill_diagonal(values, variances.to_numpy(dtype=float))

        # ② 非对角缺失协方差填零，并记录缺失配对数。
        off_diagonal = ~np.eye(n_products, dtype=bool)
        fill_mask = np.isnan(values) & off_diagonal
        values[fill_mask] = 0.0
        pairwise_missing = int(fill_mask.sum() // 2)

        # ③ 强制对称。
        values = (values + values.T) / 2.0

        # ④⑤ 特征值分解，负特征值截断为零后重建半正定矩阵。
        # 前置契约保证对角线有限、非对角已填零，故矩阵必为有限。
        min_eigen = np.nan
        clipped_eigen_count = 0
        if n_products > 0:
            assert np.isfinite(values).all(), (
                '协方差矩阵在数值修复前仍含非有限值，前置校验被绕过。'
            )
            eigvals, eigvecs = np.linalg.eigh(values)
            min_eigen = float(eigvals[0])
            negative = eigvals < 0.0
            clipped_eigen_count = int(negative.sum())
            if clipped_eigen_count > 0:
                eigvals = np.clip(eigvals, 0.0, None)
                values = (eigvecs * eigvals) @ eigvecs.T
                values = (values + values.T) / 2.0

        cov_mat = pd.DataFrame(
            values, index=products, columns=products)
        cov_mat.index.name = products.name
        cov_mat.columns.name = products.name

        sample_start = ret_wide.index.min() if n_products > 0 else pd.NaT
        sample_end = ret_wide.index.max() if n_products > 0 else pd.NaT
        metadata = pd.Series(
            {
                'sample_start': sample_start,
                'sample_end': sample_end,
                'valid_prod_count': int((valid_obs >= min_periods).sum()),
                'pairwise_missing': pairwise_missing,
                'min_eigen': min_eigen,
                'clipped_eigen_count': clipped_eigen_count,
            },
            dtype=object,
        )
        return CovarianceEstimationResult(cov_mat=cov_mat, metadata=metadata)



    def get_invalid_products(self, ):
        """
        获取非法品种——回看窗口中，
        非缺失收益率计数小于风险配置的 `covariance_min_periods`
        """
        ret_series = self._return_to_series(
            lag=self._config.covariance_lookback # pyright: ignore[reportArgumentType]
        )
        ret_wide = ret_series.rename('return').reset_index().pivot(
            index='timestamp', columns='product', values='return'
        ) # DataFrame, Index[timestamp], columns are products, values are returns
        valid_obs_series = ret_wide.notna().sum(axis=0) # Index[product]
        invalid_products: list[str] = valid_obs_series[
            valid_obs_series < self._config.covariance_min_periods
        ].index.tolist()
        return invalid_products











    def _return_to_series(self, lag: int) -> pd.Series:
        """返回收益率Series, Index[timestamp, product]。"""
        past_series = self._return_history[-lag:]
        return pd.concat(past_series, axis=0).rename('returns')