"""
工厂函数：负责构造和组装对象
"""
from data.cleaning.cleaning import *

from data.stitching.stitching import ContinuousContractManager

from data.rules.product_meta_manager import ProductMetaManager
from data.rules.instrument_registry_manager import InstrumentRegistryManager
from data.rules.exchange_holiday_manager import ExchangeHolidayManager
from data.rules.instrument_daily_status_manager import InstrumentDailyStatusManager
from data.rules.trading_rule_service import TradingRuleService
from data.storage.daily_snapshot_manager import DailySnapshotManager

from data.calendar.calendar import TradingCalendar

from data.feed.aggregator import BarAggregator
from data.feed.research_feed import ResearchFeed
from data.feed.simulation_feed import SimulationFeed

from data.storage.daily_snapshot_loader import DailySnapshotLoader
from data.storage.file_locator import FileLocator
from data.storage.bar_loader import BarLoader

from data.config import RolloverConfig, AdjustmentConfig, SamplingConfig, EnvironmentConfig 


def _create_trading_rule_components(env_config: EnvironmentConfig) -> tuple[
    ProductMetaManager,
    InstrumentRegistryManager,
    ExchangeHolidayManager,
    InstrumentDailyStatusManager,
    TradingRuleService,
]:
    """统一组装交易规则相关组件。"""
    meta_manager = ProductMetaManager(
        product_meta_dir=env_config.product_meta_dir
    )
    registry_manager = InstrumentRegistryManager(
        instrument_registry_dir=env_config.instrument_registry_dir
    )
    holiday_manager = ExchangeHolidayManager(
        exchange_holiday_dir=env_config.exchange_holiday_dir
    )
    daily_status_manager = InstrumentDailyStatusManager(
        instrument_daily_status_dir=env_config.daily_status_dir,
        meta_manager=meta_manager,
        registry_manager=registry_manager,
    )
    trading_rule_service = TradingRuleService(
        meta_manager=meta_manager,
        registry_manager=registry_manager,
        holiday_manager=holiday_manager,
        daily_status_manager=daily_status_manager,
    )
    return (
        meta_manager,
        registry_manager,
        holiday_manager,
        daily_status_manager,
        trading_rule_service,
    )


def create_research_feed(
        product: str,
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
    meta_manager, registry_manager, _, _, trading_rule_service = _create_trading_rule_components(env_config)
    calendar = TradingCalendar(
        product=product, 
        meta_manager=meta_manager, 
        calendar_dir=env_config.calendar_dir,
        trading_rule_service=trading_rule_service
        )
    file_locator = FileLocator(
        data_dir=env_config.data_dir_1min, 
        calendar=calendar,
        meta_manager=meta_manager,
        registry_manager=registry_manager
        )
    bar_loader = BarLoader(
        max_cache_size=env_config.bar_loader_max_cache_size
        )

    # 2. 注入换月规则
    contract_manager = ContinuousContractManager(
        product=product,
        rollover_config=rollover_config,
        master_table_dir=env_config.master_table_dir
        )

    # 3. 注入 K 线聚合规则
    bar_aggregator = BarAggregator(calendar=calendar,
                                   sample_config=sample_config)
    # 4. 组装 ResearchFeed
    return ResearchFeed(
        product=product,
        contract_manager=contract_manager,
        meta_manager=meta_manager,
        calendar=calendar,
        file_locator=file_locator,
        bar_loader=bar_loader,
        bar_aggregator=bar_aggregator,
        adj_config=adj_config #复权计算在 ResearchFeed 的内部
    )

def create_simulation_feed(
        product: str,
        rollover_config: RolloverConfig = RolloverConfig(),
        env_config: EnvironmentConfig = EnvironmentConfig()
        ) -> SimulationFeed:
    """
    根据换月配置和环境配置，组装并返回一个 SimulationFeed 实例。
    """
    meta_manager, registry_manager, _, _, trading_rule_service = _create_trading_rule_components(env_config)
    calendar = TradingCalendar(
        product=product,
        meta_manager=meta_manager,
        calendar_dir=env_config.calendar_dir,
        trading_rule_service=trading_rule_service
    )
    file_locator = FileLocator(
        data_dir=env_config.data_dir_tick,
        calendar=calendar,
        meta_manager=meta_manager,
        registry_manager=registry_manager)
    contract_manager = ContinuousContractManager(
        product=product,
        master_table_dir=env_config.master_table_dir,
        rollover_config=rollover_config
    )
    
    # 构造数据清洗管道(data cleaning pipeline)
    clean_pipeline = CleaningPipeline()
    # 1. 数据增广器，涨跌停价
    daily_snapshot_loader = DailySnapshotLoader(
        daily_snapshot_dir=env_config.daily_snapshot_dir
    )
    daily_snapshot_manager = DailySnapshotManager(
        daily_snapshot_loader=daily_snapshot_loader
    )
    enricher_price_limit = DataEnricherPriceLimit(
        product=product,
        daily_snapshot_manager=daily_snapshot_manager
    )
    clean_pipeline.add_filter(enricher_price_limit)
    # 2. 数据增广器，日历交易时间戳
    enricher_gen_cal_ts = DataEnricherGenerateCalendarTimestamp(
        calendar=calendar
    )
    clean_pipeline.add_filter(enricher_gen_cal_ts)
    # 3. 异常值过滤器
    outlier_filter = OutlierFilter()
    clean_pipeline.add_filter(outlier_filter)
    # 4. 涨跌停状态打标器
    price_limit_filter = PriceLimitFilter()
    clean_pipeline.add_filter(price_limit_filter)
    # 5. 数据格式规范器
    output_enforcer = OutputSchemaEnforcer()
    clean_pipeline.add_filter(output_enforcer)

    # 组装 SimulationFeed
    return SimulationFeed(
        product=product,
        calendar=calendar,
        file_locator=file_locator,
        clean_pipeline=clean_pipeline,
        contract_manager=contract_manager
    )


