"""tests/factors/diagnostic_portfolio/test_weights.py — 权重状态机测试。

覆盖开发方案 §11、§12、§14、§24.3、§24.4 以及黄金路径 §20.1 / §20.2：

    - market_rank_ls：平均排名、去中心化、gross/net、冻结与强制退出、
      新品种等待、无效截面空仓、单品种上限 cap-and-redistribute
    - ts_inv_vol_gross_one / ts_inv_vol_target_vol：clip、逆波动率、
      gross-one、目标波动率缩放、max_gross、单品种 clip、退化空仓

状态机内部一律以 ``np.ndarray`` 承载权重（位置与 ``products`` 排序一致），
因此断言直接比对数组。风险管理器通过实例打桩提供确定的波动率/协方差结果，
从而可以逐项手工复算。
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
    _average_rank,
)
from factors.profiles import (
    market_rank_ls,
    ts_inv_vol_gross_one,
    ts_inv_vol_target_vol,
)


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


def _gross_one_spec(*, max_product_weight=None,
                    clip=1.0) -> DiagnosticPortfolioSpec:
    return DiagnosticPortfolioSpec(
        name='gross_one', harvest_profile=ts_inv_vol_gross_one,
        rebalance=RebalanceConfig(interval=1),
        constraint=ConstraintConfig(max_product_weight=max_product_weight),
        cost=CostConfig(rate=0.0),
        realized_volatility_lookback=3,
        risk=RiskConfig(volatility_lookback=3, volatility_min_periods=2),
        clip_bound=clip,
    )


def _target_vol_spec(*, max_gross=10.0, max_scaler=5.0,
                     max_product_weight=None,
                     clip=1.0) -> DiagnosticPortfolioSpec:
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
        clip_bound=clip,
    )


def _eligible(products, flags=None) -> np.ndarray:
    return np.asarray(
        [True] * len(products) if flags is None else flags, dtype=bool)


def _ret(products) -> np.ndarray:
    """当期收益率。时间戳由 update(timestamp=...) 单独传入。"""
    return np.zeros(len(products), dtype=float)


_RET_TS = pd.Timestamp('2024-01-01')


def _same(left: np.ndarray, right) -> None:
    """逐值比较权重向量。"""
    np.testing.assert_allclose(
        np.asarray(left, dtype=float),
        np.asarray(right, dtype=float),
        rtol=0, atol=1e-12)


def _stub_uniform_vol(machine, mapping: list[float]) -> None:
    """把风险管理器的单品种波动率替换为给定向量。"""
    machine._risk_manager.ex_ante_volatility = lambda: np.asarray(
        mapping, dtype=float)


def _stub_cov(machine, value: float, warnings=(),
              invalid=None) -> None:
    """替换协方差组合波动率估计与无效品种判定。"""
    machine._risk_manager.invalid_mask = lambda: np.asarray(
        invalid if invalid is not None else [False] * machine._n, dtype=bool)
    machine._risk_manager.ex_ante_portfolio_volatility_annualized = \
        lambda weights, annualization_factor, valid_mask=None: \
        ExAntePortfolioVolatility(
            value=value,
            cov_mat_estimation_meta=pd.Series({'stub': 1.0}),
            warnings=warnings)


# ============================================================
# 平均秩（向量化的口径基准）
# ============================================================
def test_average_rank_matches_pandas() -> None:
    """向量化平均秩与 Series.rank(method='average') 逐位相同。"""
    cases = [
        [10.0, 20.0, 30.0, 40.0],
        [10.0, 20.0, 20.0, 40.0, 50.0],       # 含 tie
        [5.0, 5.0, 5.0],                     # 全部相同
        [-1.0, 0.0, 1.0, 1.0, -1.0],
        [0.1, 0.1, 0.2, 0.2, 0.2, 0.3],
    ]
    for values in cases:
        expected = pd.Series(values).rank(method='average').to_numpy()
        got = _average_rank(np.asarray(values, dtype=float))
        np.testing.assert_array_equal(got, expected)


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
    signal = np.array([10, 20, 20, 40, 50], dtype=float)

    result = machine.update(True, _eligible(products), signal).weights

    _same(result, [-1 / 3, -1 / 12, -1 / 12, 1 / 6, 1 / 3])
    assert result.sum() == pytest.approx(0.0, abs=1e-12)   # 市场中性
    assert np.abs(result).sum() == pytest.approx(1.0)      # gross=1


def test_rank_freeze_and_forced_exit() -> None:
    """非调仓日退出品种归零，其余冻结，且不向其他品种重分配。"""
    products = ['A', 'B', 'C', 'D', 'E']
    machine = MarketRankLSWeightStateMachine(_rank_spec(), products)
    signal = np.array([10, 20, 20, 40, 50], dtype=float)
    base = machine.update(True, _eligible(products), signal).weights

    eligible = _eligible(products, [True, False, True, True, True])
    frozen = machine.update(False, eligible, signal).weights

    expected = base.copy()
    expected[1] = 0.0
    _same(frozen, expected)


def test_rank_new_product_waits_for_next_rebalance() -> None:
    products = ['A', 'B', 'C']
    machine = MarketRankLSWeightStateMachine(_rank_spec(), products)
    signal = np.array([10, 20, 30], dtype=float)

    base = machine.update(
        True, _eligible(products, [True, True, False]), signal).weights
    assert base[2] == 0.0

    entered = machine.update(False, _eligible(products), signal).weights
    _same(entered, base)
    assert entered[2] == 0.0  # 新进入品种待下一调仓日


def test_rank_insufficient_cross_section_goes_flat() -> None:
    products = ['A', 'B', 'C']
    machine = MarketRankLSWeightStateMachine(_rank_spec(min_cs=3), products)
    signal = np.array([1.0, np.nan, np.nan])

    result = machine.update(True, _eligible(products), signal).weights
    _same(result, np.zeros(3))


def test_rank_constant_signal_goes_flat() -> None:
    products = ['A', 'B', 'C']
    machine = MarketRankLSWeightStateMachine(_rank_spec(), products)
    signal = np.array([5.0, 5.0, 5.0])

    result = machine.update(True, _eligible(products), signal).weights
    _same(result, np.zeros(3))


def test_rank_failed_rebalance_records_state() -> None:
    products = ['A', 'B', 'C']
    machine = MarketRankLSWeightStateMachine(_rank_spec(), products)
    good = np.array([1.0, 2.0, 3.0])
    held = machine.update(True, _eligible(products), good).weights
    assert np.abs(held).sum() > 0

    bad = np.array([1.0, np.nan, np.nan])
    flat = machine.update(True, _eligible(products), bad).weights
    _same(flat, np.zeros(3))
    _same(machine._w, np.zeros(3))   # 失败调仓也写入内部状态

    held_again = machine.update(False, _eligible(products), bad).weights
    _same(held_again, np.zeros(3))


def test_rank_product_cap_scaled_and_redistributed() -> None:
    """单侧可达 exposure 缩小后 cap-and-redistribute；net 仍为 0。"""
    products = ['A', 'B', 'C', 'D', 'E']
    machine = MarketRankLSWeightStateMachine(
        _rank_spec(max_product_weight=0.2), products)
    signal = np.array([10, 20, 20, 40, 50], dtype=float)

    result = machine.update(True, _eligible(products), signal).weights

    _same(result, [-0.2, -0.1, -0.1, 0.2, 0.2])
    assert result.sum() == pytest.approx(0.0, abs=1e-12)
    assert np.abs(result).max() <= 0.2 + 1e-12


def test_rank_cap_and_redistribute_manual() -> None:
    machine = MarketRankLSWeightStateMachine(
        _rank_spec(max_product_weight=0.3), ['X', 'Y'])
    out = machine._cap_and_redistribute(np.array([0.4, 0.1]))
    _same(out, [0.3, 0.2])


def test_cap_and_redistribute_no_space_raises() -> None:
    machine = MarketRankLSWeightStateMachine(
        _rank_spec(max_product_weight=0.1), ['X', 'Y', 'Z'])
    with pytest.raises(ValueError, match='无剩余权重分配空间'):
        machine._cap_and_redistribute(np.array([0.5, 0.4]))


def test_rank_update_returns_copy() -> None:
    products = ['A', 'B', 'C']
    machine = MarketRankLSWeightStateMachine(_rank_spec(), products)
    signal = np.array([1.0, 2.0, 3.0])
    out = machine.update(True, _eligible(products), signal).weights
    out[0] = 999.0
    assert machine._w[0] != 999.0


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
    _stub_uniform_vol(machine, [0.02, 0.01])

    result = machine._gen_base_weight(np.array([2.0, -1.5]))

    _same(result, [1 / 3, -2 / 3])
    assert np.abs(result).sum() == pytest.approx(1.0)


def test_ts_clip_bounds_symmetric() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _stub_uniform_vol(machine, [0.02, 0.01])

    result = machine._gen_base_weight(np.array([5.0, -5.0]))

    _same(result, [1 / 3, -2 / 3])


def test_ts_missing_vol_or_signal_weight_zero() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _stub_uniform_vol(machine, [np.nan, 0.01])

    result = machine._gen_base_weight(np.array([1.0, 1.0]))

    assert result[0] == 0.0
    assert result[1] == pytest.approx(1.0)


def test_ts_nan_signal_participates_as_zero() -> None:
    """信号为 NaN 的品种不参与计算，权重为 0。"""
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _stub_uniform_vol(machine, [0.02, 0.01])

    result = machine._gen_base_weight(np.array([2.0, np.nan]))

    _same(result, [1.0, 0.0])


# ============================================================
# clip_bound 为 None（时序模式下不截断信号）
# ============================================================
def test_ts_none_clip_bound_still_applies_inverse_vol_and_gross_one() -> None:
    """clip_bound=None 只跳过 clip，逆波动率缩放与 gross=1 照常执行。

    signal=[2.0, -1.5]、vol=[0.02, 0.01]：
    - clip_bound=None -> u=[100, -150] -> gross=250 -> [0.4, -0.6]
    - clip_bound=1.0  -> clip -> [1, -1] -> u=[50, -100] -> [1/3, -2/3]

    两者不同，正说明 None 分支确实跳过了 clip，而缩放与归一化仍然生效
    （若 bug 回归成直接返回原始信号，结果会是 [2.0, -1.5]）。
    """
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(
        _gross_one_spec(clip=None), products)
    _stub_uniform_vol(machine, [0.02, 0.01])

    result = machine._gen_base_weight(np.array([2.0, -1.5]))

    _same(result, [0.4, -0.6])
    assert np.abs(result).sum() == pytest.approx(1.0)


def test_ts_none_clip_bound_still_zeroes_missing_vol_and_nan_signal() -> None:
    """clip_bound=None 时 NaN 填充与缺失波动率归零逻辑同样生效。"""
    products = ['A', 'B']

    # 事前波动率为 NaN 的品种归零。
    machine = TSInverseVolatilityWeightMachine(
        _gross_one_spec(clip=None), products)
    _stub_uniform_vol(machine, [np.nan, 0.01])
    _same(machine._gen_base_weight(np.array([1.0, 1.0])), [0.0, 1.0])

    # 信号为 NaN 的品种归零。
    machine = TSInverseVolatilityWeightMachine(
        _gross_one_spec(clip=None), products)
    _stub_uniform_vol(machine, [0.02, 0.01])
    _same(machine._gen_base_weight(np.array([2.0, np.nan])), [1.0, 0.0])


def test_ts_none_clip_bound_does_not_mutate_input_signal() -> None:
    """clip_bound=None 时 signal 与 output 不应是同一个对象。"""
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(
        _gross_one_spec(clip=None), products)
    _stub_uniform_vol(machine, [0.02, 0.01])
    signal = np.array([2.0, -1.5])

    result = machine._gen_base_weight(signal)

    assert result is not signal
    _same(signal, [2.0, -1.5])  # 输入未被原地修改


def test_ts_update_none_clip_bound_applies_max_product_weight_on_weights() -> None:
    """clip_bound=None 时 max_product_weight 作用在归一化后的权重上。

    signal=[2.0, -1.5]、vol=[0.02, 0.01] -> 未 clip 时基础权重为
    [0.4, -0.6]，绝对值均不超过 0.9，因此不会被截断。
    若 bug 回归（提前 return 原始信号），则会拿 [2.0, -1.5] 去和 0.9 比较，
    被截断成 [0.9, -0.9]。
    """
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(
        _gross_one_spec(clip=None, max_product_weight=0.9), products)
    _stub_uniform_vol(machine, [0.02, 0.01])

    result = machine.update(
        True, _eligible(products), np.array([2.0, -1.5]), _ret(products),
        timestamp=_RET_TS).weights

    _same(result, [0.4, -0.6])


def test_ts_clip_bound_present_differs_from_none_on_oversized_signal() -> None:
    """回归测试：clip_bound=None 与 clip_bound=1.0 在超大信号上必须不同。

    signal=[5.0, -5.0]、vol=[0.02, 0.02]：
    - clip_bound=None  -> u=[250, -250]  -> [0.5, -0.5]
    - clip_bound=1.0   -> clip -> [1, -1] -> u=[50, -50] -> [0.5, -0.5]
    两者归一化后相同，故改用非对称组合使比例可区分：
    signal=[5.0, -1.0]、vol=[0.02, 0.01]
    - clip_bound=None  -> u=[250, -100] -> [0.714285..., -0.285714...]
    - clip_bound=1.0   -> clip -> [1, -1] -> u=[50, -100] -> [1/3, -2/3]
    """
    products = ['A', 'B']
    signal = np.array([5.0, -1.0])

    unclipped = TSInverseVolatilityWeightMachine(
        _gross_one_spec(clip=None), products)
    _stub_uniform_vol(unclipped, [0.02, 0.01])
    unclipped_result = unclipped._gen_base_weight(signal)

    clipped = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _stub_uniform_vol(clipped, [0.02, 0.01])
    clipped_result = clipped._gen_base_weight(signal)

    _same(unclipped_result, np.array([250.0, -100.0]) / 350.0)
    _same(clipped_result, [1 / 3, -2 / 3])
    assert np.abs(unclipped_result).sum() == pytest.approx(1.0)
    assert np.abs(clipped_result).sum() == pytest.approx(1.0)


def test_ts_target_vol_allows_none_clip_bound() -> None:
    """ts_inv_vol_target_vol 同样支持 clip_bound=None。"""
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(
        _target_vol_spec(clip=None), products)
    _stub_uniform_vol(machine, [0.02, 0.01])
    _stub_cov(machine, 0.2)

    result = machine.update(
        True, _eligible(products), np.array([2.0, -1.5]), _ret(products),
        timestamp=_RET_TS).weights

    assert np.abs(result).sum() == pytest.approx(1.0)
    assert np.isfinite(result).all()


def test_ts_update_gross_one_and_missing_signal() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _stub_uniform_vol(machine, [0.02, 0.01])
    signal = np.array([2.0, np.nan])

    result = machine.update(
        True, _eligible(products), signal, _ret(products),
        timestamp=_RET_TS).weights

    _same(result, [1.0, 0.0])
    assert np.abs(result).sum() == pytest.approx(1.0)


def test_ts_freeze_and_forced_exit() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _stub_uniform_vol(machine, [0.02, 0.01])
    signal = np.array([2.0, -1.5])
    base = machine.update(
        True, _eligible(products), signal, _ret(products),
        timestamp=_RET_TS).weights

    frozen = machine.update(
        False, _eligible(products, [True, False]), signal,
        _ret(products), timestamp=_RET_TS).weights
    expected = base.copy()
    expected[1] = 0.0
    _same(frozen, expected)


def test_ts_all_invalid_goes_flat() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _stub_uniform_vol(machine, [np.nan, np.nan])
    signal = np.array([1.0, 1.0])

    result = machine.update(
        True, _eligible(products), signal, _ret(products),
        timestamp=_RET_TS).weights
    _same(result, np.zeros(2))


def test_ts_requires_ret_and_timestamp() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    with pytest.raises(ValueError, match='收益率'):
        machine.update(True, _eligible(products), np.array([1.0, 2.0]))
    with pytest.raises(ValueError, match='时间戳'):
        machine.update(True, _eligible(products), np.array([1.0, 2.0]),
                       _ret(products))


# ============================================================
# ts_inv_vol_target_vol / 约束
# ============================================================
def test_ts_scale_to_target_vol_manual() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_target_vol_spec(), products)
    _stub_cov(machine, value=0.1)  # lambda_star = 0.2 / 0.1 = 2
    w = np.array([0.5, -0.5])

    scaled, vol_result = machine._scale_to_target_vol(w, np.array([True, True]))

    _same(scaled, [1.0, -1.0])
    assert vol_result.value == pytest.approx(0.1)


def test_ts_scale_to_target_vol_respects_max_scaler() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(
        _target_vol_spec(max_scaler=5.0), products)
    _stub_cov(machine, value=0.01)  # lambda_star = 20 -> 截断为 5
    w = np.array([0.5, -0.5])

    scaled, _ = machine._scale_to_target_vol(w, np.array([True, True]))

    _same(scaled, [2.5, -2.5])


def test_ts_scale_to_target_vol_zero_vol_goes_flat() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_target_vol_spec(), products)
    _stub_cov(machine, value=0.0, warnings=('cov_failed',))
    w = np.array([0.5, -0.5])

    scaled, _ = machine._scale_to_target_vol(w, np.array([True, True]))

    _same(scaled, np.zeros(2))


def test_ts_target_vol_update_flattens_and_records_warning() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_target_vol_spec(), products)
    _stub_uniform_vol(machine, [0.02, 0.01])
    _stub_cov(machine, value=0.0, warnings=('cov_failed',))
    signal = np.array([2.0, -1.5])

    current = machine.update(
        True, _eligible(products), signal, _ret(products),
        timestamp=_RET_TS)

    _same(current.weights, np.zeros(2))
    assert 'cov_failed' in current.warnings


def test_ts_target_vol_zeroes_invalid_products() -> None:
    """invalid_mask 命中的品种权重归零，并重新归一化到 gross=1。

    A 被剔除后基础权重 [1/3, -2/3] 变成 [0, -2/3]，归一化得 [0, -1]；
    随后目标波动率缩放（stub vol=0.1，target=0.2 -> lambda=2）得 [0, -2]。
    """
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_target_vol_spec(), products)
    _stub_uniform_vol(machine, [0.02, 0.01])
    _stub_cov(machine, value=0.1, invalid=[True, False])

    current = machine.update(
        True, _eligible(products), np.array([2.0, -1.5]), _ret(products),
        timestamp=_RET_TS)

    assert current.weights[0] == 0.0
    assert current.weights[1] == pytest.approx(-2.0)


def test_ts_enforce_max_gross_scale_and_none() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(
        _target_vol_spec(max_gross=0.5), products)
    scaled = machine._enforce_max_gross(np.array([0.6, -0.6]))
    _same(scaled, [0.25, -0.25])

    unbounded = TSInverseVolatilityWeightMachine(
        _target_vol_spec(max_gross=None), products)
    raw = np.array([3.0, -3.0])
    _same(unbounded._enforce_max_gross(raw), raw)


def test_ts_enforce_max_product_weight_clips_without_redistribution() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(
        _gross_one_spec(max_product_weight=0.4), products)
    clipped = machine._enforce_max_product_weight(np.array([0.6, -0.6]))
    # 纯 clip，不把超额重分配给另一品种，gross 由 1.2 降到 0.8。
    _same(clipped, [0.4, -0.4])


def test_ts_scale_to_gross_one_zero_denominator() -> None:
    products = ['A', 'B']
    machine = TSInverseVolatilityWeightMachine(_gross_one_spec(), products)
    _same(machine._scale_to_gross_one(np.zeros(2)), np.zeros(2))