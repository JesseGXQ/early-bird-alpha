"""
data/factory.py

工厂函数：负责构造和组装对象
"""
from data.stitching.stitching import RolloverDetector, AdjustmentCalculator, ContinuousContractManager
from data.metadata.meta_manager import InstrumentMetaManager
from data.calendar.calendar import TradingCalendar
from data.feed.aggregator import BarAggregator
from data.feed.feed import ResearchFeed

import config


def create_research_feed(symbol_root: str) -> ResearchFeed:
    """组装并返回一个 ResearchFeed 实例。"""
    #1. 构造连续合约大管家: contract_manager
    detector = RolloverDetector(
        data_dir=config.ONE_MINUTE_DATA_DIR,
        symbol_root=symbol_root,
        buffer_days=config.BUFFER_DAYS,
        ratio_threshold=config.RATIO_THRESHOLD,
    )
    rollover_calendar = detector.generate_rollover_calendar()
    calculator = AdjustmentCalculator(
        data_dir=config.ONE_MINUTE_DATA_DIR, 
        symbol_root=symbol_root)
    master_table = calculator.calculate_adjustment_factors(rollover_calendar=rollover_calendar)
    contract_manager = ContinuousContractManager(
        master_table, 
        transition_days=config.TRANSITION_DAYS)
    
    #2. 构造期货品种数据管家: meta_manager
    meta_manager = InstrumentMetaManager(meta_dir=config.META_DIR)

    #3. 构造交易日历: calendar
    calendar = TradingCalendar(
        symbol_root=symbol_root,
        meta_manager=meta_manager,
        calendar_dir=config.CALENDAR_DIR
    )

    #4. 构造 file_locator 和 bar_loader
    file_locator = FileLocator()
    bar_loader = BarLoader()

    #5. 构造K线聚合器: bar_aggregator
    bar_aggregator = BarAggregator(calendar=calendar)

    #最后一步，组装
    return ResearchFeed(
        symbol_root=symbol_root,
        contract_manager=contract_manager,
        meta_manager=meta_manager,
        calendar=calendar,
        file_locator=file_locator,
        bar_loader=bar_loader,
        bar_aggregator=bar_aggregator,
    )


