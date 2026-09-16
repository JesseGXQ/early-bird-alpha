from types import SimpleNamespace

import pandas as pd
import pytest

from data.storage.warehouse_receipt_loader import (
    WAREHOUSE_RECEIPT_COLUMNS,
    WarehouseReceiptLoader,
)


class FakeMetaManager:
    def get_static_meta(self, product: str) -> SimpleNamespace:
        assert product == "ZN"
        return SimpleNamespace(exchange="SHFE")


def make_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trading_date": pd.to_datetime(["2026-01-05"]),
            "product": ["ZN"],
            "product_name": ["沪锌"],
            "exchange": ["SHFE"],
            "source_symbol": ["ZN"],
            "source_product_name": ["锌"],
            "source_exchange": ["SHFE"],
            "warehouse": ["测试仓库"],
            "warehouse_id": ["001"],
            "previous_receipt_quantity": pd.Series([10], dtype="Int64"),
            "receipt_quantity": pd.Series([12], dtype="Int64"),
            "receipt_change_quantity": pd.Series([2], dtype="Int64"),
            "unit": ["吨"],
            "area": [pd.NA],
            "year": [pd.NA],
            "grade": [pd.NA],
            "brand": [pd.NA],
            "place": [pd.NA],
            "premium_discount": pd.Series([pd.NA], dtype="Int64"),
            "is_converted_receipt": [pd.NA],
        }
    ).loc[:, WAREHOUSE_RECEIPT_COLUMNS]


def test_load_warehouse_receipt_data(tmp_path):
    make_frame().to_parquet(tmp_path / "SHFE_ZN.parquet", index=False)
    loader = WarehouseReceiptLoader(str(tmp_path), FakeMetaManager())

    result = loader.load("zn")

    assert result.columns.tolist() == WAREHOUSE_RECEIPT_COLUMNS
    assert result.loc[0, "product"] == "ZN"
    assert result.loc[0, "receipt_quantity"] == 12
    assert pd.api.types.is_datetime64_any_dtype(result["trading_date"])


def test_missing_file_has_actionable_error(tmp_path):
    loader = WarehouseReceiptLoader(str(tmp_path), FakeMetaManager())

    with pytest.raises(FileNotFoundError, match="请先执行仓单入库"):
        loader.load("ZN")
