import pandas as pd
from datetime import date
from pathlib import Path
from data.config import ALL_EXCHANGES

class ExchangeCalendarManager:
    """
    交易所交易日历管家。

    Parameters
    ------
    calendar_dir: str
        交易所交易日历所在路径。
    
    Methods
    ------
    is_trading_date(exchange, date)
        判断指定日期是否为某交易所交易日。
    next_trading_date(exchange, date)
        返回某交易所指定日期之后的下一个交易日。
    get_trading_date(exchange, calendar_timestamp)
        返回某交易所时间戳对应的交易日，夜盘归属于下一交易日。
    """
    def __init__(self, calendar_dir: str):
        self.exchange_trading_dates: dict[str, pd.DatetimeIndex] = {} # key is 交易所代码, value is 该交易所的所有交易日
    
        for exchange in ALL_EXCHANGES:
            path = Path(calendar_dir) / f'{exchange}.csv'
            # 设置为 pd.DatetimeIndex；"in" operation is O(1), and use get_loc method to return index.
            df_cal = pd.read_csv(path)
            assert df_cal.shape[1] == 1 # 确保只有一列，就是 trading_date
            self.exchange_trading_dates[exchange] = pd.to_datetime(df_cal.iloc[:, 0].tolist(), format='%Y%m%d').sort_values()

    def is_trading_date(self, exchange: str, date: date) -> bool:
        """判断某个日期是否为某交易所交易日。"""
        return pd.Timestamp(date) in self.exchange_trading_dates[exchange]
    
    def next_trading_date(self, exchange: str, date: date) -> date:
        """返回某交易所的指定日期之后的下一个交易日。"""
        if self.exchange_trading_dates[exchange][-1].date() <= date:
            raise ValueError(f'【ExchangeCalendarManager】找不到所查询的日期 {date} 的下一个交易日。' + 
            f'目前已储存的历史交易日列表的最新值为 {self.exchange_trading_dates[exchange][-1].date()}。')
        
        _ts = pd.Timestamp(date)
        _idx = self.exchange_trading_dates[exchange].get_indexer([_ts])[0]
        if _idx != -1: #exact match
            return self.exchange_trading_dates[exchange][_idx + 1].date()
        # else: no exact match - 输入的 date 参数不是交易日，此时返回在该日期后最临近的一个交易日
        _idx_nearest_after = self.exchange_trading_dates[exchange].get_indexer([_ts], method='backfill')[0]
        return self.exchange_trading_dates[exchange][_idx_nearest_after].date()
    
    def get_trading_date(self, exchange: str, calendar_timestamp: pd.Timestamp) -> date:
        """
        对某交易所而言，返回日历交易时间戳归属的交易日(Trading Date)，夜盘时间戳归属于下一个交易日。
        该方法仅处理（也仅应该处理）合法的交易时间戳。也就是说，该方法本身不判断“时间戳”是否属于该交易所的合法交易时段。
        """
        calendar_date = calendar_timestamp.date()
        # 过了下午 17:00，一定是下一个交易日的夜盘
        if calendar_timestamp.time() > pd.Timestamp('17:00:00').time():
            return self.next_trading_date(exchange, calendar_date)
        else:
            # 17:00 之前，如果日历日本身是交易日，那就是今天
            if self.is_trading_date(exchange, calendar_date):
                return calendar_date
            # 17:00 之前，但日历日是非交易日
            return self.next_trading_date(exchange, calendar_date)

