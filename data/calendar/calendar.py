import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date, time
import copy
from data.metadata.meta_manager import InstrumentMetaManager
import warnings

"""
TradingCalendar, InstrumentMetaManager 相关数据储存方式的说明

一、期货相关数据的储存

储存位置
data/
└── metadata
    └── instrument_meta/
        ├── RB.yaml     # 螺纹钢
        ├── CU.yaml     # 铜
        ├── AL.yaml     # 铝
        ├── IF.yaml     # 沪深300股指
        └── ...

说明：一个品种一个yaml文件。静态属性直接存，历史变化的字段用 effective_date 版本化管理。


三、TradingCalendar 的设计

* 历史交易日列表的储存
    储存位置：
    data/
    └── metadata
        └── trading_calendars
            ├── SHFE.csv    # 上期所（RB、CU、AL等）
            ├── DCE.csv     # 大商所（M、Y、I等）
            ├── CZCE.csv    # 郑商所（MA、TA、CF等）
            ├── CFFEX.csv   # 中金所（IF、IC、T等）
            └── INE.csv     # 上海国际能源中心（SC等）

    说明：CSV 文件只有一列 trading_date 。

"""
class TradingCalendar:
    #Note: 对象的职责边界与实例化粒度（Object Responsibility & Instantiation Granularity）问题。 
    #由于不同的品种对应交易所不同，所以交易日历不同，因此，该类在实例化时，更理想的方式是服务于某个特定品种，其应该是有状态的(Stateful)。
    #也就是说，在实例化一开始，就传入特定品种参数，从而让每一个实例服务于一个特定品种。例如 `rb_calendar`。
    """
    交易日历。

    Parameters
    ------
    symbol_root:
        品种代码前缀，例如'RB'。
    calendar_dir: str
        (各交易所的)交易日历 CSV 文件储存路径。


    Methods
    ------
    is_trading_date(...)
        判断某个日期是否为交易日。
    next_trading_date(...)
        返回指定日期之后的下一个交易日。
    get_trading_date(...)
        返回日历交易时间戳归属的交易日(Trading Date), 夜盘时间戳归属于下一个交易日。
    get_session_id(...)
        判断每个时间戳落在哪一个交易小节。
    get_minute_offset(...)
        计算时间戳距离其所属 Session 起点的绝对分钟数。
    get_trading_dates(...) / get_session_ids() / get_minute_offsets()
        相应methods的向量化版本。
    """
    def __init__(self, 
                 symbol_root: str,
                 meta_manager: InstrumentMetaManager,
                 calendar_dir: str = './data/metadata/trading_calendars'):
        self.symbol_root = symbol_root
        self.meta_manager = meta_manager

        #向 meta_manager 询问该品种所对应的交易所
        exchange = self.meta_manager.get_static_meta(symbol_root).exchange
        #读取该交易所的交易日期
        path = Path(calendar_dir) / f'{exchange}.csv'
        
        #设置为pd.DatetimeIndex，"in" operation is O(1), and use get_loc method to return index.
        df_cal = pd.read_csv(path)
        assert df_cal.shape[1] == 1 # 确保只有一列，就是 trading_date
        self.exchange_trading_dates = pd.DatetimeIndex(df_cal.iloc[:, 0]).sort_values()

    def is_trading_date(self, date: date) -> bool:
        """判断某个日期是否为交易日。
        
        Parameters
        ---
        date: datetime.date
            需要查询的日期。

        Returns
        ------
        bool
            若输入的日期是交易日，返回 True。不是则返回 False。
        """
        return pd.Timestamp(date) in self.exchange_trading_dates
    
    def next_trading_date(self, date: date) -> date:
        """
        返回指定日期之后的下一个交易日。

        Parameters
        ------
        date: datetime.date
            需要查询的日期。

        Returns
        ------
        datetime.date
            历史交易日列表中，所查询交易日的下一个交易日期。
        
        Raises
        ------
        ValueError
            若查询日期 `date` 超出历史交易日列表范围（即大于等于所储存的最新交易日）。

        """
        if self.exchange_trading_dates[-1].date() <= date:
            raise ValueError(f'【TradingCalendar】找不到所查询的日期 {date} 的下一个交易日。目前已储存的历史交易日列表的最新值为 {self.exchange_trading_dates[-1].date()}。')
        
        _idx = self.exchange_trading_dates.get_indexer(pd.to_datetime([date]))[0]
        if _idx != -1: #exact match
            return self.exchange_trading_dates[_idx + 1].date()
        # else: no exact match - 输入的 date 参数不是交易日，此时返回在该日期后最临近的一个交易日
        _idx_nearest_after = self.exchange_trading_dates.get_indexer(pd.to_datetime([date]), method='backfill')[0]
        return self.exchange_trading_dates[_idx_nearest_after].date()
    
    def get_trading_date(self, calendar_timestamp: pd.Timestamp) -> date:
        """
        返回日历交易时间戳归属的交易日(Trading Date), 夜盘时间戳归属于下一个交易日。采用业界标准的“17:00 清算物理分割线”法则。

        Parameters
        ------
        calendar_timestamp: pd.Timestamp
            (日历)交易时间戳
        
        Returns
        ------
        datetime.date
            该时间戳所归属的交易日，夜盘归属下一个交易日。
        
        """
        calendar_date = calendar_timestamp.date()
        # 过了下午 17:00，一定是下一个交易日的夜盘
        if calendar_timestamp.time() > pd.Timestamp('17:00:00').time():
            return self.next_trading_date(calendar_date)
        else:
            #17:00 之前，如果日历日本身是交易日，那就是今天
            if self.is_trading_date(calendar_date):
                return calendar_date
            #17:00 之前，但日历日是非交易日 (比如周六凌晨 02:00 的原油夜盘)
            return self.next_trading_date(calendar_date)
    
    def get_session_id(self, calendar_timestamp: pd.Timestamp) -> int:
        """
        判断每个时间戳落在哪一个交易小节。

        Parameters
        ------
        calendar_timestamp: pd.Timestamp
            (日历)交易时间戳。
        
        Returns
        ---
        int
            时间戳所属交易小节。0(夜盘), 1(早盘), 2(午盘), -1(异常状态, 输入时间戳不属于合法交易时段)。
        
        """
        # 1. 判定夜盘（动态处理是否跨零点） 
        trading_date = self.get_trading_date(calendar_timestamp)
        session = self.meta_manager.get_sessions(self.symbol_root, trading_date) #根据品种代码和交易日，获取相应的交易时段规则
        t = calendar_timestamp.time()

        if session.night_sessions:
            for start_str, end_str in session.night_sessions:
                start_t = pd.Timestamp(start_str).time()
                end_t = pd.Timestamp(end_str).time()
                # 若该夜盘时间段（开始-结束）不跨零点 (如 21:00 - 23:00)
                if start_t < end_t: 
                    if start_t <= t < end_t:  # 若在夜盘时段内
                        return 0 
                # 若跨越零点 (如 21:00 - 02:30)
                else: 
                    if t > start_t or t < end_t:  #若晚于夜盘开始时间，或早于夜盘结束时间(例如 t 为 01:35:05, end_t 为02:30:00)
                        return 0
            
        # 2. 判定早盘与午盘
        day_sessions = session.day_sessions
        # 确保早盘和午盘时间是分开放置的
        try:
            assert len(day_sessions) > 1
        except AssertionError:
            raise ValueError(f'请确保 {self.symbol_root} 的 品种 YAML 文件-交易时间段，早盘和午盘分开放置。')
        # 默认最后一个 session 是午盘，前面都是早盘
        morning_sessions = day_sessions[:-1]
        afternoon_session = day_sessions[-1]

        #是否属于早盘？
        for start, end in morning_sessions:
            if pd.Timestamp(start).time() <= t <= pd.Timestamp(end).time(): 
                return 1
        
        #若不属于早盘，那么是否属于午盘？
        start, end = afternoon_session
        if pd.Timestamp(start).time() <= t <= pd.Timestamp(end).time(): 
            return 2
        
        warnings.warn(f'【TradingCalendar】警告：时间戳 {calendar_timestamp} 不属于 {self.symbol_root}的合法交易时段！')
        return -1 #返回异常状态
    
    def get_time_offset(self, calendar_timestamp: pd.Timestamp) -> pd.Timedelta: 
        """
        计算时间戳距离其所属交易小节起点的推移。交易小节分为早盘、午盘和夜盘。可正确处理盘中休息时段。        

        Parameters
        ------
        calendar_timestamp: pd.Timestamp
            (日历)交易时间戳。
        
        Returns
        ------
        pd.Timedelta
            时间戳距离其所属交易小节起点的推移。
        
        """
        # 1. 首先获取所在交易小节
        sid = self.get_session_id(calendar_timestamp) #获取交易小节
        if sid == -1: 
            return pd.Timedelta(seconds=0)

        # 2. 根据交易小节，获取距离交易小节起点的offset，注意区分早盘是否有休息
        trading_date = self.get_trading_date(calendar_timestamp) # 首先获取该时间戳所对应的“交易日”
        session = self.meta_manager.get_sessions(self.symbol_root, trading_date) #根据品种代码和交易日，获取相应的交易时段规则

        ts_date = calendar_timestamp.date().isoformat() #记录该时间戳的日期
        t = calendar_timestamp.time()

        # 2.1 早盘情况
        if sid == 1:
            morning_sessions = session.day_sessions[:-1] #提取早盘
            morning_start = session.day_sessions[0][0]
            # 若早盘没有休息
            if len(session.day_sessions) == 2:
                offset = calendar_timestamp - pd.Timestamp(f'{ts_date} {morning_start}')
            # 若早盘有休息
            else: 
                break_offset = pd.Timedelta(seconds=0) # 初始化早盘总休息时间推移变量
                for i, (start_str, end_str) in enumerate(morning_sessions):
                    start_t, end_t = pd.Timestamp(start_str).time(), pd.Timestamp(end_str).time()
                    if start_t <= t <= end_t: 
                        offset_with_break = calendar_timestamp - pd.Timestamp(f'{ts_date} {morning_start}')
                        offset = offset_with_break - break_offset
                    else:
                        next_start_str = morning_sessions[i+1][0]
                        break_offset += pd.to_timedelta(next_start_str + ':00') - pd.to_timedelta(end_str + ':00')

        # 2.2 午盘情况
        elif sid == 2:
            # 获取午盘开盘时间
            afternoon_start = session.day_sessions[1][0] if len(session.day_sessions) == 2 else session.day_sessions[2][0]
            offset = calendar_timestamp - pd.Timestamp(f'{ts_date} {afternoon_start}')
        
        # 2.3 夜盘情况（需要区分是否跨0点）
        else: # sid ==0
            night_start = session.night_sessions[0][0]
            night_start_t = pd.Timestamp(night_start).time()
            #若跨过了0点(时间戳的时间部分小于night_start_t，例如01:30 小于 21:30)
            if t <= night_start_t:
                _prev_calendar_date_str = (calendar_timestamp - pd.Timedelta(days=1)).normalize().date().isoformat()
                offset = calendar_timestamp - pd.Timestamp(f'{_prev_calendar_date_str} {night_start}')
            else:
                offset = calendar_timestamp - pd.Timestamp(f'{ts_date} {night_start}')
        
        return offset

    def get_minute_offset(self, calendar_timestamp: pd.Timestamp) -> int:
        """计算时间戳距离其所属交易小节起点的分钟数。交易小节分为早盘、午盘和夜盘。可正确处理盘中休息时段。    
        
        内部调用 `get_time_offset` 方法。
        
        Parameters
        ------
        calendar_timestamp: pd.Timestamp
            (日历)交易时间戳。
        
        Returns
        ------
        int
            时间戳距离其所属交易小节起点的分钟数。

        """
        offset = self.get_time_offset(calendar_timestamp)
        return int(offset.total_seconds()) // 60
    
    def get_trading_dates(self, calendar_timestamps: pd.DatetimeIndex) -> np.ndarray:
        """
        返回日历交易时间戳归属的交易日(Trading Date), 夜盘时间戳归属于下一个交易日。

        内部调用 `get_trading_date` 方法。

        Parameters
        ------
        calendar_timestamps: pd.DatetimeIndex
            日历交易时间戳。
        
        Returns
        ------
        np.ndarray
            时间戳所归属的交易日，夜盘归属下一个交易日。
        
        """
        return np.array([
            self.get_trading_date(ts) for ts in calendar_timestamps
        ])
    
    def get_session_ids(self, calendar_timestamps: pd.DatetimeIndex) -> np.ndarray:
        """
        判断每个时间戳落在哪一个交易小节。

        内部调用 `get_session_id` 方法。

        parameters
        ------
        calendar_timestamps: pd.DatetimeIndex
            (日历)交易时间戳。
        
        Returns
        ------
        np.ndarray
            时间戳所属交易小节。0(夜盘), 1(早盘), 2(午盘)。
        """
        return np.array([
            self.get_session_id(ts) for ts in calendar_timestamps
        ])

    def get_minute_offsets(self, calendar_timestamps: pd.DatetimeIndex) -> np.ndarray:
        """
        计算时间戳距离其所属交易小节起点的分钟数。交易小节分为早盘、午盘和夜盘。可正确处理盘中休息时段。  
        
        内部调用 `get_minute_offset` 方法。

        Parameters
        ------
        calendar_timestamps: pd.DatetimeIndex
            (日历)交易时间戳。
        
        Returns
        ------
        np.ndarray
            时间戳距离其所属交易小节起点的分钟数。
        """
        return np.array([
            self.get_minute_offset(ts) for ts in calendar_timestamps
        ])


    


                


            

            



        
        

    
        
        






    

        
            



        
