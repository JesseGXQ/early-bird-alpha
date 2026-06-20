"""
离线任务：负责从公告日历日生成 `inferred_no_night_trading_date`

可接收命令行参数，即 exchange_holiday_dir（交易所节假日 csv 文件所在目录）
"""
import pandas as pd
from pathlib import Path
from data.calendar.calendar import ExchangeTradingCalendar   

CALENDAR_DIR = 'db/calendar/trading_calendars'
EXCHANGE_HOLIDAY_DIR = 'db/reference/exchange_holiday'

def infer_and_write(
    holiday_file_path: Path) -> None:
    """
    读取初始的 exchange holiday csv 文件，
    生成一列新的 no_night_trading_date，
    覆盖保存 csv 文件。
    """
    # 根据 csv 的文件名，构造交易所交易日历
    exchange = holiday_file_path.stem
    exchange_calendar = ExchangeTradingCalendar(exchange, CALENDAR_DIR)
    # 读取 csv 文件
    df = pd.read_csv(holiday_file_path)
    # 推断交易日
    df['no_night_calendar_date'] = pd.to_datetime(df['no_night_calendar_date']).date # convert to an array of datetime.date
    df['inferred_no_night_trading_date'] = [
        exchange_calendar.next_trading_date(dt) 
        for dt in df['no_night_calendar_date'].tolist()
    ]
    # 写入新的csv文件
    df.to_csv(holiday_file_path)

def infer_and_write_all() -> None:
    """将 exchange_holiday_dir 下的所有 exchange holiday csv 文件，
    都写入一列新的 no_night_trading_date，并覆盖保存 csv 文件。
    """
    paths = Path(EXCHANGE_HOLIDAY_DIR).glob('*.csv')
    for path in paths:
        exchange = path.stem
        # 构造交易所交易日历
        print(f'计算 no_night_trading_date，交易所为 {exchange} ')
        infer_and_write(path)
    
main = infer_and_write_all

if __name__ == '__main__':
    main()
