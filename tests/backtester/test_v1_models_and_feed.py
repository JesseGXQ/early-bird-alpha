from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backtester.market_feed import BarMarketFeed
from backtester.models import BarMarketEvent
from portfolio.models import TargetPosition


def test_core_model_is_frozen():
    event = BarMarketEvent(
        product="RB",
        contract="RB2510",
        timestamp=pd.Timestamp("2026-01-02"),
        trading_date=date(2026, 1, 2),
        adj_open=100,
        adj_high=101,
        adj_low=99,
        adj_close=100,
        raw_open=3500,
        raw_high=3510,
        raw_low=3490,
        raw_close=3500,
    )

    with pytest.raises(Exception):
        event.raw_open = 1

    target = TargetPosition("RB", 1, pd.Timestamp("2026-01-02"))
    assert target.product == "RB"


def test_bar_market_feed_from_dataframe():
    df = pd.DataFrame(
        {
            "trading_date": [date(2026, 1, 2)],
            "Dominant_Contract": ["RB2510"],
            "Adj_Open": [100.0],
            "Adj_High": [101.0],
            "Adj_Low": [99.0],
            "Adj_Close": [100.5],
            "Raw_Open": [3500.0],
            "Raw_High": [3510.0],
            "Raw_Low": [3490.0],
            "Raw_Close": [3505.0],
            "is_roll": [False],
        },
        index=[pd.Timestamp("2026-01-02")],
    )
    feed = BarMarketFeed(facade=None)

    event = next(feed.from_dataframe(product="rb", df=df))

    assert event.product == "RB"
    assert event.contract == "RB2510"
    assert event.adj_close == 100.5
    assert event.raw_open == 3500.0


def test_bar_market_feed_validates_columns():
    feed = BarMarketFeed(facade=None)
    df = pd.DataFrame({"Raw_Open": [1.0]}, index=[pd.Timestamp("2026-01-02")])

    with pytest.raises(ValueError, match="缺少必要字段"):
        list(feed.from_dataframe(product="RB", df=df))

