from __future__ import annotations
from typing import TYPE_CHECKING, Iterable
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date, time, timedelta
import warnings

if TYPE_CHECKING:
    from data.rules.product_meta_manager import ProductMetaManager  

class TradingCalendar:
    #Note: 对象的职责边界与实例化粒度（Object Responsibility & Instantiation Granularity）问题。 
    #由于不同的品种对应交易所不同，所以交易日历不同，因此，该类在实例化时，更理想的方式是服务于某个特定品种，其应该是有状态的(Stateful)。
    #也就是说，在实例化一开始，就传入特定品种参数，从而让每一个实例服务于一个特定品种。例如 `rb_calendar`。
    """
    交易日历。

    Parameters
    ------
    product: str
        品种代码，例如'RB'。
    meta_manager: ProductMetaManager
        品种元数据管家。用于查询品种对应的交易所，从而加载相应的交易日历。
    calendar_dir: str
        (各交易所的)交易日历 CSV 文件储存路径。

    Methods
    ------
    is_trading_date(date)
        判断指定日期是否为交易日。
    next_trading_date(date)
        返回指定日期之后的下一个交易日。
    get_trading_date(calendar_timestamp)
        返回时间戳对应的交易日，夜盘归属于下一交易日。
    get_session_id(calendar_timestamp)
        判断时间戳所属的交易小节。
    get_time_offset(calendar_timestamp)
        计算时间戳距所属交易小节起点的时间偏移。
    get_minute_offset(calendar_timestamp)
        计算时间戳距所属交易小节起点的分钟数偏移。
    get_trading_dates(calendar_timestamps)
        批量返回时间戳对应的交易日。
    get_session_ids(calendar_timestamps)
        批量返回时间戳所属的交易小节。
    get_minute_offsets(calendar_timestamps)
        批量返回时间戳距所属交易小节起点的分钟数偏移。
    get_calendar_timestamp_from_date_session_offset(trading_date, session_id, minute_offset)
        反向翻译交易日、交易小节和分钟偏移为时间戳。
    get_calendar_timestamps_from_date_session_offset(trading_dates, session_ids, minute_offsets)
        批量反向翻译为交易时间戳。
    get_standard_timestamps(start_date, end_date, freq)
        获取区间内指定频率的完美交易时间戳。

    """
    def __init__(self, 
                 product: str,
                 meta_manager: ProductMetaManager,
                 calendar_dir: str):
        self.product: str = product
        self.meta_manager: ProductMetaManager = meta_manager

        #向 meta_manager 询问该品种所对应的交易所
        exchange = self.meta_manager.get_static_meta(product).exchange
        #读取该交易所的交易日期
        path = Path(calendar_dir) / f'{exchange}.csv'
        
        #设置为pd.DatetimeIndex，"in" operation is O(1), and use get_loc method to return index.
        df_cal = pd.read_csv(path)
        assert df_cal.shape[1] == 1 # 确保只有一列，就是 trading_date
        self.exchange_trading_dates: pd.DatetimeIndex = pd.DatetimeIndex(df_cal.iloc[:, 0]).sort_values()

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
        
        _ts = pd.Timestamp(date)
        _idx = self.exchange_trading_dates.get_indexer([_ts])[0]
        if _idx != -1: #exact match
            return self.exchange_trading_dates[_idx + 1].date()
        # else: no exact match - 输入的 date 参数不是交易日，此时返回在该日期后最临近的一个交易日
        _idx_nearest_after = self.exchange_trading_dates.get_indexer([_ts], method='backfill')[0]
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
        session = self.meta_manager.get_sessions(self.product, trading_date) #根据品种代码和交易日，获取相应的交易时段规则
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
            raise ValueError(f'请确保 {self.product} 的 品种 YAML 文件-交易时间段，早盘和午盘分开放置。')
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
        
        warnings.warn(f'【TradingCalendar】警告：时间戳 {calendar_timestamp} 不属于 {self.product}的合法交易时段！')
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
        session = self.meta_manager.get_sessions(self.product, trading_date) #根据品种代码和交易日，获取相应的交易时段规则

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
            if t < night_start_t: #必须小于，而不是小于等于
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
    
    def _get_prev_trading_date(self, trading_date: date) -> date:
        """获取输入交易日的前一个交易日"""
        try:
            _idx = self.exchange_trading_dates.get_loc(trading_date) # this raises a KeyError if not found
        except KeyError:
            raise ValueError(f'【TradingCalendar】交易日期 {trading_date} 不在 {self.product} 的交易日列表中。')
        if _idx == 0: # 此时 _idx -1 = -1,会返回最后一个交易日，逻辑错误。
            raise ValueError(f'【TradingCalendar】品种 {self.product} 没有比 {trading_date} 更早的交易日了。')
        return self.exchange_trading_dates[_idx - 1].date()
    
    def _get_cal_ts_for_night_sesssion(self, night_start_t: time, t: time, trading_date: date, ) -> pd.Timestamp:
        """内部辅助函数：对于夜盘，利用夜盘开始时间（night_start_t），所需翻译的时间（t）和交易日（trading_date），翻译出日历交易时间戳。"""
        if t < night_start_t: # 超过了零点，日历日为 trading_date 的前一个交易日的后一个自然日
            cal_dt = self._get_prev_trading_date(trading_date) + timedelta(days=1)
        else: # 若没有超过零点，则日历日为 trading_date 的前一个交易日
            cal_dt = self._get_prev_trading_date(trading_date)
        calendar_timestamp = pd.Timestamp(f'{cal_dt.isoformat()} {str(t)}')
        return calendar_timestamp
    
    def get_calendar_timestamp_from_date_session_offset(self, 
                                                        trading_date: date, 
                                                        session_id: int, 
                                                        minute_offset: int) -> pd.Timestamp:
        """
        反向翻译器（单值版本）：根据交易日期(trading_date)、交易小节(session_id)和分钟偏移量(minute_offset)，返回交易时间戳。
        
        Parameters
        ------
        trading_date: datetime.date
            交易日期。
        session_id: int
            交易小节。0(夜盘), 1(早盘), 2(午盘)。
        minute_offset: int
            分钟偏移量，即距离交易小节起点的分钟数。

        Returns
        ------
        pd.Timestamp
            交易时间戳。
        """
        session = self.meta_manager.get_sessions(self.product, trading_date=trading_date)
        # 若需要翻译的是夜盘(session_id==0)
        if session_id == 0:
            night_start = session.night_sessions[0][0]
            night_start_t = pd.Timestamp(night_start).time()

            minutes_passed = 0 #夜盘累计分钟数

            for start_str, end_str in session.night_sessions:
                # 利用内部辅助函数，翻译出该sub_session的开始时间戳和结束时间戳
                start_t = pd.Timestamp(start_str).time()
                end_t = pd.Timestamp(end_str).time()
                start_ts = self._get_cal_ts_for_night_sesssion(
                    night_start_t=night_start_t, 
                    t=start_t,
                    trading_date=trading_date)
                end_ts = self._get_cal_ts_for_night_sesssion(
                    night_start_t=night_start_t,
                    t=end_t,
                    trading_date=trading_date
                )
                minutes_passed += int((end_ts - start_ts).total_seconds() /60)
                if minutes_passed >= minute_offset:  #在这个sub_session，夜盘累计分钟数大于分钟偏移量，说明需要的时间戳在这个sub_session里
                    minutes_to_end = minutes_passed - minute_offset #所需要的时间戳距离末端的分钟距离
                    return end_ts - pd.Timedelta(minutes=minutes_to_end)
            
            # 如果循环结束都没有返回，说明分钟偏移量超过了夜盘的总分钟数，抛出异常
            raise ValueError(f'【TradingCalendar】分钟偏移量 {minute_offset} 超过了 {self.product} 在 {trading_date} 的夜盘总分钟数 {minutes_passed}。请检查输入的分钟偏移量是否合理。')

        # 若需要翻译的是早盘(session_id==1)
        elif session_id == 1:
            minutes_passed = 0 #早盘累计分钟数
            for start_str, end_str in session.day_sessions[:-1]: #早盘时间段列表，注意排除最后一个午盘时间段
                start_ts = pd.Timestamp(f'{trading_date.isoformat()} {start_str}')
                end_ts = pd.Timestamp(f'{trading_date.isoformat()} {end_str}')
                minutes_passed += int((end_ts - start_ts).total_seconds() /60)
                if minutes_passed >= minute_offset: #早盘累计分钟数大于分钟偏移量
                    minutes_to_end = minutes_passed - minute_offset #所需要的时间戳距离末端的分钟距离
                    return end_ts - pd.Timedelta(minutes=minutes_to_end)
            # 如果循环结束都没有返回，说明分钟偏移量超过了早盘的总分钟数，抛出异常
            raise ValueError(f'【TradingCalendar】分钟偏移量 {minute_offset} 超过了 {self.product} 在 {trading_date} 的早盘总分钟数 {minutes_passed}。请检查输入的分钟偏移量是否合理。')
        
        else: # 午盘(session_id==2)
            start_str, end_str = session.day_sessions[-1]
            start_ts = pd.Timestamp(f'{trading_date.isoformat()} {start_str}')
            return start_ts + pd.Timedelta(minutes=minute_offset)

    def get_calendar_timestamps_from_date_session_offset(self,
                                                         trading_dates: Iterable[date],
                                                         session_ids: Iterable[int],
                                                         minute_offsets: Iterable[int]) -> np.ndarray:
        """
        反向翻译器（向量化版本）：根据交易日期(trading_date)、交易小节(session_id)和分钟偏移量(minute_offset)，返回交易时间戳。
        
        内部调用单值版本

        Parameters
        ------
        trading_dates: Iterable[datetime.date]
            交易日期列表。
        session_ids: Iterable[int]
            交易小节列表。
        minute_offsets: Iterable[int]
            分钟偏移量列表。

        Returns
        ------
        np.ndarray
            交易时间戳列表。
        """
        return np.array([
            self.get_calendar_timestamp_from_date_session_offset(trading_date, session_id, minute_offset)
            for trading_date, session_id, minute_offset in zip(trading_dates, session_ids, minute_offsets)
        ])
    
    def get_standard_timestamps(self, start_date: date, end_date: date, freq: str) -> np.ndarray:
        """
        获取日期时段内，特定频率的“完美”（即无空缺的）交易时间戳。
        这里的时间戳指的是日历交易时间戳。

        Parameters
        ------
            start_date: datetime.date
                起始日期（交易日）。
            end_date: datetime.date
                结束日期（交易日）。
            freq: str
                时间频率。格式同 Pandas Offset Aliases 保持一致。

        Returns
        ------
        np.ndarray
            （日历）交易时间戳列表。

        """

        # 从 exchange_trading_dates 中提取从 start_date 至 end_date 的交易日期列表
        trading_dates = self.exchange_trading_dates[self.exchange_trading_dates.slice_indexer(start=start_date, end=end_date)].date
        
        standard_timestamps = []

        for trading_date in trading_dates:
            session = self.meta_manager.get_sessions(product=self.product, trading_date=trading_date)
            
            #日盘（无需处理是否跨零点问题）
            day_sessions = session.day_sessions
            day_timestamps = [
                ts
                for sub_session in day_sessions
                for ts in pd.date_range(
                    start=f'{trading_date.isoformat()} {sub_session[0]}',
                    end=f'{trading_date.isoformat()} {sub_session[1]}',
                    freq=freq,
                    inclusive='left'
                )
            ]
            standard_timestamps.extend(day_timestamps)
            
            #夜盘（需要处理是否跨零点）
            night_sessions = session.night_sessions
            night_start_t = pd.Timestamp(night_sessions[0][0]).time()

            night_timestamps = []
            for sub_session in night_sessions:
                start_str, end_str = sub_session
                start_t = pd.Timestamp(start_str).time()
                end_t = pd.Timestamp(end_str).time()
                # 利用内部辅助函数，翻译出该sub_session的开始时间戳和结束时间戳
                start_ts = self._get_cal_ts_for_night_sesssion(
                    night_start_t=night_start_t,
                    t=start_t,
                    trading_date=trading_date
                )
                end_ts = self._get_cal_ts_for_night_sesssion(
                    night_start_t=night_start_t,
                    t=end_t,
                    trading_date=trading_date
                )
                sub_session_timestamps = pd.date_range(
                    start=start_ts,
                    end=end_ts,
                    freq=freq,
                    inclusive='left'
                ).tolist()
                night_timestamps.extend(sub_session_timestamps)
            
            standard_timestamps.extend(night_timestamps)

        return np.array(standard_timestamps)

    def get_calendar_timestamp_from_trading_date_and_time(self, 
                                                          trading_date: date, 
                                                          t: time) -> pd.Timestamp:
        """
        翻译器（单值版本）：将交易日期+时间的组合翻译为日历交易时间戳。
        时间是指时间戳的时间部分（时分秒）。

        Parameters
        ------
        trading_date: datetime.date
            交易日期。
        t: datetime.time
            时间（时分秒）。

        Returns
        ------
        pd.Timestamp
            日历交易时间戳。
        
        """
        session = self.meta_manager.get_sessions(self.product, trading_date=trading_date)
        night_sessions = session.night_sessions

        # 如果没有夜盘，则直接进入早午盘逻辑
        # TODO: [Tech Debt]
        # 这里我没有加入校验，即没有判断输入的时间t是否合法（即是否在早午盘的时间范围内）。
        # 如果输入了一个不合法的时间（例如 15:30），虽然它不属于夜盘，但它也不属于早午盘，这时函数会直接返回一个日历交易时间戳，但这个时间戳实质上是错误的。
        # 后续可以考虑加入校验逻辑，抛出异常。
        if not night_sessions: # 如果没有夜盘，直接将交易日和时间合成日历交易时间戳返回（默认输入的t是合法的，即在早午盘时间范围内）
            calendar_timestamp = pd.Timestamp(f'{trading_date.isoformat()} {str(t)}')
            return calendar_timestamp
        else:
            # 如果有夜盘，进入判断逻辑，判断时间t是否属于夜盘
            night_start_t = pd.Timestamp(night_sessions[0][0]).time()

            for start_str, end_str in night_sessions:
                start_t = pd.Timestamp(start_str).time()
                end_t = pd.Timestamp(end_str).time()
                # 若该sub_session的时间段（开始-结束）不跨零点 (如 21:00 - 23:00)
                if start_t < end_t: 
                    if start_t <= t <= end_t:  # 若在夜盘时段内：晚于夜盘开始时间，且早于夜盘结束时间
                        return self._get_cal_ts_for_night_sesssion(
                            night_start_t=night_start_t,
                            t=t,
                            trading_date=trading_date
                        )
                # 若跨越零点 (如 21:00 - 02:30)
                else: 
                    if t > start_t or t < end_t:  #若在夜盘时段内：晚于夜盘开始时间，或早于夜盘结束时间
                        return self._get_cal_ts_for_night_sesssion(
                            night_start_t=night_start_t,
                            t=t,
                            trading_date=trading_date
                        )
            # 如果循环结束都没有返回，说明输入的时间t不属于夜盘，那么直接将交易日和时间合成日历交易时间戳返回（默认输入的t是合法的，即在早午盘时间范围内）
            calendar_timestamp = pd.Timestamp(f'{trading_date.isoformat()} {str(t)}')
            return calendar_timestamp
    
    def get_calendar_timestamps_from_trading_date_and_time(self,
                                                          trading_dates: Iterable[date],
                                                          t_s: Iterable[time]) -> np.ndarray:
        """
        翻译器（向量化版本）：将交易日期和时间翻译为日历交易时间戳。
        时间是指时间戳的时间部分（时分秒）。

        内部调用单值版本。

        Parameters
        ------
        trading_dates: Iterable[datetime.date]
            交易日期列表。
        t_s: Iterable[datetime.time]
            时间列表（时分秒）。

        Returns
        ------
        np.ndarray
            日历交易时间戳列表。
        
        """
        return np.array([
            self.get_calendar_timestamp_from_trading_date_and_time(trading_date, t)
            for trading_date, t in zip(trading_dates, t_s)
        ])
