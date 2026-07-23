from __future__ import annotations

from datetime import date

import pandas as pd

from account.account import Account
from account.models import Fill
from backtester.models import BarMarketEvent


def _bar(contract: str, raw_close: float) -> BarMarketEvent:
    return BarMarketEvent(
        product="RB",
        contract=contract,
        timestamp=pd.Timestamp("2026-01-03"),
        trading_date=date(2026, 1, 3),
        adj_open=100,
        adj_high=101,
        adj_low=99,
        adj_close=100,
        raw_open=raw_close,
        raw_high=raw_close,
        raw_low=raw_close,
        raw_close=raw_close,
    )


def test_long_open_close_path():
    account = Account(initial_cash=1_000_000, multiplier_provider=lambda contract: 10)
    account.apply_fill(Fill("F1", "O1", "RB", "RB2510", "BUY", 1, 3500, pd.Timestamp("2026-01-02"), 2))
    account.mark_to_market(_bar("RB2510", 3520))

    assert account.position_quantity("RB") == 1
    assert account.unrealized_pnl == 200
    assert account.equity == 1_000_198

    account.apply_fill(Fill("F2", "O2", "RB", "RB2510", "SELL", 1, 3520, pd.Timestamp("2026-01-04"), 2))

    assert account.position_quantity("RB") == 0
    assert account.realized_pnl == 200
    assert account.cash == 1_000_196


def test_short_open_close_path():
    account = Account(initial_cash=1_000_000, multiplier_provider=lambda contract: 10)
    account.apply_fill(Fill("F1", "O1", "RB", "RB2510", "SELL", 1, 3500, pd.Timestamp("2026-01-02"), 0))
    account.mark_to_market(_bar("RB2510", 3480))

    assert account.position_quantity("RB") == -1
    assert account.unrealized_pnl == 200

    account.apply_fill(Fill("F2", "O2", "RB", "RB2510", "BUY", 1, 3480, pd.Timestamp("2026-01-04"), 0))

    assert account.position_quantity("RB") == 0
    assert account.realized_pnl == 200


def test_reverse_long_to_short_splits_close_and_open():
    account = Account(initial_cash=1_000_000, multiplier_provider=lambda contract: 10)
    account.apply_fill(Fill("F1", "O1", "RB", "RB2510", "BUY", 1, 3500, pd.Timestamp("2026-01-02"), 0))
    account.apply_fill(Fill("F2", "O2", "RB", "RB2510", "SELL", 2, 3520, pd.Timestamp("2026-01-03"), 0))

    pos = account.get_position("RB2510")
    assert pos is not None
    assert pos.quantity == -1
    assert pos.average_price == 3520
    assert account.realized_pnl == 200

