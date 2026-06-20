from __future__ import annotations
from typing import TYPE_CHECKING, Iterable, Literal
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date, time, timedelta
import warnings

from data.rules.models import TradingSessions



if TYPE_CHECKING:
    from data.rules.product_meta_manager import ProductMetaManager  
    from data.rules.trading_rule_service import TradingRuleService

class ExchangeTradingCalendar:
    """
    交易所级交易日历。

    只负责交易所交易日序列相关的基础能力，不依赖品种级交易时段规则。
    """
    def __init__(self, exchange: str, calendar_dir: str):
        self.exchange: str = exchange.upper()

        path = Path(calendar_dir) / f'{self.exchange}.csv'
        
        # 设置为 pd.DatetimeIndex；"in" operation is O(1), and use get_loc method to return index.
        df_cal = pd.read_csv(path)
        assert df_cal.shape[1] == 1 # 确保只有一列，就是 trading_date
        self.exchange_trading_dates: pd.DatetimeIndex = pd.DatetimeIndex(df_cal.iloc[:, 0]).sort_values()

    def is_trading_date(self, date: date) -> bool:
        """判断某个日期是否为交易日。"""
        return pd.Timestamp(date) in self.exchange_trading_dates
    
    def next_trading_date(self, date: date) -> date:
        """返回指定日期之后的下一个交易日。"""
        if self.exchange_trading_dates[-1].date() <= date:
            raise ValueError(f'【ExchangeTradingCalendar】找不到所查询的日期 {date} 的下一个交易日。目前已储存的历史交易日列表的最新值为 {self.exchange_trading_dates[-1].date()}。')
        
        _ts = pd.Timestamp(date)
        _idx = self.exchange_trading_dates.get_indexer([_ts])[0]
        if _idx != -1: #exact match
            return self.exchange_trading_dates[_idx + 1].date()
        # else: no exact match - 输入的 date 参数不是交易日，此时返回在该日期后最临近的一个交易日
        _idx_nearest_after = self.exchange_trading_dates.get_indexer([_ts], method='backfill')[0]
        return self.exchange_trading_dates[_idx_nearest_after].date()
    
    def get_trading_date(self, calendar_timestamp: pd.Timestamp) -> date:
        """
        返回日历交易时间戳归属的交易日(Trading Date)，夜盘时间戳归属于下一个交易日。
        """
        calendar_date = calendar_timestamp.date()
        # 过了下午 17:00，一定是下一个交易日的夜盘
        if calendar_timestamp.time() > pd.Timestamp('17:00:00').time():
            return self.next_trading_date(calendar_date)
        else:
            # 17:00 之前，如果日历日本身是交易日，那就是今天
            if self.is_trading_date(calendar_date):
                return calendar_date
            # 17:00 之前，但日历日是非交易日
            return self.next_trading_date(calendar_date)

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
    get_session_id(instrument, calendar_timestamp, view)
        判断时间戳所属的交易小节。
    get_time_offset(instrument, calendar_timestamp, view)
        计算时间戳距所属交易小节起点的时间偏移。
    get_minute_offset(instrument, calendar_timestamp, view)
        计算时间戳距所属交易小节起点的分钟数偏移。
    get_trading_dates(calendar_timestamps)
        批量返回时间戳对应的交易日。
    get_session_ids(instruments, calendar_timestamps, view)
        批量返回时间戳所属的交易小节。
    get_minute_offsets(instruments, calendar_timestamps, view)
        批量返回时间戳距所属交易小节起点的分钟数偏移。
    get_calendar_timestamp_from_date_session_offset(instrument, trading_date, session_id, minute_offset, view)
        反向翻译交易日、交易小节和分钟偏移为时间戳。
    get_calendar_timestamps_from_date_session_offset(instruments, trading_dates, session_ids, minute_offsets, view)
        批量反向翻译为交易时间戳。
    get_standard_timestamps(instrument, start_date, end_date, freq, view)
        获取区间内指定频率的完美交易时间戳。

    """
    def __init__(self, 
                 product: str,
                 meta_manager: ProductMetaManager,
                 calendar_dir: str,
                 trading_rule_service: TradingRuleService | None = None):
        self.product: str = product
        self.meta_manager: ProductMetaManager = meta_manager
        self.trading_rule_service: TradingRuleService | None = trading_rule_service

        # 向 meta_manager 询问该品种所对应的交易所
        exchange = self.meta_manager.get_static_meta(product).exchange
        # 构造交易所层交易日历
        self.exchange_calendar: ExchangeTradingCalendar = ExchangeTradingCalendar(exchange=exchange, calendar_dir=calendar_dir)
        
        # 设置为pd.DatetimeIndex，"in" operation is O(1), and use get_loc method to return index.
        self.exchange_trading_dates: pd.DatetimeIndex = self.exchange_calendar.exchange_trading_dates

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
        return self.exchange_calendar.is_trading_date(date)
    
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
        return self.exchange_calendar.next_trading_date(date)
    
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
        return self.exchange_calendar.get_trading_date(calendar_timestamp)
    
    def _require_trading_rule_service(self) -> TradingRuleService:
        """校验 TradingRuleService 已注入，并返回它。"""
        if self.trading_rule_service is None:
            raise RuntimeError(f'未注入 TradingRuleService 的实例。')
        return self.trading_rule_service
    
    def _get_trading_sessions(
        self,
        instrument: str,
        trading_date: date,
        view: Literal['scheduled', 'realized'] = 'scheduled',
    ):
        rule_service = self._require_trading_rule_service()
        return rule_service.get_trading_sessions(instrument, trading_date, view=view)
    
    @staticmethod
    def _time_in_interval(t: time, start_t: time, end_t: time) -> bool:
        """左闭右开判断，兼容跨零点区间。"""
        if start_t < end_t:
            return start_t <= t < end_t
        return t >= start_t or t < end_t
    
    def _get_session_intervals_by_id(self, session: TradingSessions, session_id: int) -> tuple[str, list[list[str]]]:
        """内部辅助函数：通过交易小节的 id 来获取交易时段列表。返回值例如('morning', [['9:00','10:15'],['10:30':'11:30']])。"""
        if session_id == 0:
            return 'night', session.night_sessions
        if session_id == 1:
            return 'morning', session.morning_sessions
        if session_id == 2:
            return 'afternoon', session.afternoon_sessions
        raise ValueError(f'【TradingCalendar】未知交易小节 session_id={session_id}。仅支持 0(夜盘)、1(早盘)、2(午盘)。')
    
    @staticmethod
    def _day_session_timestamp(trading_date: date, time_str: str) -> pd.Timestamp:
        return pd.Timestamp(f'{trading_date.isoformat()} {time_str}')
    
    def _session_interval_timestamps(
        self,
        session_group: str,
        intervals: list[list[str]],
        trading_date: date,
    ) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
        if session_group == 'night':
            if not intervals:
                return []
            night_start_t = pd.Timestamp(intervals[0][0]).time()
            return [
                (
                    self._get_cal_ts_for_night_sesssion(night_start_t, pd.Timestamp(start).time(), trading_date),
                    self._get_cal_ts_for_night_sesssion(night_start_t, pd.Timestamp(end).time(), trading_date),
                )
                for start, end in intervals
            ]

        return [
            (
                self._day_session_timestamp(trading_date, start),
                self._day_session_timestamp(trading_date, end),
            )
            for start, end in intervals
        ]
    
    def get_session_id(
        self,
        instrument: str,
        calendar_timestamp: pd.Timestamp,
        view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> int:
        """
        判断时间戳落在哪一个交易小节。

        Parameters
        ------
        instrument: str
            合约代码。
        calendar_timestamp: pd.Timestamp
            (日历)交易时间戳。
        
        Returns
        ---
        int
            时间戳所属交易小节。0(夜盘), 1(早盘), 2(午盘), -1(异常状态, 输入时间戳不属于合法交易时段)。
        
        """
        trading_date = self.get_trading_date(calendar_timestamp)
        trading_session = self._get_trading_sessions(instrument, trading_date, view=view)
        t = calendar_timestamp.time()

        for session_id, intervals in (
            (0, trading_session.night_sessions),
            (1, trading_session.morning_sessions),
            (2, trading_session.afternoon_sessions),
        ):
            for start_str, end_str in intervals:
                start_t = pd.Timestamp(start_str).time()
                end_t = pd.Timestamp(end_str).time()
                if self._time_in_interval(t, start_t, end_t):
                    return session_id
        
        warnings.warn(f'【TradingCalendar】警告：时间戳 {calendar_timestamp} 不属于 {instrument} 的交易时段！')
        return -1 #返回异常状态
    
    def get_time_offset(
        self,
        instrument: str,
        calendar_timestamp: pd.Timestamp,
        view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> pd.Timedelta: 
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
        sid = self.get_session_id(instrument, calendar_timestamp, view=view) #获取交易小节
        if sid == -1: 
            raise ValueError(f'合约 {instrument} 的时间戳 {calendar_timestamp} 不属于合法交易时段。无法计算距离交易小节起点的推移。')

        trading_date = self.get_trading_date(calendar_timestamp) # 首先获取该时间戳所对应的“交易日”
        session = self._get_trading_sessions(instrument, trading_date, view=view)
        session_group, intervals = self._get_session_intervals_by_id(session, sid)
        interval_timestamps = self._session_interval_timestamps(session_group, intervals, trading_date)

        offset = pd.Timedelta(seconds=0)
        for start_ts, end_ts in interval_timestamps:
            if start_ts <= calendar_timestamp < end_ts:
                return offset + (calendar_timestamp - start_ts)
            offset += end_ts - start_ts

        raise RuntimeError(f'''合约 {instrument} 的时间戳 {calendar_timestamp} 不属于合法交易时段  
        {interval_timestamps}。
        无法计算距离交易小节起点的推移。''')

    def get_minute_offset(
        self,
        instrument: str,
        calendar_timestamp: pd.Timestamp,
        view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> int:
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
        offset = self.get_time_offset(instrument, calendar_timestamp, view=view)
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
    
    def get_session_ids(
        self,
        instruments: Iterable[str],
        calendar_timestamps: pd.DatetimeIndex,
        view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> np.ndarray:
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
            self.get_session_id(instrument, ts, view=view)
            for instrument, ts in zip(instruments, calendar_timestamps)
        ])

    def get_minute_offsets(
        self,
        instruments: Iterable[str],
        calendar_timestamps: pd.DatetimeIndex,
        view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> np.ndarray:
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
            self.get_minute_offset(instrument, ts, view=view)
            for instrument, ts in zip(instruments, calendar_timestamps)
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
        """内部辅助函数: 对于夜盘, 利用夜盘开始时间 (night_start_t), 所需翻译的时间 (t) 和交易日 (trading_date)，翻译出日历交易时间戳。"""
        if t < night_start_t: # 超过了零点，日历日为 trading_date 的前一个交易日的后一个自然日
            cal_dt = self._get_prev_trading_date(trading_date) + timedelta(days=1)
        else: # 若没有超过零点，则日历日为 trading_date 的前一个交易日
            cal_dt = self._get_prev_trading_date(trading_date)
        calendar_timestamp = pd.Timestamp(f'{cal_dt.isoformat()} {str(t)}')
        return calendar_timestamp
    
    def get_calendar_timestamp_from_date_session_offset(
        self,
        instrument: str,
        trading_date: date,
        session_id: int,
        minute_offset: int,
        view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> pd.Timestamp:
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
        if minute_offset < 0:
            raise ValueError(f'【TradingCalendar】分钟偏移量不能为负数，实际为 {minute_offset}。')

        session = self._get_trading_sessions(instrument, trading_date, view=view)
        session_group, intervals = self._get_session_intervals_by_id(session, session_id)
        interval_timestamps = self._session_interval_timestamps(session_group, intervals, trading_date)

        minutes_passed = 0
        for start_ts, end_ts in interval_timestamps:
            interval_minutes = int((end_ts - start_ts).total_seconds() / 60)
            if minute_offset < minutes_passed + interval_minutes:
                return start_ts + pd.Timedelta(minutes=minute_offset - minutes_passed)
            minutes_passed += interval_minutes

        raise ValueError(
            f'【TradingCalendar】分钟偏移量 {minute_offset} 超过了 {instrument} 在 {trading_date} '
            f'的 {session_group} 小节总分钟数 {minutes_passed}。请检查输入的分钟偏移量是否合理。'
        )

    def get_calendar_timestamps_from_date_session_offset(self,
                                                         instruments: Iterable[str],
                                                         trading_dates: Iterable[date],
                                                         session_ids: Iterable[int],
                                                         minute_offsets: Iterable[int],
                                                         view: Literal['scheduled', 'realized'] = 'scheduled') -> np.ndarray:
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
            self.get_calendar_timestamp_from_date_session_offset(
                instrument,
                trading_date,
                session_id,
                minute_offset,
                view=view,
            )
            for instrument, trading_date, session_id, minute_offset
            in zip(instruments, trading_dates, session_ids, minute_offsets)
        ])
    
    def get_standard_timestamps(
        self,
        instrument: str,
        start_date: date,
        end_date: date,
        freq: str,
        view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> np.ndarray:
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
            session = self._get_trading_sessions(instrument, trading_date, view=view)
            
            for session_group, intervals in (
                ('morning', session.morning_sessions),
                ('afternoon', session.afternoon_sessions),
                ('night', session.night_sessions),
            ):
                for start_ts, end_ts in self._session_interval_timestamps(session_group, intervals, trading_date):
                    standard_timestamps.extend(pd.date_range(
                        start=start_ts,
                        end=end_ts,
                        freq=freq,
                        inclusive='left'
                    ).tolist())

        return np.array(standard_timestamps)

    def get_calendar_timestamp_from_trading_date_and_time(
        self,
        instrument: str,
        trading_date: date,
        t: time,
        view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> pd.Timestamp:
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
        session = self._get_trading_sessions(instrument, trading_date, view=view)

        for session_group, intervals in (
            ('night', session.night_sessions),
            ('morning', session.morning_sessions),
            ('afternoon', session.afternoon_sessions),
        ):
            for start_str, end_str in intervals:
                start_t = pd.Timestamp(start_str).time()
                end_t = pd.Timestamp(end_str).time()
                if self._time_in_interval(t, start_t, end_t):
                    if session_group == 'night':
                        night_start_t = pd.Timestamp(intervals[0][0]).time()
                        return self._get_cal_ts_for_night_sesssion(
                            night_start_t=night_start_t,
                            t=t,
                            trading_date=trading_date
                        )
                    return pd.Timestamp(f'{trading_date.isoformat()} {str(t)}')

        raise ValueError(f'【TradingCalendar】时间 {t} 不属于 {instrument} 在 {trading_date} 的 {view} 交易时段。')
    
    def get_calendar_timestamps_from_trading_date_and_time(
        self,
        instruments: Iterable[str],
        trading_dates: Iterable[date],
        t_s: Iterable[time],
        view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> np.ndarray:
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
            self.get_calendar_timestamp_from_trading_date_and_time(
                instrument,
                trading_date,
                t,
                view=view,
            )
            for instrument, trading_date, t in zip(instruments, trading_dates, t_s)
        ])
