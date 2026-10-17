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
        """
        加载指定品种的仓单日报数据。

        Parameters
        ----------
        product: str
            品种代码（大小写不敏感），读取 ``{exchange}_{product}.parquet``。
        columns: list[str] | None, optional
            要读取的列子集，为 None 时读取全部 ``WAREHOUSE_RECEIPT_COLUMNS``。
            只裁剪列，不改变行顺序。

        Returns
        -------
        pd.DataFrame
            该品种的仓单明细，未做任何聚合，每行是“交易日 × 仓库（部分品种再细
            分到年份/等级/品牌/产地）”的一条记录。索引为 ``RangeIndex``
            （0..n-1 行号，**不是日期索引**，按日期切片请用 ``trading_date`` 列），
            行按 ``trading_date`` 升序。

            列固定为 ``WAREHOUSE_RECEIPT_COLUMNS``，顺序即文件内顺序：

            ==========================  ================  ==============================
            列                           dtype             含义
            ==========================  ================  ==============================
            trading_date                datetime64[ns]    交易日
            product                     string            归一化品种代码，如 ``ZN``
            product_name                string            归一化品种中文名
            exchange                    string            归一化交易所代码
            source_symbol               string            数据源原始品种代码
            source_product_name         string            数据源原始品种名称
            source_exchange             string            数据源原始交易所
            warehouse                   string            仓库/交割库名称
            warehouse_id                string            仓库编号，上期所/中金所多缺失
            previous_receipt_quantity   Int64             上一交易日仓单量
            receipt_quantity            Int64             当日仓单量
            receipt_change_quantity     Int64             当日仓单增减量
            unit                        string            计量单位，如 吨/千克/桶
            area                        string            地区
            year                        string            年份/年度标识，仅部分品种有值
            grade                       string            等级
            brand                       string            品牌
            place                       string            产地
            premium_discount            Int64             升贴水
            is_converted_receipt        string            是否转抛仓单，源端未提供，恒缺失
            ==========================  ================  ==============================

            注意：

            * ``source_*`` 是来源证据，可能与 ``product`` 不同（如 ``INE_BC`` 中
              来自 ``CU`` 响应的行），统计时以 ``product / exchange`` 为准。
            * ``warehouse`` 为 ``合计`` / ``总计`` 的行是源端夹带的分地区小计，
              不是独立仓库记录，按日加总前需剔除。
            * 除 ``trading_date``、``product``、``receipt_quantity`` 等少数列外，
              多数字段按品种稀疏（全缺失或仅个别交易所有值），使用前需处理缺失。

        Raises
        ------
        FileNotFoundError
            品种文件不存在，需先执行仓单入库。
        ValueError
            文件缺少 ``WAREHOUSE_RECEIPT_COLUMNS`` 中的字段（旧版 schema）。
        """
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
