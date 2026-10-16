"""
离线任务：计算连续合约的主表（master_table），并将结果持久化到本地磁盘，以便后续分析和使用。
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from pathlib import Path
import pandas as pd

from data.stitching.stitching import RolloverDetector, RollGapCalculator

if TYPE_CHECKING:
    from data.calendar.product_calendar import ProductCalendar
    from data.config import RolloverConfig
    from data.rules.instrument_registry_manager import InstrumentRegistryManager
    from data.storage.daily_snapshot_loader import DailySnapshotLoader


def build_product_master_table(
        product: str, rollover_config: RolloverConfig, 
        master_table_dir: str,
        daily_snapshot_loader: DailySnapshotLoader,
        registry_manager: InstrumentRegistryManager,
        calendar: ProductCalendar,
        overwrite: bool = False,
        save_rollover_calendar: bool = False, rollover_calendar_dir: str | None = None):
    """
    构建期货品种换月总表（master_table）的 ETL 编排器，负责数据加载、换月检测、换月价差计算和结果持久化等步骤。

    Notes
    -----
    换月价差采用成对价格口径：优先使用旧主力最后一个交易日的新、旧主力
    收盘价；任意一方收盘价缺失时，双方统一回退为结算价。结算价对仍不完整
    时严格报错，不把未知价差当作 0。该价差供 ResearchFeed 构造
    close-preferred, settlement-fallback backward additive adjustment 使用。
    """
    product = product.upper()
    # 1. 读取日频快照数据
    loader = daily_snapshot_loader
    daily_raw_df = loader.load(
        product, columns=['date', 'instrument', 'oi', 'close', 'settle']
    )
    if daily_raw_df.empty:
        raise ValueError(f'{product} 日频快照为空，无法生成换月表。')
    daily_raw_df.rename(columns={'date': 'trading_date', 'instrument': 'contract', 'oi': 'open_interest'}, inplace=True)
    daily_matrix = daily_raw_df.pivot(index='trading_date', columns='contract', values='close')
    daily_matrix.index = pd.to_datetime(daily_matrix.index)
    settle_matrix = daily_raw_df.pivot(
        index='trading_date', columns='contract', values='settle'
    )
    settle_matrix.index = pd.to_datetime(settle_matrix.index)

    # 制作索引和交易日历对齐的 oi_matrix
    oi_matrix = daily_raw_df.pivot(index='trading_date', columns='contract', values='open_interest')
    oi_matrix.index = pd.to_datetime(oi_matrix.index)
    all_trading_dates = calendar.exchange_trading_dates
    start_dt = oi_matrix.index[0]
    end_dt = oi_matrix.index[-1]
    mask = (all_trading_dates >= start_dt) & (all_trading_dates <= end_dt)
    trading_dates = all_trading_dates[mask]
    if len(trading_dates) == 0:
        raise ValueError(f'{product} 日频快照与交易日历没有重叠的日期。')
    oi_matrix = oi_matrix.reindex(trading_dates)

    # 2. 生成换月日历(rollover_calendar)
    detector = RolloverDetector(
        registry_manager=registry_manager,
        calendar=calendar,
        buffer_days=rollover_config.buffer_days,
        ratio_threshold=rollover_config.ratio_threshold,
        transition_days=rollover_config.transition_days,
    )
    rollover_calendar = detector.generate_rollover_calendar(oi_matrix=oi_matrix)

    if save_rollover_calendar:
        assert rollover_calendar_dir is not None
        rollover_calendar_path = Path(rollover_calendar_dir)
        if not rollover_calendar_path.exists():
            rollover_calendar_path.mkdir(parents=True, exist_ok=True)
        
        path = Path(rollover_calendar_dir) / f'{product}.csv'
        print(f'{product} 保存 rollover_calendar 至 {str(path)}')
        rollover_calendar.to_csv(path, encoding='utf-8-sig')
    
    # 3. 生成换月总表(master_table)
    calculator = RollGapCalculator()
    master_table = calculator.build_master_table(rollover_calendar=rollover_calendar, 
                                                 daily_matrix=daily_matrix,
                                                 settle_matrix=settle_matrix)
    
    # 4. 持久化落盘带指纹的 master_table 文件
    base_path = Path(master_table_dir)
    save_path =  base_path / f'{product}_{rollover_config.signature}.parquet'
    if save_path.exists():
        if overwrite:
            print(f"{product} 换月总表已存在，正在覆盖旧文件: {save_path}")
            master_table.to_parquet(save_path)
        else:
            print(f"{product} 换月总表已存在，跳过。")
    else:
        print(f"{product} 换月总表不存在，正在保存到: {save_path}")
        master_table.to_parquet(save_path)
    
# =======================================================
# build master tables for all products
# =======================================================
if __name__ == '__main__':
    import datetime
    from data.calendar.exchange_calendar_manager import ExchangeCalendarManager
    from data.calendar.product_calendar import ProductCalendar
    from data.rules.product_meta_manager import ProductMetaManager
    from data.rules.instrument_registry_manager import InstrumentRegistryManager
    from data.config import RolloverConfig
    from data.storage.daily_snapshot_loader import DailySnapshotLoader

    # 1. obtain product list
    product_meta_dir = r'db\rules\product_meta'
    meta_manager = ProductMetaManager(product_meta_dir)
    registry_manager = InstrumentRegistryManager(r'db\rules\instrument_registry')
    products = meta_manager.get_product_universe(return_type='list') 

    # 2. 设定换月参数配置
    rollover_config = RolloverConfig() # 使用默认参数配置

    # 3. 构建 daily_snapshot_loader
    daily_snapshot_dir = r'db\market\daily_snapshots\bar_1d\bar_1d_tushare\cleaned'
    daily_snapshot_loader = DailySnapshotLoader(daily_snapshot_dir=daily_snapshot_dir, meta_manager=meta_manager)

    # 4. 设定换月总表路径，对于每个品种逐一调用 build_product_master_table，生成换月总表（目前使用默认配置）
    master_table_dir = r'db\derived\master_tables'
    rollover_calendar_dir=r'db\derived\master_tables\rollover_calendar'

    # 5. 构造交易所交易日历（用于构建品种交易日历）
    calendar_dir = r'db\calendar\trading_calendars'
    exchange_calendar_manager = ExchangeCalendarManager(calendar_dir)
    failed_reason: dict[str, str] = {}
    for product in products:
        print(f'\n ------------------对于品种 {product} 生成换月总表 -----------------------')
        # 构造交易日历
        calendar = ProductCalendar(
            product=product, meta_manager=meta_manager,
            exchange_calendar_manager=exchange_calendar_manager,
        )
        try:
            build_product_master_table(
                product=product, rollover_config=rollover_config,
                master_table_dir=master_table_dir, daily_snapshot_loader=daily_snapshot_loader,
                registry_manager=registry_manager, calendar=calendar,
                overwrite=True, save_rollover_calendar=True, rollover_calendar_dir=rollover_calendar_dir)
        except FileNotFoundError as e:
            print('对该品种无法找到相关文件：', e)
            failed_reason[product] = e.__str__()
        except ValueError as e:
            print(f'对该品种无法生成换月表: {e}')
            failed_reason[product] = e.__str__()
    print(f'共成功{len(products) - len(failed_reason)} 个品种；',
          f'失败 {len(failed_reason)} 个品种，')

    # 5. 在 master_table_dir 目录下新建 README.MD 文档并写入内容
    readme_path = Path(master_table_dir) / 'README.MD'

    # 汇总成功/失败品种，并把失败原因整理成 markdown 可读格式
    succeeded = sorted(p for p in products if p not in failed_reason)
    success_section = '\n'.join(f'- `{p}`' for p in succeeded) if succeeded else '- 无'
    if failed_reason:
        failed_products_section = '\n'.join(f'- `{p}`' for p in sorted(failed_reason))
        # 每个品种一个三级标题 + 代码块，便于直接阅读报错原文
        failure_detail_section = '\n\n'.join(
            f'### {p}\n\n```text\n{reason.strip()}\n```'
            for p, reason in sorted(failed_reason.items())
        )
    else:
        failed_products_section = '- 无'
        failure_detail_section = '无。'

    readme_content = f"""# 换月总表 (Master Tables)

本目录用于存放各期货品种的换月总表（master_table）文件。
最新换月总表和换月日历生成时间为 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}。

## 换月价差与复权口径

`Price_Gap` 采用成对价格口径：优先使用旧主力最后一个交易日的新、旧主力
**收盘价**；任意一方收盘价缺失时，双方统一 fall back to 当日**结算价**。若结算价对
仍不完整，直接报错，不把未知价差当作 0。`Price_Gap_Basis` 在实际换月边界
记录 `close` 或 `settle_fallback`。

## 执行结果

成功生成 {len(succeeded)} 个品种：

{success_section}

失败 {len(failed_reason)} 个品种：

{failed_products_section}

## 失败品种报错信息

{failure_detail_section}
"""
    readme_path.write_text(readme_content, encoding='utf-8-sig')

    print(f'已写入 README 文档至: {readme_path}')
    print('ALL DONE.')

# ======================================
# for debug
# ======================================
# if __name__ == '__main__':

#     import datetime
#     from data.calendar.exchange_calendar_manager import ExchangeCalendarManager
#     from data.calendar.product_calendar import ProductCalendar
#     from data.rules.product_meta_manager import ProductMetaManager
#     from data.rules.instrument_registry_manager import InstrumentRegistryManager
#     from data.config import RolloverConfig
#     from data.storage.daily_snapshot_loader import DailySnapshotLoader

#     # 1. obtain product list
#     product_meta_dir = r'db\rules\product_meta'
#     meta_manager = ProductMetaManager(product_meta_dir)
#     registry_manager = InstrumentRegistryManager(r'db\rules\instrument_registry')

#     # 2. 设定换月参数配置
#     rollover_config = RolloverConfig() # 使用默认参数配置

#     # 3. 构建 daily_snapshot_loader
#     daily_snapshot_dir = r'db\market\daily_snapshots\bar_1d\bar_1d_tushare\cleaned'
#     daily_snapshot_loader = DailySnapshotLoader(daily_snapshot_dir=daily_snapshot_dir, meta_manager=meta_manager)

#     # 4. 设定换月总表路径，对于每个品种逐一调用 build_product_master_table，生成换月总表（目前使用默认配置）
#     master_table_dir = r'db\derived\master_tables'
#     rollover_calendar_dir=r'db\derived\master_tables\rollover_calendar'

#     # 5. 构造交易所交易日历（用于构建品种交易日历）
#     calendar_dir = r'db\calendar\trading_calendars'
#     exchange_calendar_manager = ExchangeCalendarManager(calendar_dir)
#     failed_reason: dict[str, str] = {}

#     product = 'CY'
#     print(f'\n ------------------对于品种 {product} 生成换月总表 -----------------------')
#     # 构造交易日历
#     calendar = ProductCalendar(
#         product=product, meta_manager=meta_manager,
#         exchange_calendar_manager=exchange_calendar_manager,
#     )
#     try:
#         build_product_master_table(
#             product=product, rollover_config=rollover_config,
#             master_table_dir=master_table_dir, daily_snapshot_loader=daily_snapshot_loader,
#             registry_manager=registry_manager, calendar=calendar,
#             overwrite=True, save_rollover_calendar=True, rollover_calendar_dir=rollover_calendar_dir)
#     except FileNotFoundError as e:
#         print('对该品种无法找到相关文件：', e)
#         failed_reason[product] = e.__str__()
#     except ValueError as e:
#         print(f'对该品种无法生成换月表: {e}')
#         failed_reason[product] = e.__str__()
