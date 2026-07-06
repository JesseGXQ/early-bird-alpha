"""
离线任务：根据各交易所的交易日历和国家法定节假日，生成各交易所的无夜盘交易日csv文件，即包含 inferred_no_night_trading_date 列。

脚本假设：
* （至少从2013年中国期货市场夜盘开始出现以来）中国的期货交易所所采用的节假日完全等同于国家法定节假日安排。

"""
import pandas as pd
from data.calendar.calendar import ExchangeTradingCalendar   
import datetime

CALENDAR_DIR = 'db/calendar/trading_calendars'
EXCHANGE_HOLIDAY_DIR = 'db/rules/exchange_holiday'
NATIONAL_HOLIDAY_PATH = 'db/rules/exchange_holiday/official_chinese_holidays/holiday.csv'
ALL_EXCHANGES = ['CFFEX', 'CZCE', 'DCE', 'GFEX', 'INE', 'SHFE']
EXCHANGE_NIGHT_SESSION_START_DATE = {
    'SHFE': datetime.date(2013, 7, 5), 
    'DCE':  datetime.date(2014, 7, 4),
    'CZCE': datetime.date(2014, 12, 12),
    'INE':  datetime.date(2018, 3, 26),
}

def infer_and_write(exchange: str) -> None:
    """
    读取初始的 (national) holiday csv 文件和交易所交易日历，
    制作并保存交易所节假日 csv 文件，其中包含列 inferred_no_night_trading_date。
    """
    exchange = exchange.upper()
    # 1. 构造交易所交易日历
    exchange_calendar = ExchangeTradingCalendar(exchange, CALENDAR_DIR)
    # 2. 读取 national holiday csv 文件
    df = pd.read_csv(NATIONAL_HOLIDAY_PATH)
    # 3. 推断无夜盘的交易日——节假日后的第一个交易日无夜盘
    df['date'] = pd.to_datetime(df['date'])
    # 首先做日期过滤，在节假日 dataframe 中，仅保留日期大于等于交易所首个夜盘交易日的行
    if exchange not in EXCHANGE_NIGHT_SESSION_START_DATE: # 若没有过夜盘，直接返回
        print('该交易所无夜盘，直接跳过!')
        return
    df = df[df['date'].dt.date >= EXCHANGE_NIGHT_SESSION_START_DATE[exchange]]

    df['year'] = df['date'].dt.year
    df.sort_values(['date'], inplace=True)
    df = df.drop_duplicates(subset=['year','holiday_str'], keep='last') # 仅保留节假日的最后一天（例如2013-01-01、2013-01-02、2013-01-03)都是元旦，仅保留 2013-01-03这一天。
    
    df['inferred_no_night_trading_date'] = [
        exchange_calendar.next_trading_date(dt) 
        for dt in df['date'].dt.date.tolist()
    ]
    # 4. 写入新的csv文件
    out_path = f'{EXCHANGE_HOLIDAY_DIR}/{exchange}.csv'
    df.rename(columns={'date':'holiday_date_last'}).to_csv(out_path, encoding='utf-8-sig', index=False)

def infer_and_write_all() -> None:
    """写入所有交易所的节假日csv文件。"""
    for exchange in ALL_EXCHANGES:
        print(f'制作交易所节假日文件， exchange={exchange} ')
        infer_and_write(exchange)
    
main = infer_and_write_all

if __name__ == '__main__':
    main()
