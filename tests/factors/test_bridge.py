"""因子桥接层测试（虚拟数据，不依赖真实数据库）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from factors.bridge import FactorDataBridge
from factors.library.others.future_native import (
    DeliverableCoverage,
    LimitPressure,
    MarginShockDeleveraging,
    OvernightDayReturn,
    SeasonalReceiptScarcity,
)
from factors.models import DataRequirement


_DATES = pd.date_range('2025-01-06', periods=3, freq='B')


def _daily_frame(product: str = 'RB') -> pd.DataFrame:
    index = pd.DatetimeIndex(_DATES, name='trading_date')
    return pd.DataFrame(
        {
            'Raw_Close': [100.0, 101.0, 102.0],
            'Settle': [100.5, 100.8, 101.5],
            'Upper_Limit': [110.0, 110.0, 110.0],
            'Lower_Limit': [90.0, 90.0, 90.0],
            'True_Return': [0.01, 0.01, 0.01],
            'OpenInterest': [100.0, 110.0, 121.0],
            'Dominant_Contract': [f'{product}2605'] * 3,
        },
        index=index,
    )


def _term_frame(product: str = 'RB') -> pd.DataFrame:
    timestamp = _DATES[0]
    dtes = [30, 90, 180]
    index = pd.MultiIndex.from_tuples(
        [(timestamp, f'{product}{i}') for i in range(len(dtes))],
        names=['trading_date', 'contract'],
    )
    return pd.DataFrame(
        {
            'Close': [3000.0, 3100.0, 3200.0],
            'is_dominant': [False, True, False],
            'last_trade_date': timestamp + pd.to_timedelta(dtes, unit='D'),
            'OpenInterest': [100.0, 50.0, 10.0],
            'Volume': [100.0, 100.0, 100.0],
            'multiplier': [10, 10, 10],
        },
        index=index,
    )


def _warehouse_frame(product: str = 'RB') -> pd.DataFrame:
    return pd.DataFrame([{
        'trading_date': _DATES[0],
        'product': product,
        'receipt_quantity': 1000.0,
        'unit': '吨',
    }])


def _status_frame(product: str = 'RB') -> pd.DataFrame:
    return pd.DataFrame({
        'instrument': [f'{product}2605'] * len(_DATES),
        'trading_date': [timestamp.date() for timestamp in _DATES],
        'long_margin_ratio': [0.10, 0.10, 0.15],
        'short_margin_ratio': [0.10, 0.10, 0.15],
    })


def _minute_frame(product: str = 'RB') -> pd.DataFrame:
    rows = [
        ('2025-01-03 21:00', date(2025, 1, 6), 0.01),
        ('2025-01-03 21:01', date(2025, 1, 6), 0.01),
        ('2025-01-06 09:00', date(2025, 1, 6), -0.01),
        ('2025-01-06 09:01', date(2025, 1, 6), -0.01),
        ('2025-01-06 21:00', date(2025, 1, 7), 0.02),
        ('2025-01-06 21:01', date(2025, 1, 7), 0.02),
        ('2025-01-07 09:00', date(2025, 1, 7), 0.03),
        ('2025-01-07 09:01', date(2025, 1, 7), 0.03),
    ]
    index = pd.DatetimeIndex([pd.Timestamp(row[0]) for row in rows])
    return pd.DataFrame(
        {
            'trading_date': [row[1] for row in rows],
            'True_Return': [row[2] for row in rows],
        },
        index=index,
    )


class _FakeRuleQuery:
    def __init__(self, status: dict[str, pd.DataFrame]):
        self._status = status

    def get_product_daily_status(self, product: str) -> pd.DataFrame:
        if product not in self._status:
            raise FileNotFoundError(f'no status for {product}')
        return self._status[product].copy()


class _FakeFacade:
    def __init__(
            self,
            daily: dict[str, pd.DataFrame],
            term: dict[str, pd.DataFrame],
            warehouse: dict[str, pd.DataFrame],
            status: dict[str, pd.DataFrame],
            minute: dict[str, pd.DataFrame],
    ):
        self._daily = daily
        self._term = term
        self._warehouse = warehouse
        self._minute = minute
        self.rule_query = _FakeRuleQuery(status)
        self.calls: list[str] = []

    def get_historical_vector_multiple_products(
            self,
            products: list[str],
            start_date=None,
            end_date=None,
            freq: str = '1d',
            view: str = 'scheduled',
    ) -> pd.DataFrame:
        self.calls.append('daily_panel')
        frames = []
        for product in products:
            frame = self._daily[product].copy()
            frame['product'] = product
            frames.append(frame.set_index('product', append=True))
        return pd.concat(frames).sort_index()

    def get_term_structure_data_multiple_products(
            self,
            products: list[str],
            start_date=None,
            end_date=None,
            price_col: str = 'Close',
    ) -> pd.DataFrame:
        self.calls.append('term_structure')
        frames = []
        for product in products:
            frame = self._term[product].copy()
            frame['product'] = product
            frames.append(frame)
        return pd.concat(frames).sort_index()

    def get_warehouse_receipt_data(
            self,
            product: str,
            start_date=None,
            end_date=None,
            aggregate: bool = False,
            agg_unit_conversions=None,
    ) -> pd.DataFrame:
        self.calls.append(f'warehouse:{product}')
        if product not in self._warehouse:
            raise FileNotFoundError(product)
        return self._warehouse[product].copy()

    def get_historical_vector(
            self,
            product: str,
            start_date=None,
            end_date=None,
            freq: str = '1d',
            view: str = 'scheduled',
    ) -> pd.DataFrame:
        self.calls.append(f'{freq}:{product}')
        if product not in self._minute:
            raise FileNotFoundError(product)
        return self._minute[product].copy()


@pytest.fixture()
def fake_facade() -> _FakeFacade:
    return _FakeFacade(
        daily={'RB': _daily_frame('RB')},
        term={'RB': _term_frame('RB')},
        warehouse={'RB': _warehouse_frame('RB')},
        status={'RB': _status_frame('RB')},
        minute={'RB': _minute_frame('RB')},
    )


def test_resolve_single_source_returns_dataframe(fake_facade) -> None:
    factor = LimitPressure()
    bridge = FactorDataBridge(fake_facade)

    data = bridge.resolve(
        factor.data_requirements,
        products=['rb'],
        start_date=date(2025, 1, 6),
        end_date=date(2025, 1, 7),
    )

    assert isinstance(data, pd.DataFrame)
    assert list(data.index.names) == ['timestamp', 'product']
    assert list(data.columns) == ['Raw_Close', 'Upper_Limit', 'Lower_Limit']
    assert len(data) == 2
    assert factor.compute(data).notna().all()


def test_resolve_multiple_sources_supports_factor_compute(fake_facade) -> None:
    factor = DeliverableCoverage()
    bridge = FactorDataBridge(fake_facade)

    data = bridge.resolve(
        factor.data_requirements,
        products=['RB'],
        start_date=date(2025, 1, 6),
        end_date=date(2025, 1, 7),
    )

    assert isinstance(data, dict)
    assert set(data) == {'term_structure', 'warehouse_receipt'}
    assert list(data['term_structure'].index.names) == [
        'timestamp', 'product', 'contract'
    ]
    values = factor.compute(data)
    assert values.iloc[0] == pytest.approx(0.0)


def test_resolve_margin_sources(fake_facade) -> None:
    factor = MarginShockDeleveraging(lookback=1)
    bridge = FactorDataBridge(fake_facade)

    data = bridge.resolve(
        factor.data_requirements,
        products=['RB'],
        start_date=date(2025, 1, 6),
        end_date=date(2025, 1, 8),
    )

    assert isinstance(data, dict)
    assert set(data) == {'main_panel', 'instrument_daily_status'}
    values = factor.compute(data)
    assert values.loc[(pd.Timestamp('2025-01-08'), 'RB')] == pytest.approx(-0.05)


def test_resolve_session_return(fake_facade) -> None:
    factor = OvernightDayReturn(component='diff')
    bridge = FactorDataBridge(fake_facade)

    data = bridge.resolve(
        factor.data_requirements,
        products=['RB'],
        start_date=date(2025, 1, 6),
        end_date=date(2025, 1, 7),
    )

    assert isinstance(data, pd.DataFrame)
    assert list(data.index.names) == ['timestamp', 'product']
    assert list(data.columns) == ['night_return', 'day_return']
    assert data.loc[(pd.Timestamp('2025-01-06'), 'RB'), 'night_return'] == (
        pytest.approx(0.01 * 2 + 0.0001)
    )
    values = factor.compute(data)
    assert values.loc[(pd.Timestamp('2025-01-06'), 'RB')] == pytest.approx(
        0.0201 - (-0.0199)
    )


def test_resolve_reuses_dataset_without_facade_calls() -> None:
    class _ExplodingFacade:
        rule_query = None

        def __getattr__(self, name):
            raise AssertionError(f'facade 不应被调用: {name}')

    panel = _daily_frame('RB').copy()
    product_level = pd.Index(['RB'] * len(panel), name='product')
    panel.index = pd.MultiIndex.from_arrays(
        [panel.index, product_level], names=['timestamp', 'product']
    )
    term = _term_frame('RB').copy()
    term.index = pd.MultiIndex.from_arrays(
        [
            term.index.get_level_values('trading_date'),
            ['RB'] * len(term),
            term.index.get_level_values('contract'),
        ],
        names=['timestamp', 'product', 'contract'],
    )
    dataset = SimpleNamespace(
        main_panel=panel,
        auxiliary={
            'term_structure': term,
            'warehouse_receipt': _warehouse_frame('RB'),
        },
    )
    factor = DeliverableCoverage()
    bridge = FactorDataBridge(_ExplodingFacade(), dataset=dataset)  # type: ignore[arg-type]

    data = bridge.resolve(
        factor.data_requirements,
        products=['RB'],
        start_date=date(2025, 1, 6),
        end_date=date(2025, 1, 7),
    )

    assert isinstance(data, dict)
    assert factor.compute(data).iloc[0] == pytest.approx(0.0)


def test_resolve_warns_on_missing_warehouse_file(fake_facade) -> None:
    factor = SeasonalReceiptScarcity()
    bridge = FactorDataBridge(fake_facade)

    data = bridge.resolve(
        factor.data_requirements,
        products=['RB', 'CU'],
        start_date=date(2025, 1, 6),
        end_date=date(2025, 1, 7),
    )

    assert isinstance(data, pd.DataFrame)
    assert len(data) == 1
    assert bridge.warnings
    assert 'CU' in bridge.warnings[0]


def test_resolve_raises_for_missing_requested_column(fake_facade) -> None:
    bridge = FactorDataBridge(fake_facade)
    requirement = DataRequirement(
        source='main_panel', columns=('Not_A_Column',)
    )

    with pytest.raises(ValueError):
        bridge.resolve(
            (requirement,),
            products=['RB'],
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 7),
        )


def test_resolve_raises_for_unknown_source(fake_facade) -> None:
    bridge = FactorDataBridge(fake_facade)

    with pytest.raises(ValueError):
        bridge.resolve(
            (DataRequirement(source='unknown'),),
            products=['RB'],
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 7),
        )


def test_resolve_validates_products_and_dates(fake_facade) -> None:
    bridge = FactorDataBridge(fake_facade)
    requirements = LimitPressure().data_requirements

    with pytest.raises(ValueError):
        bridge.resolve(
            requirements,
            products=[],
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 7),
        )
    with pytest.raises(ValueError):
        bridge.resolve(
            requirements,
            products='RB',  # type: ignore[arg-type]
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 7),
        )
    with pytest.raises(ValueError):
        bridge.resolve(
            requirements,
            products=['RB'],
            start_date=date(2025, 1, 8),
            end_date=date(2025, 1, 6),
        )


def test_resolve_merges_duplicate_source_columns(fake_facade) -> None:
    bridge = FactorDataBridge(fake_facade)
    requirements = (
        DataRequirement(source='main_panel', columns=('Raw_Close',)),
        DataRequirement(source='main_panel', columns=('Settle',)),
    )

    data = bridge.resolve(
        requirements,
        products=['RB'],
        start_date=date(2025, 1, 6),
        end_date=date(2025, 1, 6),
    )

    assert isinstance(data, pd.DataFrame)
    assert list(data.columns) == ['Raw_Close', 'Settle']


def test_resolve_rejects_conflicting_request_params(fake_facade) -> None:
    bridge = FactorDataBridge(fake_facade)
    requirements = (
        DataRequirement(
            source='term_structure', request_params={'price_col': 'Close'}
        ),
        DataRequirement(
            source='term_structure', request_params={'price_col': 'Settle'}
        ),
    )

    with pytest.raises(ValueError):
        bridge.resolve(
            requirements,
            products=['RB'],
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 7),
        )
