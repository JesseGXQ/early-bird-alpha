import datetime

import pandas as pd
import pytest

from data.calendar.product_calendar import ProductCalendar
from data.config import SamplingConfig
from data.feed.aggregator import BarAggregator

pytestmark = [pytest.mark.real_data, pytest.mark.integration]
sample_config = SamplingConfig() # 默认 strict + ffill，用于触发 align_to_standard_timestamps
product = 'rb'
contract = 'RB2510'
trading_date = datetime.date(2025, 3, 25)


def _select_time_window(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    index = pd.DatetimeIndex(df.index)
    mask = (index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))
    return df.loc[mask]


def _build_virtual_raw_df(calendar: ProductCalendar) -> pd.DataFrame:
    """
    构造虚拟 1min raw_df。

    09:05-09:09 整个 5min 桶没有任何成交记录，因此在 raw_df 中完全隐藏。
    aggregate(freq='5min') 在 strict alignment 下应把 09:05 这根隐藏 K 线补出来。
    """
    traded_offsets = list(range(0, 5)) + list(range(10, 15))
    timestamps = [
        calendar.get_calendar_timestamp_from_date_session_offset(
            instrument=contract,
            trading_date=trading_date,
            session_id=1,
            minute_offset=offset,
        )
        for offset in traded_offsets
    ]

    rows = []
    for i, ts in enumerate(timestamps):
        open_price = 3500 + i
        rows.append({
            'Dominant_Contract': contract,
            'Open': open_price,
            'Close': open_price + 0.5,
            'High': open_price + 1,
            'Low': open_price - 1,
            'Volume': 10 + i,
            'OpenInterest': 1000 + i,
        })

    return pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps, name='calendar_time'))


def test_bar_aggregator(meta_manager, rule_service, exchange_calendar_manager):
    # 构造品种交易日历
    calendar = ProductCalendar(
        product=product,
        meta_manager=meta_manager,
        exchange_calendar_manager=exchange_calendar_manager,
        trading_rule_service=rule_service,
    )
    aggregator = BarAggregator(calendar=calendar, sample_config=sample_config)

    raw_df = _build_virtual_raw_df(calendar)
    display_start = '2025-03-25 09:00:00'
    display_end = '2025-03-25 09:15:00'

    print('\n[BarAggregator Test] 虚拟 raw_df（注意缺少 09:05-09:09）：')
    print(raw_df)

    print('\n[BarAggregator Test] calendar.get_standard_timestamps 生成的标准 5min 时间轴局部：')
    standard_timestamps = calendar.get_standard_timestamps(
        instrument=contract,
        start_date=trading_date,
        end_date=trading_date,
        freq='5min',
    )
    standard_df = pd.DataFrame(index=pd.DatetimeIndex(standard_timestamps))
    print(_select_time_window(standard_df, display_start, display_end).index)

    try:
        result = aggregator.aggregate(raw_df=raw_df, freq='5min')
    except Exception as exc:
        print('\n[BarAggregator Test] aggregate(freq="5min") 执行失败：')
        print(f'{type(exc).__name__}: {exc}')
        raise

    hidden_bar_ts = pd.Timestamp('2025-03-25 09:05:00')

    print('\n[BarAggregator Test] aggregate(freq="5min") 后的局部结果：')
    print(_select_time_window(result, display_start, display_end))

    print('\n[BarAggregator Test] 被 align_to_standard_timestamps 补出的隐藏 K 线：')
    print(result.loc[[hidden_bar_ts]])

    assert hidden_bar_ts in result.index
    assert result.loc[hidden_bar_ts, 'Volume'] == 0
    assert result.loc[hidden_bar_ts, 'Open'] == result.loc[hidden_bar_ts, 'Close']
    assert result.loc[hidden_bar_ts, 'Dominant_Contract'] == contract
