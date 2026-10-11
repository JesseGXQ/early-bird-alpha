"""tests/factors/diagnostic_portfolio/test_risk.py — 事前风险估计测试。

本模块内部一律以 numpy 数组承载窗口与结果，因此用例直接比对 ndarray；
pandas 只出现在两处：构造测试输入的宽表，以及 ``cov_mat_estimation_meta``
审计元数据。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.diagnostic_portfolio.models import RiskConfig
from factors.diagnostic_portfolio.risk import (
    DiagnosticPortfolioRiskManager,
    EX_ANTE_VARIANCE_TOLERANCE,
)


# ============================================================
# 辅助
# ============================================================
def _append(mgr: DiagnosticPortfolioRiskManager,
            values: pd.DataFrame) -> None:
    """按时间戳逐期 append 收益。values: Index[timestamp], columns=product。"""
    for ts, row in values.iterrows():
        mgr.append(row.to_numpy(dtype=float), pd.Timestamp(ts))


def _mgr(values: pd.DataFrame, **overrides) -> DiagnosticPortfolioRiskManager:
    """构造 risk manager 并灌入宽表；``overrides`` 传给 ``_config``。"""
    mgr = DiagnosticPortfolioRiskManager(_config(**overrides),
                                        n_products=values.shape[1])
    _append(mgr, values)
    return mgr


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
# 单品种事前波动率（按列向量化）
# ============================================================
def test_ex_ante_volatility_computes_per_column() -> None:
    """按列标准差；有效观测不足与非缺失值全相等时为 NaN。"""
    wide = _wide({
        'A': [0.01, 0.02, 0.03],
        'B': [0.01, np.nan, 0.03],
        'C': [0.01, 0.01, 0.01],
        'D': [0.01, np.nan, np.nan],
    })
    mgr = _mgr(wide, volatility_min_periods=2)

    vol = mgr.ex_ante_volatility()

    assert isinstance(vol, np.ndarray) and vol.shape == (4,)
    assert vol[0] == pytest.approx(np.std([0.01, 0.02, 0.03], ddof=1))
    assert vol[1] == pytest.approx(np.std([0.01, 0.03], ddof=1))
    assert np.isnan(vol[2])   # 常数列 -> 标准差无定义
    assert np.isnan(vol[3])   # 有效观测仅 1 期 < min_periods


def test_ex_ante_volatility_respects_min_periods() -> None:
    """min_periods 提高到 3 后，仅 2 期有效观测的品种转为 NaN。"""
    wide = _wide({'A': [0.01, np.nan, 0.03]})
    assert mgr_vol(wide, 2) == pytest.approx(np.std([0.01, 0.03], ddof=1))

    assert np.isnan(mgr_vol(wide, 3))


def mgr_vol(wide: pd.DataFrame, min_periods: int) -> float:
    """取单品种的事前波动率（辅助函数）。"""
    mgr = DiagnosticPortfolioRiskManager(
        _config(volatility_min_periods=min_periods), n_products=1)
    _append(mgr, wide)
    return float(mgr.ex_ante_volatility()[0])


def test_ex_ante_volatility_single_period_is_nan() -> None:
    """窗口不足 2 期时全部为 NaN（ddof=1 无定义）。"""
    wide = _wide({'A': [0.01]})
    mgr = _mgr(wide)

    assert np.isnan(mgr.ex_ante_volatility()[0])


def test_ex_ante_volatility_uses_last_lookback_window() -> None:
    mgr = _mgr(_wide({'A': [0.0, 0.02, 0.06], 'B': [0.0, 0.04, 0.08]}),
               volatility_lookback=2)

    vol = mgr.ex_ante_volatility()

    assert vol[0] == pytest.approx(0.04 / np.sqrt(2.0))
    assert vol[1] == pytest.approx(0.04 / np.sqrt(2.0))


# ============================================================
# 回看窗口（环形缓冲）
# ============================================================
def test_window_keeps_only_last_lag() -> None:
    mgr = _mgr(_wide({'A': [0.01, 0.02, 0.03], 'B': [0.01, 0.02, 0.03]}))

    window = mgr._window(lag=2)

    assert isinstance(window, np.ndarray)
    assert window.shape == (2, 2)
    np.testing.assert_allclose(window, [[0.02, 0.02], [0.03, 0.03]])


def test_window_shape_and_order() -> None:
    mgr = _mgr(_wide({'A': [0.01, 0.02, 0.03], 'B': [0.04, 0.05, 0.06]}))

    window = mgr._window(lag=3)

    assert window.shape == (3, 2)
    np.testing.assert_allclose(window[0], [0.01, 0.04])
    np.testing.assert_allclose(window[2], [0.03, 0.06])


def test_window_keeps_nan_and_row_order() -> None:
    """缺失值保留为 NaN；行序跟随 append 的时间顺序，不做重排。"""
    mgr = DiagnosticPortfolioRiskManager(_config(), n_products=2)
    for timestamp, a in (('2024-01-01', 0.01), ('2024-01-02', np.nan),
                         ('2024-01-03', 0.03)):
        mgr.append(np.array([a, 0.02]), pd.Timestamp(timestamp))

    window = mgr._window(lag=3)

    assert np.isnan(window[1, 0])
    assert window[0, 0] == 0.01
    assert window[2, 0] == 0.03


def test_window_takes_tail_in_time_order() -> None:
    """历史长于 lag 时只取尾部若干期，且仍是时间升序。"""
    mgr = DiagnosticPortfolioRiskManager(
        _config(volatility_lookback=2), n_products=1)
    for i in range(5):
        mgr.append(np.array([float(i)]),
                   pd.Timestamp('2024-01-01') + pd.Timedelta(days=i))

    window = mgr._window(lag=2)

    np.testing.assert_allclose(window.ravel(), [3.0, 4.0])


def test_window_before_any_append_is_empty() -> None:
    """尚未 append 时取窗返回空矩阵而非报错。"""
    mgr = DiagnosticPortfolioRiskManager(_config(), n_products=3)

    window = mgr._window(lag=3)

    assert window.shape == (0, 3)


# ============================================================
# 滚动样本协方差
# ============================================================
def test_covariance_matrix_diagonal_and_covariance() -> None:
    wide = _wide({'A': [0.01, 0.02, 0.03], 'B': [0.02, 0.01, 0.04]})
    mgr = _mgr(wide, covariance_lookback=3, covariance_min_periods=3)

    cov = mgr._covariance_matrix_pairwise(
        wide.to_numpy(dtype=float)).cov_mat

    assert cov.shape == (2, 2)
    assert cov[0, 0] == pytest.approx(wide['A'].var(ddof=1))
    assert cov[1, 1] == pytest.approx(wide['B'].var(ddof=1))
    assert cov[0, 1] == pytest.approx(1e-4)
    assert cov[0, 1] == cov[1, 0]


def test_covariance_missing_pairs_filled_zero_and_counted() -> None:
    # A 与 B 的观测区间完全不重叠 -> 成对协方差缺失，填零并计数。
    wide = _wide({'A': [0.01, 0.02, np.nan, np.nan],
                  'B': [np.nan, np.nan, 0.03, 0.04]})
    mgr = _mgr(wide, covariance_lookback=4, covariance_min_periods=2)

    result = mgr._covariance_matrix_pairwise(wide.to_numpy(dtype=float))

    assert result.cov_mat[0, 1] == 0.0
    assert result.cov_mat[1, 0] == 0.0
    assert result.metadata['pairwise_missing'] == 1
    assert result.metadata['valid_prod_count'] == 2


def test_covariance_repaired_matrix_is_psd_and_metadata_complete() -> None:
    wide = _wide({'A': [0.01, 0.02, 0.03, 0.02],
                  'B': [0.02, 0.01, 0.04, 0.00]})
    mgr = _mgr(wide, covariance_lookback=4, covariance_min_periods=3)

    result = mgr._covariance_matrix_pairwise(wide.to_numpy(dtype=float))

    values = result.cov_mat
    assert np.allclose(values, values.T)
    assert np.linalg.eigvalsh(values).min() >= -1e-12

    meta = result.metadata
    assert meta['sample_start'] == pd.Timestamp('2024-01-01')
    assert meta['sample_end'] == pd.Timestamp('2024-01-04')
    assert meta['valid_prod_count'] == 2
    assert isinstance(meta['pairwise_missing'], (int, np.integer))
    assert np.isfinite(meta['min_eigen'])
    assert isinstance(meta['clipped_eigen_count'], (int, np.integer))


def test_covariance_insufficient_observations_raises() -> None:
    wide = _wide({'A': [0.01, np.nan, np.nan], 'B': [0.01, 0.02, 0.03]})
    mgr = _mgr(wide, covariance_lookback=3, covariance_min_periods=2)

    with pytest.raises(ValueError, match='有效观测数不足'):
        mgr._covariance_matrix_pairwise(wide.to_numpy(dtype=float))


def test_invalid_mask() -> None:
    # B 只有 2 个有效观测 < 3。
    wide = _wide({'A': [0.01, 0.02, 0.03], 'B': [0.01, np.nan, 0.03]})
    mgr = _mgr(wide, covariance_lookback=3, covariance_min_periods=3)

    mask = mgr.invalid_mask()

    assert isinstance(mask, np.ndarray) and mask.dtype == bool
    assert mask.tolist() == [False, True]


# ============================================================
# 组合年化事前波动率
# ============================================================
def test_portfolio_vol_annualized_value() -> None:
    wide = _wide({'A': [0.01, 0.02, 0.03], 'B': [0.02, 0.01, 0.04]})
    mgr = _mgr(wide, covariance_lookback=3, covariance_min_periods=3)
    weights = np.array([0.5, -0.5])

    result = mgr.ex_ante_portfolio_volatility_annualized(
        weights=weights, annualization_factor=252)

    sigma = wide.cov(ddof=1)
    assert result.value == pytest.approx(
        np.sqrt(252.0 * float(weights @ sigma @ weights)))
    assert result.warnings == ()


def test_portfolio_vol_zero_variance_flat_with_warning() -> None:
    # 常数收益 -> 协方差全零 -> 落在绝对容差内。
    wide = _wide({'A': [0.01, 0.01, 0.01], 'B': [0.01, 0.01, 0.01]})
    mgr = _mgr(wide, covariance_lookback=3, covariance_min_periods=3)

    result = mgr.ex_ante_portfolio_volatility_annualized(
        weights=np.array([0.5, -0.5]), annualization_factor=252)

    assert result.value == 0.0
    assert len(result.warnings) == 1
    assert '数值容差' in result.warnings[0]
    assert str(EX_ANTE_VARIANCE_TOLERANCE) in result.warnings[0]


def test_portfolio_vol_subsetting_by_valid_mask() -> None:
    """valid_mask 选出的子集与其单独估计的结果一致。"""
    wide = _wide({'A': [0.01, 0.02, 0.03],
                  'B': [0.02, 0.01, 0.04],
                  'C': [0.01, np.nan, np.nan]})
    mgr = _mgr(wide, covariance_lookback=3, covariance_min_periods=2)
    mask = np.array([True, True, False])

    got = mgr.ex_ante_portfolio_volatility_annualized(
        weights=np.array([0.5, -0.5]), annualization_factor=252,
        valid_mask=mask)

    cov = mgr._covariance_matrix_pairwise(mgr._window(3)[:, :2]).cov_mat
    w = np.array([0.5, -0.5])
    assert got.value == pytest.approx(np.sqrt(252.0 * (w @ cov @ w)))