"""tests/factors/diagnostic_portfolio/test_weights.py — 权重状态机测试。

覆盖开发方案 §11、§12、§14、§24.3、§24.4 以及黄金路径 §20.1 / §20.2：

    - market_rank_ls：平均排名、去中心化、gross/net、冻结与强制退出、
      新品种等待、无效截面空仓、单品种上限 cap-and-redistribute
    - ts_inv_vol_gross_one / ts_inv_vol_target_vol：clip、逆波动率、
      gross-one、目标波动率缩放、max_gross、单品种 clip、退化空仓

所有用例仅使用 synthetic Series，风险管理器通过实例打桩提供确定的
波动率/协方差结果，从而可以逐项手工复算。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.diagnostic_portfolio.models import (
    ConstraintConfig,
    CostConfig,
    DiagnosticPortfolioSpec,
    RebalanceConfig,
    RiskConfig,
)
from factors.diagnostic_portfolio.risk import ExAntePortfolioVolatility
from factors.diagnostic_portfolio.weights import (
    MarketRankLSWeightStateMachine,
    TSInverseVolatilityWeightMachine,
)
from factors.profiles import (
    market_rank_ls,
    ts_inv_vol_gross_one,
    ts_inv_vol_target_vol,
)

INDEX_NAMES = ('timestamp', 'product')


# ============================================================
# 辅助
# ============================================================
def _rank_spec(*, max_product_weight=None, min_cs=2) -> DiagnosticPortfolioSpec:
    return DiagnosticPortfolioSpec(
        name='rank', harvest_profile=market_rank_ls,
        rebalance=RebalanceConfig(interval=1),
        constraint=ConstraintConfig(max_product_weight=max_product_weight),
        cost=CostConfig(rate=0.0),
        realized_volatility_lookback=3,
        min_cross_section_size=min_cs,
    )


def _gross_one_spec(*, max_product_weight=None) -> DiagnosticPortfolioSpec:
    return DiagnosticPortfolioSpec(
        name='gross_one', harvest_profile=ts_inv_vol_gross_one,
        rebalance=RebalanceConfig(interval=1),
        constraint=ConstraintConfig(max_product_weight=max_product_weight),
        cost=CostConfig(rate=0.0),
        realized_volatility_lookback=3,
        risk=RiskConfig(volatility_lookback=3, volatility_min_periods=2),
        clip_bound=1.0,
    )


def _target_vol_spec(*, max_gross=10.0, max_scaler=5.0,
                     max_product_weight=None) -> DiagnosticPortfolioSpec:
    return DiagnosticPortfolioSpec(
        name='target_vol', harvest_profile=ts_inv_vol_target_vol,
        rebalance=RebalanceConfig(interval=1),
        constraint=ConstraintConfig(
            max_gross=max_gross, max_volatility_scaler=max_scaler,
            max_product_weight=max_product_weight),
        cost=CostConfig(rate=0.0),
        realized_volatility_lookback=3,
        risk=RiskConfig(
            volatility_lookback=3, volatility_min_periods=2,
            covariance_lookback=3, covariance_min_periods=2,
            target_volatility=0.2),
        clip_bound=1.0,
    )


def _eligible(products, flags=None) -> pd.Series:
    return pd.Series(
        True if flags is None else flags, index=products, dtype=bool)


def _ret(products) -> pd.Series:
    """当期收益率：Index[product]。时间戳由 update(timestamp=...) 单独传入。"""
    return pd.Series(0.0, index=products)


_RET_TS = pd.Timestamp('2024-01-01')


def _same(left: pd.Series, right: pd.Series) -> None:
    pd.testing.assert_series_equal(
        left, right, check_names=False, check_dtype=False, atol=1e-12)


def _stub_uniform_vol(machine, mapping: dict[str, float]) -> None:
    """把风险管理器的单品种波动率替换为给定映射。"""
    machine._risk_manager.ex_ante_volatility = \
        lambda: pd.Series(mapping, dtype=float)


def _stub_cov(machine, value: float, warnings=()) -> None:
    """替换协方差组合波动率估计与无效品种判定。"""
    machine._risk_manager.get_invalid_products = lambda: []
    machine._risk_manager.ex_ante_portfolio_volatility_annualized = \
        lambda weights, annualization_factor: ExAntePortfolioVolatility(
            value=value,
            cov_mat_estimation_meta=pd.Series({'stub': 1.0}),
            warnings=warnings)


# ============================================================
# market_rank_ls
# ============================================================
def test_rank_rejects_wrong_profile() -> None:
    with pytest.raises(ValueError, match='market_rank_ls'):
        MarketRankLSWeightStateMachine(_gross_one_spec(), ['A', 'B'])


def test_rank_base_weights_golden_path() -> None:
    """§20.2：平均排名去中心化后 gross=1、net=0。"""
    products = ['A', 'B', 'C', 'D', 'E']
    machine = MarketRankLSWeightStateMachine(_rank_spec(), products)
    signal = pd.Series([10, 20, 20, 40, 50], index=products, dtype=float)

    result = machine.update(True, _eligible(products), signal).weights

    expected = pd.Series(
        [-1 / 3, -1 / 12, -1 / 12, 1 / 6, 1 / 3], index=products)
    _same(result, expected)
    assert result.sum() == pytest.approx(0.0, abs=1e-12)   # 市场中性
    assert result.abs().sum() == pytest.approx(1.0)         # gross=1


def test_rank_freeze_and_forced_exit() -> None:
    """非调仓日退出品种归零，其余冻结，且不向其他品种重分配。"""
    products = ['A', 'B', 'C', 'D', 'E']
    machine = MarketRankLSWeightStateMachine(_rank_spec(), products)
    signal = pd.Series([10, 20, 20, 40, 50], index=products, dtype=float)
    base = machine.update(True, _eligible(products), signal).weights

    eligible = _eligible(products, [True, False, True, True, True])
    frozen = machine.update(False, eligible, signal).weights

    expected = base.copy()
    expected['B'] = 0.0
    _same(frozen, expected)


def test_rank_new_product_waits_for_next_rebalance() -> None:
    products = ['A', 'B', 'C']
    machine = MarketRankLSWeightStateMachine(_rank_spec(), products)
    signal = pd.Series([10, 20, 30], index=products, dtype=float)

    base = machine.update(
        True, _eligible(products, [True, True, False]), signal).weights
    assert base['C'] == 0.0

    entered = machine.update(False, _eligible(products), signal).weights
    _same(entered, base)
    assert entered['C'] == 0.0  # 新进入品种待下一调仓日


def test_rank_insufficient_cross_section_goes_flat() -> None:
    products = ['A', 'B', 'C']
    machine = MarketRankLSWeightStateMachine(_rank_spec(min_cs=3), products)
    signal = pd.Series([1.0, np.nan, np.nan], index=products)

    result = machine.update(True, _eligible(products), signal).weights
    _same(result, pd.Series(0.0, index=products))


def test_rank_constant_signal_goes_flat() -> None:
    products = ['A', 'B', 'C']
    machine = MarketRankLSWeightStateMachine(_rank_spec(), products)
    signal = pd.Series([5.0, 5.0, 5.0], index=products)

    result = machine.update(True, _eligible(products), signal).weights
    _same(result, pd.Series(0.0, index=products))


def test_rank_failed_rebalance_records_state() -> None:
    products = ['A', 'B', 'C']
    machine = MarketRankLSWeightStateMachine(_rank_spec(), products)
    good = pd.Series([1.0, 2.0, 3.0], index=products)
    held = machine.update(True, _eligible(products), good).weights
    assert held.abs().sum() > 0

    bad = pd.Series([1.0, np.nan, np.nan], index=products)
    flat = machine.update(True, _eligible(products), bad).weights
    _same(flat, pd.Series(0.0, index=products))
    _same(machine._last_weights, pd.Series(0.0, index=products))

    held_again = machine.update(False, _eligible(products), bad).weights
    _same(held_again, pd.Series(0.0, index=products))
    assert len(machine._weight_history) == 3  # 失败调仓也入账


def test_rank_product_cap_scaled_and_redistributed() -> None:
    """单侧可达 exposure 缩小后 cap-and-redistribute；net 仍为 0。"""
    products = ['A', 'B', 'C', 'D', 'E']
    machine = MarketRankLSWeightStateMachine(
        _rank_spec(max_product_weight=0.2), products)
    signal = pd.Series([10, 20, 20, 40, 50], index=products, dtype=float)

    result = machine.update(True, _eligible(products), signal).weights

    expected = pd.Series([-0.2, -0.1, -0.1, 0.2, 0.2], index=products)
    _same(result, expected)
    assert result.sum() == pytest.approx(0.0, abs=1e-12)
    assert result.abs().max() <= 0.2 + 1e-12


def test_rank_cap_and_redistribute_manual() -> None:
    machine = MarketRankLSWeightStateMachine(
        _rank_spec(max_product_weight=0.3), ['X', 'Y'])
    out = machine._cap_and_redistribute(
        pd.Series([0.4, 0.1], index=['X', 'Y']))
    _same(out, pd.Series([0.3, 0.2], index=['X', 'Y']))


def test_rank_rejects_inconsistent_index() -> None:
    products = ['A', 'B', 'C', 'D']
    machine = MarketRankLSWeightStateMachine(_rank_spec(), products)
    with pytest.raises(ValueError, match='不完全相同'):
        machine.update(
            True, pd.Series([True, True], index=['A', 'B']),
            pd.Series([1.0, 2.0, 3.0], index=['A', 'B', 'C']))



def test_weight_change_property() -> None:
    products = ['A', 'B', 'C']
    machine = MarketRankLSWeightStateMachine(_rank_spec(), products)
    signal = pd.Series([1.0, 2.0, 3.0], index=products)
    first = machine.update(True, _eligible(products), signal).weights
    _same(machine.weight_change, first)  # 相对初始空仓

    second = machine.update(
        False, _eligible(products, [True, True, False]), signal).weights
    _same(machine.weight_change, second - first)


def test_rank_update_returns_copy() -> None:
    products = ['A', 'B', 'C']
    machine = MarketRankLSWeightStateMachine(_rank_spec(), products)
    signal = pd.Series([1.0, 2.0, 3.0], index=products)
    out = machine.update(True, _eligible(products), signal).weights
    out.iloc[0] = 999.0
    assert machine._last_weights.iloc[0] != 999.0


# ============================================================
# ts_inv_vol_gross_one
# ============================================================
def test_ts_rejects_wrong_profile() -> None:
    with pytest.raises(ValueError, match='ts_inv_vol_gross_one'):
        TSInverseVolatilityWeightMachine(_rank_spec(), ['A', 'B'])


def test_ts_inv_vol_golden_path() -> None:
    """§20.1：clip(2.0, -1.5) -> 1/-1；u=50/-100；w=1/3, -2/3。"""
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _stub_uniform_vol(machine, {'A': 0.02, 'B': 0.01})

    result = machine._gen_base_weight(
        pd.Series({'A': 2.0, 'B': -1.5}))

    expected = pd.Series({'A': 1 / 3, 'B': -2 / 3})
    _same(result, expected)
    assert result.abs().sum() == pytest.approx(1.0)


def test_ts_clip_bounds_symmetric() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _stub_uniform_vol(machine, {'A': 0.02, 'B': 0.01})

    result = machine._gen_base_weight(pd.Series({'A': 5.0, 'B': -5.0}))

    _same(result, pd.Series({'A': 1 / 3, 'B': -2 / 3}))


def test_ts_missing_vol_or_signal_weight_zero() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _stub_uniform_vol(machine, {'A': np.nan, 'B': 0.01})

    result = machine._gen_base_weight(pd.Series({'A': 1.0, 'B': 1.0}))

    assert result['A'] == 0.0
    assert result['B'] == pytest.approx(1.0)


def test_ts_update_gross_one_and_missing_signal() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _stub_uniform_vol(machine, {'A': 0.02, 'B': 0.01})
    signal = pd.Series({'A': 2.0, 'B': np.nan})

    result = machine.update(
        True, _eligible(products), signal, _ret(products),
        timestamp=_RET_TS).weights

    _same(result, pd.Series({'A': 1.0, 'B': 0.0}))
    assert result.abs().sum() == pytest.approx(1.0)


def test_ts_freeze_and_forced_exit() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _stub_uniform_vol(machine, {'A': 0.02, 'B': 0.01})
    signal = pd.Series({'A': 2.0, 'B': -1.5})
    base = machine.update(
        True, _eligible(products), signal, _ret(products),
        timestamp=_RET_TS).weights

    frozen = machine.update(
        False, _eligible(products, [True, False]), signal,
        _ret(products), timestamp=_RET_TS).weights
    expected = base.copy()
    expected['B'] = 0.0
    _same(frozen, expected)


def test_ts_all_invalid_goes_flat() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _stub_uniform_vol(machine, {'A': np.nan, 'B': np.nan})
    signal = pd.Series({'A': 1.0, 'B': 1.0})

    result = machine.update(
        True, _eligible(products), signal, _ret(products),
        timestamp=_RET_TS).weights
    _same(result, pd.Series(0.0, index=products))


# ============================================================
# ts_inv_vol_target_vol / 约束
# ============================================================
def test_ts_scale_to_target_vol_manual() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_target_vol_spec(), products)
    _stub_cov(machine, value=0.1)  # lambda_star = 0.2 / 0.1 = 2

    scaled, vol_result = machine._scale_to_target_vol(
        pd.Series({'A': 0.5, 'B': -0.5}))

    _same(scaled, pd.Series({'A': 1.0, 'B': -1.0}))
    assert vol_result.value == pytest.approx(0.1)


def test_ts_scale_to_target_vol_respects_max_scaler() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(
        _target_vol_spec(max_scaler=5.0), products)
    _stub_cov(machine, value=0.01)  # lambda_star = 20 -> 截断为 5

    scaled, _ = machine._scale_to_target_vol(
        pd.Series({'A': 0.5, 'B': -0.5}))

    _same(scaled, pd.Series({'A': 2.5, 'B': -2.5}))


def test_ts_scale_to_target_vol_zero_vol_goes_flat() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_target_vol_spec(), products)
    _stub_cov(machine, value=0.0, warnings=('cov_failed',))

    scaled, _ = machine._scale_to_target_vol(
        pd.Series({'A': 0.5, 'B': -0.5}))

    _same(scaled, pd.Series(0.0, index=products))


def test_ts_target_vol_update_flattens_and_records_warning() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_target_vol_spec(), products)
    _stub_uniform_vol(machine, {'A': 0.02, 'B': 0.01})
    _stub_cov(machine, value=0.0, warnings=('cov_failed',))
    signal = pd.Series({'A': 2.0, 'B': -1.5})

    current = machine.update(
        True, _eligible(products), signal, _ret(products),
        timestamp=_RET_TS)

    _same(current.weights, pd.Series(0.0, index=products))
    assert 'cov_failed' in current.warnings


def test_ts_enforce_max_gross_scale_and_none() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(
        _target_vol_spec(max_gross=0.5), products)
    scaled = machine._enforce_max_gross(pd.Series({'A': 0.6, 'B': -0.6}))
    _same(scaled, pd.Series({'A': 0.25, 'B': -0.25}))

    unbounded = TSInverseVolatilityWeightMachine(
        _target_vol_spec(max_gross=None), products)
    raw = pd.Series({'A': 3.0, 'B': -3.0})
    _same(unbounded._enforce_max_gross(raw), raw)


def test_ts_enforce_max_product_weight_clips_without_redistribution() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(
        _gross_one_spec(max_product_weight=0.4), products)
    clipped = machine._enforce_max_product_weight(
        pd.Series({'A': 0.6, 'B': -0.6}))
    # 纯 clip，不把超额重分配给另一品种，gross 由 1.2 降到 0.8。
    _same(clipped, pd.Series({'A': 0.4, 'B': -0.4}))
