"""
交易时段规则解释器
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from datetime import date, time
from typing import TYPE_CHECKING, Literal, Any
from data.rules.models import BaseSessions, SessionOverrideRules, TradingSessions

if TYPE_CHECKING:
    from data.rules.instrument_registry_manager import InstrumentRegistryManager
    from data.rules.product_meta_manager import ProductMetaManager
    from data.rules.exchange_holiday_manager import ExchangeHolidayManager

class SessionRuleResolver:
    """
    交易时段规则解释器，专门处理 session override 的匹配、动作收集和动作执行，
    以及 realized 异常事件的处理。

    该解析器由 TradingRuleService 使用，不直接对下游暴露。
    """
    def __init__(
        self,
        registry_manager: InstrumentRegistryManager,
        meta_manager: ProductMetaManager,
        holiday_manager: ExchangeHolidayManager
    ):
        self.registry_manager: InstrumentRegistryManager = registry_manager
        self.meta_manager: ProductMetaManager = meta_manager
        self.holiday_manager: ExchangeHolidayManager = holiday_manager

        # 设定解析器版本
        self.version: int = 1

    def resolve_rules(
        self, 
        contract: str,
        trading_date: date,
        base_sessions: BaseSessions,
        session_override_rules: SessionOverrideRules,
        view: Literal['scheduled', 'realized']
    ) -> TradingSessions:
        """
        收集 actions, apply actions

        if view='realized', apply exception intervals 

        -> TradingSessions
        """
        # 验证解析器版本
        if self.version != session_override_rules.schema_version:
            raise ValueError(f'交易时段特殊规则存储的schema 版本和 SessionRuleResolver 的版本不一致，无法继续。')

        # Initialize action list
        applied_override_ids: list[str] = []
        actions: list[dict[str, Any]] = []

        # Collect matched lifecycle rules 
        matched_lifecycle_rules = self._collect_recurring_lifecycle_rules(
            contract, trading_date, session_override_rules.recurring_instrument_lifecycle_rules)
        
        applied_override_ids.extend([rule['rule_id'] for rule in matched_lifecycle_rules])
        for rule in matched_lifecycle_rules: # rule is a dict
            for action in rule['actions']: # rule['actions'] is a list, action is a dict
                actions.append(action)

        # Collect matched scheduled dated overrides
        matched_dated_overrides = self._collect_scheduled_dated_overrides(
            contract, trading_date, session_override_rules.scheduled_dated_overrides)
        
        applied_override_ids.extend(override['event_id'] for override in matched_dated_overrides)
        for override in matched_dated_overrides: # override is a dict
            for action in override['actions']: # override['actions'] is a list, action is a dict
                actions.append(action)

        scheduled_sessions = self._apply_actions(base_sessions, actions)

        if view == 'scheduled':
            scheduled_trading_sessions = TradingSessions(
                trading_date=trading_date,
                contract=contract,
                morning_sessions=scheduled_sessions.morning_sessions,
                afternoon_sessions=scheduled_sessions.afternoon_sessions,
                night_sessions=scheduled_sessions.night_sessions,
                applied_override_ids=applied_override_ids,
                view='scheduled'
            )
            return scheduled_trading_sessions
        
        # view == 'realized'
        # 1. 收集 realized session events exception intervals
        session_events = self._collect_realized_session_events(
                contract, trading_date, session_override_rules.realized_session_events)
        exception_intervals: list[dict[str, Any]] = []
        for event in session_events: # event is a dict
            for exception in event['exception_intervals']: # event['exception_intervals'] is a list, exception is dict
                exception_intervals.append(exception) 

        # 2. apply exception intervals
        realized_sessions = self._apply_exception_interval(
            scheduled_sessions, exception_intervals)

        # 3. 将 exception interval 对应的 event_id 加入 applied_override_ids
        applied_override_ids.extend([event['event_id'] for event in session_events])

        # 4. 组装并返回
        realized_trading_sessions = TradingSessions(
            trading_date=trading_date,
            contract=contract,
            morning_sessions=realized_sessions.morning_sessions,
            afternoon_sessions=realized_sessions.afternoon_sessions,
            night_sessions=realized_sessions.night_sessions,
            applied_override_ids=applied_override_ids,
            view='realized'
        )
        return realized_trading_sessions
        

    def _collect_recurring_lifecycle_rules(
        self, 
        contract: str, 
        trading_date: date, 
        recurring_instrument_lifecycle_rules: list[dict[str, Any]] # 有关设计，见"session_override_rules 数据存储设计_CodeX.md" - 五、`recurring_instrument_lifecycle_rules` 设计
        ) -> list[dict[str, Any]]: 
        """收集匹配的 life cycle rules。"""
        matched_lifecyle_rules: list[dict[str, Any]] = []
        
        for rule in recurring_instrument_lifecycle_rules:
            # 1. 判断 scope 是否匹配
            if not self._scope_matches(rule['scope'], contract, trading_date):
                continue
            # 2. 判断日期范围是否包含输入的 trading_date
            effective_from_date = pd.Timestamp(rule['effective_from']).date() 
            if trading_date < effective_from_date: # trading_date 小于起始日期，表明不受影响
                continue
            else:
                if rule['effective_to'] is not None and trading_date > pd.Timestamp(rule['effective_to']).date(): # trading_date 大于废止日期，表明不受影响。根据设计，这里的effective_to是闭区间。 也就是说在 effective_to 后一天，规则正式取消，在当天规则仍有效。
                    continue

            # 3. 调取 trigger['type']，调用 InstrumentRegistryManager 相应的方法获取触发值，进而判断规则是否触发，以及收集 actions
            trigger_type: str = rule['trigger']['type']
            
            if trigger_type == 'instrument_last_trade_date':
                last_trade_date = self.registry_manager.get_instrument_registry(contract).last_trade_date
                # 这里要做一次非空值校验，因为这个 last_trade_date 来源于外部数据源。
                try:
                    assert pd.notna(last_trade_date)
                except AssertionError:
                    raise ValueError(f'合约 {contract} 的注册信息中， last_trade_date 为空，无法进行交易时段特殊规则-合约生命周期规则匹配。')
                if trading_date == last_trade_date:
                    matched_lifecyle_rules.append(rule) #将该条rule收集起来
            else:
                raise ValueError(f"目前仅接受 trigger.type 取 'instrument_last_trade_date'，输入的为 {trigger_type}")
        return matched_lifecyle_rules

    def _collect_scheduled_dated_overrides(
        self, 
        contract: str, 
        trading_date: date, 
        scheduled_dated_overrides: list[dict[str, Any]]
        ) -> list[dict[str, Any]]: 
        """收集匹配的 dated overrides。"""
        matched_dated_overrides: list[dict[str, Any]] = []

        for override in scheduled_dated_overrides:
            # 1. 判断 scope 是否匹配
            if not self._scope_matches(override['scope'], contract, trading_date):
                continue

            # 2. 根据 date_selector.type 进行判断
            date_selector: dict[str, Any] = override['date_selector']
            date_selector_type: str = date_selector['type']
            
            if date_selector_type == 'range':
                start: date = pd.Timestamp(date_selector['start']).date()
                end: date = pd.Timestamp(date_selector['end']).date()
                if start <= trading_date <= end:
                    matched_dated_overrides.append(override) # 收集该条 override
            elif date_selector_type == 'dates':
                dates: list[date] = pd.to_datetime(date_selector['dates']).date.tolist()
                if trading_date in dates:
                    matched_dated_overrides.append(override) # 收集该条 override
            elif date_selector_type == 'exchange_holiday_arrangements':
                exchange = self.registry_manager.get_instrument_registry(contract).exchange
                if self.holiday_manager.is_no_night_trading_date(exchange, trading_date):
                    matched_dated_overrides.append(override) # 收集该条 override
            else:
                raise ValueError(f"目前仅接受 date_selector_type 取 'range', 'dates', 'exchange_holiday_arrangements'，输入的为 {date_selector_type}")
        
        return matched_dated_overrides

    def _collect_realized_session_events(
        self, 
        contract: str, 
        trading_date: date, 
        realized_session_events: list[dict[str, Any]]
        ) -> list[dict[str, Any]]: 
        """收集匹配的 realized session event。
        """
        session_events: list[dict[str, Any]] = []

        for event in realized_session_events:
            scope_match = self._scope_matches(event['scope'], contract, trading_date)
            date_match = pd.Timestamp(event['trading_date']).date() == trading_date
            if scope_match and date_match: 
                session_events.append(event)
        return session_events
    
    def _apply_actions(
        self, 
        base_sessions: BaseSessions, 
        actions: list[dict[str, Any]]
        ) -> BaseSessions: 
        """
        执行一系列对交易时段进行调整的actions。

        返回一个 BaseSessions 实例，包含已根据各 actions 调整过的交易小节。
        """
        """
        example of actions:

        [
            {
                "operation": "remove_session_group",
                "session_group": "night"
                }
            {
                "operation": "remove_session_group",
                "session_group": "afternoon"
                }
            {
                "operation": "replace_session_group",
                "session_group": "night"
                "intervals": [
                    ["22:00","23:00"]
                ]
            }
            {   
                "operation": "set_first_interval_start",
                "session_group": "night",
                "time": "22:30"
            }
            {
                "operation": "set_last_interval_end",
                "session_group": "night",
                "time": "23:25"
            }

            {
                "operation": "apply_exception_interval",
                "session_group": "morning",
                "start": "10:00",
                "end": "10:01",
                "market_state": "halted"
            }
            {
                "operation": "apply_exception_interval",
                "session_group": "morning",
                "start": "10:02",
                "end": "10:02",
                "market_state": "call_auction"
            }
        ]

        session_group: morning/afternoon/night,
        operation: remove_session_group/replace_session_group/set_first_interavl_start/set_last_interval_end/apply_exception_interval

        执行顺序： remove > replace > set interval start or end > apply_exception_interval

        执行规则：

        1. 存在 remove 时，执行并跳过后续所有；
        2. 不存在 remove，但存在 replace 时，执行 replace，跳过边界收缩，执行 apply_exception_interval；
        3. 不存在 remove 和 replace，但存在边界收缩动作时，执行边界收缩，执行 apply_exception_interval；
        """
        # 进行简单校验： 
        # operation 必须属于以下四个枚举：remove_session_group, replace_session_group, set_first_interval_start, set_last_interval_end
        # session_group 必须属于以下三者： morning, afternoon, night.
        allowed_operations = ['remove_session_group', 'replace_session_group', 
        'set_first_interval_start', 'set_last_interval_end', 'apply_exception_interval']
        session_groups = ['morning', 'afternoon', 'night']
        for action in actions:
            if action['operation'] not in allowed_operations:
                raise ValueError(f"operation {action['operation']} is not allowed. Allowed operations are: {allowed_operations}")
            if action['session_group'] not in session_groups:
                raise ValueError(f"session_group {action['session_group']} is not allowed. Allowed session groups are: {session_groups}")
            if action['operation'] == 'apply_exception_interval':
                for key in ('start', 'end', 'market_state'):
                    if key not in action:
                        raise ValueError(f"apply_exception_interval action 缺少必要字段 {key}: {action}")
        modified_sessions: dict[str, list[list[str]]] = {
            'morning': [interval.copy() for interval in base_sessions.morning_sessions],
            'afternoon': [interval.copy() for interval in base_sessions.afternoon_sessions],
            'night':[interval.copy() for interval in base_sessions.night_sessions]
        }

        # def operation_exists(
        #     actions: list[dict[str, str | list[list[str]]]], 
        #     operation: Literal['remove_session_group', 'replace_session_group',
        #     'set_first_interval_start','set_last_interval_end']
        #     ) -> bool:
        #     for action in actions:
        #         if action['operation'] == operation:
        #             return True
        #     return False

        def sort_trading_times(times: np.ndarray, morning_session_start_time: time) -> list[time]:
            "将一组交易时间（时间戳的时间部分—— time ）按从早到晚排序，兼容跨零点。"
            before_midnight_times = times[times >= morning_session_start_time]
            after_midnight_times = times[times < morning_session_start_time]
            times_sorted = sorted(before_midnight_times) + sorted(after_midnight_times)
            return times_sorted

        for session_group in ('morning', 'afternoon', 'night'):

            actions_in_session = [action for action in actions if action['session_group']==session_group]

            # if there's remove operation, implement it and skip all else
            remove_actions_in_session = [action for action in actions_in_session if action['operation']=='remove_session_group']
            if remove_actions_in_session:
                modified_sessions[session_group] = []
                continue

            # then no remove, if there's replace, implement replace, skip 边界收缩
            replace_implemented = False

            replace_actions_in_session = [action for action in actions_in_session if action['operation']=='replace_session_group']
            if replace_actions_in_session:  
                try: # 只允许一个 replace session group。否则肯定是 yaml 文件储存的信息出错。
                    assert len(replace_actions_in_session) == 1
                except:
                    raise ValueError(f'交易时段特殊规则安排，对每一个小节(morning/afternoon/night)，仅允许一个 replace session group， 现在找到 {len(replace_actions_in_session)} 个。')
                intervals = replace_actions_in_session[0]['intervals']
                assert isinstance(intervals, list) # narrow its type to list
                modified_sessions[session_group] = intervals
                replace_implemented = True

            # 若没有执行过 remove， 也没有执行过 replace，则执行（可能存在的）边界收缩
            if not replace_implemented:
                set_start_actions = [action for action in actions_in_session if action['operation']=='set_first_interval_start']
                set_end_actions = [action for action in actions_in_session if action['operation']=='set_last_interval_end']
                
                start_end_time_for_replace: dict[str, None | time] = {'start_time': None, 'end_time': None}
                morning_session_start_time = pd.Timestamp(base_sessions.morning_sessions[0][0]).time()

                # 若有action要设定开始时间，则取最晚的一个
                if set_start_actions: 
                    start_times = pd.to_datetime([action['time'] for action in set_start_actions]).time
                    start_times_sorted = sort_trading_times(start_times, morning_session_start_time)
                    latest: time = start_times_sorted[-1]
                    start_end_time_for_replace['start_time'] = latest
                # 若有action要设定结束时间，取最早的一个。
                if set_end_actions:
                    end_times = pd.to_datetime([action['time'] for action in set_end_actions]).time
                    end_times_sorted = sort_trading_times(end_times, morning_session_start_time)
                    earliest: time = end_times_sorted[0]
                    start_end_time_for_replace['end_time'] = earliest

                # If there are both first interval start and last interval end for replace
                # 确保 start time for replace is before end time for replace
                # 只要用 sort_trading_times 函数做一次排序，若排序出来 start_time 不是前面的那个，就抛出错误
                if all([value for _, value in start_end_time_for_replace.items()]): 
                    start_time = start_end_time_for_replace['start_time']
                    end_time = start_end_time_for_replace['end_time']
                    _sorted = sort_trading_times(np.array([start_time, end_time]), morning_session_start_time)
                    if not start_time == _sorted[0]:
                        raise ValueError(f'交易时间段特殊安排中，在小节 {session_group}，出现了欲修改的开始时间 {start_time} 晚于结束时间 {end_time} 的情况。')

                # 若需要替换小节的开始时间
                if start_end_time_for_replace['start_time'] is not None: 
                    # 首先检查一下该小节确实存在
                    if not modified_sessions[session_group]:
                        raise ValueError(f'需要替换交易小节 {session_group} 开始时间为 {start_end_time_for_replace['start_time']}，但该小节实际上不存在。')
                    modified_sessions[session_group][0][0] = start_end_time_for_replace['start_time'].strftime("%H:%M")
                
                # 若需要替换小节的结束时间
                if start_end_time_for_replace['end_time'] is not None:
                    # 首先检查一下该小节确实存在
                    if not modified_sessions[session_group]:
                        raise ValueError(f'需要替换交易小节 {session_group} 结束时间为 {start_end_time_for_replace['end_time']}，但该小节实际上不存在。')
                    modified_sessions[session_group][-1][-1] = start_end_time_for_replace['end_time'].strftime("%H:%M")
            
            # implement apply_exception_interval
            group_exceptions = [
                action for action in actions
                if action['operation'] == 'apply_exception_interval'
                and action['session_group'] == session_group
            ]
            modified_sessions[session_group] = self._subtract_exceptions(modified_sessions[session_group], group_exceptions)
            
        # 组装返回（一个BaseSession实例）
        return BaseSessions(
            effective_date=base_sessions.effective_date,
            morning_sessions=modified_sessions['morning'],
            afternoon_sessions=modified_sessions['afternoon'],
            night_sessions=modified_sessions['night']
        )

    def _subtract_exceptions(
        self,
        session_intervals: list[list[str]],
        group_exceptions: list[dict[str, Any]],) -> list[list[str]]:
        """
        从 session_intervals (早盘/午盘/夜盘的 intervals) 中减去 a group of exceptions。得到 remaining session intervals。
        """
        """
        这个函数是CodeX写的，下面我描述核心算法逻辑。

        首先将 interval([start_str, end_str]) 转化为一个可比较大小的分钟数。这里考虑了跨零点的情况，即如果发现跨零点（用的具体逻辑是时间小于交易小节开始时间），则加上24*60分钟。
        如此一来，不论时间字符串是什么，都可以进行大小的比较，这大小比较在逻辑上就是时间的先后顺序。
        例如 [22:30, 1:30], 交易小节开始是 21:30，则前者转换为22*60+30=1350，后者是24*60+60*1+30=1530，后者大于前者，表明后者是更晚的。

        随后将所有的scheduled_session 和 exception_intervals 中的 "intervals" 都转换为上述的分钟数。
        例如对于夜盘， scheduled_session=[["21:30","02:30"]], exception_intervals=[["21:45","22:50"],["23:55","01:00"]]，
        前者转换为[1290, 1590]，后者是[[1305, 1370], [1435, 1500]]。

        随后执行剪切。假设一个 base_interval，一个 exception_interval（内部组成都是分钟数），从前者里面减去后者，得到 remaining intervals。
        算法：
        1. 取 base_interval(base_start, base_end) 和 exception_interval(exception_start, exception_end) 的开始的较大值(overlap_start)，结束的较小值(overlap_end)。
        2. 若 overlap_start > base_start， 合并一个 [base_start, overlap_start]。
        3. 若 overlap_end < base_end，合并一个[overlap_end, base_end]。
        4. 若 overlap_start >= overlap_end，表明两个区间没有交叉，此时输出 base_interval，实际上是不做剪切。

        这个算法可以保证：
        1. 若 exception_interval 被包裹在 base_interval 以内， 则输出 [[base_start, exception_start],[exception_end, base_end]]
        2. 若前者在后者之外，即两者没有交叉，不做剪切。
        3. 边界情况正确处理。例如 exception_end=base_end。

        然后做一个循环：对每一个exception_intervals中的interval，让 base_interval中的每个区间剪切一次前者，得到剩余的。

        最后再将所有的intervals中的分钟数转化回时间字符串。
        
        """

        # ---------------- 一系列内部辅助函数 ----------------------

        def parse_time(value: str) -> time:
            """将以 string 形式表示的时间转换为 datetime.time"""
            try:
                return pd.Timestamp(value).time()
            except Exception as exc:
                raise ValueError(f"无法解析 exception interval 时间: {value}") from exc

        def time_to_minutes(value: str | time, anchor: time) -> int:
            """
            根据 value 是否大于 anchor，
            将时间(value)转换为距离上一个或上上个 midnight 的分钟数。

            如果 value 大于 anchor，转换为距离上一个 midnight的分钟数：
            例如 value = 22:45，anchor = 21:00，则输出 22*60+45；

            如果 value 小于 anchor，转换为距离上上个 midnight的分钟数：
            例如 value = 00:50, anchor = 21:00，表明跨越了0点，给加上一整天，输出 0*60+50+24*60
            """
            parsed_time = parse_time(value) if isinstance(value, str) else value
            minutes = parsed_time.hour * 60 + parsed_time.minute
            anchor_minutes = anchor.hour * 60 + anchor.minute
            if minutes < anchor_minutes:
                minutes += 24 * 60
            return minutes

        def minutes_to_time_str(minutes: int) -> str:
            """
            将某一个分钟数转换为时间字符串。

            实际上是将任何一个分钟数转化为距离 midnight 的时间。

            例如 0*60+50+24*60：首先去掉24*60的整数倍，然后剩1个小时50分钟，因此会转换为 01:50。
            """
            minutes = minutes % (24 * 60)
            hour = minutes // 60
            minute = minutes % 60
            return f"{hour:02d}:{minute:02d}"

        def normalize_interval(interval: list[str], anchor: time) -> tuple[int, int]:
            """
            内部调用 time_to_minutes 将 interval 的开始和结束时间转化为距离(上个或上上个) midnight 的分钟数。
            """
            if len(interval) != 2:
                raise ValueError(f"交易时段 interval 必须包含 [start, end] 两个元素，实际为: {interval}")
            start = time_to_minutes(interval[0], anchor)
            end = time_to_minutes(interval[1], anchor)
            if end <= start:
                raise ValueError(f"交易时段 interval 开始时间必须早于结束时间，实际为: {interval}")
            return start, end

        def normalize_exception_interval(exception: dict[str, Any], anchor: time) -> tuple[int, int]:
            """
            内部调用 time_to_minutes 将 exception_interval 的开始和结束时间转化为距离(上个或上上个) midnight 的分钟数。
            """
            for key in ('session_group', 'start', 'end', 'market_state'):
                if key not in exception:
                    raise ValueError(f"exception interval 缺少必要字段 {key}: {exception}")

            start = time_to_minutes(exception['start'], anchor)
            end = time_to_minutes(exception['end'], anchor)
            if end < start:
                raise ValueError(f"exception interval 开始时间不能晚于结束时间，实际为: {exception}")
            return start, end

        def subtract_one_interval(
            base_interval: tuple[int, int],
            exception_interval: tuple[int, int],
        ) -> list[tuple[int, int]]:
            """从base_interval中减去exception_interval, 得到 remaing。 base_interval, exception_interval 和 remaining 中的所有数字都为分钟数。"""
            base_start, base_end = base_interval
            exception_start, exception_end = exception_interval

            overlap_start = max(base_start, exception_start)
            overlap_end = min(base_end, exception_end)
            if overlap_start >= overlap_end: 
                return [base_interval]

            remaining: list[tuple[int, int]] = []
            if base_start < overlap_start:
                remaining.append((base_start, overlap_start))
            if overlap_end < base_end:
                remaining.append((overlap_end, base_end))
            return remaining

        # ---------------- end of 内部辅助函数 ----------------------

        if not session_intervals:
            return []
        if not group_exceptions:
            return [interval.copy() for interval in session_intervals]

        anchor = parse_time(session_intervals[0][0]) # 交易小节的开始时间
        remaining_intervals = [
            normalize_interval(interval, anchor)
            for interval in session_intervals
        ]

        normalized_exceptions = [
            normalize_exception_interval(exception, anchor)
            for exception in group_exceptions
        ]
        normalized_exceptions.sort(key=lambda interval: interval[0])

        for exception_interval in normalized_exceptions:
            next_remaining: list[tuple[int, int]] = []
            for interval in remaining_intervals:
                next_remaining.extend(subtract_one_interval(interval, exception_interval))
            remaining_intervals = next_remaining
            if not remaining_intervals:
                break

        return [
            [minutes_to_time_str(start), minutes_to_time_str(end)]
            for start, end in remaining_intervals
            if start < end
        ]

    def _scope_matches(self, scope: dict[str, Any], 
        contract: str, trading_date: date | None = None) -> bool:
        """判断规则是否覆盖当前合约""" 
        inst_registry = self.registry_manager.get_instrument_registry(contract)
        exchange = inst_registry.exchange
        product = inst_registry.product

        # 1. 检查交易所是否匹配
        if exchange not in scope['exchanges']:
                return False
        # 2. 检查品种是否匹配
        if 'products' in scope:
            if product not in scope['products']:
                return False
        elif 'product_filter' in scope:
            product_filter: list[str] = scope['product_filter']
            if 'all' in product_filter: # 无过滤
                pass
            if 'has_night_session_on_trading_date' in product_filter: # 若要求当日有夜盘
                #检查该品种当日是否有夜盘
                assert trading_date is not None # 输入了 trading_date 参数
                night_sessions = self.meta_manager.get_base_sessions(product, trading_date).night_sessions
                if not night_sessions: # 当日无夜盘
                    return False
        # 3. 检查合约代码是否匹配
        if 'instruments' in scope:
            if scope['instruments'] == 'all': 
                pass # 匹配全部合约，不做过滤
            elif isinstance(scope['instruments'], list): #若 instruments 字段不是 'all'，而是一个列表
                if contract not in scope['instruments']:
                    return False
        return True

    def _apply_exception_interval(
        self, 
        scheduled_sessions: BaseSessions, 
        exception_intervals: list[dict[str, Any]],
        ) -> BaseSessions:
        """
        执行从schedule sessions 中减去所有的exception_intervals。

        例如对于早盘 ['9:30','11:30'], exception_intervals = [
            ['9:45','9:55'],
            ['10:20','10:50']
        ]
        对于夜盘 ['21:30','2:30'], exception_intervals = [
            ['22:00':'22:45'],
            ['23:55','00:45'],
            ['00:55','01:25']    
        ]
        """
        session_groups = ('morning', 'afternoon', 'night')

        modified_sessions: dict[str, list[list[str]]] = {
            'morning': [interval.copy() for interval in scheduled_sessions.morning_sessions],
            'afternoon': [interval.copy() for interval in scheduled_sessions.afternoon_sessions],
            'night': [interval.copy() for interval in scheduled_sessions.night_sessions],
        }
        # 检查 session_group 字段是否为 morning, afternnon, night 三者之一
        for exception in exception_intervals:
            session_group = exception.get('session_group')
            if session_group not in session_groups:
                raise ValueError(
                    ("exception interval session_group 仅接受 "
                    f"{session_groups}，实际为: {session_group}"
                    )
                )

        for session_group in session_groups:
            group_exceptions = [
                exception for exception in exception_intervals
                if exception['session_group'] == session_group
            ]
            modified_sessions[session_group] = self._subtract_exceptions(
                modified_sessions[session_group],
                group_exceptions,
            )

        return BaseSessions(
            effective_date=scheduled_sessions.effective_date,
            morning_sessions=modified_sessions['morning'],
            afternoon_sessions=modified_sessions['afternoon'],
            night_sessions=modified_sessions['night'],
        )
    
    
    

