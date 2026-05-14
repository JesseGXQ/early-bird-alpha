from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.calendar.calendar import TradingCalendar

import os
import pandas as pd
from pathlib import Path
from datetime import date
import warnings

class FileLocator:
    """
    文件寻址器。寻找相关的 CSV 或 Parquet 源数据文件路径
    
    1 min 或 TICK 级别数据文件搜寻通用。

    Parameters
    ------
    data_dir: str
        储存 TICK 数据或 1min 数据的目录
    calendar: TradingCalendar
        交易日历实例。
    
    Methods
    ------
    get_1min_file_path(contract)
        返回合约的 1min 数据储存路径。
    get_tick_file_path(contract, trading_date, calendar_date, calendar_timestamp)
        根据日期（交易日/日历日）或（日历日）时间戳，返回合约的 TICK 数据储存路径。
    """
    def __init__(self, 
                 data_dir: str, 
                 calendar: TradingCalendar,
                 ):

        self.data_dir = Path(data_dir)
        self.calendar = calendar

    def get_1min_file_path(self, contract: str) -> str:
        """获取合约的 1min 数据储存路径。
        
        Parameters
        ------
        contract: str
            合约名称
        
        Returns
        ------
        str
            Parquet 或 CSV 数据文件路径。
        """
        # 1min 文件名称：RB2607.parquet 或 RB2607.csv
        pq_file_path = self.data_dir / f'{contract}.parquet'
        if pq_file_path.exists():
            return str(pq_file_path)
        # 若未找到 parquet，fall back to csv
        csv_file_path = self.data_dir / f'{contract}.csv'
        if csv_file_path.exists():
            warnings.warn(f'【FileLocator】未找到相关 Parquet数据: str(pq_file_path)，使用低效率的 CSV 文件。')
            return str(csv_file_path)
        #若都没找到，警告一下，返回空字符串
        warnings.warn(f'【FileLocator】未找到相关 Parquet 或 CSV 数据：{str(pq_file_path), str(csv_file_path)}')
        return ''

    def get_tick_file_path(self,
                           contract: str, 
                           trading_date: date | None,
                           calendar_date: date | None,
                           calendar_timestamp: pd.Timestamp | None
                           ) -> str:
        """
        根据日期或时间戳以及合约名，返回 TICK 数据储存路径。

        日期可为交易日(trading_date)或日历日(calendar_date)，时间戳为日历交易时间戳(calendar_timestamp)。
        
        Parameters
        ------
        contract: str
            合约名称
        trading_date: date or None
            交易日
        calendar_date: date or None
            日历日
        calendar_timestamp: pd.Timestamp or None
            日历交易时间戳
        
        Returns
        ------
        str 
            Parquet 或 CSV 数据文件路径。
        
        """
        #1. 先将日期统一起来
        if trading_date and (not calendar_date) and (not calendar_timestamp) :
            #传入了 trading_date 参数，但实际上并非交易日
            if not self.calendar.is_trading_date(trading_date):
                warnings.warn(f'【FileLocator】传入的 trading_date: {trading_date} 不是交易日，请检查！')
                return '' 
        #若传了 calendar_timestamp ，将其转化为 trading_date
        elif calendar_timestamp and (not trading_date) and (not calendar_date):
            trading_date = self.calendar.get_trading_date(calendar_timestamp)
        #若只传了一个 calendar_date，则默认为非夜盘交易时间（避免映射到下一个交易日)，这里选择中午11:15分
        elif calendar_date and (not trading_date) and (not calendar_timestamp):
            _ts = pd.Timestamp(f'{calendar_date.isoformat()} 11:15:00')
            trading_date = self.calendar.get_trading_date(_ts)
        else:
            ValueError(f'必须且仅能传入 trading_date, calendar_date, calendar_timestamp 三者之一。')
        
        # TICK文件名称: RB2607_20260106.parquet 或 RB2607_20260106.csv ，即一个交易日一个文件
        # TODO [数据相关]
        # 根据上述要求统一 TICK 数据文件名
        pq_file_path = self.data_dir / f'{contract}_{trading_date.strftime('%Y%m%d')}.parquet'
        if pq_file_path.exists():
            return str(pq_file_path)
        #若找不到 parquet, fall back to csv
        csv_file_path = self.data_dir / f'{contract}_{trading_date.strftime('%Y%m%d')}.csv'
        if csv_file_path.exists():
            warnings.warn(f'【FileLocator】未找到相关 Parquet数据: str(pq_file_path)，使用低效率的 CSV 文件。')
            return str(csv_file_path)
        #若 CSV 也找不到，警告一下，返回空字符串
        warnings.warn(f'【FileLocator】未找到相关 Parquet 或 CSV 数据：{str(pq_file_path), str(csv_file_path)}')
        return ''


