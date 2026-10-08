"""tests/factors/diagnostic_portfolio/test_risk.py — 风险估计测试。

覆盖开发方案 §11.2、§13、§24.6：

    - 单品种事前波动率窗口、min_periods、ddof=1、零波动
    - 滚动样本协方差：pairwise、缺失填零、对称化、PSD 修复、元数据
    - 无效品种剔除
    - 组合年化事前波动率、绝对数值容差空仓

全部使用 synthetic 收益序列，手工复算期望值。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.diagnostic_portfolio.models import RiskConfig
from factors.diagnostic_portfolio.risk import (
    EX_ANTE_VARIANCE_TOLERANCE,
    DiagnosticPortfolioRiskManager,
)

INDEX_NAMES = ('timestamp', 'product')
PRODUCTS = ['A', 'B']


# ============================================================
# 辅助
# ============================================================
def _append(mgr: DiagnosticPortfolioRiskManager,
            values: pd.DataFrame) -> None:
    """按时间戳逐期 append 收益。values: Index[timestamp], columns=product。"""
    for ts, row in values.iterrows():
        mgr.append(
            pd.Series(row.to_numpy(dtype=float), index=row.index),
            pd.Timestamp(ts),
        )


def _wide(rows: dict[str, list[float]],
          timestamps: list[str] | None = None) -> pd.DataFrame:
    timestamps = timestamps or [
        f'2024-01-0{i + 1}' for i in range(len(next(iter(rows.values()))))]
    return pd.DataFrame(rows, index=pd.to_datetime(timestamps))


def _config(**overrides) -> RiskConfig:
    kwargs = dict(volatility_lookback=3, volatility_min_periods=2)
    kwargs.update(overrides)
    return RiskConfig(**kwargs)


# ============================================================
# 单品种事前波动率
# ============================================================
def test_single_product_vol_min_periods() -> None:
    mgr = DiagnosticPortfolioRiskManager(_config(volatility_min_periods=3))
    assert np.isnan(mgr._ex_ante_vol_one_product(pd.Series([0.01, 0.02])))


def test_single_product_vol_constant_is_nan() -> None:
    mgr = DiagnosticPortfolioRiskManager(_config())
    assert np.isnan(mgr._ex_ante_vol_one_product(pd.Series([0.01, 0.01, 0.01])))


def test_single_product_vol_ddof_one() -> None:
    mgr = DiagnosticPortfolioRiskManager(_config())
    value = mgr._ex_ante_vol_one_product(pd.Series([0.0, 0.02]))
    assert value == pytest.approx(0.02 / np.sqrt(2.0))


def test_ex_ante_volatility_uses_last_lookback_window() -> None:
    mgr = DiagnosticPortfolioRiskManager(_config(volatility_lookback=2))
    _append(mgr, _wide({
        'A': [0.0, 0.02, 0.06],
        'B': [0.0, 0.04, 0.08],
    }))

    vol = mgr.ex_ante_volatility()

    assert vol.index.name == 'product'
    # 只用最近 2 期：std([0.02, 0.06]) = std([0.04, 0.08]) = 0.04/sqrt(2)
    assert vol['A'] == pytest.approx(0.04 / np.sqrt(2.0))
    assert vol['B'] == pytest.approx(0.04 / np.sqrt(2.0))


def test_return_window_keeps_only_last_lag() -> None:
    """回看窗口只保留最后 lag 期，且行序为时间升序。"""
    mgr = DiagnosticPortfolioRiskManager(_config())
    _append(mgr, _wide({
        'A': [0.01, 0.02, 0.03], 'B': [0.01, 0.02, 0.03]}))

    window = mgr._return_window(lag=2)

    assert list(window.index) == list(
        pd.to_datetime(['2024-01-02', '2024-01-03']))


def test_return_window_shape_and_labels() -> None:
    """窗口矩阵的形状、行列顺序与索引命名。"""
    mgr = DiagnosticPortfolioRiskManager(_config())
    _append(mgr, _wide({
        'A': [0.01, 0.02, 0.03], 'B': [0.04, 0.05, 0.06]}))

    window = mgr._return_window(lag=3)

    assert window.shape == (3, 2)
    assert list(window.columns) == ['A', 'B']
    assert window.index.name == 'timestamp'
    assert window.columns.name == 'product'
    assert window.to_numpy()[0].tolist() == [0.01, 0.04]
    assert window.to_numpy()[2].tolist() == [0.03, 0.06]


def test_return_window_keeps_nan_and_row_order() -> None:
    """缺失值保留为 NaN；行序跟随 append 的时间顺序，不做重排。"""
    mgr = DiagnosticPortfolioRiskManager(_config())
    for timestamp, a in (('2024-01-01', 0.01), ('2024-01-02', np.nan),
                         ('2024-01-03', 0.03)):
        mgr.append(pd.Series({'A': a, 'B': 0.02}, index=['A', 'B']),
                   pd.Timestamp(timestamp))

    window = mgr._return_window(lag=3)

    assert window.loc[pd.Timestamp('2024-01-02'), 'A'] != \
        window.loc[pd.Timestamp('2024-01-02'), 'A']  # 即确为 NaN
    assert window['A'].tolist()[0] == 0.01
    assert window['A'].tolist()[2] == 0.03


def test_append_rejects_inconsistent_product_index() -> None:
    """各期品种索引必须一致，否则窗口矩阵无法按列对齐。"""
    mgr = DiagnosticPortfolioRiskManager(_config())
    mgr.append(pd.Series({'A': 0.01, 'B': 0.02}, index=['A', 'B']),
               pd.Timestamp('2024-01-01'))

    with pytest.raises(ValueError, match='品种索引'):
        mgr.append(pd.Series({'A': 0.01, 'C': 0.02}, index=['A', 'C']),
                   pd.Timestamp('2024-01-02'))


# ============================================================
# 滚动样本协方差
# ============================================================
def test_covariance_matrix_diagonal_and_covariance() -> None:
    cfg = _config(covariance_lookback=3, covariance_min_periods=3)
    mgr = DiagnosticPortfolioRiskManager(cfg)
    ret_wide = _wide({
        'A': [0.01, 0.02, 0.03],
        'B': [0.02, 0.01, 0.04],
    })

    result = mgr._covariance_matrix_pairwise(ret_wide)
    cov = result.cov_mat

    assert list(cov.index) == ['A', 'B'] and list(cov.columns) == ['A', 'B']
    assert cov.loc['A', 'A'] == pytest.approx(
        ret_wide['A'].var(ddof=1))
    assert cov.loc['B', 'B'] == pytest.approx(
        ret_wide['B'].var(ddof=1))
    assert cov.loc['A', 'B'] == pytest.approx(1e-4)
    # 对称
    assert cov.loc['A', 'B'] == cov.loc['B', 'A']


def test_covariance_missing_pairs_filled_zero_and_counted() -> None:
    cfg = _config(covariance_lookback=4, covariance_min_periods=2)
    mgr = DiagnosticPortfolioRiskManager(cfg)
    # A 与 B 的观测区间完全不重叠 -> 成对协方差缺失，填零并计数。
    ret_wide = _wide({
        'A': [0.01, 0.02, np.nan, np.nan],
        'B': [np.nan, np.nan, 0.03, 0.04],
    })

    result = mgr._covariance_matrix_pairwise(ret_wide)

    assert result.cov_mat.loc['A', 'B'] == 0.0
    assert result.cov_mat.loc['B', 'A'] == 0.0
    assert result.metadata['pairwise_missing'] == 1
    assert result.metadata['valid_prod_count'] == 2


def test_covariance_repaired_matrix_is_psd_and_metadata_complete() -> None:
    cfg = _config(covariance_lookback=4, covariance_min_periods=3)
    mgr = DiagnosticPortfolioRiskManager(cfg)
    ret_wide = _wide({
        'A': [0.01, 0.02, 0.03, 0.02],
        'B': [0.02, 0.01, 0.04, 0.00],
    })

    result = mgr._covariance_matrix_pairwise(ret_wide)

    values = result.cov_mat.to_numpy()
    assert np.allclose(values, values.T)
    eigvals = np.linalg.eigvalsh(values)
    assert eigvals.min() >= -1e-12

    meta = result.metadata
    assert meta['sample_start'] == pd.Timestamp('2024-01-01')
    assert meta['sample_end'] == pd.Timestamp('2024-01-04')
    assert meta['valid_prod_count'] == 2
    assert isinstance(meta['pairwise_missing'], (int, np.integer))
    assert np.isfinite(meta['min_eigen'])
    assert isinstance(meta['clipped_eigen_count'], (int, np.integer))


def test_covariance_insufficient_observations_raises() -> None:
    cfg = _config(covariance_lookback=3, covariance_min_periods=2)
    mgr = DiagnosticPortfolioRiskManager(cfg)
    ret_wide = _wide({
        'A': [0.01, np.nan, np.nan],
        'B': [0.01, 0.02, 0.03],
    })

    with pytest.raises(ValueError, match='有效观测数不足'):
        mgr._covariance_matrix_pairwise(ret_wide)


def test_get_invalid_products() -> None:
    cfg = _config(covariance_lookback=3, covariance_min_periods=3)
    mgr = DiagnosticPortfolioRiskManager(cfg)
    # B 只有 2 个有效观测 < 3。
    _append(mgr, _wide({
        'A': [0.01, 0.02, 0.03],
        'B': [0.01, np.nan, 0.03],
    }))

    assert mgr.get_invalid_products() == ['B']


# ============================================================
# 组合年化事前波动率
# ============================================================
def test_portfolio_vol_annualized_value() -> None:
    cfg = _config(covariance_lookback=3, covariance_min_periods=3)
    mgr = DiagnosticPortfolioRiskManager(cfg)
    ret_wide = _wide({
        'A': [0.01, 0.02, 0.03],
        'B': [0.02, 0.01, 0.04],
    })
    _append(mgr, ret_wide)
    weights = pd.Series({'A': 0.5, 'B': -0.5})

    result = mgr.ex_ante_portfolio_volatility_annualized(
        weights=weights, annualization_factor=252)

    sigma = ret_wide.cov(ddof=1)
    expected = np.sqrt(252.0 * float(weights @ sigma @ weights))
    assert result.value == pytest.approx(expected)
    assert result.warnings == ()


def test_portfolio_vol_zero_variance_flat_with_warning() -> None:
    cfg = _config(covariance_lookback=3, covariance_min_periods=3)
    mgr = DiagnosticPortfolioRiskManager(cfg)
    # 常数收益 -> 协方差全零 -> 落在绝对容差内。
    _append(mgr, _wide({'A': [0.01, 0.01, 0.01], 'B': [0.01, 0.01, 0.01]}))
    weights = pd.Series({'A': 0.5, 'B': -0.5})

    result = mgr.ex_ante_portfolio_volatility_annualized(
        weights=weights, annualization_factor=252)

    assert result.value == 0.0
    assert len(result.warnings) == 1
    assert '数值容差' in result.warnings[0]
    assert str(EX_ANTE_VARIANCE_TOLERANCE) in result.warnings[0]


def test_portfolio_vol_returns_non_negative() -> None:
    cfg = _config(covariance_lookback=3, covariance_min_periods=3)
    mgr = DiagnosticPortfolioRiskManager(cfg)
    _append(mgr, _wide({'A': [0.01, 0.02, 0.03], 'B': [0.02, 0.01, 0.04]}))

    result = mgr.ex_ante_portfolio_volatility_annualized(
        weights=pd.Series({'A': 0.5, 'B': -0.5}), annualization_factor=252)

    assert result.value >= 0.0
