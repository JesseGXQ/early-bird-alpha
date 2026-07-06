from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pandas as pd
from pandas.errors import EmptyDataError


ROOT = Path(__file__).resolve().parents[6]

TUSHARE_RAW_DIR = ROOT / "db" / "market" / "daily_snapshots" / "bar_1d" / "bar_1d_tushare" / "raw" / "fut_daily"
BIGQUANT_LIMIT_PATH = (
    ROOT
    / "db"
    / "market"
    / "daily_snapshots"
    / "upper_lower_price_limits"
    / "cn_future_bar1d_upper_lower_price_limits.parquet"
)
REGISTRY_DIR = ROOT / "db" / "rules_ingestion" / "data" / "derived" / "20260624" / "instrument_registry"
SOURCE_TO_LOCAL_PATH = REGISTRY_DIR / "tushare_ts_code_to_instrument.parquet"
OUTPUT_DIR = Path(__file__).resolve().parent
AUDIT_PATH = OUTPUT_DIR / "AUDIT.md"

EXCHANGES = ["CFFEX", "CZCE", "DCE", "GFEX", "INE", "SHFE"]
BIGQUANT_SUFFIX_TO_EXCHANGE = {
    "CFE": "CFFEX",
    "CZC": "CZCE",
    "DCE": "DCE",
    "GFE": "GFEX",
    "INE": "INE",
    "SHF": "SHFE",
}

TUSHARE_COLUMNS = [
    "ts_code",
    "trade_date",
    "pre_close",
    "pre_settle",
    "open",
    "high",
    "low",
    "close",
    "settle",
    "change1",
    "change2",
    "vol",
    "amount",
    "oi",
    "oi_chg",
]

OUTPUT_COLUMNS = [
    "instrument",
    "date",
    "exchange",
    "product",
    "pre_close",
    "pre_settle",
    "open",
    "high",
    "low",
    "close",
    "settle",
    "change1",
    "change2",
    "vol",
    "amount",
    "oi",
    "oi_chg",
    "upper_limit",
    "lower_limit",
]


def load_registry() -> pd.DataFrame:
    frames = []
    for exchange in EXCHANGES:
        path = REGISTRY_DIR / f"{exchange}.parquet"
        frame = pd.read_parquet(path)
        frames.append(frame)

    registry = pd.concat(frames, ignore_index=True)
    registry["list_date"] = pd.to_datetime(registry["list_date"])
    registry["last_trade_date"] = pd.to_datetime(registry["last_trade_date"])
    registry["exchange_inst_key"] = registry["exchange_inst"].astype(str)
    return registry


def clean_output_dir() -> None:
    output_name_pattern = re.compile(r"^(CFFEX|CZCE|DCE|GFEX|INE|SHFE)_[A-Z0-9]+\.parquet$")
    for path in OUTPUT_DIR.glob("*.parquet"):
        if output_name_pattern.match(path.name):
            path.unlink()
    if AUDIT_PATH.exists():
        AUDIT_PATH.unlink()


def load_tushare_bars(registry: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    source_to_local = pd.read_parquet(SOURCE_TO_LOCAL_PATH)
    source_to_local["ts_code"] = source_to_local["ts_code"].astype(str)
    source_to_local["instrument"] = source_to_local["instrument"].astype(str)

    instrument_meta = registry[["instrument", "exchange", "product"]].drop_duplicates("instrument")
    mapping = source_to_local.merge(instrument_meta, on="instrument", how="left", validate="one_to_one")
    if mapping[["exchange", "product"]].isna().any().any():
        missing = mapping[mapping[["exchange", "product"]].isna().any(axis=1)]
        raise ValueError(f"source-to-local mapping contains instruments absent from registry: {len(missing)}")

    chunks = []
    total_rows = 0
    mapped_rows = 0
    csv_files = sorted(TUSHARE_RAW_DIR.rglob("*.csv"))
    unmapped_counter: Counter[str] = Counter()
    rows_by_source_exchange: Counter[str] = Counter()
    empty_or_unreadable_files: list[str] = []

    for path in csv_files:
        source_exchange = path.parent.name.split("=", 1)[-1]
        try:
            frame = pd.read_csv(path, encoding="utf-8", usecols=TUSHARE_COLUMNS)
        except EmptyDataError:
            empty_or_unreadable_files.append(str(path.relative_to(ROOT)))
            continue
        if frame.empty:
            empty_or_unreadable_files.append(str(path.relative_to(ROOT)))
            continue

        frame["ts_code"] = frame["ts_code"].astype(str)
        total_rows += len(frame)
        rows_by_source_exchange[source_exchange] += len(frame)

        merged = frame.merge(mapping, on="ts_code", how="left", validate="many_to_one")
        mapped_mask = merged["instrument"].notna()
        mapped_rows += int(mapped_mask.sum())

        if not mapped_mask.all():
            unmapped_counter.update(merged.loc[~mapped_mask, "ts_code"].astype(str).tolist())

        merged = merged.loc[mapped_mask].copy()
        if merged.empty:
            continue

        merged["date"] = pd.to_datetime(merged["trade_date"].astype(str), format="%Y%m%d")
        merged = merged.drop(columns=["ts_code", "trade_date"])
        chunks.append(merged)

    if not chunks:
        raise RuntimeError("No mapped Tushare daily bar rows were loaded.")

    bars = pd.concat(chunks, ignore_index=True)
    duplicate_mask = bars.duplicated(["instrument", "date"], keep=False)
    duplicate_groups = int(bars.loc[duplicate_mask, ["instrument", "date"]].drop_duplicates().shape[0])
    duplicate_rows = int(duplicate_mask.sum())
    if duplicate_rows:
        bars = bars.sort_values(["instrument", "date"]).drop_duplicates(["instrument", "date"], keep="last")

    audit = {
        "csv_files": len(csv_files),
        "total_rows": total_rows,
        "mapped_rows": mapped_rows,
        "unmapped_rows": total_rows - mapped_rows,
        "rows_by_source_exchange": dict(sorted(rows_by_source_exchange.items())),
        "top_unmapped_ts_code": unmapped_counter.most_common(50),
        "empty_or_unreadable_files": empty_or_unreadable_files,
        "duplicate_instrument_date_groups": duplicate_groups,
        "duplicate_instrument_date_rows": duplicate_rows,
        "rows_after_duplicate_drop": len(bars),
    }
    return bars, audit


def translate_bigquant_limits(registry: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    limits = pd.read_parquet(BIGQUANT_LIMIT_PATH)
    source_rows = len(limits)
    limits["date"] = pd.to_datetime(limits["date"])
    limits["source_suffix"] = limits["instrument"].astype(str).str.rsplit(".", n=1).str[-1]
    limits["exchange"] = limits["source_suffix"].map(BIGQUANT_SUFFIX_TO_EXCHANGE)
    limits["exchange_inst_key"] = limits["trading_code"].astype(str)

    recognized = limits[limits["exchange"].notna()].copy()
    unrecognized_rows = source_rows - len(recognized)

    registry_key = registry[
        ["instrument", "exchange", "exchange_inst_key", "list_date", "last_trade_date"]
    ].rename(columns={"instrument": "local_instrument"})

    candidates = recognized.reset_index(names="source_row_id").merge(
        registry_key,
        on=["exchange", "exchange_inst_key"],
        how="left",
        validate="many_to_many",
    )
    in_interval = candidates[
        candidates["local_instrument"].notna()
        & (candidates["date"] >= candidates["list_date"])
        & (candidates["date"] <= candidates["last_trade_date"])
    ].copy()

    candidate_counts = in_interval.groupby("source_row_id").size()
    multi_candidate_rows = int((candidate_counts > 1).sum())
    zero_candidate_rows = int(len(recognized) - candidate_counts.index.nunique())
    if multi_candidate_rows:
        raise ValueError(f"BigQuant exchange_inst/date translation is ambiguous for {multi_candidate_rows} rows.")

    translated = in_interval[["local_instrument", "date", "upper_limit", "lower_limit"]].rename(
        columns={"local_instrument": "instrument"}
    )

    conflict_counts = translated.groupby(["instrument", "date"])[["upper_limit", "lower_limit"]].nunique(dropna=False)
    conflicts = conflict_counts[(conflict_counts["upper_limit"] > 1) | (conflict_counts["lower_limit"] > 1)]
    conflict_groups = int(len(conflicts))
    if conflict_groups:
        conflict_keys = conflicts.reset_index()[["instrument", "date"]]
        translated = translated.merge(conflict_keys, on=["instrument", "date"], how="left", indicator=True)
        raise ValueError(f"BigQuant has conflicting limit prices for {conflict_groups} instrument/date groups.")

    duplicate_rows = int(translated.duplicated(["instrument", "date"]).sum())
    translated = translated.drop_duplicates(["instrument", "date"], keep="first")

    audit = {
        "source_rows": source_rows,
        "recognized_exchange_rows": len(recognized),
        "unrecognized_exchange_rows": unrecognized_rows,
        "translated_rows_before_dedupe": len(in_interval),
        "translated_unique_instrument_date_rows": len(translated),
        "zero_candidate_rows": zero_candidate_rows,
        "multi_candidate_rows": multi_candidate_rows,
        "duplicate_translated_rows": duplicate_rows,
        "conflict_instrument_date_groups": conflict_groups,
    }
    return translated, audit


def merge_and_write(
    bars: pd.DataFrame,
    limits: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    merged = bars.merge(limits, on=["instrument", "date"], how="left", validate="one_to_one")
    merged = merged[OUTPUT_COLUMNS].sort_values(["exchange", "product", "instrument", "date"])

    output_rows = []
    for (exchange, product), product_frame in merged.groupby(["exchange", "product"], sort=True):
        file_name = f"{exchange}_{product}.parquet"
        path = OUTPUT_DIR / file_name
        product_frame.to_parquet(path, index=False)
        output_rows.append(
            {
                "file": file_name,
                "exchange": exchange,
                "product": product,
                "rows": len(product_frame),
                "instruments": product_frame["instrument"].nunique(),
                "min_date": product_frame["date"].min(),
                "max_date": product_frame["date"].max(),
                "missing_limit_rows": int(product_frame["upper_limit"].isna().sum()),
            }
        )

    output_summary = pd.DataFrame(output_rows).sort_values(["exchange", "product"]).reset_index(drop=True)
    audit = {
        "merged_rows": len(merged),
        "rows_with_limit_prices": int(merged["upper_limit"].notna().sum()),
        "rows_without_limit_prices": int(merged["upper_limit"].isna().sum()),
        "output_files": len(output_summary),
        "output_rows_by_exchange": merged.groupby("exchange").size().to_dict(),
    }
    return output_summary, audit


def render_audit(
    tushare_audit: dict[str, object],
    bigquant_audit: dict[str, object],
    merge_audit: dict[str, object],
    output_summary: pd.DataFrame,
) -> str:
    top_unmapped = "\n".join(
        f"| `{code}` | {count} |" for code, count in tushare_audit["top_unmapped_ts_code"]
    )
    if not top_unmapped:
        top_unmapped = "| _none_ | 0 |"

    skipped_files = "\n".join(
        f"- `{path}`" for path in tushare_audit["empty_or_unreadable_files"]
    )
    if not skipped_files:
        skipped_files = "- _none_"

    rows_by_exchange = "\n".join(
        f"| {exchange} | {rows} |"
        for exchange, rows in sorted(merge_audit["output_rows_by_exchange"].items())
    )
    output_table = "\n".join(
        "| {file} | {rows} | {instruments} | {min_date} | {max_date} | {missing_limit_rows} |".format(
            file=row.file,
            rows=int(row.rows),
            instruments=int(row.instruments),
            min_date=row.min_date.date(),
            max_date=row.max_date.date(),
            missing_limit_rows=int(row.missing_limit_rows),
        )
        for row in output_summary.itertuples(index=False)
    )

    return f"""# Daily Snapshot Bar 1D Cleaning Audit

Generated by: `clean_fut_daily_snapshots.py`

## Inputs

- Tushare raw directory: `{TUSHARE_RAW_DIR.relative_to(ROOT)}`
- BigQuant limit file: `{BIGQUANT_LIMIT_PATH.relative_to(ROOT)}`
- Source-to-local mapping: `{SOURCE_TO_LOCAL_PATH.relative_to(ROOT)}`
- Instrument registry directory: `{REGISTRY_DIR.relative_to(ROOT)}`

## Decisions

- Tushare daily bars are the base table. BigQuant rows only provide `upper_limit` and `lower_limit`.
- Tushare rows whose `ts_code` is absent from the source-to-local mapping are dropped. These are mainly continuous, index, or main-contract style symbols rather than local plain futures contracts.
- BigQuant `trading_code` is treated as local `exchange_inst`; it is translated by `exchange + exchange_inst + date` using registry listing intervals.
- BigQuant rows that cannot be translated to a local listed contract are dropped. These are mainly `0000`, `8888`, and other non-plain-contract source records.
- Duplicate BigQuant rows mapping to the same `instrument + date` are deduplicated only after verifying that `upper_limit` and `lower_limit` are identical within each group.

## Tushare

| Metric | Value |
| --- | ---: |
| CSV files read | {tushare_audit["csv_files"]} |
| Empty or unreadable CSV files skipped | {len(tushare_audit["empty_or_unreadable_files"])} |
| Source rows | {tushare_audit["total_rows"]} |
| Rows mapped to local instrument | {tushare_audit["mapped_rows"]} |
| Rows dropped as unmapped | {tushare_audit["unmapped_rows"]} |
| Duplicate `instrument + date` rows | {tushare_audit["duplicate_instrument_date_rows"]} |
| Duplicate `instrument + date` groups | {tushare_audit["duplicate_instrument_date_groups"]} |
| Rows after duplicate handling | {tushare_audit["rows_after_duplicate_drop"]} |

Top unmapped Tushare `ts_code` values:

| ts_code | rows |
| --- | ---: |
{top_unmapped}

Skipped empty or unreadable Tushare CSV files:

{skipped_files}

## BigQuant Limits

| Metric | Value |
| --- | ---: |
| Source rows | {bigquant_audit["source_rows"]} |
| Recognized exchange rows | {bigquant_audit["recognized_exchange_rows"]} |
| Unrecognized exchange rows | {bigquant_audit["unrecognized_exchange_rows"]} |
| Rows translated before dedupe | {bigquant_audit["translated_rows_before_dedupe"]} |
| Unique translated `instrument + date` rows | {bigquant_audit["translated_unique_instrument_date_rows"]} |
| Rows without registry/date candidate | {bigquant_audit["zero_candidate_rows"]} |
| Rows with multiple registry/date candidates | {bigquant_audit["multi_candidate_rows"]} |
| Duplicate translated rows removed | {bigquant_audit["duplicate_translated_rows"]} |
| Conflicting translated limit groups | {bigquant_audit["conflict_instrument_date_groups"]} |

## Merge Output

| Metric | Value |
| --- | ---: |
| Final rows | {merge_audit["merged_rows"]} |
| Rows with limit prices | {merge_audit["rows_with_limit_prices"]} |
| Rows without limit prices | {merge_audit["rows_without_limit_prices"]} |
| Output parquet files | {merge_audit["output_files"]} |

Rows by exchange:

| exchange | rows |
| --- | ---: |
{rows_by_exchange}

Output files:

| file | rows | instruments | min_date | max_date | missing limit rows |
| --- | ---: | ---: | --- | --- | ---: |
{output_table}
"""


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    clean_output_dir()

    registry = load_registry()
    bars, tushare_audit = load_tushare_bars(registry)
    limits, bigquant_audit = translate_bigquant_limits(registry)
    output_summary, merge_audit = merge_and_write(bars, limits)

    audit_text = render_audit(tushare_audit, bigquant_audit, merge_audit, output_summary)
    AUDIT_PATH.write_text(audit_text, encoding="utf-8")

    print(f"Tushare rows mapped: {tushare_audit['mapped_rows']} / {tushare_audit['total_rows']}")
    print(f"BigQuant translated unique rows: {bigquant_audit['translated_unique_instrument_date_rows']}")
    print(f"Final rows: {merge_audit['merged_rows']}")
    print(f"Output files: {merge_audit['output_files']}")
    print(f"Audit: {AUDIT_PATH}")


if __name__ == "__main__":
    main()
