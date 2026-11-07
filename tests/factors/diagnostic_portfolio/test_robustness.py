"""tests/factors/diagnostic_portfolio/test_robustness.py — anchor 相位扫描测试。

全部使用 synthetic dataset + fake runner，不依赖 db/ 或仓库外 RAW_DATA。
覆盖：
    - anchor offset 生成与截断；
    - summary 的 count/mean/std/min/median/max/anchor0/percentile；
    - NaN 与空输入的边界；
    - 单 h 编排的调用次数、anchor 覆盖、输入不可变、失败传播；
    - 多 h 编排的返回结构、summary_indicator、绘图与空输入。
"""

from __future__ import annotations

import matplotlib

matplotlib.use('Agg')

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from factors.diagnostic_portfolio import robustness
from factors.diagnostic_portfolio.models import (
    ConstraintConfig,
    CostConfig,
    DiagnosticPortfolioSpec,
    RebalanceConfig,
)
from factors.diagnostic_portfolio.robustness import (
    AnchorVariantResult,
    _generate_anchor_offsets,
    _summarize_anchor_runs,
    examine_rebalance_timing_luck,
    run_anchor_robustness,
)
from factors.profiles import market_rank_ls


SUMMARY_COLUMNS = [
    'count', 'mean', 'std', 'min', 'median', 'max',
    'anchor0', 'anchor0_percentile',
]


# ============================================================
# 辅助
# ============================================================
def _spec(interval: int = 3) -> DiagnosticPortfolioSpec:
    return DiagnosticPortfolioSpec(
        name='robustness_test',
        harvest_profile=market_rank_ls,
        rebalance=RebalanceConfig(interval=interval),
        constraint=ConstraintConfig(),
        cost=CostConfig(rate=0.0),
        min_cross_section_size=2,
    )


def _make_inputs(dataset_factory, signal_factory, n_periods: int = 10):
    timestamps = pd.date_range('2024-01-01', periods=n_periods, freq='D')
    products = ['A', 'B', 'C']
    ds = dataset_factory(
        timestamps, products, np.zeros(len(timestamps) * len(products)))
    values = pd.Series(
        np.arange(len(ds.main_panel), dtype=float),
        index=ds.main_panel.index,
        name='signal',
    )
    signal = signal_factory(values, name='robustness_signal')
    return ds, signal


def _row(offset: int, proxy_net_sharpe: float, interval: int = 5):
    return AnchorVariantResult(
        interval=interval,
        offset=offset,
        anchor=pd.Timestamp('2024-01-01') + pd.Timedelta(days=offset),
        gross_sharpe=0.0,
        proxy_net_sharpe=proxy_net_sharpe,
        proxy_net_cagr=0.0,
        annualized_volatility=0.0,
        max_drawdown=0.0,
        avg_turnover=0.0,
        avg_active_count=0.0,
        n_rebalances=0,
        n_periods_from_anchor=0,
    )


class _FakeRunner:
    """最小 fake runner，只实现 robustness 需要的字段。"""

    calls: list[tuple] = []
    raise_on_run = False

    def __init__(self, signal, dataset, spec):
        self.signal = signal
        self.dataset = dataset
        self.spec = spec
        type(self).calls.append((signal, dataset, spec))

    def run(self):
        if type(self).raise_on_run:
            raise RuntimeError('fake runner failure')

        timeline = pd.DatetimeIndex(
            self.dataset.main_panel.index.get_level_values('timestamp')
            .unique()).sort_values()
        anchor = self.spec.rebalance.anchor
        offset = int(timeline.get_loc(anchor))
        n_timestamps = len(timeline)

        # 让 proxy_net_sharpe 随 offset 变化，便于测试 summary。
        value = float(offset + 1)
        performance = pd.DataFrame(
            {
                'sharpe': [value, value + 0.5],
                'cagr': [0.1, 0.2],
                'annualized_volatility': [0.1, 0.2],
                'max_drawdown': [-0.1, -0.2],
                'n_periods': [n_timestamps - 1, n_timestamps - 1],
            },
            index=['gross', 'proxy_net'],
        )
        decision_index = timeline[:-1]
        return SimpleNamespace(
            performance=performance,
            turnover=pd.Series(0.1, index=decision_index),
            exposures=pd.DataFrame(
                {'active_count': np.full(len(decision_index), 2)},
                index=decision_index,
            ),
            rebalance_flags=pd.Series(True, index=decision_index),
            warnings=('FAKE_WARNING',),
            portfolio_returns=pd.DataFrame(index=timeline[1:]),
        )


@pytest.fixture
def fake_runner(monkeypatch):
    _FakeRunner.calls = []
    _FakeRunner.raise_on_run = False
    monkeypatch.setattr(
        robustness, 'DiagnosticPortfolioExperimentRunner', _FakeRunner)
    return _FakeRunner


# ============================================================
# anchor offset 生成
# ============================================================
def test_generate_anchor_offsets_deterministic_and_bounded() -> None:
    timeline = pd.date_range('2024-01-01', periods=10, freq='D')

    offsets = _generate_anchor_offsets(timeline, 3, 3)

    assert offsets == (0, 1, 2)
    assert _generate_anchor_offsets(timeline, 3, 3) == offsets
    assert offsets[0] == 0
    assert len(set(offsets)) == len(offsets)
    assert all(0 <= offset < 3 for offset in offsets)


def test_generate_anchor_offsets_clamps_to_interval() -> None:
    timeline = pd.date_range('2024-01-01', periods=10, freq='D')

    assert _generate_anchor_offsets(timeline, 3, 20) == (0, 1, 2)
    assert _generate_anchor_offsets(timeline, 1, 20) == (0,)


def test_generate_anchor_offsets_evenly_spaced() -> None:
    timeline = pd.date_range('2024-01-01', periods=100, freq='D')

    offsets = _generate_anchor_offsets(timeline, 55, 20)

    assert len(offsets) == 20
    assert offsets[0] == 0
    assert offsets[-1] == 54
    assert list(offsets) == sorted(set(offsets))


@pytest.mark.parametrize('n_anchors', [0, -1, True, 1.5, 'auto'])
def test_generate_anchor_offsets_rejects_invalid_n_anchors(
        n_anchors) -> None:
    timeline = pd.date_range('2024-01-01', periods=10, freq='D')

    with pytest.raises(ValueError):
        _generate_anchor_offsets(timeline, 3, n_anchors)


def test_generate_anchor_offsets_rejects_short_timeline() -> None:
    with pytest.raises(ValueError, match='时间轴太短'):
        _generate_anchor_offsets(
            pd.DatetimeIndex(['2024-01-01']), interval=3, n_anchors=2)


# ============================================================
# summary
# ============================================================
def test_summarize_anchor_runs_basic() -> None:
    runs = {5: [_row(offset, value)
                 for offset, value in enumerate([1.0, 2.0, 3.0, 4.0, 5.0])]}

    summary = _summarize_anchor_runs(runs, 'proxy_net_sharpe')

    assert summary.index.tolist() == [5]
    assert summary.index.name == 'h'
    assert summary.columns.tolist() == SUMMARY_COLUMNS
    assert summary.loc[5, 'count'] == 5
    assert summary.loc[5, 'mean'] == pytest.approx(3.0)
    assert summary.loc[5, 'std'] == pytest.approx(
        float(np.std([1, 2, 3, 4, 5], ddof=1)))
    assert summary.loc[5, 'min'] == pytest.approx(1.0)
    assert summary.loc[5, 'median'] == pytest.approx(3.0)
    assert summary.loc[5, 'max'] == pytest.approx(5.0)
    assert summary.loc[5, 'anchor0'] == pytest.approx(1.0)
    assert summary.loc[5, 'anchor0_percentile'] == pytest.approx(0.2)
    assert summary.attrs['summary_indicator'] == 'proxy_net_sharpe'


def test_summarize_anchor_runs_excludes_nan_from_count() -> None:
    runs = {5: [
        _row(0, 1.0),
        _row(1, np.nan),
        _row(2, 3.0),
    ]}

    summary = _summarize_anchor_runs(runs, 'proxy_net_sharpe')

    assert summary.loc[5, 'count'] == 2
    assert summary.loc[5, 'mean'] == pytest.approx(2.0)
    assert summary.loc[5, 'anchor0'] == pytest.approx(1.0)
    assert summary.loc[5, 'anchor0_percentile'] == pytest.approx(0.5)


def test_summarize_anchor_runs_anchor0_nan_gives_nan_percentile() -> None:
    runs = {5: [_row(0, np.nan), _row(1, 3.0)]}

    summary = _summarize_anchor_runs(runs, 'proxy_net_sharpe')

    assert np.isnan(summary.loc[5, 'anchor0'])
    assert np.isnan(summary.loc[5, 'anchor0_percentile'])
    assert summary.loc[5, 'count'] == 1


def test_summarize_anchor_runs_multiple_h() -> None:
    runs = {
        3: [_row(0, 1.0, interval=3), _row(1, 2.0, interval=3)],
        5: [_row(0, 3.0), _row(1, 5.0)],
    }

    summary = _summarize_anchor_runs(runs, 'proxy_net_sharpe')

    assert summary.index.tolist() == [3, 5]
    assert summary.loc[3, 'mean'] == pytest.approx(1.5)
    assert summary.loc[5, 'mean'] == pytest.approx(4.0)


def test_summarize_anchor_runs_empty() -> None:
    summary = _summarize_anchor_runs({}, 'proxy_net_sharpe')

    assert summary.empty
    assert summary.columns.tolist() == SUMMARY_COLUMNS
    assert summary.index.name == 'h'
    assert summary.attrs['summary_indicator'] == 'proxy_net_sharpe'


# ============================================================
# 单 h 编排
# ============================================================
def test_run_anchor_robustness_contract(
        dataset_factory, signal_factory, fake_runner) -> None:
    ds, signal = _make_inputs(dataset_factory, signal_factory)
    spec = _spec(interval=3)
    timeline = pd.DatetimeIndex(
        ds.main_panel.index.get_level_values('timestamp')
        .unique()).sort_values()

    rows = run_anchor_robustness(signal, ds, spec, n_anchors=20)

    assert [row.offset for row in rows] == [0, 1, 2]
    assert [row.anchor for row in rows] == list(timeline[:3])
    assert len(fake_runner.calls) == 3
    assert [call[2].rebalance.anchor for call in fake_runner.calls] == \
        list(timeline[:3])
    assert all(call[2].rebalance.interval == 3 for call in fake_runner.calls)
    assert all(call[2] is not spec for call in fake_runner.calls)

    first = rows[0]
    assert first.interval == 3
    assert first.gross_sharpe == pytest.approx(1.0)
    assert first.proxy_net_sharpe == pytest.approx(1.5)
    assert first.proxy_net_cagr == pytest.approx(0.2)
    assert first.annualized_volatility == pytest.approx(0.2)
    assert first.max_drawdown == pytest.approx(-0.2)
    assert first.avg_turnover == pytest.approx(0.1)
    assert first.avg_active_count == pytest.approx(2.0)
    assert first.n_rebalances == len(timeline) - 1
    assert first.n_periods_from_anchor == len(timeline) - 1
    assert first.warnings == ('FAKE_WARNING',)
    assert rows[1].n_periods_from_anchor == len(timeline) - 2


def test_run_anchor_robustness_does_not_mutate_inputs(
        dataset_factory, signal_factory, fake_runner) -> None:
    ds, signal = _make_inputs(dataset_factory, signal_factory)
    spec = _spec(interval=3)
    original_rebalance = spec.rebalance
    original_signal_values = signal.values.copy()
    original_panel = ds.main_panel.copy()

    run_anchor_robustness(signal, ds, spec, n_anchors=2)

    assert spec.rebalance is original_rebalance
    assert spec.rebalance.anchor is None
    pd.testing.assert_series_equal(signal.values, original_signal_values)
    pd.testing.assert_frame_equal(ds.main_panel, original_panel)


def test_run_anchor_robustness_common_raises_without_calls(
        dataset_factory, signal_factory, fake_runner) -> None:
    ds, signal = _make_inputs(dataset_factory, signal_factory)

    with pytest.raises(NotImplementedError, match='common'):
        run_anchor_robustness(
            signal, ds, _spec(), n_anchors=3,
            evaluation_sample='common',
        )

    assert fake_runner.calls == []


def test_run_anchor_robustness_propagates_failure(
        dataset_factory, signal_factory, fake_runner) -> None:
    ds, signal = _make_inputs(dataset_factory, signal_factory)
    fake_runner.raise_on_run = True

    with pytest.raises(RuntimeError, match='fake runner failure'):
        run_anchor_robustness(signal, ds, _spec(), n_anchors=1)


# ============================================================
# 多 h 编排
# ============================================================
def test_examine_returns_runs_and_summary(
        dataset_factory, signal_factory, fake_runner) -> None:
    ds, signal = _make_inputs(dataset_factory, signal_factory)
    spec = _spec(interval=3)

    runs, summary = examine_rebalance_timing_luck(
        signal, ds, spec, intervals=[4, 3], n_anchors=2,
        summary_indicator='proxy_net_sharpe',
    )

    assert list(runs) == [3, 4]
    assert len(runs[3]) == 2
    assert len(runs[4]) == 2
    assert summary.index.tolist() == [3, 4]
    assert summary.columns.tolist() == SUMMARY_COLUMNS
    assert summary.attrs['summary_indicator'] == 'proxy_net_sharpe'
    assert summary.loc[3, 'count'] == 2
    assert summary.loc[3, 'mean'] == pytest.approx(2.5)
    assert summary.loc[3, 'anchor0'] == pytest.approx(1.5)
    assert summary.loc[3, 'anchor0_percentile'] == pytest.approx(0.5)
    assert summary.loc[4, 'mean'] == pytest.approx(3.0)
    assert len(fake_runner.calls) == 4


def test_examine_accepts_single_int_interval(
        dataset_factory, signal_factory, fake_runner) -> None:
    ds, signal = _make_inputs(dataset_factory, signal_factory)

    runs, summary = examine_rebalance_timing_luck(
        signal, ds, _spec(interval=3), intervals=3, n_anchors=1)

    assert list(runs) == [3]
    assert summary.index.tolist() == [3]
    assert len(runs[3]) == 1


def test_examine_auto_anchor_count(
        dataset_factory, signal_factory, fake_runner) -> None:
    ds, signal = _make_inputs(
        dataset_factory, signal_factory, n_periods=60)

    runs, _ = examine_rebalance_timing_luck(
        signal, ds, _spec(interval=25), intervals=[25], n_anchors='auto')
    assert len(runs[25]) == 20

    fake_runner.calls.clear()
    runs_short, _ = examine_rebalance_timing_luck(
        signal, ds, _spec(interval=3), intervals=[3], n_anchors='auto')
    assert len(runs_short[3]) == 3


def test_examine_summary_indicator_controls_summary(
        dataset_factory, signal_factory, fake_runner) -> None:
    ds, signal = _make_inputs(dataset_factory, signal_factory)

    _, summary = examine_rebalance_timing_luck(
        signal, ds, _spec(interval=3), intervals=[3], n_anchors=2,
        summary_indicator='proxy_net_cagr',
    )

    assert summary.loc[3, 'mean'] == pytest.approx(0.2)
    assert summary.loc[3, 'anchor0'] == pytest.approx(0.2)
    assert summary.loc[3, 'anchor0_percentile'] == pytest.approx(1.0)
    assert summary.attrs['summary_indicator'] == 'proxy_net_cagr'


def test_examine_plot_returns_axes(
        dataset_factory, signal_factory, fake_runner) -> None:
    from matplotlib.axes import Axes

    ds, signal = _make_inputs(dataset_factory, signal_factory)

    runs, summary, ax = examine_rebalance_timing_luck(
        signal, ds, _spec(interval=3), intervals=[3], n_anchors=2,
        summary_indicator='proxy_net_cagr', plot=True,
    )

    assert isinstance(ax, Axes)
    assert len(runs[3]) == 2
    assert summary.attrs['summary_indicator'] == 'proxy_net_cagr'
    assert ax.get_ylabel() == 'proxy-net CAGR'
    # 相位散点 + anchor0 星号。
    assert len(ax.collections) == 2
    # anchor0 星号使用 summary_indicator，而不是固定 Sharpe。
    anchor_star = ax.collections[-1].get_offsets()[0]
    assert anchor_star[1] == pytest.approx(0.2)


def test_examine_common_raises_without_calls(
        dataset_factory, signal_factory, fake_runner) -> None:
    ds, signal = _make_inputs(dataset_factory, signal_factory)

    with pytest.raises(NotImplementedError, match='common'):
        examine_rebalance_timing_luck(
            signal, ds, _spec(), intervals=[3],
            evaluation_sample='common',
        )

    assert fake_runner.calls == []


def test_examine_empty_intervals(
        dataset_factory, signal_factory, fake_runner) -> None:
    ds, signal = _make_inputs(dataset_factory, signal_factory)

    runs, summary = examine_rebalance_timing_luck(
        signal, ds, _spec(), intervals=[])

    assert runs == {}
    assert summary.empty
    assert summary.columns.tolist() == SUMMARY_COLUMNS
    assert summary.index.name == 'h'


def test_examine_rejects_unknown_indicator(
        dataset_factory, signal_factory, fake_runner) -> None:
    ds, signal = _make_inputs(dataset_factory, signal_factory)

    with pytest.raises(ValueError, match='summary_indicator'):
        examine_rebalance_timing_luck(
            signal, ds, _spec(), intervals=[3], summary_indicator='bad')


@pytest.mark.parametrize('bad_interval', [0, -1, True, 1.5, '3'])
def test_examine_rejects_bad_intervals(
        dataset_factory, signal_factory, fake_runner, bad_interval) -> None:
    ds, signal = _make_inputs(dataset_factory, signal_factory)

    with pytest.raises(ValueError):
        examine_rebalance_timing_luck(
            signal, ds, _spec(), intervals=[bad_interval])
