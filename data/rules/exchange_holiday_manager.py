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

        Raises
        ------
        FileNotFoundError
            如果在 `exchange_holiday_dir` 目录下找不到对应的 `{exchange}.csv` 文件。
        ValueError
            如果 CSV 文件的列数不等于 3，或者缺少必要的列：
            `no_night_calendar_date`、`holiday_str`、`inferred_no_night_trading_date`。
        """
        if exchange not in self._exchange_holiday_df_cache:
            path = self.exchange_holiday_dir / f'{exchange}.csv'
            if not path.exists():
                raise FileNotFoundError(f'未能在 {self.exchange_holiday_dir} 下找到交易所 {exchange} 的节假日 CSV 文件。')
            df = pd.read_csv(path)

            # 校验所需要的三列存在，且仅有三列（`no_night_calendar_date`、`holiday_str` 和 `inferred_no_night_trading_date`）
            if len(df.columns) != 3:
                raise ValueError(
                    f'交易所 {exchange} 的节假日文件只能存在三列(`no_night_calendar_date`、`holiday_str` 和 `inferred_no_night_trading_date`)，现找到 {len(df.columns)} 列。'
                )
            required_cols = ['no_night_calendar_date', 'holiday_str', 'inferred_no_night_trading_date']
            for col in required_cols:
                if col not in df.columns:
                    raise ValueError(f'交易所 {exchange} 的节假日文件中，缺少必要列：{col}')
            # 将 no_night_calendar_date 和 inferred_no_night_trading_date 列解析为 date
            df['no_night_calendar_date'] = pd.to_datetime(df['no_night_calendar_date']).date
            df['inferred_no_night_trading_date'] = pd.to_datetime(df['inferred_no_night_trading_date']).date
            
            # 加入缓存
            self._exchange_holiday_df_cache[exchange] = df
            self._exchange_no_night_trading_dates_cache[exchange] = set(df['inferred_no_night_trading_date'].tolist())
    
    def get_holiday_arrangements(self, exchange: str) -> pd.DataFrame:
        """
        获取指定交易所的完整节假日安排数据。

        Parameters
        ----------
        exchange : str
            交易所名称（大小写不敏感，如 "SHFE", "dce"）。

        Returns
        -------
        pd.DataFrame
            包含节假日安排的数据表，包含以下三列：
            - `no_night_calendar_date` : date, 交易所公告无夜盘的日历日。
            - `holiday_str` : str, 节假日名称（如 "春节"）。
            - `inferred_no_night_trading_date` : date, 推导出的无夜盘交易日。
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