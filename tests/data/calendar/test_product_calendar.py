import pytest
import datetime
import pandas as pd

from data.calendar.product_calendar import ProductCalendar


product = 'rb'
def test_product_calendar(meta_manager, rule_service, exchange_calendar_manager):
    calendar = ProductCalendar(
        product=product, 
        meta_manager=meta_manager,
        exchange_calendar_manager=exchange_calendar_manager,
        trading_rule_service=rule_service
    )
    # Test1 ，测试 is_trading_date，选择2025-03-25，是交易日，2025-03-29，不是交易日
    assert calendar.is_trading_date(datetime.date(2025, 3, 25)) is True
    assert calendar.is_trading_date(datetime.date(2025, 3, 29)) is False

    # Test2 ，测试 next_trading_date
    assert calendar.next_trading_date(datetime.date(2025, 3, 25)) == datetime.date(2025, 3, 26)
    assert calendar.next_trading_date(datetime.date(2025, 3, 29)) == datetime.date(2025, 3, 31)

    # Test3, 测试 get_trading_date
    ts = pd.Timestamp('2025-03-25 22:15')
    assert calendar.get_trading_date(ts) == datetime.date(2025, 3, 26)

    ts1 = pd.Timestamp('2025-03-25 10:15')
    assert calendar.get_trading_date(ts1) == datetime.date(2025, 3, 25)
    
    # Test4, 测试 get_session_id 方法
    ts2 = pd.Timestamp('2025-03-25 22:15')
    contract='RB2510'
    assert calendar.get_session_id(instrument=contract, calendar_timestamp=ts2, view='scheduled') == 0
    
    ts2 = pd.Timestamp('2025-03-25 10:00')
    assert calendar.get_session_id(instrument=contract, calendar_timestamp=ts2, view='scheduled') == 1
    
    ts3 = pd.Timestamp('2025-03-25 14:37')
    assert calendar.get_session_id(instrument=contract, calendar_timestamp=ts3, view='scheduled') == 2

    ts4 = pd.Timestamp('2025-03-25 04:35') #应该不属于合法交易时段，返回-1
    assert calendar.get_session_id(instrument=contract, calendar_timestamp=ts4, view='scheduled') == -1

    ts4 = pd.Timestamp('2025-10-08 11:00') # 这是节假日，不属于合法交易时段，返回-1
    assert calendar.get_session_id(instrument=contract, calendar_timestamp=ts4, view='scheduled') == -1

    # Test5, 测试 get_time_offset 方法
    contract = 'RB2510'
    # 1. 选择 2025-03-25 10:32:00，是从09:00-10:15, 10:30-10:32，应该是75+2=77分钟的推移，即pd.Timedelta('0 days 01:17:00')
    ts5 = pd.Timestamp('2025-03-25 10:32:00')
    assert calendar.get_time_offset(instrument=contract, calendar_timestamp=ts5) == pd.Timedelta('0 days 01:17:00')
    # 2. 选择 2025-03-25 16:15:00，应该不属于合法交易时段，抛出错误
    ts6 = pd.Timestamp('2025-03-25 16:15:00')
    with pytest.raises(RuntimeError):
        calendar.get_time_offset(instrument=contract, calendar_timestamp=ts6)
    
    # Test6, 测试 get_minute_offset 方法
    contract = 'RB2510'
    # 1. 选择 2025-03-25 10:32:00，是从09:00-10:15, 10:30-10:32，应该是75+2=77分钟的推移，即77
    ts7 = pd.Timestamp('2025-03-25 10:32:00')
    assert calendar.get_minute_offset(instrument=contract, calendar_timestamp=ts7) == 77

    # Test7, 测试 get_calendar_timestamp_from_date_session_offset 方法
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

    # Test8, 测试 get_standard_timestamps
    contract = 'RB2510'
    start_date = datetime.date(2024, 10, 16)
    end_date = datetime.date(2025, 10, 15) #这两个正好是该合约的 list_date 和 last_trade_date
    ts_array = calendar.get_standard_timestamps(
        instrument=contract, 
        start_date=start_date, 
        end_date=end_date,
        freq='5min'
    )
    print(f'合约 {contract} 在交易日期范围 {start_date} 到 {end_date} 的标准时间戳数组为：')
    print(ts_array)

    # Test9, 测试 get_calendar_timestamp_from_trading_date_and_time 方法
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
    
    
    

    






