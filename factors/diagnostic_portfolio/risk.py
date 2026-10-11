"""
``factors/diagnostic_portfolio/risk.py`` —— 事前风险估计。

设计约定（性能）
----------------
本模块在权重机主循环中被高频调用（每个调仓日一次协方差估计、一次单品种
波动率估计），窗口很小（默认 15~60 期 x 约 100 个品种）。因此**内部一律
使用 ``np.ndarray``**，把 ``(T, P)`` 的收益率窗口直接当作二维浮点矩阵处理，
不做任何 pandas 的索引对齐、Series/DataFrame 构造或 ``groupby``。

收益率历史保存在两个 list 里，按时间顺序追加；窗口取数 :meth:`_window` 只是对
历史尾部做一次 ``np.stack``。

仍保留 pandas 的两处，都是**边界**或**数值安全**考虑：

1. ``df.cov()``：``np.cov`` 不是 pairwise-complete 口径，而本模块需要成对
   完整观测。若改用充分统计量（``XᵀX`` / ``MᵀM``）向量化，会引入
   “乘积和减 n·mean²”的相消形式，在收益量级（~1e-2）下损失数位有效数字，
   而其结果还要喂给特征值分解做 PSD 修复。故保留 pandas 的实现。
2. ``cov_mat_estimation_meta``：审计用元数据，形状固定很小。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from factors.models import INDEX_NAMES

if TYPE_CHECKING:
    from factors.diagnostic_portfolio.models import RiskConfig

#: 事前组合方差 w'Σw 的绝对数值容差（单位与单期收益方差一致）。
#: 结果落在容差以内时视为协方差估计失败，当期按空仓处理（开发方案 §13.2）。
EX_ANTE_VARIANCE_TOLERANCE: float = 1e-12


@dataclass(frozen=True)
class CovarianceEstimationResult:
    cov_mat: np.ndarray          # (P, P) float64，已完成 §13.2 的修复
    metadata: pd.Series          # Index: sample_start, sample_end, valid_prod_count, pairwise_missing, min_eigen, clipped_eigen_count


@dataclass(frozen=True)
class ExAntePortfolioVolatility:
    value: float
    cov_mat_estimation_meta: pd.Series
    warnings: tuple[str, ...]


class DiagnosticPortfolioRiskManager:
    """事前波动率与事前组合波动率估计。

    内部按时间顺序保存每期的收益率与时间戳；窗口取数 :meth:`_window` 只是对
    历史尾部做一次 ``np.stack``，不重建任何 pandas 对象。
    """

    def __init__(self, risk_config: RiskConfig, n_products: int):
        """
        Parameters
        ----------
        risk_config : RiskConfig
            风险配置，决定各回看窗口长度与最少观测数。
        n_products : int
            品种数。``append`` 收到的每期收益率必须恰好是这一长度。

        """
        self._config = risk_config
        self._n_products = int(n_products)
        self._ret_rows: list[np.ndarray] = []
        self._timestamps: list[pd.Timestamp] = []

    def append(self, ret: np.ndarray, timestamp: pd.Timestamp) -> None:
        """将当期各品种的收益率并入内部历史。

        Parameters
        ----------
        ret : np.ndarray
            当期各品种收益率，形状 ``(n_products,)``，允许含 NaN。
        timestamp : pd.Timestamp
            当期时间戳。收益率本身不携带时间戳，故单独传入。

        Notes
        -----
        不校验 ``ret`` 的长度：调用方（权重机）传的是 ``(T, P)`` 面板矩阵的
        行，其长度天然等于构造时给定的 ``n_products``。

        """
        self._ret_rows.append(ret)
        self._timestamps.append(timestamp)

    # ------------------------------------------------------------------
    # 窗口
    # ------------------------------------------------------------------
    def _window(self, lag: int) -> np.ndarray:
        """取最近 ``lag`` 期的收益率窗口。

        Returns
        -------
        np.ndarray
            ``(min(lag, 已存期数), n_products)`` 的二维浮点矩阵；行按时间
            升序，缺失值为 NaN。
        """
        rows = self._ret_rows[-lag:]
        if not rows:
            return np.empty((0, self._n_products), dtype=float)
        if len(rows) == 1:
            return rows[0].reshape(1, -1)
        return np.stack(rows)

    # ------------------------------------------------------------------
    # 单品种事前波动率
    # ------------------------------------------------------------------
    def ex_ante_volatility(self) -> np.ndarray:
        """对所有品种计算事前波动率（向量化的按列实现）。

        Returns
        -------
        np.ndarray
            形状 ``(n_products,)``。非缺失收益率计数不足
            ``volatility_min_periods``、或窗口内非缺失值全相等（标准差无
            定义）的品种为 NaN。
        """
        window = self._window(self._config.volatility_lookback)
        out = np.full(self._n_products, np.nan, dtype=float)
        if window.shape[0] < 2:
            # ddof=1 需要至少 2 个观测才成立，不足时标准差无定义。
            return out

        arr = window
        valid = ~np.isnan(arr)
        counts = valid.sum(axis=0)
        safe = np.where(valid, arr, 0.0)

        # 两遍算法：先均值、再离差平方和，最后开方。与 pandas 的
        # ``DataFrame.std(ddof=1)`` 同口径（pandas 侧若装有 bottleneck 会走
        # 单遍公式，两者相差约 1e-16 相对量级）；这里选择两遍是因为它在
        # 「收益均值接近 0」时数值更稳。
        means = np.divide(safe.sum(axis=0), counts,
                          out=np.zeros(self._n_products),
                          where=counts > 0)
        sqr = np.where(valid, (arr - means) ** 2, 0.0).sum(axis=0)
        with np.errstate(invalid='ignore', divide='ignore'):
            out = np.sqrt(sqr / (counts - 1))
        out[counts < self._config.volatility_min_periods] = np.nan

        # 判定「窗口内非缺失值全相等」，即 pandas 的
        #     window.nunique(axis=0, dropna=True) <= 1
        # nunique 对 P x T 这种小数组要走逐列 hashtable 去重，比 std 慢约 38 倍，
        # 故改为向量化算术：取该列自身最大非缺失值作基准，数与它不同的非缺失值
        # 个数。
        #
        # 不能改用「out == 0」来判定：常数序列的两遍算法在浮点下并不精确为
        # 0.0，会留下 1e-24 ~ 1e-11 的残差，那样会漏判常数列。max + 比较完全
        # 绕开浮点减法与求和。
        top = np.where(valid, arr, -np.inf).max(axis=0)
        n_unique = np.where(counts > 0,
                            1 + ((arr != top) & valid).sum(axis=0),
                            0)
        out[n_unique <= 1] = np.nan
        return out

    def invalid_mask(self) -> np.ndarray:
        """协方差窗口内观测不足 ``covariance_min_periods`` 的品种掩码。

        Returns
        -------
        np.ndarray
            形状 ``(n_products,)`` 的布尔数组，True 表示该品种应被剔除。

        Notes
        -----
        对应旧实现的 ``get_invalid_products()``；改为掩码是为了让调用方直接
        在 numpy 上做子集选择，省掉 ``pd.Index(...).intersection(...)``。

        """
        window = self._window(self._config.covariance_lookback)
        counts = self._counts(window)
        return counts < self._config.covariance_min_periods

    # ------------------------------------------------------------------
    # 组合事前波动率
    # ------------------------------------------------------------------
    def ex_ante_portfolio_volatility_annualized(
            self,
            weights: np.ndarray,
            annualization_factor: int,
            valid_mask: np.ndarray | None = None,
    ) -> ExAntePortfolioVolatility:
        """计算组合的年化事前波动率。

        Parameters
        ----------
        weights : np.ndarray
            目标权重，形状 ``(n_valid,)``，对应 ``window[:, valid_mask]``
            的品种顺序。
        annualization_factor : int
            年化因子。
        valid_mask : np.ndarray, optional
            协方差窗口内观测达标的全品种掩码。缺省表示全部品种均达标。

        Returns
        -------
        ExAntePortfolioVolatility
            ``value >= 0``；估计失败（矩阵退化/全零）时 ``value == 0`` 且带
            警告。

        """
        if valid_mask is None:
            valid_mask = np.ones(self._n_products, dtype=bool)
        lag = self._config.covariance_lookback
        window = self._window(lag)[:, valid_mask]
        result = self._covariance_matrix_pairwise(window)
        cov_values = result.cov_mat

        ex_ante_port_var_raw = float(weights @ cov_values @ weights)
        if ex_ante_port_var_raw <= EX_ANTE_VARIANCE_TOLERANCE:
            # 估计失败（矩阵退化/全零、浮点重建残差），当期空仓并记录。
            warnings = (
                '估计出的事前组合方差 '
                f'{ex_ante_port_var_raw} 在绝对数值容差 '
                f'{EX_ANTE_VARIANCE_TOLERANCE} 以内，按估计失败处理，'
                '当期空仓。',
            )
            vol = 0.0
        else:
            warnings = ()
            vol = float(np.sqrt(annualization_factor * ex_ante_port_var_raw))
        return ExAntePortfolioVolatility(
            value=vol,
            cov_mat_estimation_meta=result.metadata,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # 协方差
    # ------------------------------------------------------------------
    @staticmethod
    def _counts(window: np.ndarray) -> np.ndarray:
        """各品种的非缺失观测数。"""
        return (~np.isnan(window)).sum(axis=0)

    def _covariance_matrix_pairwise(
            self,
            window: np.ndarray,
    ) -> CovarianceEstimationResult:
        """使用 pairwise complete observations 计算样本协方差，并进行数值修复。

        Parameters
        ----------
        window : np.ndarray
            收益窗口 ``(T, P)``，允许含 NaN（品种池动态进出与局部缺失）。
            列顺序即调用方选定的品种顺序。

        Returns
        -------
        CovarianceEstimationResult
            ``cov_mat`` 为 ``(P, P)`` 的协方差矩阵（已完成 §13.2 的
            五步修复）；``metadata`` 记录窗口起止、有效品种数、缺失配对数和
            特征值修复明细。

        Raises
        ------
        ValueError
            当某个品种在窗口内的有效观测数少于 ``covariance_min_periods``
            时抛出；或未配置 ``covariance_min_periods``。

        Notes
        -----
        口径对齐开发方案 §13.2：逐个元素使用成对完整观测估计，再按固定顺序
        修复——① 对角线用各品种自身样本方差；② 非对角缺失协方差填零；
        ③ 强制对称；④ 特征值分解并将负特征值截断为零；⑤ 重建半正定矩阵。

        对角线刻意用各品种自身样本方差（而不是成对口径），保证方差只由该
        品种自身的观测决定。

        ``df.cov()`` 保留 pandas 实现：``np.cov`` 不是成对完整观测口径，而
        充分统计量（``XᵀX``）在收益量级下会因相消损失数位有效数字。
        """
        if self._config.covariance_min_periods is None:
            raise ValueError(
                '未配置 RiskConfig.covariance_min_periods，无法估计协方差。')
        min_periods = self._config.covariance_min_periods
        n_products = window.shape[1]

        # 前置契约校验：调用方已通过 invalid_mask 剔除观测不足的品种。
        valid_obs = self._counts(window)
        insufficient = np.flatnonzero(valid_obs < min_periods)
        if insufficient.size:
            raise ValueError(
                '协方差窗口内存在有效观测数不足 covariance_min_periods 的品种，'
                '应由 invalid_mask 在本函数前先行剔除：'
                f'positions={insufficient.tolist()}（covariance_min_periods='
                f'{min_periods}）')

        # ① 对角线使用各品种自身样本方差（ddof=1）；非对角用成对完整观测。
        variances = np.full(n_products, np.nan, dtype=float)
        enough = valid_obs >= 2
        if enough.any():
            arr = window[:, enough]
            valid = ~np.isnan(arr)
            cnt = valid.sum(axis=0)
            safe = np.where(valid, arr, 0.0)
            mean = safe.sum(axis=0) / cnt
            sqr = np.where(valid, (arr - mean) ** 2, 0.0).sum(axis=0)
            variances[enough] = sqr / (cnt - 1)
        cov_values = self._pairwise_cov(window, min_periods)

        values = cov_values
        np.fill_diagonal(values, variances)

        # ② 非对角缺失协方差填零，并记录缺失配对数。
        off_diagonal = ~np.eye(n_products, dtype=bool)
        fill_mask = np.isnan(values) & off_diagonal
        values[fill_mask] = 0.0
        pairwise_missing = int(fill_mask.sum() // 2)

        # ③ 强制对称。
        values = (values + values.T) / 2.0

        # ④⑤ 特征值分解，负特征值截断为零后重建半正定矩阵。
        min_eigen = np.nan
        clipped_eigen_count = 0
        if n_products > 0:
            assert np.isfinite(values).all(), (
                '协方差矩阵在数值修复前仍含非有限值，前置校验被绕过。')
            eigvals, eigvecs = np.linalg.eigh(values)
            min_eigen = float(eigvals[0])
            negative = eigvals < 0.0
            clipped_eigen_count = int(negative.sum())
            if clipped_eigen_count > 0:
                eigvals = np.clip(eigvals, 0.0, None)
                values = (eigvecs * eigvals) @ eigvecs.T
                values = (values + values.T) / 2.0

        stamps = self._timestamps[-self._config.covariance_lookback:]
        sample_start = stamps[0] if stamps else pd.NaT
        sample_end = stamps[-1] if stamps else pd.NaT
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
        return CovarianceEstimationResult(cov_mat=values, metadata=metadata)

    @staticmethod
    def _pairwise_cov(window: np.ndarray, min_periods: int) -> np.ndarray:
        """成对完整观测的样本协方差矩阵。

        这是 ``DataFrame.cov(min_periods=...)`` 的薄包装：pandas 的实现走
        Cython 的成对口径，与 ``np.cov``（要求无缺失）语义不同，故不自行
        向量化，以免在收益量级下因相消损失精度。
        """
        frame = pd.DataFrame(window, copy=False)
        return frame.cov(min_periods=min_periods).to_numpy(dtype=float)


__all__ = [
    'EX_ANTE_VARIANCE_TOLERANCE',
    'CovarianceEstimationResult',
    'DiagnosticPortfolioRiskManager',
    'ExAntePortfolioVolatility',
]