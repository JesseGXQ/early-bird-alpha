"""预处理组件 —— 特征缩放类。"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Literal, Mapping, Self

import pandas as pd
from factors.preprocess.base import (
        FactorPreprocessor, PreprocessingResult, 
        PreprocessingContext, FittedFactorPreprocessor, clean_multiindex)
from scipy.stats import norm
from factors import INDEX_NAMES
import numpy as np
import copy

class FittedTimeSeriesZscore(FittedFactorPreprocessor):
    """
    一次性时序 Zscore (fit-once)，支持多品种。
    
    在固定 fit 窗口计算统计量，然后用同一组统计量对窗口外因子值做 z-score。

    Notes
    ------
    - 注意，特定时序模式的收获范式下，如果对有天然零点的因子使用该组件，会改变因子性质：
    例如对于动量因子，使用 fitted zscore transformation。因子的性质会从动量强度变为
    “相对历史平均的动量强度”。
    """

    def __init__(
            self, 
            method: Literal['conventional', 'robust'] = 'conventional'):
        
        assert method in ('conventional', 'robust'), \
            f'method 只能是 conventional 或 robust ，现在为 {method} 。'
        
        self._method = method
        self._mu: pd.Series | None = None
        self._sig: pd.Series | None = None
        self._median: pd.Series | None = None
        self._mad: pd.Series | None = None
        self._fit_windows: pd.DataFrame | None = None
        self._fit_values_name: Any = None
        self._warnings: list[str] = []

    @property
    def name(self) -> str:
        return f'fitted_ts_zscore_{self._method}'

    def fit(self, values: pd.Series, **fit_params: Any) -> Self:
        """拟合：记录 fit 窗口并计算相应统计量 (mu, sig 或者 median, mad)。"""
        # 每个品种各自的 fit 窗口起止，并校验每个品种 start < end
        self._fit_windows = values.reset_index().groupby('product').agg(
            start=('timestamp', 'min'), end=('timestamp', 'max'))
        bad = self._fit_windows[self._fit_windows['start'] >= self._fit_windows['end']]
        if not bad.empty:
            raise AssertionError(
                f'以下品种 fit 窗口开始时间不早于结束时间: {list(bad.index)}')

        self._fit_values_name = values.name
        self._warnings = []

        if self._method == 'conventional':
            # $$z = \frac{x - \mu}{\sigma}$$
            grouped = values.groupby(level='product')
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
            grouped = values.groupby(level='product')
            self._median = grouped.median()
            self._mad = grouped.apply(
                lambda s: float((s - s.median()).abs().median()))
            bad = self._mad[self._mad == 0]
            if not bad.empty:
                raise ValueError(
                    f'以下品种 fit 窗口内 MAD 为零，无法进行 robust zscore: {list(bad.index)}')

        return self

    def _check_is_fitted(self) -> None:
        if self._fit_windows is None:
            raise ValueError('必须先调用 fit。')
        if self._method == 'conventional':
            if self._mu is None or self._sig is None:
                raise ValueError('必须先调用 fit。')
        else:
            if self._median is None or self._mad is None:
                raise ValueError('必须先调用 fit。')

    @property
    def states_(self) -> Mapping[str, Any]:
        self._check_is_fitted()
        states: dict[str, Any] = {'fit_windows': self._fit_windows}
        if self._method == 'conventional':
            states['mu'] = self._mu
            states['sig'] = self._sig
        else:
            states['median'] = self._median
            states['mad'] = self._mad
        return states

    def transform(
            self, 
            values: pd.Series, 
    ) -> PreprocessingResult:
        """根据 fitted params , 将fit窗口外的 values 转换为 zscore 。"""
        self._check_is_fitted()
        fit_windows = self._fit_windows
        assert fit_windows is not None
        values = values.copy()

        # 1. 检验每个品种的 transform 窗口和其 fit 窗口是否重叠
        trans_windows = values.reset_index().groupby('product').agg(
            start=('timestamp', 'min'), end=('timestamp', 'max'))
        overlap = [
            p for p in trans_windows.index
            if p in fit_windows.index
            and trans_windows.loc[p, 'start'] <= fit_windows.loc[p, 'end']]
        if overlap:
            raise AssertionError(
                f'以下品种 transform 窗口最早值早于等于其 fit 窗口最晚值: {overlap}')

        # 2. 检验 fit 因子的名称是否等于 transform 因子的名称
        if values.name != self._fit_values_name:
            warning = (f'fit 所用因子名为 {self._fit_values_name} , '
            f'transform 因子名为 {values.name} , 两者不一致。'
            )
            self._warnings.append(warning)

        # 3. 检验 transform 中的品种是否都已参与 fit
        products = values.index.get_level_values('product')
        fit_products = self._mu.index if self._method == 'conventional' \
            else self._median.index
        never_fitted = products[~products.isin(fit_products)].unique()
        if len(never_fitted) > 0:
            raise ValueError(f'transform 中出现未参与 fit 的品种: {list(never_fitted)}')

        # 4. 开始 zscore 化
        metadata: dict[str, Any] = {
            'fit_windows': fit_windows,
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
        return {'method': self._method}

class RollingTimeSeriesZscore(FactorPreprocessor):
    """滚动时序 Zscore ，支持多品种。"""

    def __init__(self, method: Literal['conventional', 'robust'] = 'conventional',
                 window: int = 20):
        assert window >= 2, f'window 至少为 2，现在为 {window}。'
        assert method in ('conventional', 'robust'), \
                    f'method 只能是 conventional 或 robust ，现在为 {method} 。'
        self._method = method
        self._window = window

    @property
    def name(self) -> str:
        return f'rts_zscore_{self._method}_{self._window}'

    @property
    def parameters(self) -> dict[str, Any]:
        return {'method': self._method, 'window': self._window}

    def transform(self, values: pd.Series) -> PreprocessingResult:
        values = values.copy()

        if self._method == 'conventional':
            loc = clean_multiindex(
                (values
            .groupby(level='product')
            .rolling(window=self._window, min_periods=self._window)
            .agg('mean'))
            ).reindex(values.index)
            scale = clean_multiindex(
                (values
                 .groupby(level='product')
                 .rolling(window=self._window, min_periods=self._window)
                 .agg('std'))
            ).reindex(values.index)
            scaled = (values - loc) / scale
            warnings  = self._gen_invalid_warnings(values=values, scale=scale)
            metadata = {'mu': loc, 'sig': scale}

        elif self._method == 'robust':
            loc = clean_multiindex(
                (values
                 .groupby(level='product')
                 .rolling(window=self._window, min_periods=self._window)
                 .agg('median')
                )
            ).reindex(values.index)
            scale = clean_multiindex(
                (values
                 .groupby(level='product')
                 .rolling(window=self._window, min_periods=self._window)
                 .apply(lambda s: 1.4826 * (s - s.median()).abs().median() )
                )
            ).reindex(values.index)
            scaled = (values - loc) / scale
            warnings = self._gen_invalid_warnings(values=values, scale=scale)
            metadata = {'median': loc, 'mad': scale}
        else:
            raise ValueError(f'method  {self._method} 非法。')

        return PreprocessingResult(
            values = scaled,
            warnings = warnings,
            metadata = metadata
        )
    
    def _gen_invalid_warnings(self, values, scale) -> list[str]:
        """内部辅助函数：对于那些窗口已满，但scale仍未空或nan的，返回警告。"""
        def is_window_unfilled(s: pd.Series):
            return pd.Series(
                np.array(range(len(s))) < (self._window - 1) ,
                index = s.index,
            ) 
        mask_window_unfilled = values.groupby(level='product', group_keys=False).apply(is_window_unfilled)
        mask_scale_invalid = scale.isna() | (scale == 0)
        mask_invalid = (~mask_window_unfilled) & mask_scale_invalid # 窗口已满，但scale仍未空或nan，才是非法
        invalid_indices = scale[mask_invalid].index
        if self._method == 'conventional':
            warnings = [f'{idx} 标准差统计量不可用（零方差或观测值不足{self._window}）'
                        for idx in invalid_indices]
        elif self._method == 'robust':
            warnings = [f'{idx} MAD 统计量不可用（零方差或观测值不足{self._window}）'
                        for idx in invalid_indices]
        return warnings

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
        """
        对每个截面，计算 z score。
        
        Parameters
        ------
        values: pd.Series
            因子值。MultiIndex[timestamp, product]。
        
        Returns
        ------
        PreprocessingResult
            预处理结果。
            - `values`: pd.Series，z score 化的因子值。
            - `warnings`: list of str, 预处理的警告。
            - `metadata`: Mapping[str, Any], 预处理元数据。
        """
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
    

class FittedPooledScalar(FittedFactorPreprocessor):
    """
    可拟合池化缩放组件。可用于归一化因子的尺度。

    Parameters
    ------
    method: {'RMS', 'MA'}, optional.
        计算缩放尺度的方法，默认为 'RMS'。
        - ``'RMS'``: Root Mean Square Scaling.
        - ``'MA'``: Mean Absolute Scaling.
    
    """
    def __init__(self, method: Literal['RMS', 'MA'] = 'RMS'):
        assert method in {'RMS', 'MA'}
        self._method = method
        self._scaling_coef: float | None = None

    @property
    def name(self) -> str:
        return f'fitted_pooled_scalar_{self._method}'

    @property
    def parameters(self) -> dict[str, Any]:
        return {'method': self._method}

    def fit(
            self,
            values: pd.Series, # MutiIndex[timestamp, product]
            *,
            eligible: pd.Series[bool], # MutiIndex[timestamp, product]
    ) -> Self:
        assert values.index.names == eligible.index.names
        assert values.index.names == INDEX_NAMES
        assert values.index.isin(eligible.index).all()

        eligible = eligible.reindex(values.index) # 先对其索引
        values_elig = values[eligible] # 仅选中品种池中的品种做拟合

        if self._method == 'RMS':
            scale = float(np.sqrt(values_elig.pow(2).mean()))
        else:
            scale = float(values_elig.abs().mean())

        if scale == 0: # 防一手
            raise ValueError(f'拟合得到 scale = 0，说明输入的 Series 是全 0 值。'
                             '这是不合法的输入。')
        self._scaling_coef = 1 / scale

        return self

    def _check_is_fitted(self) -> None:
        if self._scaling_coef is None:
            raise ValueError('必须先调用 fit。')

    @property
    def states_(self) -> Mapping[str, Any]:
        self._check_is_fitted()
        assert self._scaling_coef is not None
        return {'scaling_coef': self._scaling_coef}

    @property
    def scaling_coef_(self) -> float:
        self._check_is_fitted()
        return self._scaling_coef  # pyright: ignore[reportReturnType]
    
    def transform(
            self, 
            values: pd.Series,
    ) -> PreprocessingResult:
        self._check_is_fitted()
        assert self._scaling_coef is not None
        assert values.index.names == INDEX_NAMES

        re_values = values * self._scaling_coef
        return  PreprocessingResult(
            values=re_values,
            warnings=[],
            metadata={},
        )

class ClipPreprocessor(FactorPreprocessor):
    """截断组件。
    
    Parameters
    ------
    clip_bound: float or int
        截断边界，必须大于等于0。
        将大于 clip_bound 的数截断到 clip_bound，将小于 - clip_bound 的数截断到 
        -clip_bound。

    """

    def __init__(self, clip_bound: float | int): 
        assert clip_bound >= 0
        self._bound = clip_bound

    @property
    def name(self) -> str:
        return f'clipper_{self._bound}'

    @property
    def parameters(self) -> dict[str, Any]:
        return {'clip_bound': self._bound}

    def transform(self, values: pd.Series) -> PreprocessingResult:
        return PreprocessingResult(
            values = values.clip(lower=-self._bound, upper=self._bound),
            warnings=[],
            metadata={}
        )
        
Clip = ClipPreprocessor
Clamp = ClipPreprocessor

class WalkForwardFitTransformPreprocessor:
    """推进型可拟合变换预处理组件（占位，尚未实现）。"""

    def __init__(
            self,
            preprocessor: FittedFactorPreprocessor,
    ):
        self._preprocessor = preprocessor

    def fit(self, values: pd.Series, **fit_params: Any) -> None:
        raise NotImplementedError(
            'WalkForwardFitTransformPreprocessor 尚未实现。')

# ==============================
# inline test
# ==============================
if __name__ == '__main__':
    # --------------------------------- RollingTimeSeriesZscore: conventional ----------------------------
    print('-------------- RollingTimeSeriesZscore: conventional（多品种） -------------------')
    ts = pd.date_range('2026-07-01', periods=10, freq='D')
    products = ['RB', 'CU']
    window = 3
    rb = np.linspace(0.0, 1.0, 10)
    cu = np.linspace(10.0, 20.0, 10)

    def build_series(arr_by_product):
        parts = []
        for p, arr in arr_by_product.items():
            idx = pd.MultiIndex.from_product([ts, [p]], names=['timestamp', 'product'])
            parts.append(pd.Series(arr, index=idx, name='test_factor'))
        return pd.concat(parts).sort_index()

    s = build_series({'RB': rb, 'CU': cu})

    z = RollingTimeSeriesZscore(method='conventional', window=window)
    res = z.transform(s)

    # 窗口满后，scaled == (x - 滚动mean) / 滚动std（ddof=1）
    for p, arr in [('RB', rb), ('CU', cu)]:
        for i in range(window - 1, 10):
            seg = arr[i - window + 1:i + 1]
            expected = (arr[i] - seg.mean()) / seg.std(ddof=1)
            got = res.values.loc[(ts[i], p)]
            assert np.isclose(got, expected, rtol=1e-10, atol=1e-12), \
                f'conventional {p}@{i}: {got} != {expected}'
    # 未满窗区（各品种前 window-1 个观测）为 NaN
    for p in products:
        for i in range(window - 1):
            assert np.isnan(res.values.loc[(ts[i], p)]), f'{p}@{i} 应为 NaN'
    assert len(res.warnings) == 0, f'正常数据不应有 warnings: {res.warnings}'
    # metadata 为逐行 Series
    assert len(res.metadata['mu']) == len(s) and len(res.metadata['sig']) == len(s)
    print(f'conventional 断言通过。warnings: {res.warnings}')
    print(f'metadata mu 首行: \n {res.metadata["mu"].head(3)}')

    # --------------------------------- robust ----------------------------
    print('-------------- RollingTimeSeriesZscore: robust（多品种） -------------------')
    z = RollingTimeSeriesZscore(method='robust', window=window)
    res = z.transform(s)

    for p, arr in [('RB', rb), ('CU', cu)]:
        for i in range(window - 1, 10):
            seg = arr[i - window + 1:i + 1]
            median = np.median(seg)
            mad = np.median(np.abs(seg - median))
            expected = (arr[i] - median) / (1.4826 * mad)
            got = res.values.loc[(ts[i], p)]
            assert np.isclose(got, expected, rtol=1e-10, atol=1e-12), \
                f'robust {p}@{i}: {got} != {expected}'
    assert len(res.warnings) == 0, f'正常数据不应有 warnings: {res.warnings}'
    print(f'robust 断言通过。warnings: {res.warnings}')

    # --------------------------------- 零方差 warning ----------------------------
    print('-------------- 零方差段：窗口满但 scale=0 应生成 warning -------------------')
    rb_zero = np.concatenate([np.full(3, 1.0), np.linspace(2.0, 5.0, 7)])
    s_zero = build_series({'RB': rb_zero})

    res = RollingTimeSeriesZscore(method='conventional', window=window).transform(s_zero)
    assert len(res.warnings) >= 1, f'应生成零方差 warning: {res.warnings}'
    assert all('RB' in w for w in res.warnings), res.warnings
    print(f'conventional 零方差 warnings: {res.warnings}')

    res = RollingTimeSeriesZscore(method='robust', window=window).transform(s_zero)
    assert len(res.warnings) >= 1, f'应生成零方差 warning: {res.warnings}'
    assert all('RB' in w for w in res.warnings), res.warnings
    print(f'robust 零方差 warnings: {res.warnings}')
    print('全部 inline test 通过。')



