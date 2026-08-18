"""预处理组件 —— 缩尾类。"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Literal, Mapping

import pandas as pd
from factors.preprocess.base import (
        FactorPreprocessor, PreprocessingResult, PreprocessingContext,
        clean_multiindex)

class RollingTimeseriesWinsorizer(FactorPreprocessor):
    """滚动时序缩尾，支持多品种。"""

    def __init__(
            self, 
            lower_quantile: float = 0.01, 
            upper_quantile: float = 0.99,
            window: int = 20
    ):
        assert 0 < lower_quantile < upper_quantile < 1, \
            f'分位数需满足 0 < lower < upper < 1，现在为 {lower_quantile}/{upper_quantile}'
        assert window >= 2, f'window 至少为 2，现在为 {window}'
        self._lower_quantile = lower_quantile
        self._upper_quantile = upper_quantile
        self._window = window

    @property
    def name(self) -> str:
        return f'rts_winsorizer_{self._lower_quantile}_{self._upper_quantile}_{self._window}'

    def transform(
            self, 
            values: pd.Series, 
    ) -> PreprocessingResult:
        values = values.copy()

        grouped = values.groupby(level='product')
        lower = clean_multiindex(grouped.rolling(
            window=self._window, min_periods=self._window
        ).quantile(self._lower_quantile)).reindex(values.index)
        upper = clean_multiindex(grouped.rolling(
            window=self._window, min_periods=self._window
        ).quantile(self._upper_quantile)).reindex(values.index)

        clipped = values.clip(lower=lower, upper=upper)
        # 未满窗区（各品种前 window-1 个观测）滚动分位数为 NaN，clip 会传播 NaN，保持原值
        clipped = clipped.where(lower.notna(), values)

        metadata = {
            'lower_clipped_count': (values < lower).groupby(level='product').sum().astype(int),
            'upper_clipped_count': (values > upper).groupby(level='product').sum().astype(int),
            'unfilled_window_unchanged_count': lower.isna().groupby(level='product').sum().astype(int),
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
            'window': self._window,
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
    ) -> PreprocessingResult:
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
    print('-------------- CrossSectionalWinsorizer -------------------')
    # 构造 MultiIndex[timestamp, product]，product 为 5 个品种，10 个交易日
    timestamp = pd.date_range('2026-07-01', periods=10, freq='D')
    products = ['AP', 'AD', 'RB', 'A', 'ZN']
    index = pd.MultiIndex.from_product([timestamp, products], names=['timestamp', 'product'])

    # 生成随机数，将 2026-07-05 的 'RB' 置为异常大值
    values = np.random.randn(len(index))
    key = (pd.Timestamp('2026-07-05'), 'RB')
    pos = index.get_loc(key)
    values[pos] = 10.0
    s = pd.Series(values, index=index, name='value')

    winsorizer = CrossSectionalWinsorizer(lower_quantile=0.1, upper_quantile=0.9)
    s_trans = winsorizer.transform(s)

    # 2026-07-05 截面中 RB 应被缩到该截面 90% 分位
    cross = s.xs(pd.Timestamp('2026-07-05'), level='timestamp')
    expected_upper = cross.quantile(0.9)
    assert s_trans.values.loc[key] == expected_upper, \
        f'截面缩尾失败: {s_trans.values.loc[key]} != {expected_upper}'
    print(f'原值 2026-07-05 RB: {s.values[pos]:.4f}')
    print(f'缩尾后 2026-07-05 RB: {s_trans.values.loc[key]:.4f}')
    print(f'metadata: \n {s_trans.metadata}')
