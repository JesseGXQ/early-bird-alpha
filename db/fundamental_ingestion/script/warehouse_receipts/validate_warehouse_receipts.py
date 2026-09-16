"""校验正式仓单数据库的覆盖范围、schema、权威映射和自然键唯一性。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from data.rules.product_meta_manager import ProductMetaManager
from data.storage.warehouse_receipt_loader import WAREHOUSE_RECEIPT_COLUMNS
from db.fundamental_ingestion.script.warehouse_receipts.ingest_warehouse_receipts import (
    NATURAL_KEY_COLUMNS,
)


def validate_database(
    product_meta_dir: str,
    warehouse_receipt_dir: str,
) -> dict[str, Any]:
    """执行全库只读校验并返回结构化结果。"""
    meta_manager = ProductMetaManager(product_meta_dir)
    products = sorted(
        product.upper() for product in meta_manager.get_product_universe("list")
    )
    data_dir = Path(warehouse_receipt_dir)
    product_results: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    total_rows = 0
    nonempty_products = 0
    quantity_inconsistencies = 0

    for product in products:
        static_meta = meta_manager.get_static_meta(product)
        path = data_dir / f"{static_meta.exchange}_{product}.parquet"
        if not path.exists():
            errors.append(f"缺少文件：{path}")
            product_results[product] = {"exists": False}
            continue

        df = pd.read_parquet(path)
        product_errors: list[str] = []
        if df.columns.tolist() != WAREHOUSE_RECEIPT_COLUMNS:
            product_errors.append("schema 或列顺序不一致")
        if not df.empty:
            nonempty_products += 1
            dates = pd.to_datetime(df["trading_date"], errors="coerce")
            if dates.isna().any():
                product_errors.append("存在无效 trading_date")
            if not dates.is_monotonic_increasing:
                product_errors.append("trading_date 未按升序排列")
            if set(df["product"].dropna().astype(str)) != {product}:
                product_errors.append("product 与 product_meta 不一致")
            if set(df["product_name"].dropna().astype(str)) != {static_meta.name}:
                product_errors.append("product_name 与 product_meta 不一致")
            if set(df["exchange"].dropna().astype(str)) != {static_meta.exchange}:
                product_errors.append("exchange 与 product_meta 不一致")
            duplicate_rows = int(df.duplicated(NATURAL_KEY_COLUMNS).sum())
            if duplicate_rows:
                product_errors.append(f"自然键重复 {duplicate_rows} 行")
            comparable = df[
                [
                    "previous_receipt_quantity",
                    "receipt_quantity",
                    "receipt_change_quantity",
                ]
            ].notna().all(axis=1)
            inconsistent = comparable & (
                df["previous_receipt_quantity"] + df["receipt_change_quantity"]
                != df["receipt_quantity"]
            )
            product_inconsistencies = int(inconsistent.sum())
        else:
            duplicate_rows = 0
            product_inconsistencies = 0

        total_rows += len(df)
        quantity_inconsistencies += product_inconsistencies
        for message in product_errors:
            errors.append(f"{product}: {message}")
        product_results[product] = {
            "exists": True,
            "rows": len(df),
            "min_date": None
            if df.empty
            else pd.to_datetime(df["trading_date"]).min().date().isoformat(),
            "max_date": None
            if df.empty
            else pd.to_datetime(df["trading_date"]).max().date().isoformat(),
            "duplicate_rows": duplicate_rows,
            "quantity_inconsistencies": product_inconsistencies,
            "errors": product_errors,
        }

    return {
        "validated_at": datetime.now().isoformat(timespec="seconds"),
        "product_count_expected": len(products),
        "product_files_found": sum(
            int(item.get("exists", False)) for item in product_results.values()
        ),
        "nonempty_products": nonempty_products,
        "total_rows": total_rows,
        "quantity_inconsistencies": quantity_inconsistencies,
        "error_count": len(errors),
        "errors": errors,
        "products": product_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="校验本地仓单日报数据库")
    parser.add_argument("--product-meta-dir", default="db/rules/product_meta")
    parser.add_argument("--data-dir", default="db/fundamental/warehouse_receipts")
    parser.add_argument(
        "--audit-path",
        default="db/fundamental_ingestion/data/audit/warehouse_receipts_validation.json",
    )
    args = parser.parse_args()

    result = validate_database(args.product_meta_dir, args.data_dir)
    audit_path = Path(args.audit_path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"files={result['product_files_found']}/{result['product_count_expected']} "
        f"nonempty={result['nonempty_products']} rows={result['total_rows']} "
        f"errors={result['error_count']} audit={audit_path}"
    )
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
