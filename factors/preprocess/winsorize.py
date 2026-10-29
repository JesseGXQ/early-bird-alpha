"""预处理组件 —— 缩尾类。"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any, Literal, Mapping
import numpy as np
import pandas as pd
from factors.preprocess.base import (
        FactorPreprocessor, PreprocessingResult, PreprocessingContext,
        clean_multiindex)

class RollingTimeseriesWinsorizer(FactorPreprocessor):
    """滚动时序缩尾，支持多品种。

    Parameters
    ----------
    pooled: bool, default False
        估计分位数时，是否做池化（所有品种合并），还是每个品种单独估计，默认单独估计。
    lower_quantile : float, default 0.01
        下缩尾分位数，(0, 1)。
    upper_quantile : float, default 0.99
        上缩尾分位数，(0, 1)。
    window : int, default 20
        滚动窗口长度（观测数），至少为 2。
    min_periods : int or None, default None
        滚动窗口计算分位数所需的最小窗口。缺省时取 window 的数值。
        若不为 None，必须小于等于 `window`。
    min_observation: int, default 5
        计算分位数所需的最小非NaN值计数。必须小于等于 `min_periods`。
    lagged: bool, default True
        分位数统计时，是否排除当期（避免当期极端值影响）。
    na_action: {'keep_raw', 'drop'}, default drop
        如何处理样本头部分位数无定义期，以及样本中出现分位数为NaN时的（因子缩尾后）取值。
        - 'keep_raw': 保留原始因子值。
        - 'drop'：令因子值为 NaN 。

    Notes
    ------
    - 如果选择 `pooled=True`，使用假设是：“所有品种的正常因子值分布大体可比”。
    如果某些品种或板块天然具有更高的（有经济意义的）因子取值，那么池化缩放会系统性地
    压缩这些品种或板块的因子值。
    """

    def __init__(
            self, 
            pooled: bool = False,
            lower_quantile: float = 0.01, 
            upper_quantile: float = 0.99,
            window: int = 20,
            min_periods: int | None = None,
            min_observations: int | None = None, 
            lagged: bool = True,
            na_action: Literal['keep_raw', 'drop'] = 'drop'
    ):
        if min_observations is None:
            raise ValueError('min_observations 为必填参数。')
        assert 0 < lower_quantile < upper_quantile < 1, \
            f'分位数需满足 0 < lower < upper < 1，现在为 {lower_quantile}/{upper_quantile}'
        assert window >= 2, f'window 至少为 2，现在为 {window}'
        if min_periods is not None:
            assert min_periods <= window
            assert min_observations <= min_periods
        assert min_observations <= window 
        
        self._pooled = pooled
        self._lower_quantile = lower_quantile
        self._upper_quantile = upper_quantile
        self._window = window
        self._min_periods = min_periods or window
        self._min_obs = min_observations
        self._lagged = lagged
        self._na_action = na_action

    @property
    def name(self) -> str:
        name = f'rts_winsorizer_{'pooled' if self._pooled else 'per-product'}'\
            + f'_{self._lower_quantile}_{self._upper_quantile}_{self._window}'
        return name

    def _pooled_rolling_quantiles_numpy(
            self, arr_from_wide: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        # 这部分代码的教学，见 `scripts\temp\numpy_sliding_window_quantile_tutorial.ipynb`
        arr = arr_from_wide
        sw = np.lib.stride_tricks.sliding_window_view(arr, self._window, axis=0)
        n_window = arr.shape[0] - self._window + 1
        # TODO [performance] 这里reshape会返回 copy，目前没有写分块逻辑；
        # 后续如果内存问题造成瓶颈，需要增加分块逻辑。
        reshaped = sw.reshape((n_window, -1))
        ok = (~np.isnan(reshaped)).sum(axis=1) > self._min_obs
        qs = [self._lower_quantile, self._upper_quantile]
        quantile_arr = np.nanquantile(reshaped[ok], q=qs, axis=1).T
        out = np.full((arr.shape[0], len(qs)), np.nan)
        out[self._window-1:][ok] = quantile_arr
        lower, upper = out[:, 0], out[:, 1]
        return lower, upper
    
    def transform(
            self, 
            values: pd.Series,
            eligible: pd.Series | None,
    ) -> PreprocessingResult:
        """滚动窗口缩尾变换。当 `eligible` 不为 `None` 时，会先应用品种池再估计
        分位数。输出的 `PreprocessingResult.values` 中，不在品种池内的品种因子
        取值为 NaN 。"""
        assert (~np.isinf(values)).all(), '因子值中存在inf！'

        values = values.copy()

        if eligible is not None:
            assert values.index.isin(eligible.index).all()
            eligible = eligible.reindex(values.index)
            values = values.where(eligible) #所有不在品种池内的，直接置 NaN
        
        if not self._pooled:
            grouped = values.groupby(level='product')
            lower = clean_multiindex(grouped.rolling(
                window=self._window, min_periods=self._min_periods
            ).quantile(self._lower_quantile)
            ).reindex(values.index)
            upper = clean_multiindex(grouped.rolling(
                window=self._window, min_periods=self._min_periods
            ).quantile(self._upper_quantile)
            ).reindex(values.index)
            if self._lagged:
                lower = lower.groupby(level='product').shift(1)
                upper = upper.groupby(level='product').shift(1)
        else:
            wide = values.unstack(level='product')
            wide_arr = wide.to_numpy()
            lower, upper = self._pooled_rolling_quantiles_numpy(wide_arr)
            lower = pd.Series(lower, index=wide.index)
            upper = pd.Series(upper, index=wide.index)
            if self._lagged:
                lower = lower.shift(1)
                upper = upper.shift(1)
            # lower, upper is Index[timestamp], 
            # needs to be converted to have MultiIndex[timestamp, product]
            lower = lower[values.index.get_level_values('timestamp')]\
            .set_axis(values.index)
            upper = upper[values.index.get_level_values('timestamp')]\
            .set_axis(values.index)

        clipped = values.clip(lower=lower, upper=upper) 
        if self._na_action == 'drop':
            clipped = clipped.mask(lower.isna(), np.nan)
        else:
            pass #因为原本就是 lower.isna() 的地方，clip 不生效，values中的原值被保留

        # metadata = {
        #     'lower_clipped_count': (values < lower).groupby(level='product').sum().astype(int),
        #     'upper_clipped_count': (values > upper).groupby(level='product').sum().astype(int),
        #     'n_dropped_under_nan_action': lower.isna().groupby(level='product').sum().astype(int) \
        #         if self._nan_action == 'drop' else 0,
        # }
        metadata = {}
        return PreprocessingResult(
            values = clipped,
            warnings = [],
            metadata = metadata
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            'pooled': self._pooled,
            'lower_quantile': self._lower_quantile,
            'upper_quantile': self._upper_quantile,
            'window': self._window,
            'min_periods': self._min_periods,
            'min_observations': self._min_obs,
            'lagged': self._lagged,
            'na_action': self._na_action
        }


class CrossSectionalWinsorizer(FactorPreprocessor):
    """截面缩尾。"""
    def __init__(
        self, 
        lower_quantile: float = 0.01, 
        upper_quantile: float = 0.99
    ):
        assert 0 < lower_quantile < upper_quantile < 1, \
            f'分位数需满足 0 < lower < upper < 1，现在为 {lower_quantile}/{upper_quantile}'
        self._lower_quantile = lower_quantile
        self._upper_quantile = upper_quantile

    @property
    def name(self) -> str:
        return f'cs_winsorizer_{self._lower_quantile}_{self._upper_quantile}'

    def transform(
            self, 
            values: pd.Series, 
            eligible: pd.Series | None
    ) -> PreprocessingResult:
        """目前代码并未应用 eligible！"""
        values = values.copy()

        grouped = values.groupby(level='timestamp', group_keys=False)
        lower = grouped.transform(lambda s: s.quantile(self._lower_quantile))
        upper = grouped.transform(lambda s: s.quantile(self._upper_quantile))

        clipped = values.clip(lower=lower, upper=upper)

        metadata = {
            'lower_clipped_count': (values < lower).groupby(level='product').sum().astype(int),
            'upper_clipped_count': (values > upper).groupby(level='product').sum().astype(int),
        }
        return PreprocessingResult(
            values = clipped,
            warnings = [],
            metadata = metadata
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            'lower_quantile': self._lower_quantile,
            'upper_quantile': self._upper_quantile,
        }


# =================================================
# simple inline test
# =================================================
if __name__ == '__main__':
    import numpy as np

    # --------------------------------- TimeSeries ----------------------------
    print('-------------- TimeseriesWinsorizer -------------------')
    # 构造 MultiIndex[timestamp, product]，product 仅 'RB'，30 个交易日
    timestamp = pd.date_range('2026-07-01', periods=30, freq='D')
    index = pd.MultiIndex.from_product([timestamp, ['RB']], names=['timestamp', 'product'])

    # 随机数，将第 25 个值置为异常大值
    values = np.random.randn(len(index))
    values[24] = 10.0
    s = pd.Series(values, index=index, name='test_factor')

    winsorizer = RollingTimeseriesWinsorizer(lower_quantile=0.1, upper_quantile=0.9, window=10)
    s_trans = winsorizer.transform(s)

    # 第 25 个值应被缩到该滚动窗口（values[15:25]）的 90% 分位
    expected_upper = np.quantile(s.values[15:25], 0.9)
    assert s_trans.values.iloc[24] == expected_upper, \
        f'上缩尾失败: {s_trans.values.iloc[24]} != {expected_upper}'
    # 未满窗区（前 9 个观测）保持原值
    assert (s_trans.values.iloc[:9] == s.values[:9]).all(), '未满窗区应保持原值'
    print(f'原值第25个: {s.values[24]:.4f}')
    print(f'缩尾后第25个: {s_trans.values.iloc[24]:.4f}')
    print(f'metadata: \n {s_trans.metadata}')

    # --------------------------------- CrossSectional ----------------------------
    # print('-------------- CrossSectionalWinsorizer -------------------')
    # # 构造 MultiIndex[timestamp, product]，product 为 5 个品种，10 个交易日
    # timestamp = pd.date_range('2026-07-01', periods=10, freq='D')
    # products = ['AP', 'AD', 'RB', 'A', 'ZN']
    # index = pd.MultiIndex.from_product([timestamp, products], names=['timestamp', 'product'])

    # # 生成随机数，将 2026-07-05 的 'RB' 置为异常大值
    # values = np.random.randn(len(index))
    # key = (pd.Timestamp('2026-07-05'), 'RB')
    # pos = index.get_loc(key)
    # values[pos] = 10.0
    # s = pd.Series(values, index=index, name='value')

    # winsorizer = CrossSectionalWinsorizer(lower_quantile=0.1, upper_quantile=0.9)
    # s_trans = winsorizer.transform(s)

    # # 2026-07-05 截面中 RB 应被缩到该截面 90% 分位
    # cross = s.xs(pd.Timestamp('2026-07-05'), level='timestamp')
    # expected_upper = cross.quantile(0.9)
    # assert s_trans.values.loc[key] == expected_upper, \
    #     f'截面缩尾失败: {s_trans.values.loc[key]} != {expected_upper}'
    # print(f'原值 2026-07-05 RB: {s.values[pos]:.4f}')
    # print(f'缩尾后 2026-07-05 RB: {s_trans.values.loc[key]:.4f}')
    # print(f'metadata: \n {s_trans.metadata}')
