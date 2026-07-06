"""
Generate TIER3 order-volume-limit daily status files from BigQuant static data.

Output files are product-level parquet files named {EXCHANGE}_{PRODUCT}.parquet
and contain only:
instrument, trading_date, max_market_order_volume, min_market_order_volume,
max_limit_order_volume, min_limit_order_volume.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path
import sys
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.rules.instrument_registry_manager import InstrumentRegistryManager

RAW_STATIC_DIR = REPO_ROOT / "db/rules_ingestion/data/raw/20260622/bigquant/cn_future_static_data"
RAW_BASIC_INFO_DIR = REPO_ROOT / "db/rules_ingestion/data/raw/20260622/bigquant/cn_future_basic_info"
REGISTRY_DIR = REPO_ROOT / "db/rules_ingestion/data/derived/20260624/instrument_registry"
OUTPUT_DIR = REPO_ROOT / "db/rules_ingestion/data/derived/20260624/instrument_daily_status_order_volume_limits"
MAPPING_PATH = REPO_ROOT / "db/rules_ingestion/data/derived/instrument_code_source_local_mapping.parquet"

VOLUME_COLUMNS = [
    "max_market_order_volume",
    "min_market_order_volume",
    "max_limit_order_volume",
    "min_limit_order_volume",
]
OUTPUT_COLUMNS = ["instrument", "trading_date", *VOLUME_COLUMNS]


def read_bigquant_static_data() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    columns = ["trading_day", "exchange", "product_id", "trading_code", *VOLUME_COLUMNS]
    for path in sorted(RAW_STATIC_DIR.glob("exchange=*/product=*.parquet")):
        df = pd.read_parquet(path, columns=columns)
        if df.empty:
            continue
        df["source_path"] = str(path.relative_to(REPO_ROOT))
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No non-empty BigQuant static_data parquet files found under {RAW_STATIC_DIR}")

    df = pd.concat(frames, ignore_index=True)
    df.insert(0, "raw_row_id", range(len(df)))
    df = df.rename(columns={"exchange": "source_exchange"})
    df["source_product"] = df["product_id"].astype(str).str.upper()
    df["exchange_inst"] = df["trading_code"].astype(str).str.strip()
    df["trading_date"] = pd.to_datetime(df["trading_day"].astype(str), format="%Y%m%d").dt.date
    return df


def read_registry() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    columns = ["instrument", "exchange", "product", "exchange_inst", "list_date", "last_trade_date"]
    for path in sorted(REGISTRY_DIR.glob("*.parquet")):
        if path.name.startswith("tushare_"):
            continue
        df = pd.read_parquet(path, columns=columns)
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No registry parquet files found under {REGISTRY_DIR}")

    df = pd.concat(frames, ignore_index=True)
    df["instrument"] = df["instrument"].astype(str).str.upper()
    df["exchange"] = df["exchange"].astype(str).str.upper()
    df["product"] = df["product"].astype(str).str.upper()
    df["exchange_inst"] = df["exchange_inst"].astype(str)
    df["list_date"] = pd.to_datetime(df["list_date"]).dt.date
    df["last_trade_date"] = pd.to_datetime(df["last_trade_date"]).dt.date
    return df


def read_bigquant_basic_info() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    columns = ["uinstrument", "trading_code"]
    for path in sorted(RAW_BASIC_INFO_DIR.glob("exchange=*.parquet")):
        df = pd.read_parquet(path, columns=columns)
        if df.empty:
            continue
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No BigQuant basic_info parquet files found under {RAW_BASIC_INFO_DIR}")
    df = pd.concat(frames, ignore_index=True)
    df["instrument"] = df["uinstrument"].astype(str).str.split(".", n=1).str[0].str.upper()
    df["trading_code_bigquant"] = df["trading_code"].astype(str).str.strip()
    df = df[df["instrument"].ne("") & df["trading_code_bigquant"].ne("")]
    return df[["instrument", "trading_code_bigquant"]].drop_duplicates()


def map_bigquant_to_registry(raw: pd.DataFrame, registry: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Map BigQuant trading_code to local instrument using registry lifecycle."""
    required = raw[raw["exchange_inst"].ne("")].copy()
    missing_contract_code = raw[raw["exchange_inst"].eq("")].copy()

    merged = required.merge(
        registry.rename(columns={"exchange": "registry_exchange", "product": "registry_product"}),
        on="exchange_inst",
        how="left",
    )
    in_lifecycle = merged[
        (merged["trading_date"] >= merged["list_date"]) & (merged["trading_date"] <= merged["last_trade_date"])
    ].copy()

    match_counts = in_lifecycle.groupby("raw_row_id").size().rename("match_count")
    mapped = in_lifecycle.merge(match_counts, on="raw_row_id", how="left")
    ok = mapped[mapped["match_count"].eq(1)].copy()
    ambiguous = mapped[mapped["match_count"].gt(1)].copy()

    ok_ids = set(ok["raw_row_id"].tolist())
    ambiguous_ids = set(ambiguous["raw_row_id"].tolist())
    unmapped = required[~required["raw_row_id"].isin(ok_ids | ambiguous_ids)].copy()

    skipped_rows: list[pd.DataFrame] = []
    if not missing_contract_code.empty:
        x = missing_contract_code.copy()
        x["reason"] = "missing_contract_code"
        skipped_rows.append(x)
    if not unmapped.empty:
        unmapped["reason"] = "unmapped_contract"
        skipped_rows.append(unmapped)
    if not ambiguous.empty:
        ambiguous["reason"] = "ambiguous_contract_mapping"
        skipped_rows.append(ambiguous)

    skipped = pd.concat(skipped_rows, ignore_index=True) if skipped_rows else pd.DataFrame()
    return ok, skipped


def validate_output_rows(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    duplicate_mask = rows.duplicated(["instrument", "trading_date"], keep=False)
    duplicates = rows[duplicate_mask].copy()
    clean = rows[~duplicate_mask].copy()

    for col in VOLUME_COLUMNS:
        clean[col] = clean[col].astype("int64")

    invalid = clean[
        (clean["max_market_order_volume"] < 0)
        | (clean["min_market_order_volume"] <= 0)
        | (clean["max_limit_order_volume"] <= 0)
        | (clean["min_limit_order_volume"] <= 0)
        | (clean["min_limit_order_volume"] > clean["max_limit_order_volume"])
        | (
            (clean["max_market_order_volume"] > 0)
            & (clean["min_market_order_volume"] > clean["max_market_order_volume"])
        )
    ].copy()
    if not invalid.empty:
        invalid["reason"] = "invalid_order_volume_values"
        clean = clean.drop(index=invalid.index)

    if not duplicates.empty:
        duplicates["reason"] = "duplicate_instrument_trading_date"

    rejects = pd.concat([duplicates, invalid], ignore_index=True) if not duplicates.empty or not invalid.empty else pd.DataFrame()
    return clean, rejects


def write_product_parquets(rows: pd.DataFrame) -> pd.DataFrame:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    file_rows: list[dict[str, Any]] = []
    for (exchange, product), group in rows.groupby(["registry_exchange", "registry_product"], sort=True):
        out = group.sort_values(["instrument", "trading_date"])[OUTPUT_COLUMNS].copy()
        out_path = OUTPUT_DIR / f"{exchange}_{product}.parquet"
        out.to_parquet(out_path, index=False)
        file_rows.append({"file": out_path.name, "exchange": exchange, "product": product, "rows": len(out)})
    return pd.DataFrame(file_rows)


def write_source_local_mapping(registry: pd.DataFrame) -> None:
    base = pd.read_parquet(REGISTRY_DIR / "tushare_ts_code_to_instrument.parquet")
    registry_map = registry[["instrument", "exchange_inst"]].drop_duplicates()

    bigquant_codes = read_bigquant_basic_info().sort_values(["instrument", "trading_code_bigquant"])
    code_counts = bigquant_codes.groupby("instrument")["trading_code_bigquant"].nunique()
    multi_code_instruments = set(code_counts[code_counts > 1].index.tolist())
    if multi_code_instruments:
        raise ValueError(f"Multiple BigQuant trading_code values found for instruments: {sorted(multi_code_instruments)[:20]}")

    out = base.merge(registry_map, on="instrument", how="left").merge(bigquant_codes, on="instrument", how="left")
    if out["exchange_inst"].isna().any():
        missing = out[out["exchange_inst"].isna()]["instrument"].head(20).tolist()
        raise ValueError(f"Missing exchange_inst while building code mapping: {missing}")
    out.to_parquet(MAPPING_PATH, index=False)


def write_audit(
    raw: pd.DataFrame,
    mapped: pd.DataFrame,
    clean: pd.DataFrame,
    skipped: pd.DataFrame,
    rejects: pd.DataFrame,
    file_report: pd.DataFrame,
) -> None:
    skipped_path = OUTPUT_DIR / "unmapped_or_skipped_rows.csv"
    if not skipped.empty or not rejects.empty:
        frames = []
        if not skipped.empty:
            frames.append(skipped)
        if not rejects.empty:
            frames.append(rejects)
        audit_rows = pd.concat(frames, ignore_index=True, sort=False)
        keep_cols = [
            "reason",
            "source_exchange",
            "source_product",
            "trading_day",
            "trading_date",
            "exchange_inst",
            "trading_code",
            "instrument",
            "registry_exchange",
            "registry_product",
            *VOLUME_COLUMNS,
            "source_path",
        ]
        for col in keep_cols:
            if col not in audit_rows.columns:
                audit_rows[col] = None
        audit_rows[keep_cols].to_csv(skipped_path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(skipped_path, index=False, encoding="utf-8-sig")

    skip_counts = Counter()
    if not skipped.empty:
        skip_counts.update(skipped["reason"].value_counts().to_dict())
    if not rejects.empty:
        skip_counts.update(rejects["reason"].value_counts().to_dict())

    lines = [
        "# InstrumentDailyStatus Order Volume Limits Ingestion Audit",
        "",
        "- Raw source: `db/rules_ingestion/data/raw/20260622/bigquant/cn_future_static_data/exchange=*/product=*.parquet`",
        "- Registry source: `db/rules_ingestion/data/derived/20260624/instrument_registry`",
        "- BigQuant basic-info source for code mapping: `db/rules_ingestion/data/raw/20260622/bigquant/cn_future_basic_info/exchange=*.parquet`",
        "- Output directory: `db/rules_ingestion/data/derived/20260624/instrument_daily_status_order_volume_limits`",
        "- Script: `db/rules_ingestion/script/instrument_daily_status/ingest_order_volume_limits.py`",
        "- Code mapping output: `db/rules_ingestion/data/derived/instrument_code_source_local_mapping.parquet`",
        "",
        "## Scope",
        "",
        "Generated product-level parquet files named `{EXCHANGE}_{PRODUCT}.parquet` for TIER3 order-volume-limit fields.",
        "Only BigQuant observed contract-day rows are written; dates outside BigQuant coverage are not forward-filled and are left to downstream joins as nulls.",
        "",
        "## Counts",
        "",
        f"- Raw rows: {len(raw)}",
        f"- Rows mapped through registry lifecycle: {len(mapped)}",
        f"- Rows written: {len(clean)}",
        f"- Output files: {len(file_report)}",
        f"- Skipped/rejected rows: {sum(skip_counts.values())}",
        "",
        "## Skipped / Rejected Rows",
        "",
    ]
    if skip_counts:
        lines.extend(["| reason | rows |", "|---|---:|"])
        for reason, count in sorted(skip_counts.items()):
            lines.append(f"| {reason} | {count} |")
    else:
        lines.append("None.")

    lines.extend(["", "## Output Files", "", "| file | rows |", "|---|---:|"])
    for row in file_report.sort_values("file").itertuples(index=False):
        lines.append(f"| `{row.file}` | {int(row.rows)} |")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- BigQuant `trading_code` is used as the exchange-format contract code and is preserved case-sensitively.",
            "- Mapping uses TIER2 registry `exchange_inst -> instrument`, constrained by `trading_date` and contract lifecycle, matching `InstrumentRegistryManager.translate_code` semantics.",
            "- `instrument_code_source_local_mapping.parquet` uses BigQuant `cn_future_basic_info.trading_code`, not `cn_future_static_data.trading_code`, so it is not limited by the short daily-static-data coverage window.",
            "- `L_F`, `PP_F`, and `V_F` BigQuant rows are expected to be skipped because their `*F` monthly-average style codes are not TIER2 real futures contracts.",
            "- A small number of rows can be skipped when BigQuant reports a contract before the TIER2 registry lifecycle starts.",
            "- `max_market_order_volume = 0` is preserved as an observed value rather than treated as missing.",
            "",
        ]
    )
    (OUTPUT_DIR / "AUDIT.md").write_text("\n".join(lines), encoding="utf-8")


def validate_with_manager_sample(clean: pd.DataFrame) -> None:
    manager = InstrumentRegistryManager(str(REGISTRY_DIR))
    sample = clean.sort_values(["registry_exchange", "registry_product", "instrument", "trading_date"]).head(200)
    for row in sample.itertuples(index=False):
        translated = manager.translate_code(
            direction="exchange_to_internal",
            code=str(row.exchange_inst),
            trading_date=row.trading_date,
        )
        if translated != row.instrument:
            raise AssertionError(
                f"translate_code sample mismatch: {row.exchange_inst} {row.trading_date} -> {translated}, expected {row.instrument}"
            )


def main() -> None:
    raw = read_bigquant_static_data()
    registry = read_registry()
    mapped, skipped = map_bigquant_to_registry(raw, registry)

    mapped = mapped.rename(columns={"instrument": "instrument"})
    clean, rejects = validate_output_rows(mapped)
    validate_with_manager_sample(clean)

    file_report = write_product_parquets(clean)
    write_source_local_mapping(registry)
    write_audit(raw, mapped, clean, skipped, rejects, file_report)

    print(f"Raw rows: {len(raw)}")
    print(f"Mapped rows: {len(mapped)}")
    print(f"Rows written: {len(clean)}")
    print(f"Output files: {len(file_report)}")
    print(f"Skipped rows: {len(skipped) + len(rejects)}")
    print(f"Output: {OUTPUT_DIR}")
    print(f"Mapping: {MAPPING_PATH}")


if __name__ == "__main__":
    main()
