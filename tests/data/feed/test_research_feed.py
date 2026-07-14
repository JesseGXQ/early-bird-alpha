from datetime import date

import pytest

from data.calendar.product_calendar import ProductCalendar
from data.feed.aggregator import BarAggregator
from data.feed.research_feed import ResearchFeed
from data.stitching.stitching import ContinuousContractManager
from data.config import RolloverConfig, SamplingConfig, AdjustmentConfig
from data.storage import daily_snapshot_manager
from data.storage.bar_loader import BarLoader
from data.storage.file_locator import FileLocator

master_table_dir = r'db\derived\master_tables'
data_dir = r'D:\量化策略\CTA策略\RAW_DATA\1min'

product = 'rb'

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
    freq = '5min'
    start_date = date(2015, 5, 13)
    end_date = date(2015, 7, 16)
    df = feed.get_historical_vector(start_date=start_date, end_date=end_date, freq=freq)
    print(f'\n 从 {start_date} 至 {end_date}, freq={freq}，复权主力合约连续价格dataframe为：\n', df)
    # # ----------------------- 30min --------------------------
    # freq = '30min'
    # df = feed.get_historical_vector(start_date=start_date, end_date=end_date, freq=freq)
    # print(f'\n 从 {start_date} 至 {end_date}, freq={freq}，复权主力合约连续价格dataframe为：\n', df)