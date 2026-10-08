"""tests/factors/diagnostic_portfolio/test_accounting.py — 会计与绩效纯函数测试。

覆盖 ``factors.diagnostic_portfolio.accounting`` 中已实现的纯函数：

    - compute_weight_changes_turnover_cost  权重变化、换手与决策成本
    - compute_contribution            下一期产品贡献与缺失收益政策
    - compute_portfolio_returns       毛收益、成本与代理净收益
    - compute_nav                     总净值与代理净净值
    - compute_exposure                总/净/多/空暴露与活跃品种数
    - compute_attribution_product     分品种 PnL 归因
    - compute_attribution_direction   按权重符号划分的多空方向归因
    - compute_attribution_sector      按板块汇总的收益贡献
    - compute_performance             核心绩效指标（开发方案 §18）

所有用例仅使用 synthetic 面板，不依赖 db 或仓库外行情。期望值尽量手工
复算（对齐开发方案 §15、§16、§17、§18），而不是复刻实现路径。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.diagnostic_portfolio.accounting import (
    PERFORMANCE_COLUMNS,
    PERFORMANCE_INDEX,
    compute_attribution_direction,
    compute_attribution_product,
    compute_attribution_sector,
    compute_contribution,
    compute_exposure,
    compute_nav,
    compute_performance,
    compute_portfolio_returns,
    compute_weight_changes_turnover_cost,
)
from factors.diagnostic_portfolio.models import CostConfig


# ============================================================
# 辅助函数
# ============================================================
def _multi_index(timestamps: list[str], products: list[str]) -> pd.MultiIndex:
    """构造升序的 MultiIndex[timestamp, product]。"""
    return pd.MultiIndex.from_product(
        [pd.to_datetime(timestamps), products],
        names=['timestamp', 'product'],
    )


# ============================================================
# 15. 换手与成本
# ============================================================
def test_turnover_initial_build_reversal_exit_and_freeze() -> None:
    """初始建仓、减仓、平仓、反向、非调仓零换手均按单边绝对权重变化。"""
    timestamps = ['2024-01-01', '2024-01-02', '2024-01-03', '2024-01-04']
    idx = _multi_index(timestamps, ['A', 'B'])
    weights = pd.Series(
        [
            0.5, -0.5,     # t0 初始建仓
            0.2, 0.0,      # t1 A 减仓，B 平仓
            -0.2, 0.3,     # t2 A 反向 +0.2 -> -0.2，B 0 -> 0.3
            -0.2, 0.3,     # t3 权重不变（非调仓冻结）
        ],
        index=idx,
    )

    weight_changes, turnover, cost = compute_weight_changes_turnover_cost(
        weights, CostConfig(rate=0.0003))

    expected_weight_changes = pd.Series(
        [0.5, -0.5, -0.3, 0.5, -0.4, 0.3, 0.0, 0.0], index=idx)
    expected_turnover = pd.Series(
        [1.0, 0.8, 0.4 + 0.3, 0.0], index=idx.levels[0],
    )
    pd.testing.assert_series_equal(
        weight_changes, expected_weight_changes, check_names=False)
    pd.testing.assert_series_equal(
        turnover, expected_turnover, check_names=False)
    pd.testing.assert_series_equal(
        cost, 0.0003 * expected_turnover, check_names=False)


def test_uniform_cost_accepts_integer_rate() -> None:
    """回归：CostConfig 允许标量为 int，统一成本率分支不得断言 float。"""
    idx = _multi_index(['2024-01-01'], ['A', 'B'])
    weights = pd.Series([0.5, -0.5], index=idx)

    _, turnover, cost = compute_weight_changes_turnover_cost(
        weights, CostConfig(rate=0))

    assert turnover.tolist() == [1.0]
    assert cost.tolist() == [0.0]


def test_per_product_cost_matches_uppercased_products() -> None:
    """回归：按品种成本需与 CostConfig 的大写规范化一致（小写品种也命中）。"""
    timestamps = ['2024-01-01', '2024-01-02']
    idx = _multi_index(timestamps, ['a', 'b'])
    weights = pd.Series([0.5, -0.5, 0.2, 0.0], index=idx)

    _, turnover, cost = compute_weight_changes_turnover_cost(
        weights, CostConfig(rate={'A': 0.001, 'B': 0.002}))

    expected_turnover = pd.Series([1.0, 0.8], index=idx.levels[0])
    # t0: 0.5*0.001 + 0.5*0.002 = 0.0015
    # t1: 0.3*0.001 + 0.5*0.002 = 0.0013
    expected_cost = pd.Series([0.0015, 0.0013], index=idx.levels[0])
    pd.testing.assert_series_equal(
        turnover, expected_turnover, check_names=False)
    pd.testing.assert_series_equal(
        cost, expected_cost, check_names=False)


def test_per_product_cost_missing_product_raises() -> None:
    """按品种成本率未覆盖某品种时，必须报错而不是静默产生 NaN 成本。"""
    idx = _multi_index(['2024-01-01'], ['A', 'B'])
    weights = pd.Series([0.5, -0.5], index=idx)

    with pytest.raises(ValueError, match='未覆盖品种'):
        compute_weight_changes_turnover_cost(weights, CostConfig(rate={'A': 0.001}))


# ============================================================
# 16. 贡献、收益与净值
# ============================================================
def test_compute_contribution_weight_times_next_return() -> None:
    """产品贡献等于形成收益时权重乘下一期 True_Return。"""
    idx = _multi_index(['2024-01-01', '2024-01-02'], ['A', 'B'])
    aligned_df = pd.DataFrame(
        {
            'true_return': [0.02, -0.01, -0.01, 0.03],
            'eligible': [True, True, True, True],
            'weight': [0.5, -0.5, 0.2, 0.0],
        },
        index=idx,
    )

    contributions, _, _ = compute_contribution(
        aligned_df, missing_return_policy='raise')

    expected = pd.Series(
        [0.5 * 0.02, -0.5 * -0.01, 0.2 * -0.01, 0.0 * 0.03], index=idx)
    pd.testing.assert_series_equal(
        contributions, expected, check_names=False)


def test_contribution_zero_weight_missing_return_is_zero() -> None:
    """零权重对应的缺失收益贡献为零，不报错。"""
    idx = _multi_index(['2024-01-01'], ['A', 'B'])
    aligned_df = pd.DataFrame(
        {
            'true_return': [np.nan, 0.01],
            'eligible': [True, True],
            'weight': [0.0, 0.4],
        },
        index=idx,
    )

    contributions, warnings, _ = compute_contribution(
        aligned_df, missing_return_policy='raise')

    assert contributions.tolist() == [0.0, 0.004]
    assert warnings == ()


def test_contribution_nonzero_weight_missing_return_raises() -> None:
    """非零权重对应的收益缺失属于致命错误。"""
    idx = _multi_index(['2024-01-01'], ['A', 'B'])
    aligned_df = pd.DataFrame(
        {
            'true_return': [np.nan, 0.01],
            'eligible': [True, True],
            'weight': [0.1, 0.4],
        },
        index=idx,
    )

    with pytest.raises(ValueError, match='下一期收益为缺失'):
        compute_contribution(aligned_df, missing_return_policy='raise')


def test_contribution_keeps_ineligible_rows_and_timestamps() -> None:
    """回归：不得按 eligible 过滤掉整行/整个时间戳，否则结果表索引不完整。"""
    idx = _multi_index(['2024-01-01', '2024-01-02'], ['A', 'B'])
    aligned_df = pd.DataFrame(
        {
            # 第二个时间戳整体不在品种池内，且收益缺失、权重为零。
            'true_return': [0.01, np.nan, np.nan, np.nan],
            'eligible': [True, True, False, False],
            'weight': [0.5, 0.0, 0.0, 0.0],
        },
        index=idx,
    )

    contributions, _, _ = compute_contribution(
        aligned_df, missing_return_policy='raise')

    assert len(contributions) == len(idx)
    assert contributions.index.equals(idx)
    assert contributions.tolist() == [0.005, 0.0, 0.0, 0.0]


def test_contribution_nonzero_weight_missing_return_zero_fills() -> None:
    """策略 default_to_zero_and_warning：缺失收益按 0 填充并留痕，不报错。"""
    idx = _multi_index(['2024-01-01'], ['A', 'B'])
    aligned_df = pd.DataFrame(
        {
            'true_return': [np.nan, 0.01],
            'eligible': [True, True],
            'weight': [0.1, 0.4],
        },
        index=idx,
    )

    contributions, warnings, metadata = compute_contribution(
        aligned_df, missing_return_policy='default_to_zero_and_warning')

    assert contributions.tolist() == [0.0, 0.004]
    assert len(warnings) == 1
    assert warnings[0].startswith(
        'MISSING_RETURN_ZERO_FILLED:policy=default_to_zero_and_warning')
    assert 'cells=1' in warnings[0] and 'products=1' in warnings[0]
    raw = metadata['missing_return_0_filled_raw_entries']
    assert len(raw) == 1 and raw.index.equals(
        idx[[0]]) and raw['weight'].tolist() == [0.1]


def test_compute_portfolio_returns_gross_cost_and_net() -> None:
    """毛收益为贡献加总；返回 gross_return / cost / proxy_net_return 三列。"""
    idx = _multi_index(['2024-01-01', '2024-01-02'], ['A', 'B'])
    contributions = pd.Series([0.01, -0.005, 0.02, 0.003], index=idx)
    costs_shifted = pd.Series([0.0005, 0.0002], index=idx.levels[0])

    returns = compute_portfolio_returns(contributions, costs_shifted)

    assert list(returns.columns) == [
        'gross_return', 'cost', 'proxy_net_return']
    expected_gross = pd.Series([0.005, 0.023], index=idx.levels[0])
    pd.testing.assert_series_equal(
        returns['gross_return'], expected_gross, check_names=False)
    pd.testing.assert_series_equal(
        returns['cost'], costs_shifted, check_names=False)
    pd.testing.assert_series_equal(
        returns['proxy_net_return'], expected_gross - costs_shifted,
        check_names=False)


def test_portfolio_returns_cost_column_aligned_to_gross_index() -> None:
    """回归：cost 列必须 reindex 到毛收益时间轴，不能在 concat 中并入多余时点。"""
    idx = _multi_index(['2024-01-01', '2024-01-02'], ['A', 'B'])
    contributions = pd.Series([0.01, -0.005, 0.02, 0.003], index=idx)
    # 成本多出一个决策末端的时点（例如尚未 shift 掉的最后一期）。
    extra_index = pd.to_datetime(
        ['2024-01-01', '2024-01-02', '2024-01-03'])
    costs_shifted = pd.Series([0.0005, 0.0002, 0.001], index=extra_index)

    returns = compute_portfolio_returns(contributions, costs_shifted)

    assert returns.index.tolist() == list(idx.levels[0])
    assert returns['cost'].tolist() == [0.0005, 0.0002]


def test_compute_nav_compound() -> None:
    """净值由收益序列复利累积，返回 gross_nav / proxy_net_nav 两列。"""
    idx = pd.to_datetime(['2024-01-01', '2024-01-02'])
    gross = pd.Series([0.1, -0.05], index=idx)
    net = pd.Series([0.09, -0.06], index=idx)

    nav = compute_nav(gross, net)

    assert list(nav.columns) == ['gross_nav', 'proxy_net_nav']
    pd.testing.assert_series_equal(
        nav['gross_nav'], pd.Series([1.1, 1.1 * 0.95], index=idx),
        check_names=False)
    pd.testing.assert_series_equal(
        nav['proxy_net_nav'], pd.Series([1.09, 1.09 * 0.94], index=idx),
        check_names=False)


@pytest.mark.parametrize('gross, net', [
    ([0.05, -1.0], [0.05, -1.0]),
    ([0.05, 0.02], [0.05, -1.2]),
])
def test_compute_nav_rejects_return_at_or_below_minus_one(
        gross: list[float], net: list[float]) -> None:
    """任一单期收益 <= -1 时净值无有效复利含义，实验失败。"""
    idx = pd.to_datetime(['2024-01-01', '2024-01-02'])
    with pytest.raises(ValueError, match='小于等于 -1'):
        compute_nav(pd.Series(gross, index=idx), pd.Series(net, index=idx))


# ============================================================
# 17. 暴露与归因
# ============================================================
def test_compute_exposure() -> None:
    """总/净/多/空暴露与活跃品种数。"""
    idx = _multi_index(['2024-01-01', '2024-01-02'], ['A', 'B'])
    weights = pd.Series([0.5, -0.3, 0.2, 0.1], index=idx)

    exposure = compute_exposure(weights, active_weight_threshold=0.25)

    assert list(exposure.columns) == [
        'gross_exposure', 'net_exposure', 'long_exposure',
        'short_exposure', 'active_count']
    expected_index = idx.levels[0]
    pd.testing.assert_series_equal(
        exposure['gross_exposure'], pd.Series([0.8, 0.3], index=expected_index),
        check_names=False)
    pd.testing.assert_series_equal(
        exposure['net_exposure'], pd.Series([0.2, 0.3], index=expected_index),
        check_names=False)
    pd.testing.assert_series_equal(
        exposure['long_exposure'], pd.Series([0.5, 0.3], index=expected_index),
        check_names=False)
    pd.testing.assert_series_equal(
        exposure['short_exposure'], pd.Series([-0.3, 0.0], index=expected_index),
        check_names=False)
    # threshold=0.25：t0 两侧都超过阈值，t1 无一超过。
    pd.testing.assert_series_equal(
        exposure['active_count'], pd.Series([2, 0], index=expected_index),
        check_names=False)


def test_compute_attribution_product() -> None:
    """分品种汇总贡献、占比、正负期数与多空方向贡献拆分。"""
    idx = _multi_index(['2024-01-01', '2024-01-02'], ['A', 'B'])
    contributions = pd.Series([0.01, -0.005, 0.02, 0.003], index=idx)
    weights_shifted = pd.Series([0.5, -0.3, 0.2, 0.1], index=idx)

    attribution = compute_attribution_product(
        contributions, weights_shifted, active_weight_threshold=0.05)

    assert attribution.index.name == 'product'
    assert attribution.loc['A', 'gross_contribution_sum'] == pytest.approx(
        0.03)
    assert attribution.loc['B', 'gross_contribution_sum'] == pytest.approx(
        -0.002)
    total = 0.028
    assert attribution.loc['A', 'contribution_share'] == pytest.approx(
        0.03 / total)
    assert attribution.loc['B', 'contribution_share'] == pytest.approx(
        -0.002 / total)
    assert attribution.loc['A', 'positive_period_count'] == 2
    assert attribution.loc['B', 'positive_period_count'] == 1
    assert attribution.loc['A', 'negative_period_count'] == 0
    assert attribution.loc['B', 'negative_period_count'] == 1
    assert attribution.loc['A', 'active_period_count'] == 2
    assert attribution.loc['B', 'active_period_count'] == 2
    assert attribution.loc['A', 'long_contribution_sum'] == pytest.approx(
        0.03)
    assert attribution.loc['B', 'long_contribution_sum'] == pytest.approx(
        0.003)
    assert attribution.loc['B', 'short_contribution_sum'] == pytest.approx(
        -0.005)
    # 多空贡献之和必须等于组合毛收益贡献。
    long_sum = attribution['long_contribution_sum'].sum()
    short_sum = attribution['short_contribution_sum'].sum()
    assert long_sum + short_sum == pytest.approx(contributions.sum())


def test_attribution_product_share_is_nan_when_total_is_zero() -> None:
    """回归：总贡献为零时占比为 NaN，而不是 ±inf。"""
    idx = _multi_index(['2024-01-01'], ['A', 'B'])
    contributions = pd.Series([0.01, -0.01], index=idx)
    weights_shifted = pd.Series([0.5, -0.5], index=idx)

    attribution = compute_attribution_product(
        contributions, weights_shifted, active_weight_threshold=0.0)

    assert attribution['gross_contribution_sum'].sum() == 0
    assert attribution['contribution_share'].isna().all()


def test_attribution_direction_uses_weight_sign() -> None:
    """方向归因按权重符号划分：多头亏损仍计入 long_contribution。"""
    idx = _multi_index(['2024-01-01', '2024-01-02'], ['A', 'B'])
    contributions = pd.Series([0.01, -0.005, -0.002, 0.003], index=idx)
    weights_shifted = pd.Series([0.5, -0.3, 0.2, 0.1], index=idx)

    direction = compute_attribution_direction(contributions, weights_shifted)

    assert list(direction.columns) == [
        'long_contribution', 'short_contribution']
    assert direction.index.tolist() == list(idx.levels[0])
    # t0：多头仅 A -> 0.01；空头仅 B -> -0.005。
    # t1：A、B 均为多头（含 A 的负贡献 -0.002）-> 0.001；无空头 -> 0。
    assert direction.loc['2024-01-01', 'long_contribution'] == \
        pytest.approx(0.01)
    assert direction.loc['2024-01-01', 'short_contribution'] == \
        pytest.approx(-0.005)
    assert direction.loc['2024-01-02', 'long_contribution'] == \
        pytest.approx(-0.002 + 0.003)
    assert direction.loc['2024-01-02', 'short_contribution'] == \
        pytest.approx(0.0)
    # 某侧无持仓时时点必须补 0，而不是 NaN；多空之和等于当日毛收益。
    assert not direction.isna().any().any()
    per_day_total = direction.sum(axis=1)
    assert per_day_total.tolist() == pytest.approx(
        contributions.groupby(level='timestamp').sum().tolist())


def test_compute_attribution_sector_with_unclassified() -> None:
    """按板块汇总贡献；分类缺失的产品归入 unclassified 而非被删除。"""
    idx = _multi_index(
        ['2024-01-01', '2024-01-02'], ['A', 'B', 'C'])
    contributions = pd.Series(
        [0.01, -0.005, 0.004, 0.02, 0.003, -0.001], index=idx)
    classification = pd.DataFrame(
        {'product': ['A', 'B'], 'sector': ['black', 'agri']})

    sector = compute_attribution_sector(
        contributions, classification, sector_col='sector')

    assert sector.index.names == ['timestamp', 'sector']
    # groupby 按 (timestamp, sector) 字母序输出：agri < black < unclassified。
    expected = pd.Series(
        [-0.005, 0.01, 0.004, 0.003, 0.02, -0.001],
        index=pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp('2024-01-01'), 'agri'),
                (pd.Timestamp('2024-01-01'), 'black'),
                (pd.Timestamp('2024-01-01'), 'unclassified'),
                (pd.Timestamp('2024-01-02'), 'agri'),
                (pd.Timestamp('2024-01-02'), 'black'),
                (pd.Timestamp('2024-01-02'), 'unclassified'),
            ],
            names=['timestamp', 'sector'],
        ),
    )
    pd.testing.assert_series_equal(sector, expected, check_names=False)
    # 板块贡献之和必须等于当日组合毛收益。
    per_day = sector.groupby(level='timestamp').sum()
    assert per_day.tolist() == pytest.approx(
        contributions.groupby(level='timestamp').sum().tolist())


def test_compute_attribution_sector_requires_columns() -> None:
    """classification 缺少 product 或 sector 列时应报错。"""
    idx = _multi_index(['2024-01-01'], ['A'])
    contributions = pd.Series([0.01], index=idx)

    with pytest.raises(AssertionError, match='product'):
        compute_attribution_sector(
            contributions, pd.DataFrame({'sector': ['black']}),
            sector_col='sector')
    with pytest.raises(AssertionError, match='sector'):
        compute_attribution_sector(
            contributions, pd.DataFrame({'product': ['A']}),
            sector_col='sector')


# ============================================================
# 18. 核心绩效指标
# ============================================================
def test_compute_performance_metrics() -> None:
    """总收益、算术年化、CAGR、年化波动率、Sharpe、最大回撤逐项复算。"""
    idx = pd.to_datetime(
        ['2024-01-01', '2024-01-02', '2024-01-03'])
    gross = pd.Series([0.01, -0.02, 0.03], index=idx)
    net = gross - 0.001

    performance, warnings = compute_performance(gross, net, 252)

    assert warnings == ()
    assert list(performance.index) == list(PERFORMANCE_INDEX)
    assert list(performance.columns) == list(PERFORMANCE_COLUMNS)
    assert performance.loc['gross', 'n_periods'] == 3
    assert performance.loc['gross', 'total_return'] == pytest.approx(
        1.01 * 0.98 * 1.03 - 1.0)
    assert performance.loc['gross', 'annualized_arithmetic_return'] == \
        pytest.approx(252 * (0.01 - 0.02 + 0.03) / 3)
    assert performance.loc['gross', 'annualized_volatility'] == \
        pytest.approx(np.sqrt(252) * np.std(
            [0.01, -0.02, 0.03], ddof=1))
    assert performance.loc['gross', 'sharpe'] == pytest.approx(
        np.sqrt(252) * np.mean([0.01, -0.02, 0.03]) /
        np.std([0.01, -0.02, 0.03], ddof=1))
    assert performance.loc['gross', 'cagr'] == pytest.approx(
        (1.01 * 0.98 * 1.03) ** (252 / 3) - 1.0)
    # 峰值包含初始净值 1：第二期回撤 = 0.9898 / 1.01 - 1 = -0.02。
    assert performance.loc['gross', 'max_drawdown'] == pytest.approx(-0.02)


def test_performance_max_drawdown_includes_initial_nav() -> None:
    """回归：首期收益为负时，回撤应相对初始净值 1 计入。"""
    idx = pd.to_datetime(['2024-01-01'])
    returns = pd.Series([-0.05], index=idx)

    performance, _ = compute_performance(returns, returns, 252)

    assert performance.loc['gross', 'max_drawdown'] == pytest.approx(-0.05)


def test_performance_sharpe_undefined_returns_nan_with_warning() -> None:
    """标准差为零或有效期数少于 2 时 Sharpe 为 NaN，并产生稳定代码警告。"""
    idx = pd.to_datetime(['2024-01-01', '2024-01-02', '2024-01-03'])
    flat = pd.Series([0.0, 0.0, 0.0], index=idx)

    performance, warnings = compute_performance(flat, flat, 252)

    assert performance.loc['gross', 'sharpe'] != performance.loc['gross', 'sharpe']
    assert performance['sharpe'].isna().all()
    assert set(warnings) == {
        'SHARPE_UNDEFINED:gross', 'SHARPE_UNDEFINED:proxy_net'}

    single = pd.Series([0.05], index=idx[:1])
    performance_one, warnings_one = compute_performance(single, single, 252)
    assert performance_one.loc['gross', 'sharpe'] != \
        performance_one.loc['gross', 'sharpe']
    assert 'SHARPE_UNDEFINED:gross' in warnings_one


def test_performance_empty_returns() -> None:
    """空收益序列返回 n_periods=0 与 NaN 指标，不抛异常。"""
    empty = pd.Series([], dtype='float64')

    performance, warnings = compute_performance(empty, empty, 252)

    assert performance['n_periods'].tolist() == [0, 0]
    assert performance['total_return'].isna().all()
    assert performance['max_drawdown'].isna().all()
    assert set(warnings) == {
        'SHARPE_UNDEFINED:gross', 'SHARPE_UNDEFINED:proxy_net'}


# ============================================================
# DecisionLedger：权重/警告/协方差元数据记账
# ============================================================
def test_decision_ledger_to_weights_series() -> None:
    """决策记录册输出 MultiIndex[timestamp, product] 权重。"""
    from factors.diagnostic_portfolio.accounting import (
        DecisionLedger, DecisionRecord)
    from factors.diagnostic_portfolio.weights import WeightMeta

    ts0 = pd.Timestamp('2024-01-01')
    ts1 = pd.Timestamp('2024-01-02')
    ledger = DecisionLedger()
    ledger.append(DecisionRecord(
        timestamp=ts0, weights=pd.Series([0.5, -0.5], index=['A', 'B']),
        rebalance=True, weight_warnings=(), weight_meta=WeightMeta(None)))
    ledger.append(DecisionRecord(
        timestamp=ts1, weights=pd.Series([0.2, -0.2], index=['A', 'B']),
        rebalance=False, weight_warnings=(), weight_meta=WeightMeta(None)))

    weights = ledger.to_weights_series()

    assert tuple(weights.index.names) == ('timestamp', 'product')
    assert weights.index.is_unique
    assert weights.tolist() == [0.5, -0.5, 0.2, -0.2]


def test_decision_ledger_warnings_prefix_timestamp() -> None:
    """回归：警告需带决策时点前缀，且空警告记录被跳过。"""
    from factors.diagnostic_portfolio.accounting import (
        DecisionLedger, DecisionRecord)
    from factors.diagnostic_portfolio.weights import WeightMeta

    ts = pd.Timestamp('2024-01-02')
    ledger = DecisionLedger()
    ledger.append(DecisionRecord(
        timestamp=pd.Timestamp('2024-01-01'),
        weights=pd.Series([0.0], index=['A']),
        rebalance=True, weight_warnings=(), weight_meta=WeightMeta(None)))
    ledger.append(DecisionRecord(
        timestamp=ts, weights=pd.Series([0.0], index=['A']),
        rebalance=True, weight_warnings=('w1', 'w2'),
        weight_meta=WeightMeta(None)))

    warnings = ledger.warnings

    assert warnings == (f'{ts.isoformat()} : w1；w2',)


def test_decision_ledger_metadata_covariance_estimation() -> None:
    """协方差估计元数据按决策时点汇总为 DataFrame。"""
    from factors.diagnostic_portfolio.accounting import (
        DecisionLedger, DecisionRecord)
    from factors.diagnostic_portfolio.weights import WeightMeta

    ts0 = pd.Timestamp('2024-01-01')
    ts1 = pd.Timestamp('2024-01-02')
    ledger = DecisionLedger()
    ledger.append(DecisionRecord(
        timestamp=ts0, weights=pd.Series([0.0], index=['A']),
        rebalance=True, weight_warnings=(),
        weight_meta=WeightMeta(
            pd.Series({'valid_prod_count': 2, 'min_eigen': 0.1}))))
    ledger.append(DecisionRecord(
        timestamp=ts1, weights=pd.Series([0.0], index=['A']),
        rebalance=True, weight_warnings=(), weight_meta=WeightMeta(None)))

    meta = ledger.metadata

    cov_meta = meta['covariance_estimation']
    assert cov_meta is not None
    assert list(cov_meta.index) == [ts0]
    assert cov_meta.loc[ts0, 'valid_prod_count'] == 2


def test_decision_ledger_metadata_without_covariance_is_none() -> None:
    from factors.diagnostic_portfolio.accounting import (
        DecisionLedger, DecisionRecord)
    from factors.diagnostic_portfolio.weights import WeightMeta

    ledger = DecisionLedger()
    ledger.append(DecisionRecord(
        timestamp=pd.Timestamp('2024-01-01'),
        weights=pd.Series([0.0], index=['A']),
        rebalance=True, weight_warnings=(), weight_meta=WeightMeta(None)))

    assert ledger.metadata['covariance_estimation'] is None


def test_weight_changes_sorted_internally() -> None:
    """回归：权重输入乱序时，内部排序后再计算换手（不能按输入顺序 diff）。"""
    idx = _multi_index(['2024-01-01', '2024-01-02'], ['A'])
    ordered = pd.Series([0.5, 0.2], index=idx)
    shuffled = ordered.iloc[::-1]

    _, turnover_ordered, _ = compute_weight_changes_turnover_cost(
        ordered, CostConfig(rate=0.0))
    _, turnover_shuffled, _ = compute_weight_changes_turnover_cost(
        shuffled, CostConfig(rate=0.0))

    pd.testing.assert_series_equal(
        turnover_ordered, turnover_shuffled, check_names=False)
