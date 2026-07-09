"""
交易所节假日管家。
"""

import pandas as pd
from datetime import date
from pathlib import Path

class ExchangeHolidayManager:
    """
    交易所节假日数据管理类。

    该类负责按交易所（如 SHFE, DCE, CZCE, CFFEX, INE）加载并缓存节假日与无夜盘交易安排的数据，
    并提供查询某个交易日是否为无夜盘交易日的功能。

    Parameters
    ----------
    exchange_holiday_dir : str
        保存各交易所节假日 CSV 文件的目录路径。

    Attributes
    ----------
    exchange_holiday_dir : Path
        节假日 CSV 目录的 pathlib.Path 对象。
    _exchange_holiday_df_cache : dict of str to pd.DataFrame
        以交易所名称为键，其对应的节假日 DataFrame 为值的缓存字典。
    _exchange_no_night_trading_dates_cache : dict of str to set of datetime.date
        以交易所名称为键，其无夜盘交易日集合为值的缓存字典。
    """

    def __init__(self, exchange_holiday_dir: str):
        """
        初始化 ExchangeHolidayManager 实例。

        Parameters
        ----------
        exchange_holiday_dir : str
            各交易所节假日 CSV 文件的存放目录。
        """
        self.exchange_holiday_dir: Path = Path(exchange_holiday_dir)

        self._exchange_holiday_df_cache: dict[str, pd.DataFrame] = {}
        self._exchange_no_night_trading_dates_cache: dict[str, set[date]] = {}

    def _load_exchange(self, exchange: str) -> None:
        """
        加载并解析指定交易所的节假日数据，并将其存入缓存。

        如果指定交易所的数据已经在缓存中，则直接返回，不重复读取。

        Parameters
        ----------
        exchange : str
            交易所的大写名称缩写（如 "SHFE", "DCE"）。

        Returns
        -------
        None
        """
        if exchange not in self._exchange_holiday_df_cache:
            path = self.exchange_holiday_dir / f'{exchange}.csv'
            if not path.exists():
                raise FileNotFoundError(f'未能在 {self.exchange_holiday_dir} 下找到交易所 {exchange} 的节假日 CSV 文件。')
            df = pd.read_csv(path)

            # 将 inferred_no_night_trading_date 列解析为 date
            df['inferred_no_night_trading_date'] = pd.to_datetime(df['inferred_no_night_trading_date']).dt.date
            
            # 加入缓存
            self._exchange_holiday_df_cache[exchange] = df
            self._exchange_no_night_trading_dates_cache[exchange] = set(df['inferred_no_night_trading_date'].tolist())
    
    def get_holiday_arrangements(self, exchange: str) -> pd.DataFrame:
        """
        获取指定交易所的完整节假日安排的 DataFrame。

        Parameters
        ----------
        exchange : str
            交易所名称（大小写不敏感，如 "SHFE", "dce"）。

        Returns
        -------
        pd.DataFrame
            交易所节假日安排的 DataFrame。
        """
        exchange = exchange.upper()
        self._load_exchange(exchange)
        return self._exchange_holiday_df_cache[exchange]
    
    def is_no_night_trading_date(self, exchange: str, trading_date: date) -> bool:
        """
        判断给定的交易日是否是该交易所的无夜盘交易日。

        Parameters
        ----------
        exchange : str
            交易所名称（大小写不敏感，如 "SHFE", "dce"）。
        trading_date : datetime.date
            要查询的交易日。

        Returns
        -------
        bool
            如果该交易日是无夜盘交易日，则返回 True，否则返回 False
        """
        exchange = exchange.upper()
        self._load_exchange(exchange)
        return trading_date in self._exchange_no_night_trading_dates_cache[exchange]
    
# ============================================
#  a simple test
# ============================================
if __name__ == '__main__':
    exchange_holiday_dir = r'db\rules\exchange_holiday'
    manager = ExchangeHolidayManager(exchange_holiday_dir=exchange_holiday_dir)
    
    # 1. 获取完整节假日安排 dataframe
    exchange = 'SHFE'
    df = manager.get_holiday_arrangements(exchange)
    print(f'exchange={exchange} 的节假日安排完整 dataframe 前5行：', df.head())
    print(f'exchange={exchange} 的节假日安排完整 dataframe 后5行：', df.tail())

    # 2. 判定特定交易日是否是(因节假日导致的)无夜盘交易日
    exchange = 'SHFE'
    from datetime import date
    trading_date = date(2025, 10, 10) #应该有夜盘 
    is_no_night = manager.is_no_night_trading_date(exchange, trading_date)
    print(f'交易日 {trading_date} 是否无夜盘： {is_no_night}')

    trading_date = date(2025, 10, 9) #应该无夜盘 
    is_no_night = manager.is_no_night_trading_date(exchange, trading_date)
    print(f'交易日 {trading_date} 是否无夜盘： {is_no_night}')

