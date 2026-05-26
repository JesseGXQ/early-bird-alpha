"""
data/factory.py

工厂函数：负责构造和组装对象
"""
from data.stitching.stitching import  ContinuousContractManager
from data.metadata.meta_manager import InstrumentMetaManager
from data.calendar.calendar import TradingCalendar
from data.feed.aggregator import BarAggregator
from data.feed.research_feed import ResearchFeed
from data.storage.file_locator import FileLocator
from data.storage.bar_loader import BarLoader

from data.config import RolloverConfig, AdjustmentConfig, SamplingConfig, EnvironmentConfig 

def create_research_feed(
        symbol_root: str,
        rollover_config: RolloverConfig = RolloverConfig(),
        adj_config: AdjustmentConfig = AdjustmentConfig(),
        sample_config: SamplingConfig = SamplingConfig(),
        env_config: EnvironmentConfig = EnvironmentConfig()
        ) -> ResearchFeed:
    """
    根据四组参数（配置），组装并返回一个 ResearchFeed 实例。
    
    配置类分别为：换月配置类、复权配置类、抽样配置类和环境配置类。

    """
    #1. 解析环境
    meta_manager = InstrumentMetaManager(
        meta_dir=env_config.meta_dir
        )
    calendar = TradingCalendar(
        symbol_root=symbol_root, 
        meta_manager=meta_manager, 
        calendar_dir=env_config.calendar_dir
        )
    file_locator = FileLocator(
        data_dir=env_config.data_dir_1min, 
        calendar=calendar
        )
    bar_loader = BarLoader(
        max_cache_size=env_config.bar_loader_max_cache_size
        )

    # 2. 注入换月规则
    contract_manager = ContinuousContractManager(
        symbol_root=symbol_root,
        rollover_config=rollover_config,
        master_table_dir=env_config.master_table_dir
        )

    # 3. 注入 K 线聚合规则
    bar_aggregator = BarAggregator(calendar=calendar,
                                   sample_config=sample_config)
    # 4. 组装 ResearchFeed
    return ResearchFeed(
        symbol_root=symbol_root,
        contract_manager=contract_manager,
        meta_manager=meta_manager,
        calendar=calendar,
        file_locator=file_locator,
        bar_loader=bar_loader,
        bar_aggregator=bar_aggregator,
        adj_config=adj_config #复权计算在 ResearchFeed 的内部
    )



