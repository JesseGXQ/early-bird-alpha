"""
离线任务：计算连续合约的主表（master_table），并将结果持久化到本地磁盘，以便后续分析和使用。


"""
from __future__ import annotations
from doctest import master
from typing import TYPE_CHECKING, Literal
from pathlib import Path

from numpy import roll


from data.stitching.stitching import RolloverDetector, RollGapCalculator

if TYPE_CHECKING:
    from data.config import RolloverConfig
    from data.storage.daily_snapshot_loader import DailySnapshotLoader

from data.storage import daily_snapshot_loader
from utils.validation_utils import check_df_columns_exist

def build_product_master_table(
        product: str, rollover_config: RolloverConfig, 
        master_table_dir: str, daily_snapshot_loader: DailySnapshotLoader, 
        overwrite: bool = False,
        save_rollover_calendar: bool = False, rollover_calendar_dir: str | None = None):
    """
    构建期货品种换月总表（master_table）的 ETL 编排器，负责数据加载、换月检测、换月价差计算和结果持久化等步骤。   
    """
    product = product.upper()
    # 1. 读取日频快照数据
    loader = daily_snapshot_loader
    daily_raw_df = loader.load(product, columns=['date', 'instrument', 'oi', 'settle'])
    daily_raw_df.rename(columns={'date': 'trading_date', 'instrument': 'contract', 'oi': 'open_interest'}, inplace=True)

    oi_matrix = daily_raw_df.pivot(index='trading_date', columns='contract', values='open_interest')
    daily_matrix = daily_raw_df.pivot(index='trading_date', columns='contract', values='settle')
    
    # 2. 生成换月日历(rollover_calendar)
    detector = RolloverDetector(buffer_days=rollover_config.buffer_days,
                              ratio_threshold=rollover_config.ratio_threshold)
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
                                                 daily_matrix=daily_matrix)
    
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
    
    

# =====================================================
# inline testing
# =====================================================
# if __name__ == '__main__':
#     product = 'rb'

#     from data.config import RolloverConfig
#     rollover_config = RolloverConfig() # 使用默认参数配置
    
#     master_table_dir = r'db\derived\master_tables\test'

#     # 构建 daily_snapshot_loader
#     from data.rules.product_meta_manager import ProductMetaManager
#     from data.storage.daily_snapshot_loader import DailySnapshotLoader

#     product_meta_dir = r'db\rules\product_meta'
#     meta_manager = ProductMetaManager(product_meta_dir)
#     daily_snapshot_dir = r'db\market\daily_snapshots\bar_1d\bar_1d_tushare\cleaned'
#     daily_snapshot_loader = DailySnapshotLoader(daily_snapshot_dir=daily_snapshot_dir, meta_manager=meta_manager)

#     # 调用build_product_master_table
#     build_product_master_table(product=product, rollover_config=rollover_config,
#                                master_table_dir=master_table_dir, daily_snapshot_loader=daily_snapshot_loader)
    

# =======================================================
# build master tables for all products
# =======================================================
if __name__ == '__main__':
    # 1. obtain product list
    from data.rules.product_meta_manager import ProductMetaManager
    product_meta_dir = r'db\rules\product_meta'
    meta_manager = ProductMetaManager(product_meta_dir)
    products = meta_manager.get_product_universe(return_type='list')

    # 2. 设定换月参数配置
    from data.config import RolloverConfig
    rollover_config = RolloverConfig() # 使用默认参数配置

    # 3. 构建 daily_snapshot_loader
    from data.storage.daily_snapshot_loader import DailySnapshotLoader
    daily_snapshot_dir = r'db\market\daily_snapshots\bar_1d\bar_1d_tushare\cleaned'
    daily_snapshot_loader = DailySnapshotLoader(daily_snapshot_dir=daily_snapshot_dir, meta_manager=meta_manager)

    # 4. 设定换月总表路径，对于每个品种逐一调用 build_product_master_table，生成换月总表（目前使用默认配置）
    master_table_dir = r'db\derived\master_tables'
    rollover_calendar_dir=r'db\derived\master_tables\rollover_calendar' # 也保存一下换月日历
    file_not_found_products: list[str] = []
    for product in products:
        print(f'\n ------------------对于品种 {product} 生成换月总表 -----------------------')
        try:
            build_product_master_table(
                product=product, rollover_config=rollover_config,
                master_table_dir=master_table_dir, daily_snapshot_loader=daily_snapshot_loader,
                overwrite=True, save_rollover_calendar=True, rollover_calendar_dir=rollover_calendar_dir)
        except FileNotFoundError as e: 
            print('对该品种无法找到相关文件：', e)
            file_not_found_products.append(product)
    
    print(f'未找到文件的期货品种代码为 {file_not_found_products}')
    print('ALL DONE.')