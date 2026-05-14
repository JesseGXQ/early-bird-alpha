import pandas as pd
import numpy as np
from utils.validation_utils import check_df_columns_exist
from data.calendar.calendar import TradingCalendar
from data.metadata.meta_manager import InstrumentMetaManager

class BarAggregator:
    """
    K 线降频采样聚合器

    基于"绝对分钟索引法"(Absolute Cumulative Trading Minute Indexing)将 1min 源数据
    聚合成其他频率的 K 线数据(如 M15，H1，日频等)。仅支持日频及以下。

    严格遵循 "Session aligned windowing" 原则。即 K 线不允许跨越交易小节。

    一个交易日有三个交易小节：
    上一个夜盘(night session): 如 21:00 - 23:00;
    本日早盘(morning session): 09:00 - 11:30(常忽略早盘休息的 15 min);
    本日午盘(Afternoon session): 13:30 - 15:00。

    例：不允许出现 K 线的时间跨度为早盘 11:15-11:30 + 午盘 13:30 - 15:00

    Parameters
    ------
    calendar: TradingCalendar
        交易日历实例。

    Methods
    ------
    aggregate(raw_df, freq, add_trading_date_col)
        核心降频采样运算。仅支持日频及以下。
    
    """

    def __init__(self, calendar: TradingCalendar):
        self.calendar = calendar #这个实例是“针对某个特定品种的，是 stateful 的。因此 BarAggregator 类的实例，也是 stateful 的。

    def _parse_freq_to_minutes(self, freq: str) -> int:
        """将 freq 转化为分钟数。 """
        if freq == '1d':
            return -1
        if freq.startswith('M'):
            return int(freq.replace('M',''))
        if freq.startswith('H'):
            return int(freq.replace('H','')) * 60
        raise ValueError(f'【BarAggregator】不支持的频率参数：{freq}，目前仅支持分钟(例如 "M5" )，小时(例如 "H2")和日频("1d")')
    
    def aggregate(self, 
                  raw_df: pd.DataFrame, 
                  freq: str, 
                  add_trading_date_col: bool = False) -> pd.DataFrame:
        """
        降频采样运算。

        Parameters
        ------
        raw_df: pd.DataFrame
            源数据DataFrame，索引为日历交易时间，即calendar_time。
        freq: str
            输出数据的频率，支持分钟级，小时级和日频。
            例: M1, M5, M15, M30, H1, H2, H4, 1d。
        add_trading_date_col: bool, default False.
            是否增添'trading_date'(交易日期标签)列。默认不添加。
        
        Returns
        ------
        pd.DataFrame
            降频采样后的数据集。索引为calendat_time，即日历交易时间，类型为 DatetimeIndex 。   
            
        """
        if raw_df is None or raw_df.empty:
            return pd.DataFrame()
        
        # 防御性复制和索引规范化
        df = raw_df.copy()
        df.index = pd.to_datetime(df.index) # should return a DatetimeIndex.
        df.sort_index(inplace=True)

        minutes = self._parse_freq_to_minutes(freq)

        # 1. 将索引列(calendar_time)映射到 trading_date
        # 要求提供 向量化方法(向量版本内部复用单值版本)
        _dates = self.calendar.get_trading_dates(calendar_timestamps=df.index)
        df['trading_date'] = _dates

        # 如果要求的是M1，直接输出
        if minutes == 1:
            print('需求的数据频率为 1min 级别，直接输出。')
            if not add_trading_date_col:
                return df.drop(columns=['trading_date'])
            return df

        # 2. 分配组号 (group_id)和交易小节(session_id)，后续将根据 trading_date,session_id 和 group_id 为 key 做聚合运算
        if minutes == -1: # 日频，整个 trading_date 为一个大组
            df['group_id'] = 0
            df['session_id'] = 0
        else: #为分钟级或小时级
            # 向 TradingCalendar 索要该品种的每个 calendar_timestamp 所对应的交易小节
            df['session_id'] = self.calendar.get_session_ids(calendar_timestamps=df.index)
            # 向 TradingCalendar 索要相对交易小节(session)开始时点的偏移分钟数(三个 session 分别为夜盘、早盘、午盘)
            df['minute_offset'] = self.calendar.get_minute_offsets(calendar_timestamps=df.index)
            df['group_id'] = df['minute_offset'] // minutes

        # 3. 定义聚合字典并做聚合运算
        check_df_columns_exist(df, ['Dominant_Contract', 'Adj_Factor']) # Aggregator 类处理的根据换月总表 (master_table) 拼接好的 1min 原始主力合约价格数据
        check_df_columns_exist(df, ['Close','Open','High','Low','Volume','OpenInterest'])
        
        agg_dict = {
            'Dominant_Contract': 'last',
            'Adj_Factor': 'last',
            'Open': 'first',
            'Close': 'last',
            'High': 'max',
            'Low': 'min',
            'Volume': 'sum',
            'OpenInterest': 'last'
        }

        # 将每根 freq 频率的 K 线的时间标签定为其所包含时间段的**开始时间**
        df['calendar_time'] = df.index
        agg_dict['calendar_time'] = 'first'

        if add_trading_date_col:
            agg_dict['trading_date'] = 'first'
        
        #聚合运算
        grouped = df.groupby(['trading_date','session_id','group_id'])
        resampled_df = grouped.agg(agg_dict)

        #设置索引、命名和排序
        resampled_df.set_index('calendar_time', inplace=True)
        resampled_df.index = pd.to_datetime(resampled_df.index) # be explicit, set to DatetimeIndex
        resampled_df.index.name = raw_df.index.name # 继承原数据的 Index 名称
        resampled_df.sort_index(inplace=True)

        return resampled_df
    
    








        
            


