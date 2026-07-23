from __future__ import annotations

from datetime import date

import pandas as pd

from backtester.models import BarMarketEvent
from executor.commission import FixedPerContractCommissionModel
from executor.execution_model import NextBarOpenExecutionModel
from executor.slippage import FixedSlippageModel
from portfolio.models import Order


def _bar() -> BarMarketEvent:
    return BarMarketEvent(
        product="RB",
        contract="RB2510",
        timestamp=pd.Timestamp("2026-01-03"),
        trading_date=date(2026, 1, 3),
        adj_open=100,
        adj_high=101,
        adj_low=99,
        adj_close=100,
        raw_open=3500,
        raw_high=3510,
        raw_low=3490,
        raw_close=3505,
    )


def test_buy_uses_raw_open_plus_slippage():
    model = NextBarOpenExecutionModel(
        slippage_model=FixedSlippageModel(1),
        commission_model=FixedPerContractCommissionModel(2),
    )
    order = Order("O1", "RB", "RB2510", "BUY", 1, pd.Timestamp("2026-01-02"))

    fill = model.execute(order, _bar())

    assert fill.price == 3501
    assert fill.commission == 2
    assert fill.timestamp == pd.Timestamp("2026-01-03")


def test_sell_uses_raw_open_minus_slippage():
    model = NextBarOpenExecutionModel(slippage_model=FixedSlippageModel(1))
    order = Order("O1", "RB", "RB2510", "SELL", 1, pd.Timestamp("2026-01-02"))

    fill = model.execute(order, _bar())

    assert fill.price == 3499

