from __future__ import annotations

from datetime import date

import pandas as pd

from account.account import Account
from account.models import Fill
from backtester.models import BarMarketEvent
from portfolio.models import TargetPosition
from portfolio.order_manager import OrderManager


def _bar(contract: str = "RB2510") -> BarMarketEvent:
    return BarMarketEvent(
        product="RB",
        contract=contract,
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


def test_flat_to_long_creates_buy_order():
    account = Account(initial_cash=1_000_000)
    manager = OrderManager()
    target = TargetPosition("RB", 1, pd.Timestamp("2026-01-03"))

    orders = manager.create_orders(target, account, _bar())

    assert len(orders) == 1
    assert orders[0].side == "BUY"
    assert orders[0].quantity == 1


def test_same_position_creates_no_order():
    account = Account(initial_cash=1_000_000)
    account.apply_fill(Fill("F1", "O1", "RB", "RB2510", "BUY", 1, 3500, pd.Timestamp("2026-01-02")))
    manager = OrderManager()
    target = TargetPosition("RB", 1, pd.Timestamp("2026-01-03"))

    assert manager.create_orders(target, account, _bar()) == []


def test_long_to_short_creates_sell_two():
    account = Account(initial_cash=1_000_000)
    account.apply_fill(Fill("F1", "O1", "RB", "RB2510", "BUY", 1, 3500, pd.Timestamp("2026-01-02")))
    manager = OrderManager()
    target = TargetPosition("RB", -1, pd.Timestamp("2026-01-03"))

    orders = manager.create_orders(target, account, _bar())

    assert len(orders) == 1
    assert orders[0].side == "SELL"
    assert orders[0].quantity == 2


def test_rollover_closes_old_contract_and_opens_current_contract():
    account = Account(initial_cash=1_000_000)
    account.apply_fill(Fill("F1", "O1", "RB", "RB2510", "BUY", 1, 3500, pd.Timestamp("2026-01-02")))
    manager = OrderManager()
    target = TargetPosition("RB", 1, pd.Timestamp("2026-01-03"))

    orders = manager.create_orders(target, account, _bar("RB2601"))

    assert [(order.contract, order.side, order.quantity) for order in orders] == [
        ("RB2510", "SELL", 1),
        ("RB2601", "BUY", 1),
    ]
