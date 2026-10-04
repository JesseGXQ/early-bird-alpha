"""仿真行情流的无主力控制事件及快照契约测试。"""

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from data.facade import DataFacade
from data.feed.models import (
    ContractTick, MarketSnapshot, NoDominantContractError, NoDominantEvent,
)
from data.feed.simulation_feed import SimulationFeed
from data.stitching.models import MultipleRolloverError, RollState
from data.stitching.stitching import ContinuousContractManager


def _feed(dominants: list[str | None]):
    dates = pd.bdate_range('2026-01-02', periods=len(dominants))
    manager = ContinuousContractManager.__new__(ContinuousContractManager)
    manager.product = 'TEST'
    manager.master_table = pd.DataFrame({'Dominant_Contract': dominants}, index=dates)
    manager.dates_list = dates.tolist()
    manager.transition_days = 1
    manager._build_daily_lookup()

    feed = SimulationFeed.__new__(SimulationFeed)
    feed.product = 'TEST'
    feed.calendar = SimpleNamespace(exchange_trading_dates=dates)
    feed.contract_manager = manager

    def make_ticks(trading_date: date, contract: str):
        yield ContractTick(
            contract=contract,
            calendar_timestamp=pd.Timestamp(trading_date) + pd.Timedelta(hours=9),
            trading_date=trading_date,
            last=100.0,
            bid=99.0,
            ask=101.0,
            volume=1,
            ask_volume=1,
            bid_volume=1,
            open_interest=100.0,
            is_limit_up=False,
            is_limit_down=False,
        )

    feed._make_contract_tick_generator = make_ticks
    return feed, dates


def test_mixed_stream_emits_event_and_continues_with_known_dominant():
    """空主力逐日发控制事件；恢复首日保留主力和未知换月状态。"""
    feed, dates = _feed([None, 'A', None, 'B', 'B'])
    events = list(feed.get_market_event_stream(dates[0].date(), dates[-1].date()))

    assert len(events) == len(dates)
    assert isinstance(events[0], NoDominantEvent)
    assert events[0].trading_date == dates[0].date()
    assert events[0].reason == 'NO_DOMINANT_IN_MASTER_TABLE'
    assert isinstance(events[1], MarketSnapshot)
    assert events[1].dominant.contract == 'A'
    assert events[1].outgoing is None
    assert events[1].roll_state is RollState.UNKNOWN
    assert isinstance(events[2], NoDominantEvent)
    assert events[2].trading_date == dates[2].date()
    assert isinstance(events[3], MarketSnapshot)
    assert events[3].dominant.contract == 'B'
    assert events[3].roll_state is RollState.UNKNOWN
    assert events[4].roll_state is RollState.NOT_ROLL


def test_snapshot_only_stream_raises_on_no_dominant_and_stops():
    """旧接口不吞掉无主力交易日，也不输出非快照对象。"""
    feed, dates = _feed(['A', None, 'B'])
    snapshots = feed.get_snapshot_stream(dates[0].date(), dates[-1].date())

    assert isinstance(next(snapshots), MarketSnapshot)
    with pytest.raises(NoDominantContractError, match='没有主力合约'):
        next(snapshots)
    with pytest.raises(StopIteration):
        next(snapshots)


def test_roll_snapshot_contains_both_contracts_and_multiple_rolls_fail():
    """单次换月输出新旧合约；窗口内多次换月不得任意选旧主力。"""
    feed, dates = _feed(['A', 'B', 'B'])
    events = list(feed.get_market_event_stream(dates[0].date(), dates[-1].date()))

    assert events[1].dominant.contract == 'B'
    assert events[1].outgoing.contract == 'A'
    assert events[1].roll_state is RollState.ROLL

    feed, dates = _feed(['A', 'B', 'C'])
    feed.contract_manager.transition_days = 2
    with pytest.raises(MultipleRolloverError, match='多次换月'):
        list(feed.get_market_event_stream(dates[0].date(), dates[-1].date()))


def test_facade_exposes_mixed_stream():
    """统一入口将控制事件原样交给下游。"""
    feed, dates = _feed([None, 'A'])
    facade = DataFacade.__new__(DataFacade)
    facade.get_simulation_feed = lambda product: feed

    events = list(facade.get_market_event_stream('TEST', dates[0].date(), dates[-1].date()))
    assert isinstance(events[0], NoDominantEvent)
    assert isinstance(events[1], MarketSnapshot)
