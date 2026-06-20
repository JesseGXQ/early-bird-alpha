"""
离线任务：计算连续合约的主表（master_table），并将结果持久化到本地磁盘，以便后续分析和使用。


"""
from __future__ import annotations
from typing import TYPE_CHECKING
from pathlib import Path

from data.storage.daily_snapshot_loader import DailySnapshotLoader
from data.stitching.stitching import RolloverDetector, RollGapCalculator

if TYPE_CHECKING:
    from data.config import RolloverConfig
from utils.validation_utils import check_df_columns_exist

def build_product_master_table(product: str, 
                              rollover_config: RolloverConfig, 
                              master_table_dir: str, 
                              daily_snapshot_dir: str):
    """
    构建期货品种换月总表（master_table）的 ETL 编排器，负责数据加载、换月检测、换月价差计算和结果持久化等步骤。

    Parameters
    ------
    product: str
        品种代码，例如'RB'。
    rollover_config: RolloverConfig
        换月规则配置类实例，包含 buffer_days, ratio_threshold, transition_days。用于指导换月检测和换月价差计算的逻辑。
    master_table_dir: str
        本地持久化的 master_table 文件目录路径。
    daily_snapshot_dir: str
        存放期货日频快照数据的目录路径。
    """
    
    # 1. 读取日频快照数据
    loader = DailySnapshotLoader(daily_snapshot_dir=daily_snapshot_dir)
    daily_raw_df = loader.load(product, columns=['TradingDate', 'Contract', 'OpenInterest','Close'])
    
    
    # 2. 构建 OI 矩阵和收盘价矩阵
    check_df_columns_exist(daily_raw_df, ['TradingDate', 'Contract', 'OpenInterest','Close'])

    oi_matrix = daily_raw_df.pivot(index='TradingDate', columns='Contract', values='OpenInterest')
    close_matrix = daily_raw_df.pivot(index='TradingDate', columns='Contract', values='Close')
    
    # 3. 生成换月日历(rollover_calendar)
    detector = RolloverDetector(buffer_days=rollover_config.buffer_days,
                              ratio_threshold=rollover_config.ratio_threshold)
    rollover_calendar = detector.generate_rollover_calendar(oi_matrix=oi_matrix)
    
    # 4. 生成换月总表(master_table)
    calculator = RollGapCalculator()
    master_table = calculator.build_master_table(rollover_calendar=rollover_calendar, 
                                                 daily_matrix=close_matrix)
    
    # 5. 持久化落盘带指纹的 master_table 文件
    base_path = Path(master_table_dir)
    save_path =  base_path / f'{product}_{rollover_config.signature}.parquet'
    if save_path.exists():
        print(f"{product} 换月总表已存在，正在覆盖旧文件: {save_path}")
    else:
        print(f"{product} 换月总表不存在，正在保存到: {save_path}")
    master_table.to_parquet(save_path)
    print(f"{product} 换月总表已成功保存到: {save_path}")