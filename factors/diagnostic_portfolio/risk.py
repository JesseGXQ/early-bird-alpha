from __future__ import annotations
from dataclasses import asdict, dataclass
import pandas as pd
import numpy as np

from typing import TYPE_CHECKING, Any, Mapping

from factors.models import INDEX_NAMES

if TYPE_CHECKING:
    from factors.diagnostic_portfolio.models import RiskConfig

#: 事前组合方差 w'Σw 的绝对数值容差（单位与单期收益方差一致）。
#: 结果落在容差以内时视为协方差估计失败，当期按空仓处理（开发方案 §13.2）。
EX_ANTE_VARIANCE_TOLERANCE: float = 1e-12

@dataclass(frozen=True)
class CovarianceEstimationResult:
    cov_mat: pd.DataFrame # Index[product], Column[product]
    metadata: pd.Series # Index: sample_start, sample_end, valid_prod_count, pairwise_missing, min_eigen, clipped_eigen_count

@dataclass(frozen=True)
class ExAntePortfolioVolatility:
    value: float
    cov_mat_estimation_meta: pd.Series
    warnings: tuple[str,...]

class DiagnosticPortfolioRiskManager:

    def __init__(self, risk_config: RiskConfig):
        self._config = risk_config
        self._return_history: list[tuple[pd.Timestamp, pd.Series]] = []
        self._products: pd.Index | None = None

    def append(self, ret: pd.Series, timestamp: pd.Timestamp) -> None:
        """将本期各品种的收益率合并到内部历史记录中。

        Parameters
        ----------
        ret : pd.Series
            当期各品种收益率，Index[product]（允许含 NaN）。各期之间必须
            标签与顺序完全一致，否则窗口矩阵无法按列对齐。
        timestamp : pd.Timestamp
            当期时间戳。收益率本身不携带时间戳，故单独传入。

        Raises
        ------
        ValueError
            当某期的品种索引与首期不一致时抛出。

        """
        if self._products is None:
            self._products = ret.index
        elif ret.index is not self._products \
                and not ret.index.equals(self._products):
            raise ValueError('各期收益率的品种索引必须完全一致。')
        self._return_history.append((timestamp, ret))

    def ex_ante_volatility(self, ) -> pd.Series:
        """对所有品种计算事前波动率。

        Returns
        -------
        pd.Series
            Index[product]、dtype=float64、name 为 None 的单品种事前波动率。
            非缺失收益率计数不足 ``volatility_min_periods``、或窗口内非缺失值
            全相等（波动率无定义）的品种为 NaN。

        Notes
        -----
        逐品种语义见 :meth:`_ex_ante_vol_one_product`；这里是它的按列向量化
        实现。避开 ``groupby.apply`` 的开销。

        """
        window = self._return_window(self._config.volatility_lookback)
        products = self._products
        if products is None:
            return pd.Series(dtype=float)

        if window.shape[0] < 2:
            # ddof=1 需要至少 2 个观测才成立，不足时标准差无定义。
            return pd.Series(np.nan, index=products, dtype=float)

        counts = window.count(axis=0) # 各品种非缺失收益率计数
        vol = window.std(ddof=1, axis=0) # 数值计算交给 pandas

        # 判定「窗口内非缺失值全相等」，即 pandas 的
        #     window.nunique(axis=0, dropna=True) <= 1
        # nunique 对 小数组要走逐列 hashtable 去重，比 std 慢约 38 倍，
        # 故改为向量化算术：取该列自身的最大非缺失值
        # 作基准，数与它不同的非缺失值个数。
        #
        # 不能改用「vol == 0」来判定：常数序列的 std(ddof=1) 在浮点下并不精确为
        # 0.0，nanvar 的两遍算法会留下 1e-24 ~ 1e-11 的残差（残差幅度随数值量级
        # 放大），那样会漏判常数列。max + 比较完全绕开浮点减法与求和。
        arr = window.to_numpy(dtype=float)
        valid = ~np.isnan(arr)
        top = np.where(valid, arr, -np.inf).max(axis=0)
        n_unique = np.where(counts.to_numpy() > 0,
                            1 + ((arr != top) & valid).sum(axis=0),
                            0) # 无非缺失值的列，nunique 为 0，与 pandas 一致

        invalid = ((counts < self._config.volatility_min_periods).to_numpy()
                   | (n_unique <= 1))
        values = vol.to_numpy()
        values[invalid] = np.nan
        # 显式命名：原来的 groupby(level='product') 总会把结果索引命名为
        # 'product'，这里不能因为改用窗口矩阵而丢掉该契约。
        return pd.Series(values, index=products.rename(INDEX_NAMES[1]))

    def _return_window(self, lag: int) -> pd.DataFrame:
        """把回看窗口拼成 Index[timestamp] x columns[product] 的稠密矩阵。

        收益率历史存的是逐期的稠密 ``Index[product]`` Series（品种轴已由
        实验主循环对齐），因此这里只需按时间顺序堆叠。

        本方法是风险计算的唯一窗口构造入口：单品种事前波动率、协方差矩阵与
        非法品种判定都直接消费本矩阵，不再经由 ``MultiIndex[timestamp,
        product]`` Series + ``reset_index().pivot(...)`` 的往返。

        Returns
        -------
        pd.DataFrame
            行索引为回看窗口的时间戳（按 append 顺序，名为 ``'timestamp'``），
            列索引为品种（名为 ``'product'``）。原始不存在的 (时点, 品种)
            组合为 NaN。

        """
        past = self._return_history[-lag:]
        values = np.stack([ret.to_numpy(dtype=float) for _, ret in past])
        # 两个轴都按契约显式命名：时间轴与品种轴是本模块的固定语义，不依赖
        # 调用方传入的 Index 是否恰好带名字（原先由 pivot 参数顺带产生）。
        products = self._products
        if products is not None:
            products = products.rename(INDEX_NAMES[1])
        return pd.DataFrame(
            values,
            index=pd.DatetimeIndex(
                [timestamp for timestamp, _ in past], name=INDEX_NAMES[0]),
            columns=products,
            copy=False,
        )

    def _ex_ante_vol_one_product(self, returns: pd.Series) -> float:
        """对单个品种，计算事前波动率。

        这是 :meth:`ex_ante_volatility` 的单品种参考实现；后者是它的按列
        向量化等价物。保留本方法用于测试与口径核对。

        """
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
        """计算组合的年化事前波动率。
        
        返回值满足 ExAntePortfolioVolatility.value >= 0 。"""
        ret_wide = self._return_window(
            lag=self._config.covariance_lookback # pyright: ignore[reportArgumentType]
        ) # DataFrame, Index[timestamp], columns are products, values are returns
        products = weights.index.tolist()
        ret_wide = ret_wide[products]
        # 计算样本协方差
        result = self._covariance_matrix_pairwise(ret_wide)
        cov_mat = result.cov_mat
        assert cov_mat.index.equals(weights.index)

        ex_ante_port_var_raw = float(weights @ cov_mat @ weights)
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
            vol = float(
                np.sqrt(annualization_factor * ex_ante_port_var_raw)
            )
        return ExAntePortfolioVolatility(
            value=vol,
            cov_mat_estimation_meta=result.metadata,
            warnings=warnings
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
        ret_wide = self._return_window(
            lag=self._config.covariance_lookback # pyright: ignore[reportArgumentType]
        ) # DataFrame, Index[timestamp], columns are products, values are returns
        valid_obs_series = ret_wide.notna().sum(axis=0) # Index[product]
        invalid_products: list[str] = valid_obs_series[
            valid_obs_series < self._config.covariance_min_periods
        ].index.tolist()
        return invalid_products
    