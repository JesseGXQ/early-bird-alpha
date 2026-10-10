"""修正仓单日报中 ``unit`` 字段的错标问题。

2026-10-09 在正式库中发现：部分品种存在同一品种两个计量单位并存的现象
（``SHFE_AG``/``SHFE_AU`` 的千克与吨、``INE_SC`` 的吨与桶）。核查后确认这些
少数派单位的数值与其余行属于同一量纲，只是 Tushare 在响应中把 ``unit`` 写错了；
若直接按 ``unit`` 分组换算或加总，会产生量纲错误。

本脚本以 ``previous_receipt_quantity`` 非空的行作为可信基准：这类行的 ``unit``
在当前全库中始终唯一且自洽，因此其取值即该品种的主单位。所有主单位之外的行
都被改写为主单位，数值列不做任何改动。

重要：本脚本针对的是**已经落盘的历史数据**。重新拉取数据后，错标问题是否仍然
存在必须重新检查；只有确认问题复现时才应再次执行本脚本。若源端已修正，
本脚本应当报告零改写。详见 ``db/fundamental_ingestion/README.md``。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from data.rules.product_meta_manager import ProductMetaManager
from db.fundamental_ingestion.script.warehouse_receipts.ingest_warehouse_receipts import (
    NATURAL_KEY_COLUMNS,
    write_parquet_atomic,
)

# 可信基准列：有此列取值的行，其 unit 视为可信。
TRUSTED_ANCHOR_COLUMN = "previous_receipt_quantity"
# 需要被检验是否已收敛为单一单位的列。
UNIT_COLUMN = "unit"


def resolve_trusted_unit(df: pd.DataFrame) -> tuple[str | None, str]:
    """返回该品种的可信主单位，以及无法判定时的原因。

    可信基准是 ``previous_receipt_quantity`` 非空的行：这类行由源端同时给出
    昨仓与增减量，其 ``unit`` 与数值自洽。全库核查中，可信行的 ``unit``
    始终唯一；一旦出现多个取值，说明该判据在该品种上不再成立，必须放弃修正。
    """
    if df.empty:
        return None, "文件为空"
    anchor = pd.to_numeric(df[TRUSTED_ANCHOR_COLUMN], errors="coerce")
    trusted_units = sorted(df.loc[anchor.notna(), UNIT_COLUMN].dropna().unique().tolist())
    if not trusted_units:
        # CZCE 的“张”等单位全库唯一，即便没有可信行也不存在多单位问题；
        # 但脚本不据此假定无事发生，而是明确交回给人判断。
        return None, f"没有 {TRUSTED_ANCHOR_COLUMN} 非空的行，无法判定主单位"
    if len(trusted_units) > 1:
        return None, f"可信行的 {UNIT_COLUMN} 不唯一：{trusted_units}"
    return trusted_units[0], ""


def repair_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """把少数派单位改写为可信主单位，返回修正后的数据与统计信息。"""
    trusted_unit, reason = resolve_trusted_unit(df)
    stats: dict[str, Any] = {
        "rows": len(df),
        "trusted_unit": trusted_unit,
        "skipped_reason": reason,
        "original_units": sorted(df[UNIT_COLUMN].dropna().unique().tolist()),
        "rewritten_rows": 0,
        "rewritten_from_units": {},
        "duplicate_rows_after": 0,
    }
    if trusted_unit is None:
        return df, stats

    mismatched = df[UNIT_COLUMN].ne(trusted_unit) & df[UNIT_COLUMN].notna()
    rewritten_from = (
        df.loc[mismatched, UNIT_COLUMN].value_counts().sort_index().to_dict()
    )
    repaired = df.copy()
    repaired.loc[mismatched, UNIT_COLUMN] = trusted_unit
    repaired[UNIT_COLUMN] = repaired[UNIT_COLUMN].astype(df[UNIT_COLUMN].dtype)

    stats["rewritten_rows"] = int(mismatched.sum())
    stats["rewritten_from_units"] = {
        str(unit): int(count) for unit, count in rewritten_from.items()
    }
    stats["duplicate_rows_after"] = int(repaired.duplicated(NATURAL_KEY_COLUMNS).sum())
    stats["units_after"] = sorted(repaired[UNIT_COLUMN].dropna().unique().tolist())
    return repaired, stats


def scan_database(
    product_meta_dir: str,
    warehouse_receipt_dir: str,
    products: list[str] | None = None,
) -> dict[str, Any]:
    """扫描正式库，返回每个品种的修正统计（不写盘）。"""
    meta_manager = ProductMetaManager(product_meta_dir)
    universe = [
        product.upper() for product in meta_manager.get_product_universe("list")
    ]
    requested = [product.upper() for product in (products or universe)]
    unknown = sorted(set(requested).difference(universe))
    if unknown:
        raise ValueError(f"以下品种不在 product_meta 中：{unknown}")

    data_dir = Path(warehouse_receipt_dir)
    results: dict[str, Any] = {}
    for product in requested:
        static_meta = meta_manager.get_static_meta(product)
        path = data_dir / f"{static_meta.exchange}_{product}.parquet"
        if not path.exists():
            results[product] = {"exists": False, "path": str(path)}
            continue
        df = pd.read_parquet(path)
        _, stats = repair_frame(df)
        stats["exists"] = True
        stats["path"] = str(path)
        stats["applied"] = False
        results[product] = stats
    return results


def apply_repair(
    product_meta_dir: str,
    warehouse_receipt_dir: str,
    products: list[str] | None = None,
) -> dict[str, Any]:
    """扫描并原地改写需要修正的品种文件。"""
    meta_manager = ProductMetaManager(product_meta_dir)
    universe = [
        product.upper() for product in meta_manager.get_product_universe("list")
    ]
    requested = [product.upper() for product in (products or universe)]
    unknown = sorted(set(requested).difference(universe))
    if unknown:
        raise ValueError(f"以下品种不在 product_meta 中：{unknown}")

    data_dir = Path(warehouse_receipt_dir)
    results: dict[str, Any] = {}
    for product in requested:
        static_meta = meta_manager.get_static_meta(product)
        path = data_dir / f"{static_meta.exchange}_{product}.parquet"
        if not path.exists():
            results[product] = {"exists": False, "path": str(path)}
            continue
        df = pd.read_parquet(path)
        repaired, stats = repair_frame(df)
        stats["exists"] = True
        stats["path"] = str(path)
        if stats["rewritten_rows"]:
            # unit 属于自然键，改写后若出现重复，说明判据在该品种上不成立，
            # 此时中止而不是写出一个破坏唯一性约束的正式文件。
            if stats["duplicate_rows_after"]:
                raise ValueError(
                    f"{product} 改写 unit 后自然键出现 "
                    f"{stats['duplicate_rows_after']} 行重复，已中止，未写入。"
                )
            write_parquet_atomic(repaired, path)
            stats["applied"] = True
        else:
            stats["applied"] = False
        results[product] = stats
    return results


def summarize(results: dict[str, Any]) -> dict[str, Any]:
    """把逐品种统计压缩为审计摘要。"""
    applied = sorted(
        product for product, item in results.items() if item.get("applied")
    )
    skipped = sorted(
        product
        for product, item in results.items()
        if item.get("exists") and item.get("skipped_reason")
    )
    clean = sorted(
        product
        for product, item in results.items()
        if item.get("exists")
        and not item.get("skipped_reason")
        and not item.get("rewritten_rows")
    )
    return {
        "products_scanned": sum(1 for item in results.values() if item.get("exists")),
        "products_missing": sorted(
            product for product, item in results.items() if not item.get("exists")
        ),
        "products_repaired": applied,
        "products_already_consistent": clean,
        "products_skipped": skipped,
        "total_rewritten_rows": sum(
            int(item.get("rewritten_rows", 0)) for item in results.values()
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="修正仓单日报 unit 字段错标（默认只读报告）"
    )
    parser.add_argument("--products", nargs="*", help="品种代码；默认 product_meta 全集")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写盘；缺省时只输出将要执行的改写",
    )
    parser.add_argument("--product-meta-dir", default="db/rules/product_meta")
    parser.add_argument("--data-dir", default="db/fundamental/warehouse_receipts")
    parser.add_argument(
        "--audit-path",
        default=(
            "db/fundamental_ingestion/data/audit/warehouse_receipts_unit_repair.json"
        ),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    runner = apply_repair if args.apply else scan_database
    results = runner(args.product_meta_dir, args.data_dir, args.products)
    summary = summarize(results)
    report = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "apply" if args.apply else "report",
        **summary,
        "products": results,
    }

    audit_path = Path(args.audit_path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    verb = "已修正" if args.apply else "待修正"
    print(
        f"mode={report['mode']} scanned={summary['products_scanned']} "
        f"{verb}品种={summary['products_repaired'] or '无'} "
        f"改写行数={summary['total_rewritten_rows']} "
        f"跳过={len(summary['products_skipped'])} audit={audit_path}"
    )
    for product in summary["products_repaired"]:
        item = results[product]
        print(
            f"  {product}: {item['trusted_unit']} <- "
            f"{item['rewritten_from_units']} ({item['rewritten_rows']} 行)"
        )
    for product in summary["products_skipped"]:
        print(f"  跳过 {product}: {results[product]['skipped_reason']}")


if __name__ == "__main__":
    main()