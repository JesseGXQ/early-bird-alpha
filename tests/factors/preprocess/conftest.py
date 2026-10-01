"""tests/factors/preprocess/conftest.py — 预处理流水线测试的公共 fixture。

构造纯 synthetic 的 ResearchDataset 与多条因子序列（固定种子，完全可复现），
不依赖真实 db/ 数据。测试目录镜像包结构（tests/<package>/）。
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
import pytest

from factors.models import (
    ResearchDataset,
    DatasetSpec,
    DataQualitySummary,
)
from factors.universe import Universe

# ----------------------------------------------------------------
# 公共常量
# ----------------------------------------------------------------
RNG_SEED = 2026
N_DAYS = 60
PRODUCTS = ['RB', 'CU', 'AP', 'A', 'MA', 'ZN']
FACTOR_NAME = 'f'

# v_dirty 数据注入坐标（0-based timestamp 位置）
MISS5 = 5     # 缺失注入1
MISS10 = 10   # 缺失注入2
MISS30 = 30   # 缺失注入3
OUT20 = 20    # 极端值注入1
OUT40 = 40    # 极端值注入2
NAN_TS = 25   # 全 NaN 截面
VOL0_TS = 15  # main_panel 注入 Volume=0 的截面（品种 AP）
COLLIN_TS = 45 # main_panel 注入常数 Volume 的截面（与截距共线）

# 各组合的窗口参数
WARMUP = 20   # 滚动窗口（Vol devision / rts winsor / rts zscore）


@lru_cache(maxsize=1)
def _build_env() -> tuple[pd.DatetimeIndex, pd.MultiIndex, ResearchDataset]:
    """构造合成主面板与分类表的完整 ResearchDataset。"""
    ts = pd.date_range('2026-07-01', periods=N_DAYS, freq='D')
    idx = pd.MultiIndex.from_product([ts, PRODUCTS], names=['timestamp', 'product'])
    n = len(idx)

    rng = np.random.default_rng(RNG_SEED)
    main_panel = pd.DataFrame({
        'True_Return': rng.normal(0.0005, 0.01, n),
        'Volume': rng.integers(1000, 5000, n).astype(float),
    }, index=idx)

    # 注入流动性共线/缺失分支：一个 Volume=0（log(0)->NaN），一个常数 Volume 截面（code2）
    main_panel.loc[(ts[VOL0_TS], 'AP'), 'Volume'] = 0.0
    main_panel.loc[(ts[COLLIN_TS], slice(None)), 'Volume'] = 777.0

    class_df = pd.DataFrame([
        {'product': 'RB', 'asset_class': 'black', 'sector': 'black', 'subsector': 'steel'},
        {'product': 'CU', 'asset_class': 'nonferrous', 'sector': 'nonferrous', 'subsector': 'copper'},
        {'product': 'AP', 'asset_class': 'agri', 'sector': 'agri', 'subsector': 'fruit'},
        {'product': 'A',  'asset_class': 'agri', 'sector': 'agri', 'subsector': 'oilseed'},
        {'product': 'MA', 'asset_class': 'energy', 'sector': 'energy', 'subsector': 'chemical'},
        {'product': 'ZN', 'asset_class': 'nonferrous', 'sector': 'nonferrous', 'subsector': 'zinc'},
    ])

    quality = DataQualitySummary(
        is_valid=True,
        total_rows=n,
        product_sample_counts={p: N_DAYS for p in PRODUCTS},
        missing_rate_by_column={},
        warnings=(),
        errors=(),
    )
    spec = DatasetSpec(
        facade_config=(),
        products=tuple(PRODUCTS),
        data_requirements=(),
        start_date=ts[0].date(),
        end_date=ts[-1].date(),
    )
    dataset = ResearchDataset(
        spec=spec,
        main_panel=main_panel,
        quality=quality,
        universe=Universe(
            eligible=pd.Series(True, index=idx, name='universe'),
            exclude_reason=pd.Series('', index=idx, name='exclude_reason'),
        ),
        auxiliary={'classification': class_df},
    )
    return ts, idx, dataset


# ----------------------------------------------------------------
# session 级 fixture
# ----------------------------------------------------------------
@pytest.fixture(scope='session')
def ts():
    return _build_env()[0]


@pytest.fixture(scope='session')
def idx():
    return _build_env()[1]


@pytest.fixture(scope='session')
def dataset():
    return _build_env()[2]


@pytest.fixture(scope='session')
def v_clean(dataset):
    """干净的随机因子序列（name='f'），索引与面板一致。"""
    idx = dataset.main_panel.index
    rng = np.random.default_rng(RNG_SEED + 1)
    return pd.Series(rng.normal(0, 1, len(idx)), index=idx, name=FACTOR_NAME)


@pytest.fixture(scope='session')
def v_dirty(v_clean, ts):
    """注入缺失 / 极端值 / 全 NaN 截面的因子序列。"""
    v = v_clean.copy()
    v.loc[(ts[MISS5], 'RB')] = np.nan
    v.loc[(ts[MISS10], 'CU')] = np.nan
    v.loc[(ts[MISS30], 'MA')] = np.nan
    v.loc[(ts[OUT20], 'RB')] = 100.0
    v.loc[(ts[OUT40], 'CU')] = -100.0
    v.loc[(ts[NAN_TS], slice(None))] = np.nan
    return v


@pytest.fixture(scope='session')
def v_train(v_clean, ts):
    """fit 段（前 30 个交易日）。"""
    mask = v_clean.index.get_level_values('timestamp') < ts[30]
    return v_clean[mask]


@pytest.fixture(scope='session')
def v_test(v_clean, ts):
    """transform 段（后 30 个交易日，整体晚于 fit 窗口）。"""
    mask = v_clean.index.get_level_values('timestamp') >= ts[30]
    return v_clean[mask]