"""预处理组件 —— 信号改性。"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Literal, Mapping

import pandas as pd
import numpy as np
from factors.preprocess.base import (
        FactorPreprocessor, PreprocessingResult, 
        PreprocessingContext, clean_multiindex)

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
        
            

            
    