"""
Generate TIER1 PriceTickRule YAML files for the 20260624 derived batch.

The product universe is the existing StaticMeta YAML directory. Historical
price-tick changes are taken from the local research report documented in
variable_ingestion_plan.md; BigQuant cn_future_product_info is used only to
fill one-version rules for products without a real historical tick change.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[4]
RAW_PRODUCT_INFO = (
    ROOT
    / "db"
    / "rules_ingestion"
    / "data"
    / "raw"
    / "20260622"
    / "bigquant"
    / "cn_future_product_info"
    / "full.parquet"
)
LISTING_DATES_DOC = (
    ROOT
    / "docs"
    / "代码修改笔记"
    / "期货交易规则"
    / "交易时段基础规则"
    / "期货品种上市时间汇总.md"
)
STATIC_YAML_DIR = (
    ROOT
    / "db"
    / "rules_ingestion"
    / "data"
    / "derived"
    / "20260624"
    / "product_meta_static"
    / "yaml"
)
OUT_ROOT = (
    ROOT
    / "db"
    / "rules_ingestion"
    / "data"
    / "derived"
    / "20260624"
    / "product_meta_price_tick_rule"
)
YAML_DIR = OUT_ROOT / "yaml"

PLACEHOLDER_EFFECTIVE_DATE = "1900-01-01"

# Source:
# docs/代码修改笔记/期货交易规则/
# 中国期货市场历史品种最小价格变动（Price Tick 最小变动价位）变更调研报告 _Antigravity.md
RESEARCH_REPORT_HISTORIES: dict[str, list[dict[str, Any]]] = {
    "TS": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 0.005},
        {"effective_date": "2023-11-06", "price_tick": 0.002},
    ],
    "TF": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 0.002},
        {"effective_date": "2015-03-16", "price_tick": 0.005},
    ],
    "OI": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 2.0},
        {"effective_date": "2018-05-16", "price_tick": 1.0},
    ],
    "Y": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 2.0},
        {"effective_date": "2026-04-10", "price_tick": 1.0},
    ],
    "P": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 2.0},
        {"effective_date": "2026-04-10", "price_tick": 1.0},
    ],
    "L": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 5.0},
        {"effective_date": "2021-11-01", "price_tick": 1.0},
    ],
    "V": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 5.0},
        {"effective_date": "2021-11-01", "price_tick": 1.0},
    ],
    "FB": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 0.05},
        {"effective_date": "2019-12-02", "price_tick": 0.5},
    ],
    "J": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 1.0},
        {"effective_date": "2015-04-17", "price_tick": 0.5},
    ],
    "JM": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 1.0},
        {"effective_date": "2015-04-17", "price_tick": 0.5},
    ],
    "I": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 1.0},
        {"effective_date": "2015-04-17", "price_tick": 0.5},
    ],
    "LC": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 50.0},
        {"effective_date": "2024-12-17", "price_tick": 20.0},
    ],
    "EC": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 0.1},
        {"effective_date": "2026-05-11", "price_tick": 0.5},
    ],
    "BU": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 2.0},
        {"effective_date": "2022-03-16", "price_tick": 1.0},
    ],
    "AU": [
        {"effective_date": PLACEHOLDER_EFFECTIVE_DATE, "price_tick": 0.01},
        {"effective_date": "2013-06-25", "price_tick": 0.05},
        {"effective_date": "2019-12-10", "price_tick": 0.02},
    ],
}

REPORT_NOT_REAL_CHANGES = {
    "BR": "BigQuant old_price_tick=2.0 is not a real historical futures rule change.",
    "L_F": "Monthly average swap/OTC style record; not a standard futures tick-history change.",
    "PP_F": "Monthly average swap/OTC style record; not a standard futures tick-history change.",
    "V_F": "Monthly average swap/OTC style record; not a standard futures tick-history change.",
    "RO": "Old/redundant code; do not derive OI history automatically.",
    "ME": "Old/redundant code; do not derive MA history automatically.",
    "ER": "Old/redundant code; do not derive RI history automatically.",
}


def product_universe() -> list[str]:
    return sorted(path.stem.upper() for path in STATIC_YAML_DIR.glob("*.yaml"))


def read_listing_dates() -> dict[str, str]:
    pattern = re.compile(r"\| `([^`]+)` \| [^|]+ \| ([0-9]{4}-[0-9]{2}-[0-9]{2}) \|")
    listing_dates: dict[str, str] = {}
    for line in LISTING_DATES_DOC.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if match:
            product, list_date = match.groups()
            listing_dates[product.upper()] = list_date
    return listing_dates


def read_product_info() -> pd.DataFrame:
    df = pd.read_parquet(RAW_PRODUCT_INFO)
    df["product"] = df["code"].astype(str).str.upper()
    return df


def build_product_tick_lookup(product_info: pd.DataFrame) -> dict[str, float]:
    lookup: dict[str, float] = {}
    for row in product_info.itertuples(index=False):
        if pd.isna(row.price_tick):
            continue
        lookup[row.product] = float(row.price_tick)
    return lookup


def with_listing_date(product: str, history: list[dict[str, Any]], listing_dates: dict[str, str]) -> list[dict[str, Any]]:
    if product not in listing_dates:
        raise ValueError(f"Missing listing date for {product} in {LISTING_DATES_DOC}")
    result = [dict(item) for item in history]
    result[0]["effective_date"] = listing_dates[product]
    return result


def build_histories(
    products: list[str], product_tick: dict[str, float], listing_dates: dict[str, str]
) -> tuple[dict[str, list[dict[str, Any]]], pd.DataFrame]:
    histories: dict[str, list[dict[str, Any]]] = {}
    audit_rows: list[dict[str, Any]] = []

    for product in products:
        if product in RESEARCH_REPORT_HISTORIES:
            history = with_listing_date(product, RESEARCH_REPORT_HISTORIES[product], listing_dates)
            source = "research_report_history"
            note = ""
        else:
            tick = product_tick.get(product)
            if tick is None:
                raise ValueError(f"Missing price_tick for {product} in {RAW_PRODUCT_INFO}")
            history = [{"effective_date": listing_dates[product], "price_tick": tick}]
            source = "bigquant_current_single_version"
            note = REPORT_NOT_REAL_CHANGES.get(product, "")

        histories[product] = history
        audit_rows.append(
            {
                "product": product,
                "source": source,
                "versions": len(history),
                "first_effective_date": history[0]["effective_date"],
                "latest_effective_date": history[-1]["effective_date"],
                "latest_price_tick": history[-1]["price_tick"],
                "note": note,
            }
        )

    return histories, pd.DataFrame(audit_rows)


def write_yaml_files(histories: dict[str, list[dict[str, Any]]]) -> int:
    YAML_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for product, history in sorted(histories.items()):
        payload = {"price_tick_history": history}
        with open(YAML_DIR / f"{product}.yaml", "w", encoding="utf-8") as fh:
            yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False)
        count += 1
    return count


def write_report(products: list[str], audit: pd.DataFrame, yaml_count: int) -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    audit.to_csv(OUT_ROOT / "price_tick_rule_audit.csv", index=False, encoding="utf-8-sig")

    history_products = sorted(RESEARCH_REPORT_HISTORIES)
    not_real = sorted(product for product in REPORT_NOT_REAL_CHANGES if product in products)
    lines = [
        "# PriceTickRule Ingestion Audit",
        "",
        "- Batch: 20260624",
        "- Raw snapshot: `db/rules_ingestion/data/raw/20260622`",
        "- Product universe: `db/rules_ingestion/data/derived/20260624/product_meta_static/yaml/*.yaml`",
        "- Historical fact source: `docs/代码修改笔记/期货交易规则/中国期货市场历史品种最小价格变动（Price Tick 最小变动价位）变更调研报告 _Antigravity.md`",
        "- Listing-date source: `docs/代码修改笔记/期货交易规则/交易时段基础规则/期货品种上市时间汇总.md`",
        "- Fallback current tick source: `db/rules_ingestion/data/raw/20260622/bigquant/cn_future_product_info/full.parquet`",
        "- Variables: `PriceTickRule.effective_date`, `PriceTickRule.price_tick`",
        f"- Product universe count: {len(products)}",
        f"- YAML files written: {yaml_count}",
        f"- Products with research-report historical versions: {len(history_products)}",
        f"- Products using single fallback version: {int((audit['source'] == 'bigquant_current_single_version').sum())}",
        "",
        "## Policy Applied",
        "",
        "- The first `effective_date` for every product uses the product listing date.",
        "- Research-report histories override BigQuant current or old tick values when they differ.",
        "- BigQuant `old_price_tick` is not used as an automatic history source.",
        "- Non-real changes, old-code redundancy, and monthly-average swap records are kept as single-version rules when present in the static universe.",
        "",
        "## Research-Report Historical Products",
        "",
    ]
    for product in history_products:
        lines.append(f"- {product}: {len(RESEARCH_REPORT_HISTORIES[product])} versions")

    lines.extend(["", "## Single-Version Special Cases", ""])
    for product in not_real:
        lines.append(f"- {product}: {REPORT_NOT_REAL_CHANGES[product]}")

    lines.extend(
        [
            "",
            "## Output Files",
            "",
            "- `price_tick_rule_audit.csv`",
            "- `yaml/*.yaml`",
            "",
        ]
    )
    (OUT_ROOT / "audit_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    products = product_universe()
    product_info = read_product_info()
    product_tick = build_product_tick_lookup(product_info)
    listing_dates = read_listing_dates()
    missing_listing_dates = sorted(set(products) - set(listing_dates))
    if missing_listing_dates:
        raise ValueError(f"Missing listing dates for products: {missing_listing_dates}")
    histories, audit = build_histories(products, product_tick, listing_dates)
    yaml_count = write_yaml_files(histories)
    write_report(products, audit, yaml_count)

    print(f"Product universe: {len(products)}")
    print(f"YAML files written: {yaml_count}")
    print(f"Research-report histories: {len(RESEARCH_REPORT_HISTORIES)}")
    print(f"Fallback single-version rules: {(audit['source'] == 'bigquant_current_single_version').sum()}")
    print(f"Output: {OUT_ROOT}")


if __name__ == "__main__":
    main()
