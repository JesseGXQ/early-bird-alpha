from __future__ import annotations
from typing import TYPE_CHECKING
import pandas as pd

if TYPE_CHECKING:
    from data.config import SamplingConfig
    from data.calendar.product_calendar import ProductCalendar

class BarAggregator:
    """
    K 线降频采样聚合器

    基于"绝对分钟索引法"(Absolute Cumulative Trading Minute Indexing)将 1min 源数据
    聚合成其他频率的 K 线数据（例如 5min, 15min, 30min, 1h, 2h, 4h, 1d)。仅支持日频或更细粒度。

    严格遵循 "Session aligned windowing" 原则。即 K 线不允许跨越交易小节。

    一个交易日有三个交易小节：
    上一个夜盘(night session): 如 21:00 - 23:00;
    本日早盘(morning session): 09:00 - 11:30(常忽略早盘休息的 15 min);
    本日午盘(Afternoon session): 13:30 - 15:00。

    例：不允许出现 K 线的时间跨度为早盘 11:15-11:30 + 午盘 13:30 - 15:00

    Parameters
    ------
    calendar: ProductCalendar
        交易日历实例。
    sample_config: SamplingConfig, optional
        采样配置参数，包含对齐方式(alignment)，缺失值处理方法(imputation_method)和夜盘开关(include_night_session)。
        默认 alignment='strict', imputation_method='ffill', include_night_session=True。

    Methods
    ------
    aggregate(raw_df, freq, add_trading_date_col)
        核心降频采样运算。
    
    """

    def __init__(self, 
                 calendar: ProductCalendar,
                 sample_config: SamplingConfig):
        self.calendar: ProductCalendar = calendar #这个实例是“针对某个特定品种的，是 stateful 的。因此 BarAggregator 类的实例，也是 stateful 的。
        self.sample_config: SamplingConfig = sample_config
    
    def _parse_freq_to_minutes(self, freq: str) -> int:
        """将符合 Pandas Offset Aliases 格式的 freq 字符串翻译为分钟数。"""
        try:
            offset = pd.tseries.frequencies.to_offset(freq)
            return int(pd.Timedelta(offset).total_seconds() / 60)
        except ValueError:
            raise ValueError(f'【BarAggregator】不支持的频率参数：{freq}，目前仅支持分钟(例如 "5min" )，小时(例如 "2h")和日频("1d")')

    def aggregate(self, 
                  raw_df: pd.DataFrame, 
                  freq: str) -> pd.DataFrame:
        """
        降频采样运算。

        Parameters
        ------
        raw_df: pd.DataFrame
            原始数据 DataFrame 。索引为日历交易时间（calendar_time）。
        freq: str
            输出数据的频率，支持分钟级，小时级。
            格式同 Pandas Offset aliases 保持一致。
            例: 1min, 5min, 15min, 30min, 1h, 2h, 4h。
        
        Returns
        ------
        pd.DataFrame
            降频采样后的数据集。索引为 calendar_time，即日历交易时间。按日期顺序排列（最早在前）。类型为 pd.DatetimeIndex。
        """
        assert freq != '1d' , '仅处理1min数据聚合到非日频（若请求日频，直接读取日频快照数据，无需聚合）'

        if raw_df.empty:
            return pd.DataFrame()
        
        # 防御性复制和索引规范化
        df: pd.DataFrame = raw_df.copy()
        df.sort_index(inplace=True)

        df_index = df.index # 方便后续传给 calendar 下的各种索引器使用
        assert isinstance(df_index, pd.DatetimeIndex)

        # 将索引列(calendar_time)映射到 trading_date（若是 ResearchFeed 的 get_historical_vector 方法调用，则传进来的 dataframe 是包含该列的）
        if 'trading_date' not in df:
            _dates = self.calendar.get_trading_dates(calendar_timestamps=df_index)
            df['trading_date'] = _dates
        
        minutes = self._parse_freq_to_minutes(freq=freq)

        # 分配组号 (group_id)和交易小节(session_id)，后续将根据 trading_date,session_id 和 group_id 为 key 做聚合运算
        # 向 calendar 索要该品种的每个 calendar_timestamp 所对应的交易小节
        df['session_id'] = self.calendar.get_session_ids(
            instruments=df['Dominant_Contract'],
            calendar_timestamps=df_index,
        )
        # 向 calendar 索要相对交易小节(session)开始时点的偏移分钟数(三个 session 分别为夜盘、早盘、午盘)
        df['minute_offset'] = self.calendar.get_minute_offsets(
            instruments=df['Dominant_Contract'],
            calendar_timestamps=df_index,
        )
        df['group_id'] = df['minute_offset'] // minutes
      
        # 定义聚合字典并做聚合运算
        df['calendar_time'] = df.index # 把索引列 calendar_time 变成普通列，参与聚合运算。聚合后再把它设回索引。

        #聚合运算
        grouped = df.groupby(['trading_date','session_id','group_id'])
        resampled_df = grouped.agg(
            Dominant_Contract = ('Dominant_Contract', 'last'),
            Open = ('Open', 'first'),
            Close = ('Close', 'last'),
            High = ('High', 'max'),
            Low = ('Low', 'min'),
            Volume = ('Volume', 'sum'),
            OpenInterest = ('OpenInterest', 'last'),
            calendar_time = ('calendar_time', 'first')
            )
        
        # 由于原始数据时间轴存在可能的缺失 bar， 因此 'first' of calendar_time 可能会使出现 M5 下出现 14:22 这类时间戳标签。应当对其进行标准化，改为 14:20(区间左侧)。
        # 只要用 group_id * minutes 计算出小节内的分钟偏移量，就可以联同交易日期(trading_date)，交易小节(session_id) 一起传给 calendar 的一个反向翻译器，得到标准化后的 calendar_time。
        resampled_df.reset_index(inplace=True) 
        normalized_calendar_times = self.calendar.get_calendar_timestamps_from_date_session_offset(
            instruments=resampled_df['Dominant_Contract'],
            trading_dates=resampled_df['trading_date'],
            session_ids=resampled_df['session_id'],
            minute_offsets=resampled_df['group_id'] * minutes
        )
        resampled_df['normalized_calendar_time'] = normalized_calendar_times

        #设置索引、命名和排序
        resampled_df.set_index('normalized_calendar_time', inplace=True)

        # 处理夜盘开关
        if not self.sample_config.include_night_session:
            resampled_df = resampled_df[resampled_df['session_id'] != 0]
        
        # 进行对齐(alignment)和缺失值处理(imputation_method)
        # alignment 是指挖出空缺的 K 线。例如原始数据在 ..13:55, 13:56 后面直接跟着 14:22，那么从 14:00 - 14:15 的 4 根 K 线将完全缺失，alignment = 'strict' 需要将其挖出进行补全。
        # 缺失值处理中，imputation_method == 'ffill'，则是将挖出的缺失 K 线数据缺失值进行填充。
        if self.sample_config.alignment == 'strict': # if 'lazy', do nothing
            resampled_df = self.align_to_standard_timestamps(resampled_df, freq)
            if self.sample_config.imputation_method == 'ffill': # if 'drop' do nothing
                resampled_df = self.impute_missing_bars(resampled_df)

        # 剔除辅助列
        resampled_df = resampled_df.drop(columns=['session_id','group_id','calendar_time'])

        # 将索引格式 explictly 设置为 pd.DatetimeIndex
        resampled_df.index = pd.DatetimeIndex(resampled_df.index)

        return resampled_df
    
    def align_to_standard_timestamps(self, df: pd.DataFrame, freq: str) -> pd.DataFrame:
        """
        对齐到标准的交易时间轴，暴露出因无成交而隐藏的“缺失 K 线”。
        freq 为时间频率。格式同 Pandas Offset Aliases 保持一致。
        """
        if df.empty:
            return df
        # 1. 提取当前数据的日期范围 
        start_date = df['trading_date'].min()
        end_date = df['trading_date'].max()
        # 2. 向日历索要完美的理论时间轴
        standard_timestamps = []
        date_contracts = (
            df[['trading_date', 'Dominant_Contract']]
            .dropna()
            .drop_duplicates(subset=['trading_date'], keep='first')
        )
        for trading_date, contract in date_contracts.itertuples(index=False):
            if not (start_date <= trading_date <= end_date):
                continue
            standard_timestamps.extend(
                self.calendar.get_standard_timestamps(
                    instrument=contract,
                    start_date=trading_date,
                    end_date=trading_date,
                    freq=freq,
                )
            )
        # 3. 强制对齐：将原本缺失的时间点补齐并填上 NaN
        aligned_df = df.reindex(standard_timestamps)
        return aligned_df
    
    def impute_missing_bars(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        填充缺失的 OHLCV 数据（要求必须先经过 alignment == 'strict' 对齐）
        """
        # 收盘价前向填充
        df['Close'] = df['Close'].ffill()
        # 缺失的开、高、低，直接等于上一根的收盘价
        df['Open'] = df['Open'].fillna(df['Close'])
        df['High'] = df['High'].fillna(df['Close'])
        df['Low'] = df['Low'].fillna(df['Close'])
        # 缺失的成交量为 0
        df['Volume'] = df['Volume'].fillna(0)
        # 缺失的持仓量前向填充
        df['OpenInterest'] = df['OpenInterest'].ffill()
        
        # 对于字符串列（如 Dominant_Contract），进行前向填充
        df['Dominant_Contract'] = df['Dominant_Contract'].ffill()
        df['trading_date'] = df['trading_date'].ffill()

        return df



