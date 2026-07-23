from __future__ import annotations

from datetime import date

import pandas as pd

from account.account import Account
from analytics.recorder import PerformanceRecorder
from backtester.engine import BacktestEngine
from backtester.models import BarMarketEvent
from executor.execution_model import NextBarOpenExecutionModel
from portfolio.order_manager import OrderManager
from strategy.momentum import MomentumConfig, TimeSeriesMomentumStrategy


def _bar(day: int, adj_close: float, raw_open: float, raw_close: float) -> BarMarketEvent:
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
        raw_open=raw_open,
        raw_high=max(raw_open, raw_close),
        raw_low=min(raw_open, raw_close),
        raw_close=raw_close,
    )


def test_engine_executes_signal_on_next_bar_open():
    bars = [
        _bar(0, 100, 1000, 1000),
        _bar(1, 110, 2000, 2000),
        _bar(2, 120, 3000, 3000),
    ]
    account = Account(initial_cash=1_000_000, multiplier_provider=lambda contract: 10)
    recorder = BacktestEngine(
        market_feed=bars,
        strategy=TimeSeriesMomentumStrategy(MomentumConfig(lookback=1, quantity=1)),
        account=account,
        execution_model=NextBarOpenExecutionModel(),
        order_manager=OrderManager(),
        recorder=PerformanceRecorder(),
    ).run()

    assert len(recorder.orders) == 1
    assert len(recorder.fills) == 1
    assert recorder.orders[0].created_at == bars[1].timestamp
    assert recorder.fills[0].timestamp == bars[2].timestamp
    assert recorder.fills[0].price == 3000
    assert recorder.fills[0].price != 2000
    assert account.position_quantity("RB") == 1
