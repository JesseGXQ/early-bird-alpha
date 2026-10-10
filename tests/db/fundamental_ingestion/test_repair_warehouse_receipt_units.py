from types import SimpleNamespace

import pandas as pd
import pytest

from data.storage.warehouse_receipt_loader import WAREHOUSE_RECEIPT_COLUMNS
from db.fundamental_ingestion.script.warehouse_receipts.repair_warehouse_receipt_units import (
    apply_repair,
    repair_frame,
    resolve_trusted_unit,
    scan_database,
    summarize,
)


class SingleProductMetaManager:
    def get_product_universe(self, return_type: str) -> list[str]:
        assert return_type == "list"
        return ["ZN"]

    def get_static_meta(self, product: str) -> SimpleNamespace:
        assert product == "ZN"
        return SimpleNamespace(product="ZN", exchange="SHFE", name="沪锌")


def receipt_row(
    trading_date: str,
    warehouse: str,
    quantity: int,
    unit: str,
    previous_quantity: int | None,
) -> dict:
    return {
        "trading_date": pd.Timestamp(trading_date),
        "product": "ZN",
        "product_name": "沪锌",
        "exchange": "SHFE",
        "source_symbol": "ZN",
        "source_product_name": "锌",
        "source_exchange": "SHFE",
        "warehouse": warehouse,
        "warehouse_id": pd.NA,
        "previous_receipt_quantity": previous_quantity,
        "receipt_quantity": quantity,
        "receipt_change_quantity": pd.NA,
        "unit": unit,
        "area": pd.NA,
        "year": pd.NA,
        "grade": pd.NA,
        "brand": pd.NA,
        "place": pd.NA,
        "premium_discount": pd.NA,
        "is_converted_receipt": pd.NA,
    }


def make_frame() -> pd.DataFrame:
    """构造 AG/AU 形态的数据：可信行标主单位，错标行 pre_vol 为空。"""
    rows = [
        receipt_row("2020-04-09", "甲仓", 944592, "千克", 930000),
        receipt_row("2020-04-09", "乙仓", 1263848, "千克", 1250000),
        # 错标行：单位是少数派，且昨仓为空
        receipt_row("2020-04-09", "合计", 2208440, "吨", None),
        receipt_row("2020-04-10", "甲仓", 929540, "吨", None),
        receipt_row("2020-04-10", "乙仓", 1262829, "吨", None),
        # pre_vol 为空但单位本来就正确，改写应是 no-op
        receipt_row("2020-04-11", "丙仓", 500000, "千克", None),
    ]
    return pd.DataFrame(rows).loc[:, WAREHOUSE_RECEIPT_COLUMNS]


def test_resolve_trusted_unit_uses_pre_vol_non_null_rows():
    trusted_unit, reason = resolve_trusted_unit(make_frame())

    assert trusted_unit == "千克"
    assert reason == ""


def test_resolve_trusted_unit_skips_frame_without_anchor():
    frame = make_frame().assign(
        previous_receipt_quantity=pd.array([pd.NA] * 6, dtype="Int64")
    )

    trusted_unit, reason = resolve_trusted_unit(frame)

    assert trusted_unit is None
    assert "无法判定主单位" in reason


def test_resolve_trusted_unit_skips_frame_with_empty_file():
    frame = make_frame().iloc[0:0]

    trusted_unit, reason = resolve_trusted_unit(frame)

    assert trusted_unit is None
    assert reason == "文件为空"


def test_resolve_trusted_unit_rejects_ambiguous_anchor():
    frame = make_frame()
    conflicting = receipt_row("2020-04-09", "丁仓", 100, "吨", 90)
    frame = pd.concat([frame, pd.DataFrame([conflicting])], ignore_index=True)

    trusted_unit, reason = resolve_trusted_unit(frame)

    assert trusted_unit is None
    assert "不唯一" in reason


def test_repair_frame_relabels_minority_unit_without_touching_quantities():
    frame = make_frame()

    repaired, stats = repair_frame(frame)

    assert stats["trusted_unit"] == "千克"
    assert stats["rewritten_rows"] == 3
    assert stats["rewritten_from_units"] == {"吨": 3}
    assert stats["units_after"] == ["千克"]
    assert stats["duplicate_rows_after"] == 0
    # 数值列必须原样保留：错标行的量纲本就是主单位，不做换算。
    pd.testing.assert_series_equal(
        repaired["receipt_quantity"], frame["receipt_quantity"], check_names=False
    )
    assert repaired.loc[repaired["warehouse"] == "合计", "receipt_quantity"].iloc[
        0
    ] == 2208440


def test_repair_frame_is_idempotent():
    frame = make_frame()

    once, _ = repair_frame(frame)
    twice, stats = repair_frame(once)

    assert stats["rewritten_rows"] == 0
    pd.testing.assert_frame_equal(once, twice)


def test_scan_database_does_not_write(tmp_path):
    frame = make_frame()
    path = tmp_path / "SHFE_ZN.parquet"
    frame.to_parquet(path, index=False)
    original = path.read_bytes()

    results = scan_database(
        "db/rules/product_meta", str(tmp_path), products=["ZN"]
    )

    assert results["ZN"]["rewritten_rows"] == 3
    assert results["ZN"]["applied"] is False
    assert path.read_bytes() == original


def test_apply_repair_rewrites_file_and_converges_to_single_unit(tmp_path):
    path = tmp_path / "SHFE_ZN.parquet"
    make_frame().to_parquet(path, index=False)

    results = apply_repair("db/rules/product_meta", str(tmp_path), products=["ZN"])

    assert results["ZN"]["applied"] is True
    repaired = pd.read_parquet(path)
    assert repaired["unit"].unique().tolist() == ["千克"]
    assert repaired["receipt_quantity"].tolist() == make_frame()[
        "receipt_quantity"
    ].tolist()


def test_apply_repair_reports_zero_when_already_consistent(tmp_path):
    path = tmp_path / "SHFE_ZN.parquet"
    frame = make_frame()
    frame["unit"] = "千克"
    frame.to_parquet(path, index=False)
    before = path.read_bytes()

    results = apply_repair("db/rules/product_meta", str(tmp_path), products=["ZN"])

    assert results["ZN"]["rewritten_rows"] == 0
    assert results["ZN"]["applied"] is False
    assert path.read_bytes() == before


def test_apply_repair_refuses_when_relabelling_breaks_natural_key(tmp_path):
    # 两行除 unit 外完全相同：把少数派改成主单位后自然键冲突。
    duplicate = receipt_row("2020-04-09", "甲仓", 944592, "吨", None)
    frame = pd.concat([make_frame(), pd.DataFrame([duplicate])], ignore_index=True)
    path = tmp_path / "SHFE_ZN.parquet"
    frame.to_parquet(path, index=False)
    before = path.read_bytes()

    with pytest.raises(ValueError, match="自然键"):
        apply_repair("db/rules/product_meta", str(tmp_path), products=["ZN"])

    assert path.read_bytes() == before


def test_summarize_partitions_products_by_outcome():
    results = {
        "ZN": {"exists": True, "applied": True, "rewritten_rows": 3},
        "CU": {
            "exists": True,
            "applied": False,
            "rewritten_rows": 0,
            "skipped_reason": "",
        },
        "CF": {"exists": True, "skipped_reason": "文件为空"},
        "XX": {"exists": False},
    }

    summary = summarize(results)

    assert summary["products_scanned"] == 3
    assert summary["products_repaired"] == ["ZN"]
    assert summary["products_already_consistent"] == ["CU"]
    assert summary["products_skipped"] == ["CF"]
    assert summary["products_missing"] == ["XX"]
    assert summary["total_rewritten_rows"] == 3