"""
读取日频快照数据

数据格式提示

日频快照数据，按照每个品种一个 parquet 文件存储：
    data/
    └── storage/
        └── daily_snapshots/
            ├── RB_daily.parquet
            ├── CU_daily.parquet
            ├── IF_daily.parquet
            └── ...
"""
from __future__ import annotations
import pandas as pd
from pathlib import Path

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.rules.product_meta_manager import ProductMetaManager

class DailySnapshotLoader:
    """
    日频快照数据加载器。

    Parameters
    ------
    daily_snapshot_dir: str
        存放日频快照数据的目录路径。
    meta_manager: ProductMetaManager
        品种元数据管家。

    Methods
    ------
    load(product, columns=None)
        加载特定品种的日频快照数据，返回 DataFrame。
    """

    def __init__(self, daily_snapshot_dir: str, meta_manager: ProductMetaManager):
        self.daily_snapshot_dir: Path = Path(daily_snapshot_dir)
        self.meta_manager: ProductMetaManager = meta_manager

    def load(self, product: str, columns: list[str] | None = None) -> pd.DataFrame:
        """
        加载日频快照数据。

        Parameters
        ------
        product: str
            品种代码，例如'RB'。
        
        columns: list[str] | None, optional
            要加载的列名列表，如果为 None ，加载所有列。

        Returns
        ------
        pd.DataFrame
            日频快照数据 DataFrame。
        """
        product = product.upper()
        exchange = self.meta_manager.get_static_meta(product).exchange
        file_path = self.daily_snapshot_dir / f'{exchange}_{product}.parquet'
        try:
            df = pd.read_parquet(file_path, columns=columns)
            return df
        except FileNotFoundError:
            raise FileNotFoundError(f'未找到 {product} 的日频快照数据文件，请检查路径：{file_path}')