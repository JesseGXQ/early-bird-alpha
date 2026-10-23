"""期限结构类因子测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.library import CurveSlope


def _curve_frame(
        *,
        timestamp: str = '2026-01-02',
        product: str = 'RB',
        beta: float = -0.2,
) -> pd.DataFrame:
    """构造严格服从对数线性期限结构的三合约截面。"""
    ts = pd.Timestamp(timestamp)
    dtes = np.array([30, 120, 210])
    index = pd.MultiIndex.from_tuples(
        [(ts, product, f'{product}{i}') for i in range(3)],
        names=['timestamp', 'product', 'contract'],
    )
    prices = np.exp(5.0 + beta * dtes / 365.0)
    return pd.DataFrame(
        {
            'Close': prices,
            'last_trade_date': ts + pd.to_timedelta(dtes, unit='D'),
            'OpenInterest': [100.0, 200.0, 300.0],
            'Volume': [200.0, 300.0, 400.0],
        },
        index=index,
    )


@pytest.mark.parametrize('weight_var', ['OpenInterest', 'Volume'])
def test_curve_slope_matches_documented_formula(weight_var: str) -> None:
    """因子值应为年化对数价格斜率的相反数。"""
    data = _curve_frame(beta=-0.2)
    original = data.copy(deep=True)
    factor = CurveSlope(weight_var=weight_var)  # type: ignore[arg-type]

    result = factor.compute(data)

    assert result.name == factor.name
    assert result.index.names == ['timestamp', 'product']
    assert result.iloc[0] == pytest.approx(0.2)
    assert factor.warnings == []
    pd.testing.assert_frame_equal(data, original)


def test_curve_slope_keeps_invalid_group_as_nan() -> None:
    """筛选后合约不足的截面应保留索引并产出 NaN。"""
    valid = _curve_frame(product='RB')
    invalid = _curve_frame(product='CU')
    invalid['Volume'] = 0.0
    data = pd.concat([valid, invalid]).sort_index()
    factor = CurveSlope()

    result = factor.compute(data)

    assert result.loc[(pd.Timestamp('2026-01-02'), 'RB')] == pytest.approx(0.2)
    assert np.isnan(result.loc[(pd.Timestamp('2026-01-02'), 'CU')])
    assert len(factor.warnings) == 1


def test_curve_slope_rejects_same_maturity_without_crashing() -> None:
    """合约期限没有横截面变化时应返回 NaN，而不是进入退化回归。"""
    data = _curve_frame()
    data['last_trade_date'] = pd.Timestamp('2026-06-01')
    factor = CurveSlope()

    result = factor.compute(data)

    assert np.isnan(result.iloc[0])
    assert len(factor.warnings) == 1


@pytest.mark.parametrize('column', ['Close', 'OpenInterest', 'Volume'])
def test_curve_slope_handles_nullable_numeric_columns(column: str) -> None:
    """可空数值列中的缺失观测应被剔除，而不是产生 object 数组错误。"""
    data = _curve_frame()
    data[column] = data[column].astype('Float64')
    data.iloc[0, data.columns.get_loc(column)] = pd.NA
    factor = CurveSlope(min_contract_fit=2)

    result = factor.compute(data)

    assert result.iloc[0] == pytest.approx(0.2)


def test_curve_slope_empty_input_returns_named_series() -> None:
    """空输入也应遵守 Series 输出契约。"""
    data = _curve_frame().iloc[0:0]
    factor = CurveSlope()

    result = factor.compute(data)

    assert isinstance(result, pd.Series)
    assert result.empty
    assert result.name == factor.name
    assert result.index.names == ['timestamp', 'product']


def test_curve_slope_copies_configuration() -> None:
    """外部映射及 parameters 返回值都不应改写因子内部配置。"""
    rule = {'min_dte': 3, 'max_dte': 300, 'min_volume': 100}
    factor = CurveSlope(contract_filter_rule=rule)
    rule['min_dte'] = 20
    parameters = factor.parameters
    parameters['contract_filter_rule']['min_dte'] = 30  # type: ignore[index]

    assert factor.parameters['contract_filter_rule']['min_dte'] == 3  # type: ignore[index]


@pytest.mark.parametrize(
    ('kwargs', 'exception'),
    [
        ({'weight_var': 'OI'}, ValueError),
        ({'price_col': 'Open'}, ValueError),
        ({'min_contract_fit': 1}, ValueError),
        ({'contract_filter_rule': {'min_dte': 3}}, ValueError),
    ],
)
def test_curve_slope_validates_parameters(
        kwargs: dict[str, object], exception: type[Exception]) -> None:
    """非法构造参数应在实例化时给出明确错误。"""
    with pytest.raises(exception):
        CurveSlope(**kwargs)  # type: ignore[arg-type]

