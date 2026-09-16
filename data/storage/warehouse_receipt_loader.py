"""
仓单日报数据加载器。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from data.rules.product_meta_manager import ProductMetaManager


WAREHOUSE_RECEIPT_COLUMNS = [
    "trading_date",
    "product",
    "product_name",
    "exchange",
    "source_symbol",
    "source_product_name",
    "source_exchange",
    "warehouse",
    "warehouse_id",
    "previous_receipt_quantity",
    "receipt_quantity",
    "receipt_change_quantity",
    "unit",
    "area",
    "year",
    "grade",
    "brand",
    "place",
    "premium_discount",
    "is_converted_receipt",
]


class WarehouseReceiptLoader:
    """按品种加载标准化仓单日报 Parquet 文件。"""

    def __init__(
        self,
        warehouse_receipt_dir: str,
        meta_manager: ProductMetaManager,
    ) -> None:
        self.warehouse_receipt_dir = Path(warehouse_receipt_dir)
        self.meta_manager = meta_manager

    def get_file_path(self, product: str) -> Path:
        """返回品种对应的仓单数据文件路径。"""
        product = product.upper()
        exchange = self.meta_manager.get_static_meta(product).exchange
        return self.warehouse_receipt_dir / f"{exchange}_{product}.parquet"

    def load(
        self,
        product: str,
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """加载指定品种的仓单日报数据。"""
        product = product.upper()
        file_path = self.get_file_path(product)
        try:
            df = pd.read_parquet(file_path, columns=columns)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"【WarehouseReceiptLoader】未找到 {product} 的仓单数据文件，"
                f"请先执行仓单入库：{file_path}"
            ) from exc

        expected = set(columns or WAREHOUSE_RECEIPT_COLUMNS)
        missing = expected.difference(df.columns)
        if missing:
            raise ValueError(
                f"【WarehouseReceiptLoader】{file_path} 缺少字段：{sorted(missing)}"
            )

        if "trading_date" in df.columns:
            df["trading_date"] = pd.to_datetime(df["trading_date"])
        return df
