"""
Generate TIER1 StaticMeta candidates from the 20260622 raw snapshots.

This script intentionally writes derived candidates under
db/rules_ingestion/data/derived instead of overwriting the final data/rules
database. The generated YAML files contain only the StaticMeta fields that
ProductMetaManager.get_static_meta() needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[4]
RAW_ROOT = ROOT / "db" / "rules_ingestion" / "data" / "raw" / "20260622"
OUT_ROOT = ROOT / "db" / "rules_ingestion" / "data" / "derived" / "20260624" / "product_meta_static"
YAML_DIR = OUT_ROOT / "yaml"

VALID_EXCHANGES = {"CFFEX", "CZCE", "DCE", "GFEX", "INE", "SHFE"}
EXCHANGE_ALIASES = {
    "CFE": "CFFEX",
    "CFFEX": "CFFEX",
    "CZC": "CZCE",
    "CZCE": "CZCE",
    "DCE": "DCE",
    "GFE": "GFEX",
    "GFEX": "GFEX",
    "INE": "INE",
    "SHF": "SHFE",
    "SHFE": "SHFE",
}
POSITION_DATE_TYPE_MAP = {
    "1": "distinguish_today_yesterday",
    "2": "undistinguished",
}


@dataclass(frozen=True)
class ProductKey:
    exchange: str
    product: str


def normalize_exchange(value: Any) -> str | None:
    if pd.isna(value):
        return None
    return EXCHANGE_ALIASES.get(str(value).strip().upper())


def normalize_product(value: Any) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.upper()


def unique_non_null(values: pd.Series) -> list[str]:
    if values.empty:
        return []
    return sorted({str(v) for v in values.dropna().tolist()})


def mode_value(values: pd.Series) -> Any:
    clean = values.dropna()
    if clean.empty:
        return None
    counts = clean.value_counts()
    return counts[counts == counts.max()].sort_index().index[-1]


def latest_mode(df: pd.DataFrame, column: str) -> Any:
    if df.empty or column not in df.columns:
        return None
    work = df.dropna(subset=[column])
    if work.empty:
        return None
    if "trading_day" in work.columns:
        latest_day = work["trading_day"].max()
        latest = work[work["trading_day"] == latest_day]
        value = mode_value(latest[column])
        if value is not None:
            return value
    return mode_value(work[column])


def read_product_info() -> pd.DataFrame:
    path = RAW_ROOT / "bigquant" / "cn_future_product_info" / "full.parquet"
    df = pd.read_parquet(path)
    df["exchange_local"] = df["exchange"].map(normalize_exchange)
    df["product_local"] = df["code"].map(normalize_product)
    return df


def read_basic_info() -> pd.DataFrame:
    paths = sorted((RAW_ROOT / "bigquant" / "cn_future_basic_info").glob("exchange=*.parquet"))
    frames = [pd.read_parquet(path) for path in paths]
    df = pd.concat(frames, ignore_index=True)
    df["exchange_local"] = df["exchange"].map(normalize_exchange)
    df["product_local"] = df["product_code"].map(normalize_product)
    return df


def read_static_data() -> pd.DataFrame:
    paths = sorted((RAW_ROOT / "bigquant" / "cn_future_static_data").glob("exchange=*/product=*.parquet"))
    frames: list[pd.DataFrame] = []
    for path in paths:
        df = pd.read_parquet(path)
        df["source_path"] = str(path.relative_to(ROOT))
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["exchange_local"] = df["exchange"].map(normalize_exchange)
    df["product_local"] = df["product_id"].map(normalize_product)
    return df


def build_name_lookup(product_info: pd.DataFrame, basic_info: pd.DataFrame, static_data: pd.DataFrame) -> dict[ProductKey, str]:
    result: dict[ProductKey, str] = {}
    for row in product_info.dropna(subset=["exchange_local", "product_local"]).itertuples(index=False):
        if row.exchange_local in VALID_EXCHANGES and isinstance(row.name, str) and row.name.strip():
            result[ProductKey(row.exchange_local, row.product_local)] = row.name.strip()

    for source in (basic_info, static_data):
        for (exchange, product), group in source.dropna(subset=["exchange_local", "product_local"]).groupby(
            ["exchange_local", "product_local"]
        ):
            key = ProductKey(exchange, product)
            if key in result:
                continue
            names = unique_non_null(group["name"])
            if names:
                result[key] = names[0]
    return result


def build_candidates(product_info: pd.DataFrame, basic_info: pd.DataFrame, static_data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    basic_products = {
        ProductKey(exchange, product)
        for exchange, product in basic_info.dropna(subset=["exchange_local", "product_local"])[
            ["exchange_local", "product_local"]
        ].itertuples(index=False, name=None)
        if exchange in VALID_EXCHANGES
    }
    static_products = {
        ProductKey(exchange, product)
        for exchange, product in static_data.dropna(subset=["exchange_local", "product_local"])[
            ["exchange_local", "product_local"]
        ].drop_duplicates()
        .itertuples(index=False, name=None)
        if exchange in VALID_EXCHANGES
    }
    product_info_products = {
        ProductKey(exchange, product)
        for exchange, product in product_info.dropna(subset=["exchange_local", "product_local"])[
            ["exchange_local", "product_local"]
        ].itertuples(index=False, name=None)
        if exchange in VALID_EXCHANGES
    }

    eligible = sorted(basic_products & static_products, key=lambda k: (k.exchange, k.product))
    name_lookup = build_name_lookup(product_info, basic_info, static_data)

    rows: list[dict[str, Any]] = []
    multi_value_rows: list[dict[str, Any]] = []
    for key in eligible:
        group = static_data[
            (static_data["exchange_local"] == key.exchange) & (static_data["product_local"] == key.product)
        ].copy()
        group["trading_day"] = pd.to_numeric(group["trading_day"], errors="coerce")
        product_classes = unique_non_null(group["product_class"])
        if product_classes != ["1"]:
            rows.append(
                {
                    "product": key.product,
                    "exchange": key.exchange,
                    "status": "excluded",
                    "reason": f"non_plain_future_product_class={product_classes}",
                }
            )
            continue

        raw_position_date_type = latest_mode(group, "position_date_type")
        position_date_type = POSITION_DATE_TYPE_MAP.get(str(raw_position_date_type))
        max_margin_raw = latest_mode(group, "max_margin_side_algorithm")
        combination_raw = latest_mode(group, "combination_type")

        status = "ok"
        reason = ""
        if position_date_type is None:
            status = "needs_review"
            reason = f"unknown_position_date_type={raw_position_date_type}"
        if max_margin_raw is None or combination_raw is None:
            status = "needs_review"
            reason = "; ".join(filter(None, [reason, "missing_static_meta_value"]))

        for field in ["position_date_type", "max_margin_side_algorithm", "combination_type"]:
            values = unique_non_null(group[field])
            if len(values) > 1:
                multi_value_rows.append(
                    {
                        "product": key.product,
                        "exchange": key.exchange,
                        "field": field,
                        "values": "|".join(values),
                        "chosen_latest_mode": latest_mode(group, field),
                        "first_trading_day": int(group["trading_day"].min()),
                        "last_trading_day": int(group["trading_day"].max()),
                        "distinct_instruments": group["instrument"].nunique(dropna=True),
                    }
                )

        rows.append(
            {
                "product": key.product,
                "exchange": key.exchange,
                "name": name_lookup.get(key, ""),
                "position_date_type": position_date_type,
                "position_date_type_raw": raw_position_date_type,
                "max_margin_side_algorithm": bool(int(max_margin_raw)) if max_margin_raw is not None else None,
                "max_margin_side_algorithm_raw": max_margin_raw,
                "combination_type": int(combination_raw) if combination_raw is not None else None,
                "combination_type_raw": combination_raw,
                "status": status,
                "reason": reason,
                "has_product_info": key in product_info_products,
                "basic_instruments": int(
                    basic_info[
                        (basic_info["exchange_local"] == key.exchange) & (basic_info["product_local"] == key.product)
                    ]["instrument"].nunique(dropna=True)
                ),
                "static_rows": int(len(group)),
                "static_instruments": int(group["instrument"].nunique(dropna=True)),
                "first_trading_day": int(group["trading_day"].min()),
                "last_trading_day": int(group["trading_day"].max()),
            }
        )

    rejected_product_info = []
    for key in sorted(product_info_products - set(eligible), key=lambda k: (k.exchange, k.product)):
        reasons = []
        if key not in basic_products:
            reasons.append("missing_from_basic_info")
        if key not in static_products:
            reasons.append("missing_from_static_data")
        rejected_product_info.append(
            {
                "product": key.product,
                "exchange": key.exchange,
                "source": "cn_future_product_info",
                "reason": ";".join(reasons),
            }
        )
    for row in product_info[~product_info["exchange_local"].isin(VALID_EXCHANGES)].itertuples(index=False):
        rejected_product_info.append(
            {
                "product": row.product_local,
                "exchange": row.exchange,
                "source": "cn_future_product_info",
                "reason": "invalid_exchange_or_non_china_futures",
            }
        )

    return pd.DataFrame(rows), pd.DataFrame(multi_value_rows), pd.DataFrame(rejected_product_info)


def write_yaml(candidates: pd.DataFrame) -> int:
    YAML_DIR.mkdir(parents=True, exist_ok=True)
    ok = candidates[candidates["status"].isin(["ok", "needs_review"])].copy()
    written = 0
    for row in ok.sort_values(["exchange", "product"]).itertuples(index=False):
        if not row.name or pd.isna(row.position_date_type):
            continue
        payload = {
            "product": row.product,
            "exchange": row.exchange,
            "name": row.name,
            "position_date_type": row.position_date_type,
            "max_margin_side_algorithm": bool(row.max_margin_side_algorithm),
            "combination_type": int(row.combination_type),
        }
        with open(YAML_DIR / f"{row.product}.yaml", "w", encoding="utf-8") as fh:
            yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False)
        written += 1
    return written


def write_report(candidates: pd.DataFrame, multi_values: pd.DataFrame, rejected: pd.DataFrame, yaml_count: int) -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(OUT_ROOT / "candidate_static_meta.csv", index=False, encoding="utf-8-sig")
    multi_values.to_csv(OUT_ROOT / "static_meta_multi_value_fields.csv", index=False, encoding="utf-8-sig")
    rejected.to_csv(OUT_ROOT / "rejected_product_info_codes.csv", index=False, encoding="utf-8-sig")

    by_exchange = candidates[candidates["status"].isin(["ok", "needs_review"])].groupby("exchange")["product"].count()
    lines = [
        "# StaticMeta Ingestion Audit",
        "",
        "- Batch: 20260624",
        "- Raw snapshot: `db/rules_ingestion/data/raw/20260622`",
        "- Variables: `StaticMeta.product`, `StaticMeta.exchange`, `StaticMeta.name`, `StaticMeta.position_date_type`, `StaticMeta.max_margin_side_algorithm`, `StaticMeta.combination_type`",
        f"- Candidate products: {int(candidates[candidates['status'].isin(['ok', 'needs_review'])].shape[0])}",
        f"- Candidate YAML files: {yaml_count}",
        f"- Rejected product-info rows/codes: {int(rejected.shape[0])}",
        f"- Product-field multi-value findings: {int(multi_values.shape[0])}",
        "",
        "## Candidate Products By Exchange",
        "",
    ]
    for exchange, count in by_exchange.items():
        lines.append(f"- {exchange}: {int(count)}")
    lines.extend(
        [
            "",
            "## Policy Applied",
            "",
            "- Only Chinese futures exchanges in the local standard code system are accepted: CFFEX, CZCE, DCE, GFEX, INE, SHFE.",
            "- A product must appear in both BigQuant `cn_future_basic_info` and `cn_future_static_data`.",
            "- Static-data `product_class` must be exactly `1` for the product to be treated as a plain futures product in this batch.",
            "- BigQuant `position_date_type` is mapped as `1 -> distinguish_today_yesterday`, `2 -> undistinguished`.",
            "- For non-versioned fields with multiple historical values, the generated candidate uses the mode on the latest available `trading_day`; the conflict is retained in `static_meta_multi_value_fields.csv` and requires confirmation.",
            "",
            "## Output Files",
            "",
            "- `candidate_static_meta.csv`",
            "- `static_meta_multi_value_fields.csv`",
            "- `rejected_product_info_codes.csv`",
            "- `yaml/*.yaml`",
            "",
        ]
    )
    (OUT_ROOT / "audit_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    product_info = read_product_info()
    basic_info = read_basic_info()
    static_data = read_static_data()
    candidates, multi_values, rejected = build_candidates(product_info, basic_info, static_data)
    yaml_count = write_yaml(candidates)
    write_report(candidates, multi_values, rejected, yaml_count)
    print(f"StaticMeta candidates: {candidates[candidates['status'].isin(['ok', 'needs_review'])].shape[0]}")
    print(f"YAML files written: {yaml_count}")
    print(f"Multi-value findings: {multi_values.shape[0]}")
    print(f"Rejected product-info codes: {rejected.shape[0]}")
    print(f"Output: {OUT_ROOT}")


if __name__ == "__main__":
    main()
