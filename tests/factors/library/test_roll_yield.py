"""展期因子批量计算的数值、合约选择和输入契约回归测试。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.library.basic import RollYieldFactor


COMBINATIONS = ('near_next', 'near_dominant', 'near_farthest', 'dominant_secondary')


def _frame(product='RB', dtes=(0, 30, 60, 365)):
    """构造含期限边界、主力和持仓量并列次主力的截面。"""
    ts = pd.Timestamp('2026-01-02')
    return pd.DataFrame(
        {'Close': [110., 100., 90., 80.],
         'Settle': [120., 105., 95., 85.],
         'last_trade_date': ts + pd.to_timedelta(dtes, unit='D'),
         'is_dominant': [False, True, False, False],
         'OpenInterest': [10., 100., 50., 50.]},
        index=pd.MultiIndex.from_tuples(
            [(ts, product, f'{product}{i}') for i in range(4)],
            names=['timestamp', 'product', 'contract']))


@pytest.mark.parametrize('combo', COMBINATIONS)
@pytest.mark.parametrize('price_col', ['Close', 'Settle'])
def test_roll_yield_formula_and_inclusive_dte_bounds(combo, price_col):
    """四种组合应符合公式，DTE 两端点均可参与计算，输入不被修改。"""
    data = _frame()
    original = data.copy(deep=True)
    factor = RollYieldFactor(combination=combo, price_col=price_col, volnorm=False)
    near, far = {'near_next': (0, 1), 'near_dominant': (0, 1),
                 'near_farthest': (0, 3), 'dominant_secondary': (1, 2)}[combo]
    prices = data[price_col].to_numpy()
    dtes = [0, 30, 60, 365]
    expected = (prices[near] / prices[far] - 1) / (dtes[far] - dtes[near]) * 365

    result = factor.compute(data)

    assert result.iloc[0] == expected
    assert result.name == factor.name
    assert result.index.names == ['timestamp', 'product']
    assert factor.warnings == []
    pd.testing.assert_frame_equal(data, original)
    pd.testing.assert_series_equal(result, factor.compute({'term_structure': data}))


@pytest.mark.parametrize('combo', COMBINATIONS)
def test_roll_yield_matches_scalar_selection_on_shuffled_groups(combo):
    """批量结果应与原逐截面合约选择一致，包括乱序、缺数据及过滤空组。"""
    rng = np.random.default_rng(20261101)
    frames = []
    for i in range(32):
        frame = _frame(product=f'P{i:02}', dtes=tuple(rng.choice(500, 4, replace=False)))
        frame['OpenInterest'] = rng.integers(1, 4, size=4).astype(float)
        frame['is_dominant'] = False
        if i % 5:
            frame.iloc[i % 4, frame.columns.get_loc('is_dominant')] = True
        if i % 7 == 0:
            frame['last_trade_date'] = pd.NaT
        if i % 11 == 0:
            frame['Close'] = np.nan
        frames.append(frame)
    data = pd.concat(frames).sample(frac=1, random_state=42)
    factor = RollYieldFactor(combination=combo, near_dte_min=30,
                             far_dte_max=300, volnorm=False)
    expected = {}
    warnings = []
    for (ts, prod), group in data.groupby(level=['timestamp', 'product']):
        group = group.droplevel(['timestamp', 'product']).copy()
        group['days_to_last_trade'] = (group['last_trade_date'] - ts).dt.days
        group = factor._filter_contracts_dte(group)
        prefix = f'timestamp={ts}, product={prod}:'
        if len(group) <= 1:
            expected[ts, prod] = np.nan
            warnings.append(prefix + '满足 days_to_last_trade 大于30 且小于300'
                            f'的合约数量为 {len(group)}，无法计算展期因子。')
            continue
        near, far, message = factor._select_contract_pair(group)
        if near is None or far is None:
            expected[ts, prod] = np.nan
            warnings.append(prefix + f'根据 combination ={combo} 选择合约时出错。'
                            f'错误原因为 {message}')
        else:
            expected[ts, prod] = factor._compute_roll_yield(group, near, far)
    expected = pd.Series(expected, name=factor.name).rename_axis(['timestamp', 'product'])

    actual = factor.compute(data)

    pd.testing.assert_series_equal(actual, expected, check_exact=True)
    assert factor.warnings == warnings
    factor.compute(_frame(dtes=(30, 60, 90, 120)))
    assert factor.warnings == []


def test_secondary_tie_uses_input_order_and_excludes_higher_interest():
    """次主力持仓并列时选输入中的第一张，且剔除持仓超过主力者。"""
    data = _frame().iloc[[3, 2, 1, 0]]
    factor = RollYieldFactor(combination='dominant_secondary', volnorm=False)
    assert factor.compute(data).iloc[0] == (100 / 80 - 1) / (365 - 30) * 365
    data.loc[(slice(None), slice(None), 'RB3'), 'OpenInterest'] = 101.
    assert factor.compute(data).iloc[0] == (100 / 90 - 1) / 30 * 365


@pytest.mark.parametrize('combo', ['near_next', 'near_dominant', 'near_farthest'])
def test_tied_nearest_maturity_returns_nan_and_warning(combo):
    """最近到期日并列时，仅跳过歧义截面并记录结构化警告。"""
    factor = RollYieldFactor(combination=combo, volnorm=False)

    result = factor.compute(_frame(dtes=(0, 0, 60, 90)))

    assert result.isna().all()
    role = '近月或次近月' if combo == 'near_next' else '近月'
    assert factor.warnings == [
        'timestamp=2026-01-02 00:00:00, product=RB:'
        f'根据 combination ={combo} 选择合约时出错。'
        f'错误原因为 error: 合约到期日排名存在并列，无法唯一确定{role}合约'
    ]


def test_tied_second_maturity_returns_nan_and_warning():
    """次近月到期日并列时，near_next 不应猜测具体合约。"""
    factor = RollYieldFactor(combination='near_next', volnorm=False)

    result = factor.compute(_frame(dtes=(0, 30, 30, 90)))

    assert result.isna().all()
    assert factor.warnings == [
        'timestamp=2026-01-02 00:00:00, product=RB:'
        '根据 combination =near_next 选择合约时出错。'
        '错误原因为 error: 合约到期日排名存在并列，无法唯一确定近月或次近月合约'
    ]


def test_ambiguous_group_does_not_interrupt_valid_groups():
    """批次含歧义截面时，其他截面仍应正常计算且索引保持完整。"""
    valid = _frame(product='RB', dtes=(0, 30, 60, 90))
    ambiguous = _frame(product='FU', dtes=(0, 0, 60, 90)).copy()
    ambiguous.index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp('2026-01-03'), 'FU', f'FU{i}') for i in range(4)],
        names=ambiguous.index.names,
    )
    ambiguous['last_trade_date'] += pd.Timedelta(days=1)
    factor = RollYieldFactor(combination='near_next', volnorm=False)

    result = factor.compute(pd.concat([valid, ambiguous]))

    expected_value = (110 / 100 - 1) / 30 * 365
    assert result.loc[(pd.Timestamp('2026-01-02'), 'RB')] == expected_value
    assert pd.isna(result.loc[(pd.Timestamp('2026-01-03'), 'FU')])
    assert result.index.names == ['timestamp', 'product']
    assert len(factor.warnings) == 1
    assert 'product=FU' in factor.warnings[0]
    assert '无法唯一确定近月或次近月合约' in factor.warnings[0]


def test_secondary_rejects_multiple_dominant_contracts():
    """同一截面出现多个主力时，保留原有断言。"""
    data = _frame()
    data.iloc[2, data.columns.get_loc('is_dominant')] = True
    with pytest.raises(AssertionError):
        RollYieldFactor(combination='dominant_secondary', volnorm=False).compute(data)


@pytest.mark.parametrize('combo', COMBINATIONS)
def test_empty_input(combo):
    """空数据返回有名称和双层索引的空因子序列。"""
    factor = RollYieldFactor(combination=combo, volnorm=False)
    result = factor.compute(_frame().iloc[:0])
    assert result.empty
    assert result.name == factor.name
    assert result.index.names == ['timestamp', 'product']
    assert factor.warnings == []


def test_volatility_normalization_aligns_panel_and_keeps_missing_dates():
    """归一化按标签对齐；缺失或为零的波动率产生 NaN。"""
    dates = pd.date_range('2026-01-02', periods=5)
    frames = []
    for ts in dates:
        frame = _frame()
        frame['last_trade_date'] += ts - dates[0]
        frame.index = pd.MultiIndex.from_tuples(
            [(ts, 'RB', f'RB{i}') for i in range(4)], names=frame.index.names)
        frames.append(frame)
    data = pd.concat(frames)
    panel = pd.DataFrame(
        {'True_Return': [0., 0., .1, -.1]},
        index=pd.MultiIndex.from_product([dates[:4], ['RB']], names=['timestamp', 'product']))
    original = panel.copy(deep=True)
    factor = RollYieldFactor(volnorm=True, vol_lookback=3, vol_min_periods=2)

    actual = factor.compute({'term_structure': data, 'main_panel': panel})

    raw = RollYieldFactor(volnorm=False).compute(data)
    denom = panel['True_Return'].rolling(3, min_periods=2).std() * np.sqrt(252.)
    expected = (raw / denom.where(denom > 0).reindex(raw.index)).rename(factor.name)
    pd.testing.assert_series_equal(actual, expected)
    pd.testing.assert_frame_equal(panel, original)


@pytest.mark.parametrize('data', [{}, _frame(), {'term_structure': _frame()},
                                  {'term_structure': _frame(), 'main_panel': pd.DataFrame()}])
def test_missing_required_sources(data):
    """归一化和期限结构数据源缺失时仍应给出明确异常。"""
    with pytest.raises(ValueError):
        RollYieldFactor(vol_lookback=3, vol_min_periods=2).compute(data)

@pytest.mark.parametrize('column', ['Close', 'OpenInterest', 'is_dominant'])
def test_nullable_columns_preserve_scalar_selection(column):
    """可空列的缺失价格、持仓和主力标记保持原标量计算语义。"""
    data = _frame()
    data[column] = data[column].astype('boolean' if column == 'is_dominant' else 'Float64')
    data.iloc[2, data.columns.get_loc(column)] = pd.NA
    factor = RollYieldFactor(combination='dominant_secondary', volnorm=False)
    scalar = data.droplevel(['timestamp', 'product']).copy()
    scalar['days_to_last_trade'] = [0, 30, 60, 365]
    near, far, _ = factor._select_contract_pair(scalar)
    expected = pd.Series(
        [factor._compute_roll_yield(scalar, near, far)],
        index=pd.MultiIndex.from_tuples([(pd.Timestamp('2026-01-02'), 'RB')],
                                       names=['timestamp', 'product']),
        name=factor.name)

    pd.testing.assert_series_equal(factor.compute(data), expected, check_exact=True)


def test_categorical_product_excludes_unused_groups():
    """分类品种索引不应生成不存在的截面，结果恢复普通品种索引。"""
    data = _frame()
    expected = RollYieldFactor(volnorm=False).compute(data)
    data.index = data.index.set_levels(pd.CategoricalIndex(['RB', 'CU']), level='product')
    factor = RollYieldFactor(volnorm=False)

    pd.testing.assert_series_equal(factor.compute(data), expected, check_exact=True)
    assert factor.warnings == []


def test_float32_preserves_scalar_arithmetic_precision():
    """低精度价格先相除再提升精度，保持原标量公式的运算顺序。"""
    data = _frame()
    data['Close'] = np.array([1e-4, 1e3, 3., 7.], dtype=np.float32)
    factor = RollYieldFactor(combination='dominant_secondary', volnorm=False)
    near_price = data['Close'].iloc[1]
    far_price = data['Close'].iloc[2]
    expected = (near_price / far_price - 1) / 30 * 365.

    assert factor.compute(data).iloc[0] == expected


def test_tied_farthest_maturity_uses_input_order():
    """最远月到期日并列时，仍选择输入中最先出现的合约。"""
    data = _frame(dtes=(0, 30, 90, 90)).iloc[[3, 2, 1, 0]]
    factor = RollYieldFactor(combination='near_farthest', volnorm=False)

    assert factor.compute(data).iloc[0] == (110 / 80 - 1) / 90 * 365.
