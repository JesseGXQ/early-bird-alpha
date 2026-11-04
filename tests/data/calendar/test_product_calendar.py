import pytest
import datetime
import pandas as pd

from data.calendar.product_calendar import ProductCalendar


pytestmark = [pytest.mark.real_data, pytest.mark.integration]
product = 'rb'
@pytest.fixture
def calendar(meta_manager, rule_service, exchange_calendar_manager):
    calendar = ProductCalendar(
        product=product, 
        meta_manager=meta_manager,
        exchange_calendar_manager=exchange_calendar_manager,
        trading_rule_service=rule_service
    )
    return calendar


def test_is_trading_date(calendar):
    assert calendar.is_trading_date(datetime.date(2025, 3, 25)) is True
    assert calendar.is_trading_date(datetime.date(2025, 3, 29)) is False


def test_next_trading_date(calendar):
    assert calendar.next_trading_date(datetime.date(2025, 3, 25)) == datetime.date(2025, 3, 26)
    assert calendar.next_trading_date(datetime.date(2025, 3, 29)) == datetime.date(2025, 3, 31)


def test_get_trading_date(calendar):
    ts = pd.Timestamp('2025-03-25 22:15')
    assert calendar.get_trading_date(ts) == datetime.date(2025, 3, 26)

    ts1 = pd.Timestamp('2025-03-25 10:15')
    assert calendar.get_trading_date(ts1) == datetime.date(2025, 3, 25)


def test_get_session_id(calendar):
    ts2 = pd.Timestamp('2025-03-25 22:15')
    contract='RB2510'
    assert calendar.get_session_id(instrument=contract, calendar_timestamp=ts2, view='scheduled') == 0

    ts2 = pd.Timestamp('2025-03-25 10:00')
    assert calendar.get_session_id(instrument=contract, calendar_timestamp=ts2, view='scheduled') == 1

    ts3 = pd.Timestamp('2025-03-25 14:37')
    assert calendar.get_session_id(instrument=contract, calendar_timestamp=ts3, view='scheduled') == 2

    ts4 = pd.Timestamp('2025-03-25 04:35') # 输入的日期是交易日，但不属于合法交易时段，会抛出 ValueError
    with pytest.raises(ValueError):
        calendar.get_session_id(instrument=contract, calendar_timestamp=ts4, view='scheduled')

    # 注意：不要再对“非交易日的时间戳”编写 pytest.raises 的用例。
    # get_session_id 的内部依赖链是 get_trading_date -> ExchangeCalendarManager.get_trading_date，
    # 而后者只负责做“交易日归属”的映射（17:00 分界 + 非交易日向后滚动到最近交易日），
    # 它的 docstring 已明确声明“仅处理（也仅应该处理）合法的交易时间戳”，本身不做合法性校验。
    # 因此传入 2025-10-08（国庆休市日）11:00 时，它会静默滚动到 2025-10-09 并返回 session_id，
    # 不会抛 ValueError。
    # 本层刻意不提供独立的“时间戳合法性判定”方法，所以该场景不属于 get_session_id 的职责范围，
    # 若下游需要过滤休市日时间戳，请自行结合 is_trading_date 做判断。


def test_get_time_offset(calendar):
    contract = 'RB2510'
    # 1. 选择 2025-03-25 10:32:00，是从09:00-10:15, 10:30-10:32，应该是75+2=77分钟的推移，即pd.Timedelta('0 days 01:17:00')
    ts5 = pd.Timestamp('2025-03-25 10:32:00')
    assert calendar.get_time_offset(instrument=contract, calendar_timestamp=ts5) == pd.Timedelta('0 days 01:17:00')
    # 2. 选择 2025-03-25 16:15:00，应该不属于合法交易时段，应该会在内部的 `get_session_id` 就抛出 ValueError
    ts6 = pd.Timestamp('2025-03-25 16:15:00')
    with pytest.raises(ValueError):
        calendar.get_time_offset(instrument=contract, calendar_timestamp=ts6)


def test_get_minute_offset(calendar):
    contract = 'RB2510'
    # 1. 选择 2025-03-25 10:32:00，是从09:00-10:15, 10:30-10:32，应该是75+2=77分钟的推移，即77
    ts7 = pd.Timestamp('2025-03-25 10:32:00')
    assert calendar.get_minute_offset(instrument=contract, calendar_timestamp=ts7) == 77


def test_get_calendar_timestamp_from_date_session_offset(calendar):
    contract = 'RB2510'
    trading_date = datetime.date(2025, 3, 25)
    session_id = 1
    minute_offset = 79
    view='scheduled'

    assert calendar.get_calendar_timestamp_from_date_session_offset(
        instrument=contract,
        trading_date=trading_date,
        session_id=session_id,
        minute_offset=minute_offset,
        view=view
    ) == pd.Timestamp('2025-03-25 10:34:00')

    session_id = 0 #夜盘
    minute_offset = 77 # 从夜盘开始，一共过77分钟，结果应该是22:17
    view='scheduled'
    assert calendar.get_calendar_timestamp_from_date_session_offset(
        instrument=contract, 
        trading_date=trading_date,
        session_id=session_id,
        minute_offset=minute_offset,
        view=view
    ) == pd.Timestamp('2025-03-24 22:17:00')


def test_get_standard_timestamps(calendar):
    contract = 'RB2510'
    start_date = datetime.date(2024, 10, 16)
    end_date = datetime.date(2025, 10, 15) #这两个正好是该合约的 list_date 和 last_trade_date
    ts_array = calendar.get_standard_timestamps(
        instrument=contract, 
        start_date=start_date, 
        end_date=end_date,
        freq='5min'
    )
    print(f"""
            合约 {contract} 在 {start_date} - {end_date} 之间， freq='5min'
            的完美时间戳 array 如下：
            """)
    print(ts_array)
    assert len(ts_array) > 0
    assert ts_array[0] == pd.Timestamp('2024-10-16 09:00:00')
    assert ts_array[-1] <= pd.Timestamp('2025-10-15 23:59:59')


def test_get_calendar_timestamp_from_trading_date_and_time(calendar):
    contract = 'RB2510'
    trading_date = datetime.date(2025, 3, 26)
    time = datetime.time(22, 15)
    view='scheduled'

    assert calendar.get_calendar_timestamp_from_trading_date_and_time(
        instrument=contract,
        trading_date=trading_date,
        t=time,
        view=view
    ) == pd.Timestamp('2025-03-25 22:15:00')



