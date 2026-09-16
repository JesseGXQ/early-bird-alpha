from datetime import date
from types import SimpleNamespace

import pandas as pd

from data.storage.warehouse_receipt_loader import WAREHOUSE_RECEIPT_COLUMNS
from db.fundamental_ingestion.script.warehouse_receipts.ingest_warehouse_receipts import (
    SOURCE_COLUMNS,
    WarehouseReceiptFetcher,
    deduplicate_canonical_records,
    merge_incremental_records,
    normalize_records,
    write_parquet_atomic,
)


def raw_row(trading_date: str, quantity: int, exchange: str = "SHF") -> dict:
    return {
        "trade_date": trading_date,
        "symbol": "ZN",
        "fut_name": "来源名称",
        "warehouse": "测试仓库",
        "wh_id": "001",
        "pre_vol": quantity - 1,
        "vol": quantity,
        "vol_chg": 1,
        "area": None,
        "year": None,
        "grade": "标准品",
        "brand": None,
        "place": None,
        "pd": None,
        "is_ct": "否",
        "unit": "吨",
        "exchange": exchange,
    }


class FakeClient:
    def __init__(self, rows: list[dict], limit: int) -> None:
        self.rows = rows
        self.limit = limit

    def query(
        self,
        product: str,
        start_date: date,
        end_date: date,
        offset: int = 0,
    ) -> pd.DataFrame:
        selected = [
            row
            for row in self.rows
            if start_date
            <= pd.to_datetime(row["trade_date"], format="%Y%m%d").date()
            <= end_date
        ]
        return pd.DataFrame(
            selected[offset : offset + self.limit], columns=SOURCE_COLUMNS
        )


class FakeMetaManager:
    def get_static_meta(self, product: str) -> SimpleNamespace:
        assert product == "ZN"
        return SimpleNamespace(product="ZN", exchange="SHFE", name="沪锌")


class CrossProductMetaManager:
    def get_product_universe(self, return_type: str) -> list[str]:
        assert return_type == "list"
        return ["CU", "BC"]

    def get_static_meta(self, product: str) -> SimpleNamespace:
        records = {
            "CU": SimpleNamespace(product="CU", exchange="SHFE", name="沪铜"),
            "BC": SimpleNamespace(product="BC", exchange="INE", name="国际铜"),
        }
        return records[product]


def test_fetcher_paginates_responses_at_api_limit():
    rows = [raw_row(f"2026010{day}", day) for day in range(1, 5)]
    fetcher = WarehouseReceiptFetcher(
        FakeClient(rows, limit=2),
        api_limit=2,
        request_interval=0,
        max_retries=0,
    )

    result, stats = fetcher.fetch("ZN", date(2026, 1, 1), date(2026, 1, 4))

    assert len(result) == 4
    assert stats.paginated_pages >= 1
    assert stats.api_calls > 1


def test_normalization_uses_product_meta_as_authority():
    raw = pd.DataFrame([raw_row("20260105", 12, exchange="错误交易所")])

    result, stats = normalize_records(raw, "zn", FakeMetaManager())

    assert result.columns.tolist() == WAREHOUSE_RECEIPT_COLUMNS
    assert result.loc[0, "product"] == "ZN"
    assert result.loc[0, "product_name"] == "沪锌"
    assert result.loc[0, "exchange"] == "SHFE"
    assert result.loc[0, "receipt_quantity"] == 12
    assert stats["exchange_conflicts"] == 1
    assert stats["quantity_inconsistencies"] == 0


def test_normalization_routes_embedded_product_code_and_preserves_source_fields():
    row = raw_row("20260105", 12)
    row["symbol"] = "CU"
    row["fut_name"] = "铜(BC)"

    result, stats = normalize_records(
        pd.DataFrame([row]), "CU", CrossProductMetaManager()
    )

    assert result.loc[0, "product"] == "BC"
    assert result.loc[0, "product_name"] == "国际铜"
    assert result.loc[0, "exchange"] == "INE"
    assert result.loc[0, "source_symbol"] == "CU"
    assert result.loc[0, "source_product_name"] == "铜(BC)"
    assert result.loc[0, "source_exchange"] == "SHFE"
    assert stats["rerouted_rows"] == 1


def test_direct_product_source_wins_over_linked_supplement():
    direct_row = raw_row("20260105", 12, exchange="INE")
    direct_row["symbol"] = "BC"
    direct_row["fut_name"] = "铜(BC)"
    direct, _ = normalize_records(
        pd.DataFrame([direct_row]), "BC", CrossProductMetaManager()
    )
    supplement_row = direct_row.copy()
    supplement_row["symbol"] = "CU"
    supplement_row["exchange"] = "SHFE"
    supplement_row["pre_vol"] = 11
    supplement, _ = normalize_records(
        pd.DataFrame([supplement_row]), "CU", CrossProductMetaManager()
    )

    result = deduplicate_canonical_records(
        pd.concat([direct, supplement], ignore_index=True)
    )

    assert len(result) == 1
    assert result.loc[result.index[0], "source_symbol"] == "BC"


def test_incremental_refresh_replaces_complete_date_window():
    existing_raw = pd.DataFrame(
        [raw_row("20260104", 4), raw_row("20260105", 5), raw_row("20260105", 99)]
    )
    existing, _ = normalize_records(existing_raw, "ZN", FakeMetaManager())
    incoming_raw = pd.DataFrame([raw_row("20260105", 50), raw_row("20260106", 60)])
    incoming, _ = normalize_records(incoming_raw, "ZN", FakeMetaManager())

    result = merge_incremental_records(
        existing, incoming, date(2026, 1, 5), date(2026, 1, 6)
    )

    assert result["trading_date"].dt.strftime("%Y%m%d").tolist() == [
        "20260104",
        "20260105",
        "20260106",
    ]
    assert result["receipt_quantity"].tolist() == [4, 50, 60]


def test_atomic_writer_produces_readable_parquet(tmp_path):
    frame, _ = normalize_records(
        pd.DataFrame([raw_row("20260105", 12)]), "ZN", FakeMetaManager()
    )
    path = tmp_path / "SHFE_ZN.parquet"

    write_parquet_atomic(frame, path)

    result = pd.read_parquet(path)
    assert len(result) == 1
    assert not path.with_suffix(".parquet.tmp").exists()
