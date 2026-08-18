"""预处理组件 —— 特征缩放类。"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Literal, Mapping

import pandas as pd
from factors.preprocess.base import (
        FactorPreprocessor, PreprocessingResult, 
        PreprocessingContext, FittedFactorPreprocessor)
from scipy.stats import norm

class FittedTimeSeriesZscore(FittedFactorPreprocessor):
    """
    一次性时序 Zscore (fit-once)，支持多品种。
    
    在固定 fit 窗口计算统计量，然后用同一组统计量对窗口外因子值做 z-score。


    """

    def __init__(
            self, 
            values_for_fit: pd.Series,
            method: Literal['conventional', 'robust'] = 'conventional'):
        
        super().__init__(values_for_fit=values_for_fit)

        assert method in ('conventional', 'robust'), \
            f'method 只能是 conventional 或 robust ，现在为 {method} 。'
        
        self._method = method
        self._mu: pd.Series | None = None
        self._sig: pd.Series | None = None
        self._median: pd.Series | None = None
        self._mad: pd.Series | None = None

        # 每个品种各自的 fit 窗口起止，并校验每个品种 start < end
        self._fit_windows = self._values_for_fit.reset_index().groupby('product').agg(
            start=('timestamp', 'min'), end=('timestamp', 'max'))
        bad = self._fit_windows[self._fit_windows['start'] >= self._fit_windows['end']]
        if not bad.empty:
            raise AssertionError(
                f'以下品种 fit 窗口开始时间不早于结束时间: {list(bad.index)}')

        self._warnings: list[str] = []

    @property
    def name(self) -> str:
        return f'fitted_ts_zscore_{self._method}'

    def fit(self):
        """拟合：计算相应统计量 (mu, sig 或者 median, mad)。"""
        if self._method == 'conventional':
            # $$z = \frac{x - \mu}{\sigma}$$
            grouped = self._values_for_fit.groupby(level='product')
            self._mu = grouped.mean()
            self._sig = grouped.std()
            bad = self._sig[self._sig == 0]
            if not bad.empty:
                raise ValueError(
                    f'以下品种 fit 窗口内零方差，无法进行 zscore: {list(bad.index)}')

        elif self._method == 'robust':
            #$$z_{robust} = \frac{x - \text{median}(x)}{1.4826 \times \text{MAD}(x)}$$
            # 其中 $\text{MAD} = \text{median}(|x - \text{median}(x)|)$，
            # 系数 $1.4826$ 是正态分布下 $\sigma / \text{MAD}$ 的理论比值，
            # 让正常数据的 robust z 与普通 z 尺度可比。
            grouped = self._values_for_fit.groupby(level='product')
            self._median = grouped.median()
            self._mad = grouped.apply(
                lambda s: float((s - s.median()).abs().median()))
            bad = self._mad[self._mad == 0]
            if not bad.empty:
                raise ValueError(
                    f'以下品种 fit 窗口内 MAD 为零，无法进行 robust zscore: {list(bad.index)}')

    def transform(
            self, 
            values: pd.Series, 
    ) -> PreprocessingResult:
        """根据 fitted params , 将fit窗口外的 values 转换为 zscore 。"""
        values = values.copy()

        # 1. 检验每个品种的 transform 窗口和其 fit 窗口是否重叠
        trans_windows = values.reset_index().groupby('product').agg(
            start=('timestamp', 'min'), end=('timestamp', 'max'))
        overlap = [
            p for p in trans_windows.index
            if p in self._fit_windows.index
            and trans_windows.loc[p, 'start'] <= self._fit_windows.loc[p, 'end']]
        if overlap:
            raise AssertionError(
                f'以下品种 transform 窗口最早值早于等于其 fit 窗口最晚值: {overlap}')

        # 2. 检验 fit 因子的名称是否等于 transform 因子的名称
        if values.name != self._values_for_fit.name:
            warning = (f'fit 所用因子名为 {self._values_for_fit.name} , '
            f'transform 因子名为 {values.name} , 两者不一致。'
            )
            self._warnings.append(warning)
        
        # 3. fit
        self.fit()

        # 4. 检验 transform 中的品种是否都已参与 fit
        products = values.index.get_level_values('product')
        fit_products = self._mu.index if self._method == 'conventional' \
            else self._median.index
        never_fitted = products[~products.isin(fit_products)].unique()
        if len(never_fitted) > 0:
            raise ValueError(f'transform 中出现未参与 fit 的品种: {list(never_fitted)}')

        # 5. 开始 zscore 化
        metadata: dict[str, Any] = {
            'fit_windows': self._fit_windows,
        }
        if self._method == 'conventional':
            mu = pd.Series(products.map(self._mu), index=values.index)
            sig = pd.Series(products.map(self._sig), index=values.index)
            scaled = (values - mu) / sig
            metadata['mu'] = self._mu
            metadata['sig'] = self._sig

        elif self._method == 'robust':
            median = pd.Series(products.map(self._median), index=values.index)
            mad = pd.Series(products.map(self._mad), index=values.index)
            scaled = (values - median) / (1.4826 * mad)
            metadata['median'] = self._median
            metadata['mad'] = self._mad

        return PreprocessingResult(
            values = scaled,
            warnings = self._warnings,
            metadata = metadata
        )
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            'values_for_fit': self._values_for_fit,
            'method': self._method
        }

class CrossSectionalZscore(FactorPreprocessor):
    """
    截面 zscore。
    
    对每个 timestamp 上的各品种因子值做 Zscore 化。
    """
    def __init__(self, method: Literal['conventional', 'robust'] = 'conventional'):
        assert method in ('conventional', 'robust'), \
            f'method 只能是 conventional 或 robust ，现在为 {method} 。'
        self._method = method

    @property
    def name(self) -> str:
        return f'cs_zscore_{self._method}'

    def transform(
            self, 
            values: pd.Series, 
    ) -> PreprocessingResult:
        values = values.copy()
        grouped = values.groupby(level='timestamp', group_keys=False)

        if self._method == 'conventional':
            loc = grouped.transform(lambda s: s.mean())
            scale = grouped.transform(lambda s: s.std())
        elif self._method == 'robust':
            loc = grouped.transform(lambda s: s.median())
            scale = grouped.transform(lambda s: 1.4826 * (s - s.median()).abs().median())

        scaled = (values - loc) / scale
        # 无效 timestamp（scale 为 NaN 或 0）：保留原值并告警
        invalid = scale.isna() | (scale == 0)
        scaled[invalid] = values[invalid]
        invalid_ts = values[invalid].index.get_level_values('timestamp').unique()
        warnings = [f'timestamp {ts} 截面统计量不可用（零方差或观测不足），保留原值'
                    for ts in invalid_ts]

        grouped = values.groupby(level='timestamp')
        if self._method == 'conventional':
            metadata = {'mu': grouped.mean(),'sig': grouped.std()}
        else:
            mad = grouped.apply(lambda s: (s - s.median()).abs().median())
            mad.name = 'mad'
            metadata = {'median': grouped.median(), 'mad': mad}
        return PreprocessingResult(
            values = scaled,
            warnings = warnings,
            metadata = metadata
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {'method': self._method}

class CrossSectionalRankNormalizer(FactorPreprocessor):
    """截面秩归一化。"""

    def __init__(self, method: Literal['uniform', 'normal'] = 'uniform'):
        assert method in ('uniform', 'normal'), f'只允许 uniform 或 normal， 现在为 {method}。'
        self._method = method
        self._warnings: list[str] = []

    @property
    def name(self) -> str:
        return f'cs_rank_normalizer_{self._method}'

    @property
    def parameters(self) -> dict[str, Any]:
        return {'method': self._method}

    def transform(self, values: pd.Series) -> PreprocessingResult:
        values = values.copy()
        
        u = (values.rank() - 0.5) / len(values)
        if u.nunique() == 1: # 这表明输入的 values 只有一个 unique value
            warning = f'输入的 values 只有一个 unique value: {values.unique()}'
            self._warnings.append(warning)

        if self._method == 'uniform':
            return PreprocessingResult(
                values = u,
                warnings = self._warnings,
                metadata = {}
            )

        elif self._method == 'normal':
            x = pd.Series(norm.ppf(u.values), index=u.index)
            return PreprocessingResult(
                values = x,
                warnings= self._warnings,
                metadata= {'u': u}
            )
        raise ValueError(f'非法 method:  {self._method}')
    
