"""ResearchFeed 的复权、换月与连续主力分段语义测试。"""

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from data.config import AdjustmentConfig
from data.feed.research_feed import ResearchFeed
from data.stitching.stitching import ContinuousContractManager


def _manager(master: pd.DataFrame, transition_days: int = 1):
    manager = ContinuousContractManager.__new__(ContinuousContractManager)
    manager.product = 'TEST'
    manager.master_table = master
    manager.dates_list = master.index.tolist()
    manager.transition_days = transition_days
    return manager


def _feed(master: pd.DataFrame) -> ResearchFeed:
    feed = ResearchFeed.__new__(ResearchFeed)
    feed.product = 'TEST'
    feed.contract_manager = _manager(master)
    feed.adj_config = AdjustmentConfig()
    feed._research_master_table = None
    return feed


def test_master_semantics_are_computed_before_query_slice():
    """查询结束日不得改变历史复权因子或日期级换月语义。"""
    dates = pd.bdate_range('2026-01-02', periods=7)
    master = pd.DataFrame(
        {
            'Dominant_Contract': ['A', 'A', None, 'B', 'B', 'C', 'C'],
            'Price_Gap': [0.0, None, None, 0.0, 10.0, 0.0, None],
        },
        index=dates,
    )
    feed = _feed(master)

    short = feed._query_master_table(dates[0].date(), dates[4].date())
    full = feed._query_master_table(dates[0].date(), dates[-1].date())

    pd.testing.assert_series_equal(
        short['Adj_Factor'], full.loc[short.index, 'Adj_Factor'],
    )
    assert short['Adj_Factor'].tolist() == [10.0] * 5
    assert full['Adj_Factor'].tolist() == [10.0] * 5 + [0.0, 0.0]

    assert full['roll_state'].tolist() == [
        'not_roll', 'not_roll', 'unknown', 'unknown',
        'not_roll', 'roll', 'not_roll',
    ]
    assert full['continuity_segment_id'].tolist() == [1, 1, pd.NA, 2, 2, 2, 2]
    assert pd.isna(full.at[dates[3], 'is_contract_switch'])
    assert bool(full.at[dates[5], 'is_contract_switch']) is True


def test_unexplained_missing_price_gap_is_rejected():
    """主力链连续时的价差空值不得被静默当作零。"""
    dates = pd.bdate_range('2026-01-02', periods=2)
    master = pd.DataFrame(
        {
            'Dominant_Contract': ['A', 'A'],
            'Price_Gap': [None, None],
        },
        index=dates,
    )

    with pytest.raises(ValueError, match='主力链连续但 Price_Gap 缺失'):
        _feed(master)._build_research_master_table()


def test_daily_true_return_breaks_at_gap_and_only_repairs_actual_switch():
    """恢复首日保持缺失，且只有实际切换日使用新主力前收盘价。"""
    dates = pd.bdate_range('2026-01-05', periods=3)
    df = pd.DataFrame(
        {
            'Dominant_Contract': ['B', 'B', 'C'],
            'Raw_Close': [200.0, 202.0, 306.0],
            'continuity_segment_id': pd.array([2, 2, 2], dtype='Int64'),
            'is_contract_switch': pd.array([pd.NA, False, True], dtype='boolean'),
            'is_roll': pd.array([pd.NA, True, True], dtype='boolean'),
        },
        index=dates,
    )
    feed = ResearchFeed.__new__(ResearchFeed)
    feed.calendar = SimpleNamespace(
        _get_prev_trading_date=lambda trading_date: date(2026, 1, 6),
    )
    feed.daily_snapshot_manager = SimpleNamespace(
        get_contract_snapshot_on_date=lambda contract, trading_date:
        SimpleNamespace(close=300.0),
    )

    result = feed._calculate_true_return_daily(df)

    assert pd.isna(result.at[dates[0], 'True_Return'])
    assert result.at[dates[1], 'True_Return'] == pytest.approx(0.01)
    assert result.at[dates[2], 'True_Return'] == pytest.approx(0.02)


def test_intraday_true_return_breaks_at_gap_and_repairs_switch_first_bar():
    """分钟收益不跨分段；切换日仅第一根 Bar 使用新主力历史价格。"""
    timestamps = pd.DatetimeIndex([
        '2026-01-05 09:01', '2026-01-05 09:02',
        '2026-01-06 09:01', '2026-01-06 09:02',
    ])
    df = pd.DataFrame(
        {
            'trading_date': [date(2026, 1, 5)] * 2 + [date(2026, 1, 6)] * 2,
            'Dominant_Contract': ['B', 'B', 'C', 'C'],
            'Raw_Close': [200.0, 202.0, 306.0, 309.0],
            'continuity_segment_id': pd.array([2, 2, 2, 2], dtype='Int64'),
            'is_contract_switch': pd.array([pd.NA, pd.NA, True, True], dtype='boolean'),
        },
        index=timestamps,
    )
    previous_timestamp = pd.Timestamp('2026-01-05 15:00')
    feed = ResearchFeed.__new__(ResearchFeed)
    feed.calendar = SimpleNamespace(
        _get_prev_trading_date=lambda trading_date: date(2026, 1, 5),
        get_standard_timestamps=lambda **kwargs: pd.DatetimeIndex([previous_timestamp]),
    )
    feed.file_locator = SimpleNamespace(
        get_1min_file_path=lambda contract: f'{contract}.parquet',
    )
    feed.bar_loader = SimpleNamespace(
        load_1min_bar=lambda file_path: pd.DataFrame(
            {'Close': [300.0]}, index=pd.DatetimeIndex([previous_timestamp]),
        ),
    )

    result = feed._calculate_true_return_intraday(
        df, freq='1min', view='scheduled',
    )

    assert pd.isna(result.at[timestamps[0], 'True_Return'])
    assert result.at[timestamps[1], 'True_Return'] == pytest.approx(0.01)
    assert result.at[timestamps[2], 'True_Return'] == pytest.approx(0.02)
    assert result.at[timestamps[3], 'True_Return'] == pytest.approx(309.0 / 306.0 - 1)
    assert result['is_contract_switch_first'].tolist() == [False, False, True, False]
