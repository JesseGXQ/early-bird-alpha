from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from account.account import Account
from backtester.models import BarMarketEvent
from strategy.context import HistoryView, StrategyContext
from strategy.momentum import MomentumConfig, TimeSeriesMomentumStrategy


def _bar(day: int, adj_close: float) -> BarMarketEvent:
    ts = pd.Timestamp("2026-01-01") + pd.Timedelta(days=day)
    return BarMarketEvent(
        product="RB",
        contract="RB2510",
        timestamp=ts,
        trading_date=ts.date(),
        adj_open=adj_close,
        adj_high=adj_close,
        adj_low=adj_close,
        adj_close=adj_close,
        raw_open=3500 + day,
        raw_high=3500 + day,
        raw_low=3500 + day,
        raw_close=3500 + day,
    )


def test_momentum_uses_adjusted_close_history():
    account = Account(initial_cash=1_000_000)
    context = StrategyContext(account)
    strategy = TimeSeriesMomentumStrategy(MomentumConfig(lookback=2, quantity=1))
    bars = [_bar(0, 100), _bar(1, 100), _bar(2, 110)]
    for bar in bars:
        context.on_bar(bar)

    target = strategy.on_bar(bars[-1], context)

    assert target is not None
    assert target.target_quantity == 1


def test_history_view_last_requires_positive_n():
    history = HistoryView()

    with pytest.raises(ValueError, match="n 必须为正数"):
        history.last(0)
