from __future__ import annotations
import calendar
from typing import TYPE_CHECKING, Iterable, Literal
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date, time, timedelta
import warnings
from data.rules.models import TradingSessions
from functools import lru_cache

if TYPE_CHECKING:
    from data.calendar.exchange_calendar_manager import ExchangeCalendarManager
    from data.rules.product_meta_manager import ProductMetaManager  
    from data.rules.trading_rule_service import TradingRuleService

class ProductCalendar:
    """
    特定品种的交易日历。

    该类负责针对特定品种进行交易日历的管理与查询。包含判断交易日、查找下一个交易日，
    以及将时间戳映射为交易日、交易小节（夜盘/早盘/午盘）、分钟偏移量等，并支持进行反向映射。

    Parameters
    ------
    product: str
        品种代码，例如'RB'。
    meta_manager: ProductMetaManager
        品种元数据管家，用于查询该品种所归属的交易所。
    exchange_calendar_manager: ExchangeCalendarManager
        交易所层面的交易日历管家，处理具体交易所层面的日历查询。
    trading_rule_service: TradingRuleService, optional
        期货交易规则统一门面服务，用于查询合约的交易时段 and 规则。

    Methods
    ------
    is_trading_date(date)
        判断指定日期是否为交易日。
    next_trading_date(date)
        返回指定日期之后的下一个交易日。
    get_trading_date(calendar_timestamp)
        返回日历交易时间戳归属的交易日（采用“17:00清算物理分割线”法则，夜盘时间戳归入下一个交易日）。
    get_session_id(instrument, calendar_timestamp, view)
        获取时间戳落入的交易小节 ID：0(夜盘), 1(早盘), 2(午盘)。
    get_time_offset(instrument, calendar_timestamp, view)
        计算时间戳距离其所属交易小节起点的 Timedelta 推移（正确扣除盘中休市时段）。
    get_minute_offset(instrument, calendar_timestamp, view)
        计算时间戳距离其所属交易小节起点的分钟数推移。
    get_trading_dates(calendar_timestamps)
        批量获取日历交易时间戳列表对应的归属交易日。
    get_session_ids(instruments, calendar_timestamps, view)
        批量获取时间戳所属的交易小节 ID。
    get_minute_offsets(instruments, calendar_timestamps, view)
        批量获取时间戳距离其所属交易小节起点的分钟数偏移。
    get_calendar_timestamp_from_date_session_offset(instrument, trading_date, session_id, minute_offset, view)
        根据交易日、交易小节 ID 和分钟偏移，反向翻译为唯一的日历交易时间戳。
    get_calendar_timestamps_from_date_session_offset(instruments, trading_dates, session_ids, minute_offsets, view)
        批量将交易日、交易小节 ID 和分钟偏移反向翻译为日历交易时间戳。
    get_standard_timestamps(instrument, start_date, end_date, freq, view)
        获取指定日期区间内，特定频率 of 完美交易时间戳。
    get_calendar_timestamp_from_trading_date_and_time(instrument, trading_date, t, view)
        将交易日与时间（时分秒）组合翻译为唯一的日历交易时间戳（处理夜盘跨自然日等复杂情况）。
    get_calendar_timestamps_from_trading_date_and_time(instruments, trading_dates, t_s, view)
        批量将交易日与时间组合翻译为日历交易时间戳。
    """
    def __init__(self, 
                 product: str,
                 meta_manager: ProductMetaManager,
                 exchange_calendar_manager: ExchangeCalendarManager,
                 trading_rule_service: TradingRuleService | None = None):
        self.product: str = product
        self.meta_manager: ProductMetaManager = meta_manager
        self.trading_rule_service: TradingRuleService | None = trading_rule_service
        self.exchange_calendar_manager: ExchangeCalendarManager = exchange_calendar_manager

        # 向 meta_manager 询问该品种所对应的交易所
        self.exchange: str = self.meta_manager.get_static_meta(product).exchange
        # 储存品种所对应的交易所交易日
        self.exchange_trading_dates: pd.DatetimeIndex = self.exchange_calendar_manager.exchange_trading_dates[self.exchange]

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
        return self.exchange_calendar_manager.is_trading_date(self.exchange, date)
    
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
        return self.exchange_calendar_manager.next_trading_date(self.exchange,date)
    
    def get_trading_date(self, calendar_timestamp: pd.Timestamp) -> date:
        """
        返回日历交易时间戳归属的交易日(Trading Date), 夜盘时间戳归属于下一个交易日。采用业界标准的“17:00 清算物理分割线”法则。

        注意，该方法本身没有办法判断“时间戳”是否属于品种的合法交易时段。

        Parameters
        ------
        calendar_timestamp: pd.Timestamp
            (日历)交易时间戳
        
        Returns
        ------
        datetime.date
            该时间戳所归属的交易日，夜盘归属下一个交易日。
        
        """
        return self.exchange_calendar_manager.get_trading_date(self.exchange,calendar_timestamp)
    
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
    ) -> TradingSessions:
        rule_service = self._require_trading_rule_service()
        return rule_service.get_trading_sessions(instrument, trading_date, view=view)
    
    @lru_cache(maxsize=None)
    def _get_trading_sessions_cached(
            self, instrument: str, 
            trading_date: date, 
            view: Literal['scheduled', 'realized']='scheduled'
            ) -> TradingSessions:
        return self._get_trading_sessions(instrument, trading_date, view)

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
        raise ValueError(f'【ProductCalendar】未知交易小节 session_id={session_id}。仅支持 0(夜盘)、1(早盘)、2(午盘)。')
    
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
            时间戳所属交易小节。0(夜盘), 1(早盘), 2(午盘)。
        
        """
        trading_date = self.get_trading_date(calendar_timestamp)
        trading_session = self._get_trading_sessions_cached(instrument, trading_date, view=view)
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
        
        raise ValueError(f'''【ProductCalendar】：时间戳 {calendar_timestamp} 不属于 {instrument} 的交易时段！
                         合约 {instrument} 在交易日 {trading_date} 的合法交易时段如下： 
                         {trading_session}
                         ''')
    
    def is_valid_trading_timestamp(self, instrument: str, 
                                   calendar_timestamp: pd.Timestamp, 
                                   view: Literal['scheduled','realized']='scheduled') -> bool:
        """判断某个时间戳是否为合法交易时间戳。"""
        try:
            _ = self.get_session_id(instrument=instrument, calendar_timestamp=calendar_timestamp, view=view)
        except ValueError:
            return False
        else:
            return True

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
        sid = self.get_session_id(instrument, calendar_timestamp, view=view) # 获取交易小节
        trading_date = self.get_trading_date(calendar_timestamp) # 首先获取该时间戳所对应的“交易日”
        session = self._get_trading_sessions_cached(instrument, trading_date, view=view)
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

        向量化实现：使用 numpy searchsorted 一次性批量处理所有时间戳，
        避免逐行 Python 循环调用标量版本。

        Parameters
        ------
        calendar_timestamps: pd.DatetimeIndex
            日历交易时间戳。
        
        Returns
        ------
        np.ndarray
            时间戳所归属的交易日，元素类型为 datetime.date。夜盘归属下一个交易日。
        
        """
        if len(calendar_timestamps) == 0:
            return np.array([], dtype=object)

        trading_dates_arr = self.exchange_trading_dates  # pd.DatetimeIndex，该品种所属交易所的全部交易日

        # Step 1: 向量化判断夜盘（时间严格大于 17:00:00）
        minutes_of_day = calendar_timestamps.hour * 60 + calendar_timestamps.minute
        is_night = minutes_of_day > 17 * 60

        # Step 2: 获取日历日期（去掉时分秒，保留为 pd.DatetimeIndex）
        calendar_dates = calendar_timestamps.normalize()

        # Step 3: searchsorted 找到每个日历日期在交易日列表中的插入位置
        # side='left': 返回第一个 trading_dates_arr[i] >= calendar_date 的 i
        idx = trading_dates_arr.searchsorted(calendar_dates, side='left')

        # Step 4: 判断哪些日历日期本身是交易日（精确匹配）
        # safe_idx 用 clip 防止 idx 越界，但即使越界（idx=len），等式也不可能为 True
        # 因为 calendar_date > 最后一个交易日，而 safe_idx 指向的正是最后一个交易日
        safe_idx = np.clip(idx, 0, len(trading_dates_arr) - 1)
        exact_match = trading_dates_arr[safe_idx] == calendar_dates

        # Step 5: 核心逻辑
        #   - 非夜盘 + 精确匹配 → idx 就是当天（交易日）
        #   - 非夜盘 + 非精确匹配 → idx 是 calendar_date 之后的第一个交易日 ✓
        #   - 夜盘 + 精确匹配 → 需要下一个交易日，即 idx + 1
        #   - 夜盘 + 非精确匹配 → idx 已经是 calendar_date 之后的第一个交易日 ✓
        idx[is_night & exact_match] += 1

        # Step 6: 从交易日数组中取出结果
        result = trading_dates_arr[idx]
        return result.date  # array of datetime.date

    def get_session_ids(
        self,
        instruments: Iterable[str],
        calendar_timestamps: pd.DatetimeIndex,
        view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> np.ndarray:
        """
        判断每个时间戳落在哪一个交易小节。

        内部调用 `get_session_ids_and_minute_offsets` 方法。

        parameters
        ------
        instruments: Iterable[str]
            合约代码序列。
        calendar_timestamps: pd.DatetimeIndex
            (日历)交易时间戳序列。
        
        Returns
        ------
        np.ndarray
            时间戳所属交易小节。0(夜盘), 1(早盘), 2(午盘)。
        """
        session_ids, _ = self.get_session_ids_and_minute_offsets(
            instruments=instruments,
            calendar_timestamps=calendar_timestamps,
            view=view,
        )
        return session_ids 

    def get_minute_offsets(
        self,
        instruments: Iterable[str],
        calendar_timestamps: pd.DatetimeIndex,
        view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> np.ndarray:
        """
        计算时间戳距离其所属交易小节起点的分钟数。交易小节分为早盘、午盘和夜盘。可正确处理盘中休息时段。  
        
        内部调用 `get_session_ids_and_minute_offsets` 方法。

        Parameters
        ------
        instruments: Iterable[str]
            合约代码序列。
        calendar_timestamps: pd.DatetimeIndex
            (日历)交易时间戳序列。
        
        Returns
        ------
        np.ndarray
            时间戳距离其所属交易小节起点的分钟数。
        """
        _, minute_offsets = self.get_session_ids_and_minute_offsets(
            instruments=instruments,
            calendar_timestamps=calendar_timestamps,
            view=view,
        )
        return minute_offsets

    def get_session_ids_and_minute_offsets(
        self,
        instruments: Iterable[str],
        calendar_timestamps: pd.DatetimeIndex,
        view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        批量获取 session_id 和 minute_offset。

        按 (instrument, trading_date) 分组，只为每个组合读取一次交易时段规则，
        再用区间掩码批量填充结果，避免逐行调用 get_session_id/get_minute_offset。

        Parameters
        ----------
        instruments : Iterable[str]
            合约代码序列，长度需与 calendar_timestamps 一致。
        calendar_timestamps : pd.DatetimeIndex
            (日历)交易时间戳序列。
        view : Literal['scheduled', 'realized'], default 'scheduled'
            指定使用计划时段还是实际时段。

        Returns
        -------
        session_ids : np.ndarray
            每个时间戳所属交易小节编号（0=夜盘,1=早盘,2=午盘），无效时间戳为 -1。
        minute_offsets : np.ndarray
            每个时间戳距其所属交易小节起点的分钟数，无效时间戳为 -1。
        """
        timestamps = pd.DatetimeIndex(calendar_timestamps)
        n = len(timestamps)
        if n == 0:
            return np.array([], dtype=np.int64), np.array([], dtype=np.int64)

        instruments_arr = np.asarray(list(instruments), dtype=object)
        if len(instruments_arr) != n:
            raise ValueError(
                f'【ProductCalendar】instruments 长度 {len(instruments_arr)} 与 '
                f'calendar_timestamps 长度 {n} 不一致。'
            )

        trading_dates_arr = self.get_trading_dates(timestamps)
        session_ids = np.full(n, -1, dtype=np.int64)
        minute_offsets = np.full(n, -1, dtype=np.int64)

        key_df = pd.DataFrame({
            'instrument': instruments_arr,
            'trading_date': trading_dates_arr,
        })

        for (instrument, trading_date), positions_index in key_df.groupby(
            ['instrument', 'trading_date'],
            sort=False,
        ).groups.items():
            positions = positions_index.to_numpy(dtype=np.int64)
            ts_group = timestamps.take(positions) # 获取合约-交易日的所有时间戳作为一组
            session = self._get_trading_sessions_cached( # 获取合约-交易日的交易时段
                instrument,
                trading_date,
                view=view,
            )
            # 对于（合约-交易日），计算相应的 session_id 和 minute_offset
            for session_id, session_group, intervals in (
                (0, 'night', session.night_sessions),
                (1, 'morning', session.morning_sessions),
                (2, 'afternoon', session.afternoon_sessions),
            ):
                minutes_passed = 0
                for start_ts, end_ts in self._session_interval_timestamps(
                    session_group,
                    intervals,
                    trading_date,
                ):
                    in_interval = (ts_group >= start_ts) & (ts_group < end_ts) # 一组时间戳是否在该 interval 以内
                    if in_interval.any():
                        matched_positions = positions[in_interval]
                        matched_timestamps = ts_group[in_interval]
                        session_ids[matched_positions] = session_id
                        minute_offsets[matched_positions] = (
                            minutes_passed
                            + (
                                (matched_timestamps - start_ts)
                                / pd.Timedelta(minutes=1)
                            ).astype(np.int64)
                        )
                    interval_minutes = int((end_ts - start_ts).total_seconds() / 60)
                    minutes_passed += interval_minutes

        invalid = np.where((session_ids < 0) | (minute_offsets < 0))[0]
        invalid_session_id_count = (session_ids == -1).sum()
        invalid_minute_offset_count = (minute_offsets == -1).sum()
        if len(invalid) > 0:
            idx = int(invalid[0])
            instrument = instruments_arr[idx]
            ts = timestamps[idx]
            trading_date = trading_dates_arr[idx]
            trading_session = self._get_trading_sessions_cached(
                instrument,
                trading_date,
                view=view,
            )
            raise ValueError(f'''【ProductCalendar】：存在非合法的时间戳。
                             1. 无法计算 session_id 的共 {invalid_session_id_count} 个。
                             2. 无法计算 minute_offset 的共 {invalid_minute_offset_count} 个。
                             例：{ts} 不属于 {instrument} 的交易时段，
                         合约 {instrument} 在交易日 {trading_date} 的合法交易时段如下：
                         {trading_session}
                         ''')

        return session_ids, minute_offsets
    
    def _get_prev_trading_date(self, trading_date: date) -> date:
        """获取输入交易日的前一个交易日"""
        try:
            _idx = self.exchange_trading_dates.get_loc(pd.Timestamp(trading_date)) # this raises a KeyError if not found
        except KeyError:
            raise ValueError(f'【ProductCalendar】交易日期 {trading_date} 不在 {self.product} 的交易日列表中。')
        if _idx == 0: # 此时 _idx -1 = -1,会返回最后一个交易日，逻辑错误。
            raise ValueError(f'【ProductCalendar】品种 {self.product} 没有比 {trading_date} 更早的交易日了。')
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
            raise ValueError(f'【ProductCalendar】分钟偏移量不能为负数，实际为 {minute_offset}。')

        session = self._get_trading_sessions_cached(instrument, trading_date, view=view)
        session_group, intervals = self._get_session_intervals_by_id(session, session_id)
        interval_timestamps = self._session_interval_timestamps(session_group, intervals, trading_date)

        minutes_passed = 0
        for start_ts, end_ts in interval_timestamps:
            interval_minutes = int((end_ts - start_ts).total_seconds() / 60)
            if minute_offset < minutes_passed + interval_minutes:
                return start_ts + pd.Timedelta(minutes=minute_offset - minutes_passed)
            minutes_passed += interval_minutes

        raise ValueError(
            f'【ProductCalendar】分钟偏移量 {minute_offset} 超过了 {instrument} 在 {trading_date} ' +
            f'的 {session_group} 小节总分钟数 {minutes_passed}。请检查输入的分钟偏移量是否合理。'
        )

    def get_calendar_timestamps_from_date_session_offset(self,
        instruments: Iterable[str],
        trading_dates: Iterable[date],
        session_ids: Iterable[int],
        minute_offsets: Iterable[int],
        view: Literal['scheduled', 'realized'] = 'scheduled'
    ) -> np.ndarray:
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
            session = self._get_trading_sessions_cached(instrument, trading_date, view=view)
            
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
        instrument: str
            合约代码。
        trading_date: datetime.date
            交易日期。
        t: datetime.time
            时间（时分秒）。
        view: Literal['scheduled', 'realized'], default 'scheduled'
            计划或实际交易时段，默认为计划。

        Returns
        ------
        pd.Timestamp
            日历交易时间戳。
        
        """
        session = self._get_trading_sessions_cached(instrument, trading_date, view=view)

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

        raise ValueError(f'【ProductCalendar】时间 {t} 不属于 {instrument} 在 {trading_date} 的 {view} 交易时段。')
    
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
        instruments: Iterable[str]
            合约代码列表
        trading_dates: Iterable[datetime.date]
            交易日期列表。
        t_s: Iterable[datetime.time]
            时间列表（时分秒）。
        view: Literal['scheduled', 'realized'], default 'scheduled'
            计划或实际交易时段，默认为计划。


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


    
