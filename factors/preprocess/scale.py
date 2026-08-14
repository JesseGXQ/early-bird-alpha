"""预处理组件 —— 特征缩放类。"""

from __future__ import annotations
from logging import warn
from typing import TYPE_CHECKING, Any, Literal, Mapping

import pandas as pd
from factors.preprocess.base import (
        FactorPreprocessor, PreprocessingResult, PreprocessingContext)

class FittedTimeSeriesZscore(FactorPreprocessor):
    """
    一次性时序 Zscore (fit-once)。
    
    在固定 fit 窗口计算统计量，然后用同一组统计量对窗口外因子值做 z-score。

    """

    def __init__(
            self, 
            method: Literal['conventional', 'robust']):
        assert method in ('conventional', 'robust'), \
            f'method 只能是 conventional 或 robust ，现在为 {method} 。'
        self._method = method
        self._mu: float | None = None
        self._sig: float | None = None
        self._median: float | None = None
        self._mad: float | None = None

        self._fit_window_start: pd.Timestamp | None = None
        self._fit_window_end: pd.Timestamp | None = None

        self._warnings: list[str] = []

    @property
    def name(self) -> str:
        return f'fitted_ts_zscore_{self._method}'

    def fit(self, values: pd.Series):
        """在 values 上做 fit ，计算相应统计量 (mu, sig 或者 median, mad)。"""
        # 记录 fit 因子Series的名称
        self._fit_name = values.name

        timestamps = values.index.get_level_values('timestamp')
        self._fit_window_start = pd.Timestamp(timestamps[0])
        self._fit_window_end = pd.Timestamp(timestamps[-1])
        assert self._fit_window_start < self._fit_window_end

        if self._method == 'conventional':
            # $$z = \frac{x - \mu}{\sigma}$$
            self._mu = values.mean()
            self._sig = values.std()
            if self._sig == 0:
                raise ValueError('fit 窗口内因子值零方差，无法进行 zscore。')

        elif self._method == 'robust':
            #$$z_{robust} = \frac{x - \text{median}(x)}{1.4826 \times \text{MAD}(x)}$$
            # 其中 $\text{MAD} = \text{median}(|x - \text{median}(x)|)$，
            # 系数 $1.4826$ 是正态分布下 $\sigma / \text{MAD}$ 的理论比值，
            # 让正常数据的 robust z 与普通 z 尺度可比。
            self._median = values.median()
            self._mad = float((values - self._median).abs().median())
            if self._mad == 0:
                raise ValueError('fit 窗口内因子值 MAD 为零，无法进行 robust zscore。')

    def transform(
            self, 
            values: pd.Series, 
            context: Mapping[str, pd.Series | pd.DataFrame] | None = None
    ) -> PreprocessingResult:
        """根据固定窗口上的 fitted params , 将窗口外的 values 转换为 zscore 。"""
        assert context is None
        values = values.copy()
        # 1. 检验是否已经 fit 过
        if self._fit_window_start is None or self._fit_window_end is None:
            raise RuntimeError(f'未 fit 过，不得直接 transform。')

        # 2. 检验 fit 因子的名称是否等于 transform 因子的名称
        transform_name = values.name
        if transform_name != self._fit_name:
            warning = (f'fit 所用因子名为 {self._fit_name}'
            f', transform 因子名为 {transform_name} , 两者不一致。'
            )
            self._warnings.append(warning)

        # 3. 检验 transform 窗口和 fit 窗口是否重叠
        trans_window_start = pd.Timestamp(values.index.get_level_values('timestamp').min())
        assert trans_window_start > self._fit_window_end, \
        f'transform 窗口最早值 {trans_window_start} '
        f'早于等于 fit 窗口最晚 {self._fit_window_end}。'

        # 4. 开始 zscore 化
        if self._method == 'conventional':
            scaled = (values - self._mu) / self._sig

        elif self._method == 'robust':
            scaled = (values - self._median) / (1.4826 * self._mad)

        metadata: dict[str, Any] = {
            'fit_window_start': self._fit_window_start,
            'fit_window_end': self._fit_window_end,
        }
        if self._method == 'conventional':
            metadata['mu'] = self._mu
            metadata['sig'] = self._sig
        else:
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
            context: Mapping[str, pd.Series | pd.DataFrame] | None = None
    ) -> PreprocessingResult:
        assert context is None
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

        grp = values.groupby(level='timestamp')
        if self._method == 'conventional':
            metadata = {'mu': grp.mean(),'sig': grp.std()}
        else:
            mad = grp.apply(lambda s: (s - s.median()).abs().median())
            mad.name = 'mad'
            metadata = {'median': grp.median(), 'mad': mad}
        return PreprocessingResult(
            values = scaled,
            warnings = warnings,
            metadata = metadata
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {'method': self._method}


# =================================================
# simple inline test
# =================================================
import numpy as np

# --------------------------------- FittedTimeSeriesZscore ----------------------------
# 构造 MultiIndex[timestamp, product]，product 仅 'RB'
fit_timestamp = pd.date_range('2026-07-01', '2026-07-30', freq='D')
trans_timestamp = pd.date_range('2026-08-01', '2026-08-20', freq='D')

fit_index = pd.MultiIndex.from_product([fit_timestamp, ['RB']], names=['timestamp', 'product'])
trans_index = pd.MultiIndex.from_product([trans_timestamp, ['RB']], names=['timestamp', 'product'])

# 确定性数据，便于精确断言
fit_values = np.linspace(-2.0, 2.0, len(fit_index))
trans_values = np.linspace(-1.0, 1.0, len(trans_index))

fit_s = pd.Series(fit_values, index=fit_index, name='test_factor')
trans_s = pd.Series(trans_values, index=trans_index, name='test_factor')

print('-------------- method = conventional -------------------')
winsorizer = FittedTimeSeriesZscore(method='conventional')
winsorizer.fit(fit_s)
s_trans = winsorizer.transform(trans_s)

expected_mu = fit_values.mean()
expected_sig = fit_values.std(ddof=1)
assert winsorizer._mu == expected_mu and winsorizer._sig == expected_sig
expected_z = (trans_values[0] - expected_mu) / expected_sig
assert s_trans.values.iloc[0] == expected_z, \
    f'conventional zscore 失败: {s_trans.values.iloc[0]} != {expected_z}'
print(f'fit mu={expected_mu:.4f} sig={expected_sig:.4f}')
print(f'transform 首个值: {s_trans.values.iloc[0]:.4f} (期望 {expected_z:.4f})')
print(f'metadata: \n {s_trans.metadata}')
print(f'parameters: \n {winsorizer.parameters}')

print('-------------- method = robust -------------------')
winsorizer = FittedTimeSeriesZscore(method='robust')
winsorizer.fit(fit_s)
s_trans = winsorizer.transform(trans_s)

expected_median = np.median(fit_values)
expected_mad = np.median(np.abs(fit_values - expected_median))
assert winsorizer._median == expected_median and winsorizer._mad == expected_mad
expected_z = (trans_values[0] - expected_median) / (1.4826 * expected_mad)
assert s_trans.values.iloc[0] == expected_z, \
    f'robust zscore 失败: {s_trans.values.iloc[0]} != {expected_z}'
print(f'fit median={expected_median:.4f} mad={expected_mad:.4f}')
print(f'transform 首个值: {s_trans.values.iloc[0]:.4f} (期望 {expected_z:.4f})')
print(f'metadata: \n {s_trans.metadata}')

print('-------------- method = 零方差报错 -------------------')
const_s = pd.Series(np.full(len(fit_index), 1.0), index=fit_index, name='test_factor')
try:
    FittedTimeSeriesZscore(method='conventional').fit(const_s)
    raise AssertionError('应抛出 ValueError，实际未抛出')
except ValueError as e:
    print(f'已按预期抛出 ValueError: {e}')

try:
    FittedTimeSeriesZscore(method='robust').fit(const_s)
    raise AssertionError('应抛出 ValueError，实际未抛出')
except ValueError as e:
    print(f'已按预期抛出 ValueError: {e}')

# --------------------------------- CrossSectionalZscore ----------------------------
print('-------------- CrossSectionalZscore: conventional -------------------')
# 构造 MultiIndex[timestamp, product]，10 个交易日 × 5 个品种
timestamp = pd.date_range('2026-07-01', '2026-07-10', freq='D')
products = ['AP', 'AD', 'RB', 'A', 'ZN']
index = pd.MultiIndex.from_product([timestamp, products], names=['timestamp', 'product'])

# 确定性数据
np.random.seed(0)
values = np.random.randn(len(index))
s = pd.Series(values, index=index, name='test_factor')

cs = CrossSectionalZscore(method='conventional')
s_trans = cs.transform(s)

# 断言单格缩放值 == (x - mu) / sig，mu/sig 取该 timestamp 截面
ts0 = pd.Timestamp('2026-07-01')
cross = s.xs(ts0, level='timestamp')
expected_mu = cross.mean()
expected_sig = cross.std()
for product in products:
    expected_z = (s.loc[(ts0, product)] - expected_mu) / expected_sig
    assert s_trans.values.loc[(ts0, product)] == expected_z, \
        f'conventional 截面 zscore 失败: {product}'
print(f'2026-07-01 截面 mu={expected_mu:.4f} sig={expected_sig:.4f}')
print(f'metadata mu: \n {s_trans.metadata["mu"].to_string()}')
print(f'metadata sig: \n {s_trans.metadata["sig"].to_string()}')
print(f'warnings: {s_trans.warnings}')

print('-------------- CrossSectionalZscore: robust -------------------')
cs = CrossSectionalZscore(method='robust')
s_trans = cs.transform(s)

cross = s.xs(ts0, level='timestamp')
expected_median = cross.median()
expected_mad = np.median(np.abs(cross.values - expected_median))
for product in products:
    expected_z = (s.loc[(ts0, product)] - expected_median) / (1.4826 * expected_mad)
    assert s_trans.values.loc[(ts0, product)] == expected_z, \
        f'robust 截面 zscore 失败: {product}'
print(f'2026-07-01 截面 median={expected_median:.4f} mad={expected_mad:.4f}')
print(f'metadata median: \n {s_trans.metadata["median"].to_string()}')
print(f'metadata mad: \n {s_trans.metadata["mad"].to_string()}')
print(f'warnings: {s_trans.warnings}')

print('-------------- CrossSectionalZscore: 零方差保留原值 -------------------')
# 2026-07-05 全部品种同值（零方差）；2026-07-06 仅 1 个非缺失值（sig=NaN）
bad_ts1 = pd.Timestamp('2026-07-05')
bad_ts2 = pd.Timestamp('2026-07-06')
s_bad = s.copy()
for product in products:
    s_bad.loc[(bad_ts1, product)] = 3.0
for product in products[1:]:
    s_bad.loc[(bad_ts2, product)] = np.nan

s_trans = CrossSectionalZscore(method='conventional').transform(s_bad)
# 零方差截面保留原值
for product in products:
    assert s_trans.values.loc[(bad_ts1, product)] == 3.0
# sig=NaN 截面保留原值
for product in products[1:]:
    assert np.isnan(s_trans.values.loc[(bad_ts2, product)])
assert not np.isnan(s_trans.values.loc[(bad_ts2, products[0])])
# warnings 应包含两个异常 timestamp
warn_text = '\n  '.join(s_trans.warnings)
assert '2026-07-05' in warn_text and '2026-07-06' in warn_text, warn_text
print(f'零方差及 NaN 截面已保留原值，且 warnings 覆盖两日: \n  {warn_text}')