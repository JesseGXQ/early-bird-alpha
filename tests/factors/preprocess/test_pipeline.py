"""tests/factors/preprocess/test_pipeline.py — 预处理流水线组合测试。

通过 PreprocessingPipeline 串联多组件，验证各预处理组件及其组合能否正常
运行，并对输出做独立手算对照（参考实现使用原子 pandas/numpy 操作，不依赖
组件内部实现路径）。

组合矩阵：
    P1-A 截面主链路：CS Missing -> CS Winsor -> Vol Neutralizer(devision) -> CS Zscore
    P2-B 纯时序链路：SMA -> RTS Missing -> RTS Winsor -> RTS Zscore(robust)
    P3-C 板块+排名链路：CS Missing -> CS Winsor -> Sector Neutralizer -> Rank Normalizer(normal)
    P4-D 流动性链路：RTS Missing -> RTS Winsor -> Liquidity Neutralizer -> CS Zscore(robust)
    P5-E 防未来函数链路：CS Missing -> FittedTimeSeriesZscore(fit 段 + transform 段)

另含：process() 结构/config 断言、neutralizer 无状态污染断言、零方差警告传播用例。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import norm

from factors.preprocess.base import PreprocessingPipeline, clean_multiindex
from factors.preprocess.clean import (
    CrossSectionalMissingHandler, RollingTimeSeriesMissingHandler,
)
from factors.preprocess.winsorize import (
    CrossSectionalWinsorizer, RollingTimeseriesWinsorizer,
)
from factors.preprocess.scale import (
    CrossSectionalZscore, CrossSectionalRankNormalizer, FittedTimeSeriesZscore,
)
from factors.preprocess.smooth import SMASmoother
from factors.preprocess.modify import RollingTimeSeriesZscore
from factors.preprocess.neutralize import (
    SectorDataConstructor, SectorExposureNeutralizer,
    VolatilityDataConstructor, VolatilityExposureNeutralizer,
    LiquidityDataConstructor, LiquidityExposureNeutralizer,
)

# 与 conftest.py 对齐的注入坐标
WARMUP = 20
NAN_TS = 25
COLLIN_TS = 45
VOL0_TS = 15
MISS5 = 5
MISS10 = 10
MISS30 = 30
OUT20 = 20
OUT40 = 40
APPROX_ATOL = 1e-9

LOW_Q, HIGH_Q = 0.01, 0.99


# ============================================================
# 参考实现（原子 pandas/numpy 链式手算，与组件实现路径解耦）
# ============================================================
def _ref_cs_impute_median(s: pd.Series) -> pd.Series:
    return s.groupby(level='timestamp', group_keys=False).apply(
        lambda g: g.fillna(g.median()))


def _ref_cs_winsor(s: pd.Series, lo=LOW_Q, up=HIGH_Q) -> pd.Series:
    def f(g: pd.Series) -> pd.Series:
        return g.clip(g.quantile(lo), g.quantile(up))
    return s.groupby(level='timestamp', group_keys=False).apply(f)


def _ref_cs_zscore(s: pd.Series, robust: bool = False) -> pd.Series:
    def f(g: pd.Series) -> pd.Series:
        if robust:
            loc = g.median()
            scale = 1.4826 * (g - g.median()).abs().median()
        else:
            loc, scale = g.mean(), g.std()
        if pd.isna(scale) or (scale == 0):
            return g  # 截面统计量不可用，保留原值
        return (g - loc) / scale
    return s.groupby(level='timestamp', group_keys=False).apply(f)


def _ref_sma(s: pd.Series, window: int = 3) -> pd.Series:
    return clean_multiindex(s.groupby(level='product')
                            .rolling(window, min_periods=window)
                            .agg('mean'))


def _ref_rts_impute_median(s: pd.Series, window: int = WARMUP) -> pd.Series:
    out = s.copy()
    shifted = out.groupby(level='product').shift(1)
    fill = clean_multiindex(shifted.groupby(level='product')
                            .rolling(window, min_periods=1)
                            .agg('median'))
    out.loc[out.isna()] = fill
    return out


def _ref_rts_winsor(s: pd.Series, window: int = WARMUP,
                    lo=LOW_Q, up=HIGH_Q) -> pd.Series:
    lower = clean_multiindex(s.groupby(level='product')
                             .rolling(window, min_periods=window)
                             .quantile(lo)).reindex(s.index)
    upper = clean_multiindex(s.groupby(level='product')
                             .rolling(window, min_periods=window)
                             .quantile(up)).reindex(s.index)
    clipped = s.clip(lower=lower, upper=upper)
    return clipped.where(lower.notna(), s)


def _ref_rts_zscore_robust(s: pd.Series, window: int = WARMUP) -> pd.Series:
    median = clean_multiindex(s.groupby(level='product')
                              .rolling(window, min_periods=window)
                              .agg('median')).reindex(s.index)
    mad = clean_multiindex(s.groupby(level='product')
                           .rolling(window, min_periods=window)
                           .apply(lambda w: 1.4826 * (w - w.median()).abs().median())).reindex(s.index)
    return (s - median) / mad


def _ref_sector_resid(s: pd.Series, sector_map: dict) -> pd.Series:
    """逐截面无截距虚拟变量回归残差（与组件同用 np.linalg.lstsq 数值路径）。"""
    parts = []
    for _t, cross in s.groupby(level='timestamp'):
        prods = cross.index.get_level_values('product')
        dummies = pd.get_dummies(
            pd.Series([sector_map[p] for p in prods], index=cross.index))
        X = dummies.values.astype(float)
        beta = np.linalg.lstsq(X, cross.values, rcond=None)[0]
        parts.append(pd.Series(cross.values - X @ beta, index=cross.index, name=cross.name))
    return pd.concat(parts)


def _ref_rank_normal(s: pd.Series):
    u = (s.rank() - 0.5) / len(s)
    return pd.Series(norm.ppf(u.values), index=u.index, name=s.name), u


def _ref_liquidity_resid(dataset, y: pd.Series) -> pd.Series:
    lv = dataset.main_panel['Volume'].reindex(y.index).replace({0: np.nan}).apply(np.log)
    df = pd.DataFrame({'y': y, 'lv': lv}, index=y.index)
    parts = []
    for _t, g in df.groupby(level='timestamp'):
        gv = g.dropna()
        if gv.empty or gv['lv'].nunique() <= 1:  # code1 空截面 / code2 常数 X
            parts.append(pd.Series(np.nan, index=g.index))
            continue
        X = sm.add_constant(gv['lv'], has_constant='raise')
        beta = np.linalg.lstsq(X, gv['y'], rcond=None)[0]
        parts.append((gv['y'] - X @ beta).reindex(g.index))
    return pd.concat(parts)


def _assert_series_close(got: pd.Series, exp: pd.Series) -> None:
    """按标签对齐比较：exp 非 NaN 处 got 必须一致且非 NaN。"""
    g = got.sort_index().dropna()
    e = exp.sort_index().dropna()
    assert g.index.equals(e.index), f'索引不一致: {got.index.names} vs {exp.index.names}'
    assert np.allclose(g.values, e.values, atol=APPROX_ATOL), \
        f'数值不一致，max(exp-got)={np.max(np.abs(exp.sort_index().dropna().values - got.sort_index().dropna().values))}'


# ============================================================
# P1-A 截面主链路
# ============================================================
def test_p1_cs_missing_winsor_voldev_zscore_chain(dataset, v_dirty, ts):
    pipe = PreprocessingPipeline([
        CrossSectionalMissingHandler(policy='impute', method='median'),
        CrossSectionalWinsorizer(LOW_Q, HIGH_Q),
        VolatilityExposureNeutralizer(
            VolatilityDataConstructor(dataset), method='devision', n=WARMUP),
        CrossSectionalZscore(method='conventional'),
    ])
    out, warns, meta = pipe.process(v_dirty)

    assert out.index.equals(v_dirty.index)
    assert out.index.names == ['timestamp', 'product']

    # 参考链手算：impute -> winsor -> devision -> zscore
    imputed = _ref_cs_impute_median(v_dirty)
    win = _ref_cs_winsor(imputed)
    ret = dataset.main_panel['True_Return']
    shifted = ret.groupby(level='product').shift(1)
    vol = clean_multiindex(shifted.groupby(level='product')
                           .rolling(WARMUP, min_periods=WARMUP).agg('std'))
    dev = win / vol.reindex(win.index)
    ref = _ref_cs_zscore(dev)
    _assert_series_close(out, ref)

    # 暖机：前 WARMUP-1 个 tick 全 NaN（波动率窗口未满）
    for i in range(WARMUP - 1):
        assert out.loc[ts[i]].isna().all(), f'tick[{i}] 应为 NaN'

    # 全 NaN 截面保持 NaN，且 zscore 生成警告
    assert out.loc[ts[NAN_TS]].isna().all()
    assert any('截面统计量不可用' in w for w in warns['cs_zscore_conventional'])

    # 缺失点在截面填充后恢复为有限值（暖机区之外的位置）
    assert out.loc[ts[MISS30]].notna().all(), f'{ts[MISS30]} 处应被填充'

    # 极端值已被截面缩尾吸收
    assert abs(out.loc[(ts[OUT20], 'RB')]) < 5
    assert abs(out.loc[(ts[OUT40], 'CU')]) < 5

    # warnings / metadata 键齐全
    names = ['cs_missing_handler_impute_median', 'cs_winsorizer_0.01_0.99',
             'volatility_exposure_neutralizer_devision_20', 'cs_zscore_conventional']
    assert set(warns) == set(names)
    assert set(meta) == set(names)


# ============================================================
# P2-B 纯时序链路
# ============================================================
def test_p2_sma_rtsmissing_winsor_rtszscore_chain(v_dirty, ts):
    pipe = PreprocessingPipeline([
        SMASmoother(window=3),
        RollingTimeSeriesMissingHandler(
            policy='impute', method='median', window=WARMUP, min_periods=1),
        RollingTimeseriesWinsorizer(LOW_Q, HIGH_Q, WARMUP),
        RollingTimeSeriesZscore(method='robust', window=WARMUP),
    ])
    out, warns, meta = pipe.process(v_dirty)

    assert out.index.equals(v_dirty.index)

    sma = _ref_sma(v_dirty, 3)
    imp = _ref_rts_impute_median(sma, WARMUP)
    win = _ref_rts_winsor(imp, WARMUP)
    ref = _ref_rts_zscore_robust(win, WARMUP)
    _assert_series_close(out, ref)

    # 暖机：每品种前 WARMUP-1 个观测全 NaN（滚动窗口未满）
    for i in range(WARMUP - 1):
        assert out.loc[ts[i]].isna().all(), f'tick[{i}] 应为 NaN'
    # 首 tick 仍为 NaN（SMA 暖机产生的缺失无法被时序填充回）
    assert out.loc[ts[0]].isna().all()

    # 全 NaN 截面会被时序 impute 用前序中位数填充，最终为有限值
    assert out.loc[ts[NAN_TS]].notna().all(), f'{ts[NAN_TS]} 应被时序填充为非 NaN'

    # 缺失点经链后为有限值（暖机区之外的位置）
    assert not np.isnan(out.loc[(ts[MISS30], 'MA')])

    for nm in ('sma_smoother_3', 'rts_missing_handler_impute_median_20',
               'rts_winsorizer_0.01_0.99_20', 'rts_zscore_robust_20'):
        assert nm in warns and nm in meta


# ============================================================
# P3-C 板块中性化 + 高斯排名
# ============================================================
def test_p3_sector_rank_normal_chain(dataset, v_clean):
    pipe = PreprocessingPipeline([
        CrossSectionalMissingHandler(policy='impute', method='median'),
        CrossSectionalWinsorizer(LOW_Q, HIGH_Q),
        SectorExposureNeutralizer(SectorDataConstructor(dataset), level='sector'),
        CrossSectionalRankNormalizer(method='normal'),
    ])
    out, warns, meta = pipe.process(v_clean)

    assert out.index.equals(v_clean.index)

    imputed = _ref_cs_impute_median(v_clean)
    win = _ref_cs_winsor(imputed)
    sector_map = dataset.auxiliary['classification'].set_index('product')['sector'].to_dict()
    resid = _ref_sector_resid(win, sector_map)
    ref, u = _ref_rank_normal(resid)
    _assert_series_close(out, ref)

    assert out.notna().all(), '干净数据链路不应产生 NaN'
    assert meta['cs_rank_normalizer_normal']['u'].index.equals(v_clean.index)
    assert warns['sector_exposure_neutralizer_sector'] == []

    # 抽查一个截面：残差 == x - sector 均值
    t = win.index.get_level_values('timestamp')[20]
    cross = win.xs(t, level='timestamp')
    grp = pd.Series([sector_map[p] for p in cross.index], index=cross.index)
    exp = (cross - cross.groupby(grp).transform('mean')).sort_index().values
    got = resid.xs(t, level='timestamp').sort_index().values
    assert np.allclose(got, exp, atol=APPROX_ATOL)


# ============================================================
# P4-D 流动性回归中性化链路
# ============================================================
def test_p4_rts_winsor_liquidity_robustz_chain(dataset, v_dirty, ts):
    liq_name = f"liquidity_exposure_neutralizer_{['Volume']}"
    pipe = PreprocessingPipeline([
        RollingTimeSeriesMissingHandler(
            policy='impute', method='median', window=WARMUP, min_periods=1),
        RollingTimeseriesWinsorizer(LOW_Q, HIGH_Q, WARMUP),
        LiquidityExposureNeutralizer(
            LiquidityDataConstructor(dataset), liquidity_vars=['Volume']),
        CrossSectionalZscore(method='robust'),
    ])
    out, warns, meta = pipe.process(v_dirty)

    assert out.index.equals(v_dirty.index)

    imp = _ref_rts_impute_median(v_dirty, WARMUP)
    win = _ref_rts_winsor(imp, WARMUP)
    liqui = _ref_liquidity_resid(dataset, win)
    ref = _ref_cs_zscore(liqui, robust=True)
    _assert_series_close(out, ref)

    # Volume=0 行：log(0)->NaN -> 残差与最终输出该行 NaN
    assert np.isnan(liqui.loc[(ts[VOL0_TS], 'AP')])
    assert np.isnan(out.loc[(ts[VOL0_TS], 'AP')])

    # 常数 log(Volume) 截面 -> 全 NaN + code2 共线警告
    assert liqui.loc[ts[COLLIN_TS]].isna().all()
    assert any('共线' in w for w in warns[liq_name])

    # 缺失点经链后为有限值
    assert not np.isnan(out.loc[(ts[MISS5], 'RB')])
    assert not np.isnan(out.loc[(ts[MISS30], 'MA')])


# ============================================================
# P5-E 防未来函数链路（fit-once 时序 zscore）
# ============================================================
def test_p5_fitted_ts_zscore_fit_then_transform(dataset, v_train, v_test):
    fitter = FittedTimeSeriesZscore(values_for_fit=v_train, method='conventional')
    pipe = PreprocessingPipeline([
        CrossSectionalMissingHandler(policy='impute', method='median'),
        fitter,
    ])
    out, warns, meta = pipe.process(v_test)

    assert out.index.equals(v_test.index)

    # fit 统计量：per-product mu/sig 与 v_train 分组统计一致
    mu = v_train.groupby(level='product').mean().sort_index()
    sig = v_train.groupby(level='product').std().sort_index()
    assert np.allclose(fitter._mu.sort_index().values, mu.values)
    assert np.allclose(fitter._sig.sort_index().values, sig.values)

    # transform: (x - mu) / sig（按品种映射 fit 统计量）
    prods = v_test.index.get_level_values('product')
    mu_map = pd.Series(pd.Series(prods).map(mu).values, index=v_test.index)
    sig_map = pd.Series(pd.Series(prods).map(sig).values, index=v_test.index)
    ref = (v_test - mu_map) / sig_map
    _assert_series_close(out, ref)

    # 名称一致则无警告，metadata 含 fit 窗口
    assert warns['fitted_ts_zscore_conventional'] == []
    assert 'fit_windows' in meta['fitted_ts_zscore_conventional']


# ============================================================
# 通用行为：结构 / config / 无状态污染 / 警告传播
# ============================================================
def test_all_pipelines_preserve_input_index(dataset, v_clean, v_dirty, v_train, v_test):
    """通用不变量：所有 pipeline 的 process() 输出索引顺序与输入 values 完全一致。"""
    builders = {
        'p1': lambda: PreprocessingPipeline([
            CrossSectionalMissingHandler(policy='impute', method='median'),
            CrossSectionalWinsorizer(LOW_Q, HIGH_Q),
            VolatilityExposureNeutralizer(
                VolatilityDataConstructor(dataset), method='devision', n=WARMUP),
            CrossSectionalZscore(method='conventional'),
        ]),
        'p2': lambda: PreprocessingPipeline([
            SMASmoother(window=3),
            RollingTimeSeriesMissingHandler(
                policy='impute', method='median', window=WARMUP, min_periods=1),
            RollingTimeseriesWinsorizer(LOW_Q, HIGH_Q, WARMUP),
            RollingTimeSeriesZscore(method='robust', window=WARMUP),
        ]),
        'p3': lambda: PreprocessingPipeline([
            CrossSectionalMissingHandler(policy='impute', method='median'),
            CrossSectionalWinsorizer(LOW_Q, HIGH_Q),
            SectorExposureNeutralizer(SectorDataConstructor(dataset), level='sector'),
            CrossSectionalRankNormalizer(method='normal'),
        ]),
        'p4': lambda: PreprocessingPipeline([
            RollingTimeSeriesMissingHandler(
                policy='impute', method='median', window=WARMUP, min_periods=1),
            RollingTimeseriesWinsorizer(LOW_Q, HIGH_Q, WARMUP),
            LiquidityExposureNeutralizer(
                LiquidityDataConstructor(dataset), liquidity_vars=['Volume']),
            CrossSectionalZscore(method='robust'),
        ]),
        'p5': lambda: PreprocessingPipeline([
            CrossSectionalMissingHandler(policy='impute', method='median'),
            FittedTimeSeriesZscore(values_for_fit=v_train, method='conventional'),
        ]),
    }
    cases = [
        ('p1', v_dirty),
        ('p2', v_dirty),
        ('p3', v_clean),
        ('p4', v_dirty),
        ('p5', v_test),
    ]
    for label, values in cases:
        out, _, _ = builders[label]().process(values)
        assert out.index.equals(values.index), \
            f'{label}: 输出索引与输入顺序不一致'
        assert out.index.names == values.index.names, \
            f'{label}: 输出索引名不一致'


def test_process_output_structure_and_config(dataset, v_clean):
    pipe = PreprocessingPipeline([
        CrossSectionalMissingHandler(policy='impute', method='median'),
        CrossSectionalWinsorizer(LOW_Q, HIGH_Q),
    ])
    assert pipe.config == {
        'cs_missing_handler_impute_median': {'policy': 'impute', 'method': 'median'},
        'cs_winsorizer_0.01_0.99': {'lower_quantile': LOW_Q, 'upper_quantile': HIGH_Q},
    }

    vals, warnings, metadata = pipe.process(v_clean)
    assert isinstance(vals, pd.Series)
    assert vals.index.equals(v_clean.index), '输出索引顺序与输入不一致'
    assert isinstance(warnings, dict) and isinstance(metadata, dict)
    assert list(warnings.keys()) == ['cs_missing_handler_impute_median',
                                     'cs_winsorizer_0.01_0.99']
    assert metadata['cs_missing_handler_impute_median']['is_imputed'].index.equals(v_clean.index)
    assert not metadata['cs_missing_handler_impute_median']['is_imputed'].any()

    # 同一 pipeline 连续 process 不应累积状态（neutralize 逐次 clear）
    nz = VolatilityExposureNeutralizer(
        VolatilityDataConstructor(dataset), method='regression', n=WARMUP)
    p2 = PreprocessingPipeline([nz])
    _w1 = p2.process(v_clean)[1]
    _w2 = p2.process(v_clean)[1]
    assert _w1 == _w2


def test_constant_segment_generates_rts_zscore_warning():
    """常数段触发 RollingTimeSeriesZscore 的零方差警告并传播到 pipeline。"""
    ts = pd.date_range('2026-07-01', periods=10, freq='D')
    parts = []
    for p, arr in [('RB', np.full(10, 5.0)), ('CU', np.linspace(0, 1, 10))]:
        idx = pd.MultiIndex.from_product(
            [ts, [p]], names=['timestamp', 'product'])
        parts.append(pd.Series(arr, index=idx, name='g'))
    s = pd.concat(parts).sort_index()

    pipe = PreprocessingPipeline([RollingTimeSeriesZscore(method='conventional', window=3)])
    out, warns, meta = pipe.process(s)

    assert out.index.equals(s.index), '输出索引顺序与输入不一致'
    assert any('标准差统计量不可用' in w for w in warns['rts_zscore_conventional_3'])
    # 首个满窗（RB 第二个窗口即满）为常数段：输出 NaN
    rb = out.loc[(slice(None), 'RB')]
    assert np.isnan(rb.iloc[2])
    assert set(meta['rts_zscore_conventional_3']['mu'].index) == set(s.index)