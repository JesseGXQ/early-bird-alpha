"""tests/factors/diagnostic_portfolio/test_experiment.py — 输入对齐与端到端测试。

覆盖开发方案 §9、§10、§15、§16、§20、§23、§24.2、§24.5、§24.10、§24.11：

    - align_inputs / build_global_timeline / build_rebalance_schedule
    - 三种配方的端到端运行
    - market_rank_ls 黄金路径（手工复算权重、换手、成本、收益、净值、暴露、归因）
    - ts_inv_vol_gross_one 黄金路径（§20.1 逆波动率权重）
    - ts_inv_vol_target_vol 端到端（缩放因子被 max_scaler 固定为 1 时回到基础权重）
    - 全局下一期对齐、样本末端不额外计成本
    - 因果性（改动未来数据不影响过去权重）
    - 输入不可变、重复运行确定性

全部使用 synthetic dataset，不依赖 db/ 或仓库外 RAW_DATA。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.diagnostic_portfolio.experiment import (
    DiagnosticPortfolioExperimentRunner,
    align_inputs,
    build_global_timeline,
    build_rebalance_schedule,
)
from factors.diagnostic_portfolio.models import (
    ConstraintConfig,
    CostConfig,
    DiagnosticPortfolioSpec,
    RebalanceConfig,
    RiskConfig,
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
def _rank_spec(*, interval=2, max_product_weight=None,
               cost=0.0003) -> DiagnosticPortfolioSpec:
    return DiagnosticPortfolioSpec(
        name='rank', harvest_profile=market_rank_ls,
        rebalance=RebalanceConfig(interval=interval),
        constraint=ConstraintConfig(max_product_weight=max_product_weight),
        cost=CostConfig(rate=cost),
        realized_volatility_lookback=2,
        min_cross_section_size=2,
    )


def _gross_one_spec(*, interval=1, volatility_lookback=2,
                    volatility_min_periods=2, clip=1.0,
                    cost=0.0003) -> DiagnosticPortfolioSpec:
    return DiagnosticPortfolioSpec(
        name='gross_one', harvest_profile=ts_inv_vol_gross_one,
        rebalance=RebalanceConfig(interval=interval),
        constraint=ConstraintConfig(),
        cost=CostConfig(rate=cost),
        realized_volatility_lookback=2,
        risk=RiskConfig(
            volatility_lookback=volatility_lookback,
            volatility_min_periods=volatility_min_periods),
        clip_bound=clip,
    )


def _target_vol_spec(*, anchor, interval=100, max_scaler=1.0,
                     target=1e6, max_gross=None,
                     covariance_lookback=4, covariance_min_periods=4,
                     volatility_lookback=3) -> DiagnosticPortfolioSpec:
    return DiagnosticPortfolioSpec(
        name='target_vol', harvest_profile=ts_inv_vol_target_vol,
        rebalance=RebalanceConfig(interval=interval, anchor=anchor),
        constraint=ConstraintConfig(
            max_gross=max_gross, max_volatility_scaler=max_scaler),
        cost=CostConfig(rate=0.0),
        realized_volatility_lookback=2,
        risk=RiskConfig(
            volatility_lookback=volatility_lookback, volatility_min_periods=2,
            covariance_lookback=covariance_lookback,
            covariance_min_periods=covariance_min_periods,
            target_volatility=target),
        clip_bound=1.0,
    )


# ============================================================
# align_inputs / timeline / schedule
# ============================================================
def test_align_inputs_full_panel(index_factory, dataset_factory,
                                 signal_factory) -> None:
    timestamps = ['2024-01-01', '2024-01-02']
    products = ['RB', 'MA']
    # 行主序：t0 的 RB, MA；t1 的 RB, MA。
    ds = dataset_factory(timestamps, products, [0.1, 0.2, 0.3, 0.4])
    idx = index_factory(timestamps, sorted(products))
    signal = signal_factory(pd.Series([1.0, -1.0], index=idx[:2]))

    panel = align_inputs(signal, ds)

    assert list(panel.columns) == ['true_return', 'eligible', 'signal']
    assert tuple(panel.index.names) == INDEX_NAMES
    # 品种按代码升序：MA < RB。
    assert list(panel.index.get_level_values('product').unique()) == ['MA', 'RB']
    assert panel['true_return'].tolist() == [0.2, 0.1, 0.4, 0.3]
    assert panel['eligible'].tolist() == [True] * 4
    assert panel['signal'].isna().tolist() == [False, False, True, True]


def test_align_inputs_rejects_signal_outside_panel(
        dataset_factory, signal_factory, index_factory) -> None:
    ds = dataset_factory(['2024-01-01'], ['A', 'B'], [0.1, 0.2])
    extra = pd.MultiIndex.from_tuples(
        [(pd.Timestamp('2024-01-05'), 'A')], names=INDEX_NAMES)
    signal = signal_factory(pd.Series([1.0], index=extra))

    with pytest.raises(ValueError, match='不在主面板索引中'):
        align_inputs(signal, ds)


def test_align_inputs_requires_eligible_index_equal(
        dataset_factory, signal_factory) -> None:
    import dataclasses

    from factors.universe import Universe

    ds = dataset_factory(['2024-01-01'], ['A', 'B'], [0.1, 0.2])
    subset = pd.MultiIndex.from_tuples(
        [(pd.Timestamp('2024-01-01'), 'A')], names=INDEX_NAMES)
    bad_ds = dataclasses.replace(
        ds,
        universe=Universe(
            eligible=pd.Series([True], index=subset),
            exclude_reason=pd.Series('', index=subset)))
    signal = signal_factory(pd.Series([1.0], index=subset))

    with pytest.raises(AssertionError):
        align_inputs(signal, bad_ds)


def test_build_global_timeline_sorted_unique() -> None:
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(['2024-01-02', '2024-01-01']), ['A', 'B']],
        names=INDEX_NAMES)
    panel = pd.DataFrame({'true_return': 0.0}, index=idx)

    timeline = build_global_timeline(panel)

    assert isinstance(timeline, pd.DatetimeIndex)
    assert list(timeline) == list(
        pd.to_datetime(['2024-01-01', '2024-01-02']))


def test_build_rebalance_schedule_interval_and_anchor() -> None:
    schedule = pd.date_range('2024-01-01', periods=5, freq='D')

    flags = build_rebalance_schedule(schedule, RebalanceConfig(interval=2))
    assert flags.tolist() == [True, False, True, False, False]

    flags = build_rebalance_schedule(
        schedule, RebalanceConfig(interval=2, anchor=pd.Timestamp('2024-01-02')))
    assert flags.tolist() == [False, True, False, True, False]

    # anchor 落在两个时间戳之间 -> 使用其后首个时间戳（bfill）。
    flags = build_rebalance_schedule(
        schedule,
        RebalanceConfig(interval=2, anchor=pd.Timestamp('2024-01-02 12:00')))
    assert flags.tolist() == [False, False, True, False, False]


def test_build_rebalance_schedule_anchor_too_late() -> None:
    schedule = pd.date_range('2024-01-01', periods=5, freq='D')
    with pytest.raises(ValueError, match='太晚'):
        build_rebalance_schedule(
            schedule, RebalanceConfig(interval=2, anchor=pd.Timestamp('2024-01-05')))
    with pytest.raises(ValueError, match='最后一个时间戳'):
        build_rebalance_schedule(
            schedule, RebalanceConfig(interval=2, anchor=pd.Timestamp('2024-02-01')))


# ============================================================
# 黄金路径：market_rank_ls 端到端
# ============================================================
def _build_rank_scenario(dataset_factory, signal_factory):
    timestamps = pd.to_datetime(
        ['2024-03-01', '2024-03-04', '2024-03-05', '2024-03-06', '2024-03-07'])
    products = ['A', 'B', 'C', 'D']
    returns = np.zeros(20)
    returns[0:4] = [0.05] * 4
    returns[4:8] = [0.01, 0.02, -0.01, 0.03]
    returns[12:16] = [0.02, -0.01, 0.03, -0.02]
    ds = dataset_factory(timestamps, products, returns)

    values = pd.Series(np.nan, index=ds.main_panel.index, name='signal')
    values.loc[pd.IndexSlice[timestamps[0], :]] = [1.0, 2.0, 3.0, 4.0]
    values.loc[pd.IndexSlice[timestamps[2], :]] = [4.0, 3.0, 2.0, 1.0]
    signal = signal_factory(values, name='rank_signal')
    return ds, signal, timestamps, products


def test_rank_end_to_end_golden_path(dataset_factory, signal_factory) -> None:
    ds, signal, timestamps, products = _build_rank_scenario(
        dataset_factory, signal_factory)
    spec = _rank_spec(interval=2, cost=0.0003)

    result = DiagnosticPortfolioExperimentRunner(signal, ds, spec).run()

    decision_index = timestamps[:-1]
    realization_index = timestamps[1:]

    assert result.signal_name == 'rank_signal'
    assert result.spec is spec
    assert result.sector_attribution is None
    pd.testing.assert_series_equal(
        result.rebalance_flags,
        pd.Series([True, False, True, False], index=decision_index),
        check_names=False)

    w0 = pd.Series([-0.375, -0.125, 0.125, 0.375], index=products)
    w2 = pd.Series([0.375, 0.125, -0.125, -0.375], index=products)
    for ts, expected in (
        (timestamps[0], w0), (timestamps[1], w0),
        (timestamps[2], w2), (timestamps[3], w2),
    ):
        pd.testing.assert_series_equal(
            result.target_weights.xs(ts, level='timestamp'), expected,
            check_names=False, atol=1e-12)

    # 换手与决策成本：初始建仓 1.0；t2 完全反向 2.0；冻结日 0。
    expected_turnover = pd.Series([1.0, 0.0, 2.0, 0.0], index=decision_index)
    pd.testing.assert_series_equal(
        result.turnover, expected_turnover, check_names=False)
    pd.testing.assert_series_equal(
        result.decision_cost, 0.0003 * expected_turnover, check_names=False)

    returns = result.portfolio_returns
    assert returns.index.tolist() == realization_index.tolist()
    np.testing.assert_allclose(
        returns['gross_return'].to_numpy(), [0.00375, 0.0, 0.01, 0.0],
        atol=1e-12)
    np.testing.assert_allclose(
        returns['cost'].to_numpy(), [0.0003, 0.0, 0.0006, 0.0], atol=1e-12)
    np.testing.assert_allclose(
        returns['proxy_net_return'].to_numpy(),
        [0.00345, 0.0, 0.0094, 0.0], atol=1e-12)

    np.testing.assert_allclose(
        result.nav['gross_nav'].to_numpy(),
        np.cumprod([1.00375, 1.0, 1.01, 1.0]), atol=1e-12)
    np.testing.assert_allclose(
        result.nav['proxy_net_nav'].to_numpy(),
        np.cumprod([1.00345, 1.0, 1.0094, 1.0]), atol=1e-12)

    exposure = result.exposures
    assert exposure.index.tolist() == decision_index.tolist()
    np.testing.assert_allclose(
        exposure['gross_exposure'].to_numpy(), 1.0, atol=1e-12)
    np.testing.assert_allclose(
        exposure['net_exposure'].to_numpy(), 0.0, atol=1e-12)
    np.testing.assert_allclose(
        exposure['long_exposure'].to_numpy(), 0.5, atol=1e-12)
    np.testing.assert_allclose(
        exposure['short_exposure'].to_numpy(), -0.5, atol=1e-12)
    np.testing.assert_allclose(exposure['active_count'].to_numpy(), 4, atol=0)

    attribution = result.product_attribution
    assert attribution.index.tolist() == products
    assert attribution['gross_contribution_sum'].sum() == pytest.approx(
        returns['gross_return'].sum())
    direction = result.direction_attribution
    np.testing.assert_allclose(
        (direction['long_contribution']
         + direction['short_contribution']).to_numpy(),
        returns['gross_return'].to_numpy(), atol=1e-12)

    performance = result.performance
    assert performance.index.tolist() == ['gross', 'proxy_net']
    assert performance['n_periods'].tolist() == [4, 4]


def test_rank_end_to_end_sector_attribution(dataset_factory, signal_factory) -> None:
    ds, signal, timestamps, products = _build_rank_scenario(
        dataset_factory, signal_factory)
    classification = pd.DataFrame({
        'product': ['A', 'B', 'C', 'D'],
        'sector': ['x', 'x', 'y', 'y'],
    })
    ds = dataset_factory(
        timestamps, products,
        ds.main_panel['True_Return'].to_numpy(),
        auxiliary={'classification': classification})
    # 重建 signal 索引以匹配新 dataset（索引一致，直接复用即可）。
    values = signal.values.copy()
    signal = signal_factory(values, name='rank_signal')
    spec = DiagnosticPortfolioSpec(
        name='rank_sector', harvest_profile=market_rank_ls,
        rebalance=RebalanceConfig(interval=2),
        constraint=ConstraintConfig(), cost=CostConfig(rate=0.0),
        realized_volatility_lookback=2, min_cross_section_size=2,
        sector_attribution_col='sector')

    result = DiagnosticPortfolioExperimentRunner(signal, ds, spec).run()

    sector = result.sector_attribution
    assert sector is not None
    per_day = sector.groupby(level='timestamp').sum()
    gross = result.portfolio_returns['gross_return']
    np.testing.assert_allclose(
        per_day.reindex(gross.index).to_numpy(), gross.to_numpy(), atol=1e-12)


# ============================================================
# 黄金路径：ts_inv_vol_gross_one 端到端（§20.1）
# ============================================================
def _build_ts_golden_scenario(dataset_factory, signal_factory):
    timestamps = pd.to_datetime(['2024-01-01', '2024-01-02', '2024-01-03'])
    products = ['A', 'B']
    s2 = np.sqrt(2.0)
    # t1 的收益使单品种波动率恰为 0.02 / 0.01；t2 为收益实现期。
    returns = [0.0, 0.0, 0.02 * s2, 0.01 * s2, 0.03, -0.01]
    ds = dataset_factory(timestamps, products, returns)
    values = pd.Series(
        [np.nan, np.nan, 2.0, -1.5, np.nan, np.nan], index=ds.main_panel.index)
    signal = signal_factory(values, name='ts_signal')
    return ds, signal, timestamps, products


def test_ts_gross_one_end_to_end_golden_path(dataset_factory, signal_factory) -> None:
    ds, signal, timestamps, products = _build_ts_golden_scenario(
        dataset_factory, signal_factory)
    spec = _gross_one_spec(interval=1)

    result = DiagnosticPortfolioExperimentRunner(signal, ds, spec).run()

    # t0 无足够波动率历史 -> 空仓；t1 得到 §20.1 的 1/3、-2/3。
    expected_t0 = pd.Series(0.0, index=products)
    expected_t1 = pd.Series([1 / 3, -2 / 3], index=products)
    pd.testing.assert_series_equal(
        result.target_weights.xs(timestamps[0], level='timestamp'),
        expected_t0, check_names=False, atol=1e-12)
    pd.testing.assert_series_equal(
        result.target_weights.xs(timestamps[1], level='timestamp'),
        expected_t1, check_names=False, atol=1e-12)

    # 换手与成本：t0=0，t1=1；收益实现期 t2 的毛收益 = 0.016667，扣 t1 成本。
    pd.testing.assert_series_equal(
        result.turnover,
        pd.Series([0.0, 1.0], index=timestamps[:2]), check_names=False)
    returns = result.portfolio_returns
    assert returns.index.tolist() == list(timestamps[1:])
    assert returns['gross_return'].iloc[1] == pytest.approx(1 / 3 * 0.03 + 2 / 3 * 0.01)
    assert returns['cost'].iloc[1] == pytest.approx(0.0003)


# ============================================================
# ts_inv_vol_target_vol 端到端
# ============================================================
def test_ts_target_vol_end_to_end(dataset_factory, signal_factory) -> None:
    timestamps = pd.date_range('2024-01-01', periods=8, freq='D')
    products = ['A', 'B', 'C']
    rng = np.random.default_rng(2026)
    returns = rng.normal(0, 0.01, (len(timestamps), len(products))).reshape(-1)
    ds = dataset_factory(timestamps, products, returns)

    index = ds.main_panel.index
    values = pd.Series(np.nan, index=index, name='signal')
    # 只在唯一调仓日 t4 给出信号。
    t4 = timestamps[4]
    values.loc[pd.IndexSlice[t4, :]] = [2.0, -1.5, 0.8]
    signal = signal_factory(values, name='target_signal')

    # max_scaler=1 且 target_volatility 很大 -> lambda=1，最终权重=基础 gross-one 权重。
    spec = _target_vol_spec(anchor=t4, max_scaler=1.0, target=1e6)

    result = DiagnosticPortfolioExperimentRunner(signal, ds, spec).run()

    # 独立复算 t4 的基础权重：clip -> /单品种波动率(最近 3 期) -> gross 归一。
    ret_wide = ds.main_panel['True_Return'].unstack('product')[products]
    window = ret_wide.loc[timestamps[2:5]]
    vol = window.std(ddof=1)
    clipped = pd.Series(
        {'A': 2.0, 'B': -1.5, 'C': 0.8}).clip(-1.0, 1.0)
    unit = (clipped / vol).fillna(0.0)
    expected = unit / unit.abs().sum()

    pd.testing.assert_series_equal(
        result.target_weights.xs(t4, level='timestamp'),
        expected.reindex(products), check_names=False, atol=1e-10)

    # 调仓前后：t0..t3 空仓，t5..t6 冻结 t4 权重。
    for ts in timestamps[:4]:
        np.testing.assert_allclose(
            result.target_weights.xs(ts, level='timestamp').to_numpy(), 0.0)
    for ts in timestamps[5:7]:
        pd.testing.assert_series_equal(
            result.target_weights.xs(ts, level='timestamp'),
            expected.reindex(products), check_names=False, atol=1e-10)

    # 违约检查：最终权重满足 max_gross（无）与 gross=1。
    gross_max = result.exposures['gross_exposure'].max()
    assert gross_max <= 1.0 + 1e-9


# ============================================================
# 对齐、末端成本与因果性
# ============================================================
def test_align_weight_to_global_next_return(dataset_factory, signal_factory) -> None:
    ds, signal, timestamps, products = _build_rank_scenario(
        dataset_factory, signal_factory)
    runner = DiagnosticPortfolioExperimentRunner(signal, ds, _rank_spec())
    panel = align_inputs(signal, ds)

    weights = pd.Series(
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        index=pd.MultiIndex.from_product(
            [timestamps[:2], products], names=INDEX_NAMES)).rename('weight')
    cost = pd.Series([0.01, 0.02], index=timestamps[:2])

    timeline = build_global_timeline(panel=panel)
    aligned, weights_shifted, costs_shifted = \
        runner._align_weight_return_cost(weights, panel, cost, timeline)

    # 首个时间戳被剔除；实现时点 t 使用决策时点 t-1 的权重与成本。
    assert timestamps[0] not in aligned.index.get_level_values('timestamp')
    pd.testing.assert_series_equal(
        weights_shifted.xs(timestamps[1], level='timestamp'),
        weights.xs(timestamps[0], level='timestamp'),
        check_names=False)
    pd.testing.assert_series_equal(
        weights_shifted.xs(timestamps[2], level='timestamp'),
        weights.xs(timestamps[1], level='timestamp'),
        check_names=False)
    assert costs_shifted.loc[timestamps[1]] == pytest.approx(0.01)
    assert costs_shifted.loc[timestamps[2]] == pytest.approx(0.02)


def test_no_terminal_liquidation_cost(dataset_factory, signal_factory) -> None:
    ds, signal, timestamps, products = _build_rank_scenario(
        dataset_factory, signal_factory)
    result = DiagnosticPortfolioExperimentRunner(
        signal, ds, _rank_spec(interval=2)).run()

    # 决策成本只覆盖 t0..t_{N-2}，最后一个实现时点不再产生成本。
    assert result.decision_cost.index.tolist() == timestamps[:-1].tolist()
    assert result.portfolio_returns.index.tolist() == timestamps[1:].tolist()
    # 最后一期实现时点的成本来自 t_{N-2} 决策，而非虚构平仓。
    assert result.portfolio_returns['cost'].iloc[-1] == pytest.approx(
        result.decision_cost.iloc[-1])


def test_rank_future_signal_change_does_not_affect_past_weights(
        dataset_factory, signal_factory) -> None:
    ds, signal, timestamps, products = _build_rank_scenario(
        dataset_factory, signal_factory)
    spec = _rank_spec(interval=2)

    base = DiagnosticPortfolioExperimentRunner(signal, ds, spec).run()

    perturbed_values = signal.values.copy()
    perturbed_values.loc[pd.IndexSlice[timestamps[2], :]] = [40.0, 30.0, 20.0, 10.0]
    perturbed = signal_factory(perturbed_values, name='rank_signal')
    changed = DiagnosticPortfolioExperimentRunner(perturbed, ds, spec).run()

    for ts in timestamps[:2]:
        pd.testing.assert_series_equal(
            base.target_weights.xs(ts, level='timestamp'),
            changed.target_weights.xs(ts, level='timestamp'),
            check_names=False, atol=1e-12)


def test_ts_future_return_change_does_not_affect_past_weights(
        dataset_factory, signal_factory) -> None:
    ds, signal, timestamps, products = _build_ts_golden_scenario(
        dataset_factory, signal_factory)
    spec = _gross_one_spec(interval=1)

    base = DiagnosticPortfolioExperimentRunner(signal, ds, spec).run()

    mutated_returns = ds.main_panel['True_Return'].copy()
    mutated_returns.loc[pd.IndexSlice[timestamps[2], :]] = [9.9, -9.9]
    ds_changed = dataset_factory(
        timestamps, products, mutated_returns.to_numpy())
    changed = DiagnosticPortfolioExperimentRunner(
        signal, ds_changed, spec).run()

    for ts in timestamps[:2]:
        pd.testing.assert_series_equal(
            base.target_weights.xs(ts, level='timestamp'),
            changed.target_weights.xs(ts, level='timestamp'),
            check_names=False, atol=1e-12)


def test_inputs_not_mutated_and_deterministic(dataset_factory, signal_factory) -> None:
    ds, signal, timestamps, products = _build_rank_scenario(
        dataset_factory, signal_factory)
    spec = _rank_spec(interval=2)

    panel_before = ds.main_panel.copy(deep=True)
    eligible_before = ds.universe.eligible.copy(deep=True)
    signal_before = signal.values.copy(deep=True)

    first = DiagnosticPortfolioExperimentRunner(signal, ds, spec).run()
    second = DiagnosticPortfolioExperimentRunner(signal, ds, spec).run()

    pd.testing.assert_frame_equal(ds.main_panel, panel_before)
    pd.testing.assert_series_equal(ds.universe.eligible, eligible_before)
    pd.testing.assert_series_equal(signal.values, signal_before)

    pd.testing.assert_series_equal(
        first.target_weights, second.target_weights, check_names=False)
    pd.testing.assert_frame_equal(
        first.portfolio_returns, second.portfolio_returns)
