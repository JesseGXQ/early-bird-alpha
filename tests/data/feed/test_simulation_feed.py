from datetime import date

import pytest

from data.calendar.product_calendar import ProductCalendar
from data.cleaning.cleaning_tick import (
    CleaningPipeline,
    InstrumentCodeTranslator,
    DataEnricherPriceLimit,
    DataEnricherGenerateCalendarTimestamp,
    OutlierFilter,
    PriceLimitFilter,
    OutputSchemaEnforcer,
)
from data.config import RolloverConfig
from data.feed.models import MarketSnapshot
from data.feed.simulation_feed import SimulationFeed
from data.stitching.stitching import ContinuousContractManager
from data.storage.file_locator import FileLocator


master_table_dir = r'db\derived\master_tables'

product = 'rb'

rollover_config = RolloverConfig()


def test_simulation_feed(
    meta_manager,
    exchange_calendar_manager,
    registry_manager,
    rule_service,
    daily_snapshot_manager,
    tick_data_dir,
):
    # 1. 构造依赖
    # 连续合约管家
    contract_manager = ContinuousContractManager(product, master_table_dir, rollover_config)
    # 交易日历
    calendar = ProductCalendar(product, meta_manager, exchange_calendar_manager, rule_service)
    # 文件寻址器（TICK 数据）
    file_locator = FileLocator(tick_data_dir, meta_manager, exchange_calendar_manager, registry_manager)

    # 2. 构造数据清洗管道（与 factory.create_simulation_feed 一致）
    clean_pipeline = CleaningPipeline()

    # 2.1 合约翻译器
    code_translator = InstrumentCodeTranslator(meta_manager=meta_manager, 
                                               registry_manager=registry_manager,
                                               product=product)
    clean_pipeline.add_filter(code_translator)
    # 2.2 涨跌停价注入器
    enricher_price_limit = DataEnricherPriceLimit(
        product=product, daily_snapshot_manager=daily_snapshot_manager
    )
    clean_pipeline.add_filter(enricher_price_limit)
    # 2.3 日历交易时间戳生成器
    enricher_cal_ts = DataEnricherGenerateCalendarTimestamp(calendar=calendar)
    clean_pipeline.add_filter(enricher_cal_ts)
    # 2.4 异常值过滤器
    outlier_filter = OutlierFilter()
    clean_pipeline.add_filter(outlier_filter)
    # 2.5 涨跌停状态打标器
    price_limit_filter = PriceLimitFilter()
    clean_pipeline.add_filter(price_limit_filter)
    # 2.6 数据格式规范器
    output_enforcer = OutputSchemaEnforcer()
    clean_pipeline.add_filter(output_enforcer)

    # 3. 构造 SimulationFeed 实例
    feed = SimulationFeed(
        product=product,
        calendar=calendar,
        file_locator=file_locator,
        clean_pipeline=clean_pipeline,
        contract_manager=contract_manager,
    )

    # 4. 测试 get_snapshot_stream 方法
    start_date = date(2026, 1, 5)
    end_date = date(2026, 1, 9)

    snapshot_stream = feed.get_snapshot_stream(start_date=start_date, end_date=end_date)

    print(f'\n=== SimulationFeed 快照流 ({start_date} ~ {end_date}) ===')
    print(f'品种: {product.upper()}\n')

    count = 0
    for snapshot in snapshot_stream:
        assert isinstance(snapshot, MarketSnapshot), (
            f'第 {count} 个元素不是 MarketSnapshot，实际类型: {type(snapshot)}'
        )

        # 打印前 10 个快照供人工查阅
        if count < 10:
            dom = snapshot.dominant
            out = snapshot.outgoing
            print(
                f'[{count:>4}] ts={snapshot.calendar_timestamp}  '
                f'trading_date={snapshot.trading_date}  '
                f'dominant={dom.contract if dom else "None"}  '
                f'last={dom.last if dom else "N/A":>8}  '
                f'bid={dom.bid if dom else "N/A":>8}  '
                f'ask={dom.ask if dom else "N/A":>8}  '
                f'volume={dom.volume if dom else "N/A":>8}  '
                f'is_roll={snapshot.is_roll}  '
                f'outgoing={out.contract if out else "None"}'
            )

        count += 1
        if count >= 1000:
            print(f'\n... 已达到 {count} 个快照的上限，提前中断。')
            break

    print(f'\n=== 完成：共消费 {count} 个 MarketSnapshot ===')
    assert count > 0, '未产生任何 MarketSnapshot，快照流为空！'
