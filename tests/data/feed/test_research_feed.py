from datetime import date

import pytest

from data.calendar.product_calendar import ProductCalendar
from data.feed.aggregator import BarAggregator
from data.feed.research_feed import ResearchFeed
from data.stitching.stitching import ContinuousContractManager
from data.config import (
    AdjustmentConfig,
    EnvironmentConfig,
    RolloverConfig,
    SamplingConfig,
)
from data.storage.bar_loader import BarLoader
from data.storage.file_locator import FileLocator
from data.cleaning.cleaning_bar_1min import DelayedNightOpenFixer
from data.cleaning.cleaning_tick import CleaningPipeline

environment_config = EnvironmentConfig()
master_table_dir = environment_config.master_table_dir
data_dir = environment_config.data_dir_1min

product = 'FU'

rollover_config = RolloverConfig()
sample_config = SamplingConfig()
adj_config = AdjustmentConfig()

def test_research_feed(meta_manager, exchange_calendar_manager, 
                       registry_manager, daily_status_manager, rule_service,
                       daily_snapshot_manager):
    # 1. 构造依赖
    # 连续合约管家
    contract_manager = ContinuousContractManager(product, master_table_dir, rollover_config)
    # 交易日历
    calendar = ProductCalendar(product, meta_manager, exchange_calendar_manager, rule_service)
    # 文件寻址器
    file_locator = FileLocator(data_dir, meta_manager, exchange_calendar_manager, registry_manager)
    # K线数据加载器
    bar_loader = BarLoader()
    # K线聚合器
    bar_aggregator = BarAggregator(calendar, sample_config)
    
    # 数据清洗管道
    clean_pipeline = CleaningPipeline()
    exchange = meta_manager.get_static_meta(product).exchange
    if exchange not in ['GFEX', 'CFFEX']:
        open_fixer = DelayedNightOpenFixer()
        clean_pipeline.add_filter(open_fixer)

    # 2. 构造 ResearchFeed 实例
    feed = ResearchFeed(
        product=product, 
        contract_manager=contract_manager, 
        meta_manager=meta_manager, 
        calendar=calendar, 
        file_locator=file_locator, 
        bar_loader=bar_loader, 
        bar_aggregator=bar_aggregator, 
        adj_config=adj_config,
        cleaning_pipeline=clean_pipeline,
        daily_snapshot_manager=daily_snapshot_manager)

    # 3. 测试 `get_historical_vector` 方法
    
    # ----------------------- 1d --------------------------
    freq = '1d'
    start_date = date(2015, 5, 13)
    end_date = date(2025, 5, 16)
    df = feed.get_historical_vector(start_date=start_date, end_date=end_date, freq=freq)
    print(f'\n 从 {start_date} 至 {end_date}, freq={freq}，复权主力合约连续价格dataframe为：\n', df)
    print(f'Columns are {df.columns}')
    print(f'Times of rollover is: {df['is_roll'].sum()}')

    # ----------------------- 5min --------------------------
    freq = '1h'
    start_date = date(2015, 5, 10)
    end_date = date(2019, 1, 1)
    df = feed.get_historical_vector(start_date=start_date, end_date=end_date, freq=freq, view='realized')
    print(f'\n 从 {start_date} 至 {end_date}, freq={freq}，复权主力合约连续价格dataframe为：\n', df)
    # print(f'\n === LRU Cache Info ===')
    print(f'ProductCalendar._get_trading_sessions_cached.cache_info(): {ProductCalendar._get_trading_sessions_cached.cache_info()}')
    # ----------------------- 30min --------------------------
    # freq = '30min'
    # df = feed.get_historical_vector(start_date=start_date, end_date=end_date, freq=freq)
    # print(f'\n 从 {start_date} 至 {end_date}, freq={freq}，复权主力合约连续价格dataframe为：\n', df)
