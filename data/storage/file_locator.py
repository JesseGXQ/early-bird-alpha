from __future__ import annotations
from typing import TYPE_CHECKING

import pandas as pd
from pathlib import Path
from datetime import date
import warnings

if TYPE_CHECKING:
    from data.calendar.calendar import TradingCalendar
    from data.metadata.product_meta_manager import ProductMetaManager

class FileLocator:
    """
    文件寻址器。寻找合约的 CSV 或 Parquet 源数据文件路径。
    
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
                 meta_manager: ProductMetaManager
                 ):
        self.data_dir: Path = Path(data_dir)
        self.calendar: TradingCalendar = calendar
        self.meta_manager: ProductMetaManager = meta_manager
        
        self._1min_search_symbol_exchange_mapping: dict[str, str] = {} # 内部缓存，用于加速根据 product 查找交易所代码的过程，避免频繁调用 meta_manager.get_static_meta

    def get_1min_file_path(self, 
                           product: str,
                           contract: str) -> str:
        """获取合约的 1min 数据储存路径。
        
        Parameters
        ------
        product: str
            品种代码，例如'RB'。
        contract: str
            合约名称。
        
        Returns
        ------
        str
            Parquet 或 CSV 数据文件路径。
        """
        # 本地 1min 数据存储路径：1min/{交易所代码}/{product}/{contract}.csv
        if not product in self._1min_search_symbol_exchange_mapping: #若缓存中无法找到该品种的交易所，则向元数据管家询问
            exchange = self.meta_manager.get_static_meta(product).exchange
            self._1min_search_symbol_exchange_mapping[product] = exchange
        exchange = self._1min_search_symbol_exchange_mapping[product]
        
        # 首先尝试寻找 parquet 文件
        pq_file_path = self.data_dir / exchange/ product / f'{contract}.parquet'
        if pq_file_path.exists():
            return str(pq_file_path)
        
        # 若未找到 parquet，fall back to csv
        csv_file_path = self.data_dir / exchange/ product / f'{contract}.csv'
        if csv_file_path.exists():
            warnings.warn(f'【FileLocator】未找到相关 Parquet数据: str(pq_file_path)，将使用低效率的 CSV 文件。')
            return str(csv_file_path)
        
        #若都没找到，警告一下，返回空字符串
        warnings.warn(f'【FileLocator】未找到相关 Parquet 或 CSV 数据：{str(pq_file_path), str(csv_file_path)}')
        return ''

    def get_tick_file_path(self,
                           contract: str, 
                           trading_date: date | None = None,
                           calendar_date: date | None = None,
                           calendar_timestamp: pd.Timestamp | None = None
                           ) -> str:
        """
        根据日期或时间戳以及合约名，返回 TICK 数据储存路径。

        日期可为交易日(trading_date)或日历日(calendar_date)，时间戳为日历交易时间戳(calendar_timestamp)。
        
        必须且仅能传入以上三者之一。

        Parameters
        ------
        contract: str
            合约名称
        trading_date: datetime.date or None
            交易日
        calendar_date: datetime.date or None
            日历日
        calendar_timestamp: pd.Timestamp or None
            日历交易时间戳
        
        Returns
        ------
        str 
            Parquet 或 CSV 数据文件路径。
        
        """
        #1. 先将日期统一起来：统一为 trading_date
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
            raise ValueError(f'必须且仅能传入 trading_date, calendar_date, calendar_timestamp 三者之一。')
        
        # 本地 TICK 数据存储路径： TICK/{year}/{trading_date}/{contract}.csv
        year = str(trading_date.year)
        # 首先尝试寻找 parquet 文件
        pq_file_path = self.data_dir / year / trading_date.strftime('%Y%m%d') / f'{contract}.parquet'
        if pq_file_path.exists():
            return str(pq_file_path)
        #若找不到 parquet, fall back to csv
        csv_file_path = self.data_dir / year/ trading_date.strftime('%Y%m%d') / f'{contract}.csv'
        if csv_file_path.exists():
            warnings.warn(f'【FileLocator】未找到相关 Parquet数据: str(pq_file_path)，使用低效率的 CSV 文件。')
            return str(csv_file_path)
        #若 CSV 也找不到，警告一下，返回空字符串
        warnings.warn(f'【FileLocator】未找到相关 Parquet 或 CSV 数据：{str(pq_file_path), str(csv_file_path)}')
        return ''


