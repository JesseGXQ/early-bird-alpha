from __future__ import annotations
import heapq

import pandas as pd
from typing import TYPE_CHECKING, Generator
from datetime import date
import warnings

from data.storage.tick_loader import TickLoaderFactory
from data.feed.models import ContractTick, MarketSnapshot

if TYPE_CHECKING:
    from data.stitching.stitching import ContinuousContractManager
    from data.cleaning.cleaning import CleaningPipeline
    from data.calendar.calendar import TradingCalendar
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
    严格按时间顺序下发市场快照(MarketSnapshot)，非换月日分发单合约数据，换月时期分发双合约。

    Parameters
    ------
    product: str
        品种代码，例如'RB'。
    calendar: TradingCalendar
        交易日历实例，提供交易日、交易小节、分钟偏移等查询功能。
    contract_manager: ContinuousContractManager
        连续合约大管家，包含 master_table 属性(换月总表)，提供 `get_active_physical_contracts` 方法，根据交易日期返回主力合约列表。
    file_locator: FileLocator
        文件寻址器，提供根据合约名称和日期查询 1min 数据文件路径的功能。
    clean_pipeline: CleaningPipeline
        含滤芯的清洗管道，提供 process 方法，输入原始 TICK 数据块，输出干净的 TICK 数据块。

    Methods
    ------
    get_snapshot_stream(start_date, end_date)
        返回一个生成器，按时间顺序下发指定日期范围内的市场快照(MarketSnapshot)。

    """
    def __init__(self, 
            product: str,
            calendar: TradingCalendar, 
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
        返回一个生成器，按时间顺序下发指定日期范围内的市场快照(MarketSnapshot)。

        Parameters
        ------
        start_date: datetime.date
            起始交易日
        end_date: datetime.date
            结束交易日
        
        Yields
        ------
        MarketSnapshot
            包含主力合约的市场快照对象（若换月期，为新旧主力合约），严格遵循时间顺序下发。
        """
        
        # 根据 start_date 和 end_date， 获取所有的交易日期
        mask = (
            (self.calendar.exchange_trading_dates >= pd.Timestamp(start_date) ) & 
            (self.calendar.exchange_trading_dates <= pd.Timestamp(end_date) )
        )
        trading_dates = self.calendar.exchange_trading_dates[mask].date

        for trading_date in trading_dates:
            contracts = self.contract_manager.get_active_physical_contracts(trading_date)

            # 依据 contracts 中的元素数量判断是否为换月期
            is_roll = len(contracts) == 2

            if is_roll:
                dominant_gen = self._make_contract_tick_generator(
                    trading_date=trading_date,
                    contract=contracts[1]
                )
                outgoing_gen = self._make_contract_tick_generator(
                    trading_date=trading_date,
                    contract=contracts[0]
                )
            else:
                dominant_gen=self._make_contract_tick_generator(
                    trading_date=trading_date,
                    contract=contracts[0]
                )
                outgoing_gen = None

            yield from self._stream(
                contracts=contracts,
                trading_date=trading_date,
                dominant_gen=dominant_gen,
                outgoing_gen=outgoing_gen,
                is_roll=is_roll
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
                contracts: list[str],
                trading_date: date,
                dominant_gen: Generator[ContractTick, None, None],
                outgoing_gen: Generator[ContractTick, None, None] | None,
                is_roll: bool,
                ) -> Generator[MarketSnapshot, None, None]:
        """
        根据输入的主力合约 TICK 生成器，利用 Min-heap 算法 + ZOH, 返回包含市场快照的生成器。
        
        """
        generators = {'dominant': dominant_gen, 'outgoing': outgoing_gen}

        heap = [] # contains: [ts, priority, label('domminant'/'outgoing'), tick(ContractTick的实例)]
        latest_tick: dict[str, ContractTick] = {} # 零阶保持字典(str is either 'dominant' or 'outgoing')


        # 初始化 heap
        # 这里需要处理一个边界问题：若 dominant_gen 或 outgoing_gen 中有任意一者是空生成器（当天一笔 TICK 都无）。
        dominant_tick = next(generators['dominant'], None)
        if not dominant_tick: #若 dominant_gen 是空生成器
            warnings.warn(f'（新）主力合约 {contracts[1]} 的 TICK 流在 {trading_date} 缺失，跳过该交易日。')
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
                is_roll=is_roll
            )

