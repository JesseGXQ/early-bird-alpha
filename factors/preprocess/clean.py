"""预处理组件 —— 数据清洗类。"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Literal, Mapping

from factors.models import INDEX_NAMES
import pandas as pd
from factors.preprocess.base import (
        FactorPreprocessor, PreprocessingResult, 
        PreprocessingContext, clean_multiindex)

class RollingTimeSeriesMissingHandler(FactorPreprocessor):
    """
    时序缺失值处理类，支持多品种。    
    """

    def __init__(
            self, 
            policy: Literal['preserve', 'impute'] = 'preserve', 
            method: Literal['mean', 'median'] = 'median',
            window: int = 20,
            min_periods: int = 1
            ):
        assert policy in ['preserve', 'impute'], f'缺失值政策只能是 preserve/impute ，现在为 {policy}'
        assert method in ['mean', 'median']
        self._policy = policy
        self._method = method
        self._window = window
        self._min_periods = min_periods

    @property
    def name(self) -> str:
        if self._policy == 'preserve':
            return f'ts_missing_handler_preserve'
        return f'rts_missing_handler_{self._policy}_{self._method}_{self._window}'

    def transform(
            self, 
            values: pd.Series, # 可能是单品种，也可能是多品种
            eligible: pd.Series | None = None,
    ) -> PreprocessingResult:
        values = values.copy()

        if self._policy == 'preserve':
            # 仅写缺失值情况报告到 metadata
            missing_count = values.isna().sum()
            missing_pct = missing_count / len(values)
            metadata = {'missing_count': missing_count,
                        'missing_percent': missing_pct}
            return PreprocessingResult(
                values = self._apply_eligible_mask(values, eligible),
                warnings = [],
                metadata = metadata
            )

        elif self._policy == 'impute':
            values, flags = self._impute_missings(values)
            return PreprocessingResult(
                values=self._apply_eligible_mask(values, eligible),
                warnings=[],
                metadata={'is_imputed': flags}
            )

    def _impute_missings(self, values: pd.Series):
        """内部辅助函数：填充 values 中的缺失值，输出  (填充后因子值series, 是否为填充的flag series)"""
        re_values = values.copy()
        
        re_values_shifted = re_values.groupby(level='product').shift(1)
        for_fill_series = (re_values_shifted
        .groupby(level='product')
        .rolling(window=self._window, min_periods=self._min_periods)
        .agg(self._method))
        for_fill_series = clean_multiindex(for_fill_series).reindex(re_values.index)

        re_values.loc[re_values.isna()] = for_fill_series

        flags = pd.Series( values.isna() & re_values.notna() , name='is_imputed')
        return re_values, flags

    @property
    def parameters(self) -> dict[str, Any]:
        if self._policy == 'preserve':
            return {'policy': self._policy}
        elif self._policy == 'impute':
            return {
                'policy': self._policy,
                'method': self._method,
                'window': self._window,
                'min_periods': self._min_periods
            }


class CrossSectionalMissingHandler(FactorPreprocessor):
    """截面缺失值处理类。"""

    def __init__(
            self, 
            policy: Literal['preserve', 'impute'] = 'preserve',
            method: Literal['mean', 'median'] = 'median'
    ):
        assert policy in ['preserve', 'impute'], f'缺失值政策只能是 preserve/impute ，现在为 {policy}'
        assert method in ['mean', 'median']
        self._policy = policy
        self._method = method

    @property
    def name(self) -> str:
        if self._policy == 'preserve':
            return f'cs_missing_handler_preserve'
        return f'cs_missing_handler_{self._policy}_{self._method}'

    @property
    def parameters(self) -> dict[str, Any]:
        return {'policy': self._policy, 'method': self._method}

    def transform(
            self, 
            values: pd.Series, 
            eligible: pd.Series | None = None,
    ) -> PreprocessingResult:
        values = values.copy()

        if self._policy == 'preserve':
            # 写缺失值报告到 metadata
            grouped = values.groupby(level='timestamp', group_keys=False)
            missing_count = grouped.apply(lambda df: df.isna().sum())
            missing_count.name = 'missing_count'
            missing_pct   = grouped.apply(lambda df: df.isna().sum() / len(df) )
            missing_pct.name = 'missing_percent'
            metadata = {'missing_count': missing_count, 'missing_percent': missing_pct}
            return PreprocessingResult(
                values = self._apply_eligible_mask(values, eligible),
                warnings = [],
                metadata = metadata
            )

        elif self._policy == 'impute':
            grouped = values.groupby(level='timestamp', group_keys=False)
            if self._method == 'mean':
                values = grouped.apply(lambda s: s.fillna(s.mean()))
            elif self._method == 'median':
                values = grouped.apply(lambda s: s.fillna(s.median()))
            flags = grouped.apply(lambda s: s.isna())
            
            flags.name = 'is_imputed'
            return PreprocessingResult(
                values = self._apply_eligible_mask(values, eligible),
                warnings = [],
                metadata={'is_imputed': flags}
            )
        
# =================================================
# simple inline test
# =================================================
if __name__ == '__main__':
    import numpy as np

    # 构造 MultiIndex[timestamp, product]，product 仅 'RB'
    timestamp = pd.date_range('2026-07-01', '2026-07-20', freq='D')
    index = pd.MultiIndex.from_product([timestamp, ['RB']], names=['timestamp', 'product'])

    # 随机数，将第10和第15个值置为缺失
    n = len(index)
    values = np.random.randn(n)
    nan_idx = np.array([9, 14])  # 0-based 索引，即第10和第15个值
    values[nan_idx] = np.nan

    s = pd.Series(values, index=index, name='test_factor')

    # --------------------------------- TimeSeries ----------------------------
    print('-------------- method = preserve -------------------')
    ts_missing_handler = RollingTimeSeriesMissingHandler(policy='preserve')
    s_trans = ts_missing_handler.transform(s)

    print(f'Transformed Values: \n  {s_trans.values}')
    print(f'metadata: \n {s_trans.metadata}')

    print('-------------- method = impute -------------------')
    # impute
    ts_missing_handler = RollingTimeSeriesMissingHandler(policy='impute', window=5)
    s_trans = ts_missing_handler.transform(s)
    print(f'Transformed Values: \n  {s_trans.values}')
    assert s_trans.values.iloc[9] == s_trans.values.iloc[4:9].median()
    print(f'metadata: \n {s_trans.metadata}')

    # --------------------------------- CrossSectional ----------------------------
    # 构造 MultiIndex[timestamp, product]，product 为 5 个品种
    products = ['AP', 'AD', 'RB', 'A', 'ZN']
    index = pd.MultiIndex.from_product([timestamp, products], names=['timestamp', 'product'])

    # 生成随机数
    values = np.random.randn(len(index))

    # 将 2026-07-05 和 2026-07-10 的 'AP' 置为缺失值
    for dt in ['2026-07-05', '2026-07-10']:
        key = (pd.Timestamp(dt), 'AP')
        pos = index.get_loc(key)
        values[pos] = np.nan

    # 构造 Series
    s = pd.Series(values, index=index, name='value')

    print(f'original series: \n {s}')

    print('-------------- method = preserve -------------------')
    cs_missing_handler = CrossSectionalMissingHandler(policy='preserve')
    s_trans = cs_missing_handler.transform(s)
    print(f'Transformed Values: \n  {s_trans.values.to_string()}')
    print(f'metadata: \n {s_trans.metadata}')

    print('-------------- method = impute -------------------')
    cs_missing_handler = CrossSectionalMissingHandler(policy='impute', method='median')
    s_trans = cs_missing_handler.transform(s)
    print(f'Transformed Values: \n  {s_trans.values.to_string()}')
    print(f'metadata: \n {s_trans.metadata['is_imputed'].to_string()}')


