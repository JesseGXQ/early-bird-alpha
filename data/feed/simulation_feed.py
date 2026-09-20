from __future__ import annotations
import heapq

import pandas as pd
from typing import TYPE_CHECKING, Generator
from datetime import date
import warnings

from data.storage.tick_loader import TickLoaderFactory
from data.feed.models import (
    ContractTick, MarketEvent, MarketSnapshot, NoDominantContractError,
    NoDominantEvent,
)
from data.stitching.models import RollState

if TYPE_CHECKING:
    from data.stitching.stitching import ContinuousContractManager
    from data.cleaning.cleaning_tick import CleaningPipeline
    from data.calendar.product_calendar import ProductCalendar
    from data.storage.file_locator import FileLocator


class TickStreamGenerator:
    """
    内部流式生成器(The "Crusher")
    """

    def __init__(self):
        pass

    def generate_stream(self, cleaned_tick_df_chunk: pd.DataFrame) -> Generator[ContractTick, None, None]: 
        """
        核心碾碎逻辑。将 Pandas DataFrame 数据块转换为生成器，逐笔输出 ContractTick 。

        Parameters
        ---
        cleaned_tick_df_chunk: pd.DataFrame
            已通过清洗管道处理完毕的 TICK 数据块。
        
        Yields
        ------
        ContractTick
            逐笔产出的 TICK 数据对象。
        """
        if cleaned_tick_df_chunk.empty:
            return

        #逐笔 yield 吐出
        for row in cleaned_tick_df_chunk.itertuples():
            yield ContractTick(
                contract=str(row.contract), #做一次 explicit conversion，收紧类型，下同
                calendar_timestamp=pd.Timestamp(row.calendar_timestamp),  #type: ignore
                trading_date=pd.Timestamp(row.trading_date).date(), # type: ignore
                last=float(row.last),  #type: ignore
                bid=float(row.bid), #type: ignore
                ask=float(row.ask), #type: ignore
                volume=int(row.volume), #type: ignore
                ask_volume=int(row.ask_volume), #type: ignore
                bid_volume=int(row.bid_volume), #type: ignore
                open_interest=float(row.open_interest), #type: ignore
                is_limit_up = bool(row.is_limit_up), 
                is_limit_down = bool(row.is_limit_down)
            )

class SimulationFeed:
    """
    回测用事件分发器， Data Engine 面对回测引擎(Execution模块)的唯一实盘流式接口。
    按交易日下发市场事件；有主力时输出市场快照，无主力时输出控制事件。

    Parameters
    ------
    product: str
        品种代码，例如'RB'。
    calendar: ProductCalendar
        交易日历实例，提供交易日、交易小节、分钟偏移等查询功能。
    contract_manager: ContinuousContractManager
        连续合约大管家，包含换月总表；根据交易日期返回结构化的主力、
        旧主力与换月状态。
    file_locator: FileLocator
        文件寻址器，提供根据合约代码和日期查询数据文件路径的功能。
    clean_pipeline: CleaningPipeline
        含滤芯的清洗管道，提供 process 方法，输入原始 TICK 数据块，输出干净的 TICK 数据块。

    Methods
    ------
    get_market_event_stream(start_date, end_date)
        输出快照与无主力控制事件组成的混合流。
    get_snapshot_stream(start_date, end_date)
        仅输出市场快照；遇无主力交易日时明确报错。

    """
    def __init__(self, 
            product: str,
            calendar: ProductCalendar, 
            file_locator: FileLocator,
            clean_pipeline: CleaningPipeline, # 已经添加了滤芯的清洗管道
            contract_manager: ContinuousContractManager):
        self.product = product
        self.calendar = calendar
        self.file_locator = file_locator
        self.clean_pipeline = clean_pipeline 
        self.contract_manager = contract_manager
        self.tsg = TickStreamGenerator() 
        
    def get_snapshot_stream(self, 
            start_date: date, 
            end_date: date) -> Generator[MarketSnapshot, None, None]:
        """
        输出指定日期范围内的市场快照，不混入控制事件。

        Parameters
        ----------
        start_date : datetime.date
            起始交易日。
        end_date : datetime.date
            结束交易日。

        Yields
        ------
        MarketSnapshot
            按时间顺序下发的主力合约市场快照。

        Notes
        -----
        这是仅快照兼容接口，内部消费混合事件流。遇到 ``NoDominantEvent``
        时抛出 ``NoDominantContractError``，不静默跳过，也不把控制事件
        伪装为市场快照。生成器异常传出后不能继续迭代；需要继续处理后续
        交易日的调用方应使用 ``get_market_event_stream``。
        """
        for event in self.get_market_event_stream(start_date, end_date):
            if isinstance(event, NoDominantEvent):
                raise NoDominantContractError(
                    f'{event.product} 在 {event.trading_date} 的换月表中没有主力合约；'
                    '仅快照接口无法继续，请改用混合事件流。'
                )
            yield event

    def get_market_event_stream(
            self, start_date: date, end_date: date
    ) -> Generator[MarketEvent, None, None]:
        """输出逐笔市场快照与逐日无主力控制事件。

        Parameters
        ----------
        start_date : datetime.date
            起始交易日。
        end_date : datetime.date
            结束交易日。

        Yields
        ------
        MarketSnapshot | NoDominantEvent
            主力已知时逐笔输出快照；主力为空时输出一条逐日控制事件。

        Notes
        -----
        按交易日遍历换月表。当日主力为空时，在该日任何行情快照之前
        输出一次 ``NoDominantEvent``，不创建 TICK 生成器，并继续下个交易日。
        当日主力已知而历史窗口缺失时，仍订阅该主力，快照的 ``roll_state``
        为 ``UNKNOWN``，且不推断旧主力。正常换月则同时订阅旧、新合约。
        控制事件只有交易日期，没有虚构的日历时间戳；流的外层顺序按
        交易日排列，同一交易日的行情快照按实际时间戳排列。
        换月表缺少整行、多次换月等数据或接口错误会直接传出，
        不转换为无主力控制事件。
        """
        # 根据 start_date 和 end_date， 获取所有的交易日期
        mask = (
            (self.calendar.exchange_trading_dates >= pd.Timestamp(start_date) ) & 
            (self.calendar.exchange_trading_dates <= pd.Timestamp(end_date) )
        )
        if not mask.any(): # 如果输入的日期范围完全在交易日历之外
            raise ValueError(f'''输入的 start_date {start_date} 和 end_date {end_date} 完全落于交易日历之外。
                             目前 {self.product} 的交易日历最早为 {self.calendar.exchange_trading_dates[0].date()}，
                             最晚为 {self.calendar.exchange_trading_dates[-1].date()}
                             ''')
        trading_dates = self.calendar.exchange_trading_dates[mask].date

        for trading_date in trading_dates:
            selection = self.contract_manager.get_active_physical_contracts(trading_date)
            if selection.dominant is None:
                yield NoDominantEvent(product=self.product, trading_date=trading_date)
                continue

            contracts = selection.contracts
            if selection.roll_state is RollState.ROLL:
                assert selection.outgoing is not None
                dominant_gen = self._make_contract_tick_generator(
                    trading_date=trading_date,
                    contract=selection.dominant
                )
                outgoing_gen = self._make_contract_tick_generator(
                    trading_date=trading_date,
                    contract=selection.outgoing
                )
            else:
                dominant_gen=self._make_contract_tick_generator(
                    trading_date=trading_date,
                    contract=selection.dominant
                )
                outgoing_gen = None

            yield from self._stream(
                contracts=contracts,
                trading_date=trading_date,
                dominant_gen=dominant_gen,
                outgoing_gen=outgoing_gen,
                roll_state=selection.roll_state
            )

    def _make_contract_tick_generator(self, 
                                      trading_date: date, 
                                      contract: str) -> Generator[ContractTick, None, None]:
        
        """根据输入的交易日和合约，寻找 TICK 数据地址，创建加载器，获取原始 TICK 数据块流，通过清洗流水线，最终生成 ContractTick 流"""

        # 寻找相应的 TICK 数据地址，并创建相应的 TICK 数据加载器
        file_path = self.file_locator.get_tick_file_path(
            contract=contract, 
            trading_date=trading_date
            )
        tick_loader = TickLoaderFactory.create_loader(file_path) #获得一个 CSVTickLoader 的实例或 ParquetTickLoader 的实例
        # 获取原始 TICK 数据块流
        raw_tick_df_chunk_stream = tick_loader.get_tick_df_chunk_stream()
        for raw_tick_df_chunk in raw_tick_df_chunk_stream:
            # 通过（已加载滤芯的）清洗流水线，得到干净的数据块
            cleaned_tick_df_chunk = self.clean_pipeline.process(raw_tick_df_chunk) 
            # 生成 ContractTick 流
            yield from self.tsg.generate_stream(cleaned_tick_df_chunk)

    def _stream(self, 
                contracts: tuple[str, ...],
                trading_date: date,
                dominant_gen: Generator[ContractTick, None, None],
                outgoing_gen: Generator[ContractTick, None, None] | None,
                roll_state: RollState,
                ) -> Generator[MarketSnapshot, None, None]:
        """
        合并主力与旧主力的 TICK 流，逐时点输出市场快照。

        Notes
        -----
        使用最小堆按日历时间戳合并两路 TICK；同一时间戳的 TICK
        全部处理完后才输出一条快照。每路合约保留最新 TICK 作零阶保持，
        因此一方尚未出现首笔数据时，其快照字段仍可能为空。
        ``roll_state`` 原样传递到每条快照，历史窗口缺失时不会被
        强行转换为布尔换月标记。若主力 TICK 流为空，沿用现有行为：
        发出警告且该日没有快照；旧主力 TICK 流为空则仅输出主力行情。
        """
        generators = {'dominant': dominant_gen, 'outgoing': outgoing_gen}

        heap = [] # contains: [ts, priority, label('domminant'/'outgoing'), tick(ContractTick的实例)]
        latest_tick: dict[str, ContractTick] = {} # 零阶保持字典(str is either 'dominant' or 'outgoing')


        # 初始化 heap
        # 这里需要处理一个边界问题：若 dominant_gen 或 outgoing_gen 中有任意一者是空生成器（当天一笔 TICK 都无）。
        dominant_tick = next(generators['dominant'], None)
        if not dominant_tick: #若 dominant_gen 是空生成器
            warnings.warn(f'主力合约 {contracts[-1]} 的 TICK 流在 {trading_date} 缺失，跳过该交易日。')
            return 
        ts = dominant_tick.calendar_timestamp
        priority = 1
        label = 'dominant'
        heapq.heappush(heap, [ts, priority,label, dominant_tick])
        if outgoing_gen: #若换月期，outgoing_gen 不是 None
            outgoing_tick = next(generators['outgoing'], None)
            if not outgoing_tick: #若 outgoing_gen 是空生成器
                warnings.warn(f'（旧）主力合约 {contracts[0]} 的 TICK 流在 {trading_date} 缺失，市场快照流将仅包含（新主力）合约 {contracts[1]}')
                pass
            else:
                ts = outgoing_tick.calendar_timestamp
                priority = 0
                label = 'outgoing'
                heapq.heappush(heap, [ts, priority, label, outgoing_tick])
        
        while heap: #当 heap 中有元素时做
            # 取出 heap 中的最小元素（即第一个元素），也就是时间戳最早的一个
            ts, _, label, tick = heapq.heappop(heap)
            latest_tick.update({label: tick})
            # 推进相应的 TICK 生成器，并压入 heap
            try:
                new_tick = next(generators[label])
                new_ts = new_tick.calendar_timestamp
                priority = 0 if label == 'outgoing' else 1
                heapq.heappush(heap, [new_ts, priority, label, new_tick])
            except StopIteration:
                pass

            # 检查 heap 的最小元素，是否其时间戳和所取出的一样
            # Notes: 这里分为两种情况：同一时间戳，两个合约都有 TICK；同一时间戳，某合约有大于等于两个 TICK。
            # 对于后者情况，若一直是同一时间戳，则一直推进，直到耗尽该合约在同一微秒内的 TICK。此时吐出的 MarketSnapshot 中，该合约的 TICK 是该时间戳（同一微秒）下的最后一个。如此才能保证时间轴的单调递增，永远不会出现两个快照的时间戳完全一样的情况。
            while heap and heap[0][0] == ts:
                # 取出，并推进相应的 TICK 生成器，压入 heap
                _, _, label, tick = heapq.heappop(heap)
                latest_tick.update({label: tick})
                try:
                    new_tick = next(generators[label])
                    new_ts = new_tick.calendar_timestamp
                    priority = 0 if label == 'outgoing' else 1
                    heapq.heappush(heap, [new_ts, priority, label, new_tick])
                except StopIteration:
                    pass

            # 吐出市场快照（MarketSnapshot)
            yield MarketSnapshot(
                calendar_timestamp=ts,
                trading_date=trading_date,
                dominant=latest_tick.get('dominant'),
                outgoing=latest_tick.get('outgoing'),
                roll_state=roll_state
            )

