from __future__ import annotations

import importlib.util
import os
import re
import sys
import types
import warnings
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
RAW_DIR = REPO_ROOT / "db/rules_ingestion/data/raw/20260622/tushare/fut_basic"
STATIC_META_YAML_DIR = (
    REPO_ROOT / "db/rules_ingestion/data/derived/20260624/product_meta_static/yaml"
)
OUTPUT_DIR = REPO_ROOT / "db/rules_ingestion/data/derived/20260624/instrument_registry"
AUDIT_PATH = OUTPUT_DIR / "AUDIT.md"
SOURCE_TO_LOCAL_PATH = OUTPUT_DIR / "tushare_ts_code_to_instrument.parquet"

EXCHANGES = ["SHFE", "INE", "DCE", "CZCE", "GFEX", "CFFEX"]
OUTPUT_COLUMNS = [
    "instrument",
    "product",
    "exchange",
    "list_date",
    "last_trade_date",
    "delivery_year",
    "delivery_month",
    "end_deliv_date",
    "exchange_inst",
    "multiplier",
]

CONTRACT_CODE_RE = re.compile(r"^[A-Z]+[0-9]{3,4}$")


def normalize_exchange(exchange: Any) -> str:
    value = str(exchange).strip().upper()
    mapping = {
        "SHF": "SHFE",
        "CZC": "CZCE",
        "ZCE": "CZCE",
        "CFE": "CFFEX",
        "CFX": "CFFEX",
        "GFE": "GFEX",
    }
    return mapping.get(value, value)


def parse_yyyymmdd(value: Any) -> date | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    if pd.isna(parsed):
        parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def parse_d_month(value: Any) -> tuple[int | None, int | None]:
    if pd.isna(value):
        return None, None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if not re.fullmatch(r"[0-9]{6}", text):
        return None, None
    return int(text[:4]), int(text[4:6])


def parse_intish(value: Any) -> int | None:
    if pd.isna(value):
        return None
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    if abs(float(parsed) - round(float(parsed))) > 1e-9:
        return None
    return int(round(float(parsed)))


def source_instrument(row: pd.Series) -> str:
    ts_code = str(row["ts_code"]).strip().upper()
    if "." in ts_code:
        return ts_code.split(".", 1)[0]
    return str(row["symbol"]).strip().upper()


def format_exchange_inst(symbol: Any, exchange: str) -> str:
    value = str(symbol).strip().upper()
    if exchange in {"CFFEX", "CZCE"}:
        return value
    return value.lower()


def rejection_reason(row: pd.Series, instrument: str) -> str | None:
    fut_code = str(row.get("fut_code", "")).strip().upper()
    symbol = str(row.get("symbol", "")).strip().upper()
    ts_code = str(row.get("ts_code", "")).strip().upper()
    if "TAS" in fut_code or symbol.endswith("TAS") or "TAS" in ts_code:
        return "tas_contract_not_plain_futures"
    if not CONTRACT_CODE_RE.fullmatch(instrument):
        return "non_plain_futures_contract_code"
    if row[["list_date", "delist_date", "d_month"]].isna().any():
        return "missing_lifecycle_or_delivery_month"
    return None


def load_static_products() -> set[str]:
    if not STATIC_META_YAML_DIR.exists():
        return set()
    return {path.stem.upper() for path in STATIC_META_YAML_DIR.glob("*.yaml")}


def load_raw() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(RAW_DIR.glob("*.parquet")):
        df = pd.read_parquet(path)
        df["source_file"] = path.name
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No fut_basic parquet files found under {RAW_DIR}")
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="The behavior of DataFrame concatenation with empty or all-NA entries is deprecated.*",
            category=FutureWarning,
        )
        return pd.concat(frames, ignore_index=True)


def build_registry(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []

    for _, row in raw.iterrows():
        instrument = source_instrument(row)
        exchange = normalize_exchange(row["exchange"])
        reason = rejection_reason(row, instrument)
        if reason is not None:
            rejected_rows.append(
                {
                    "ts_code": row.get("ts_code"),
                    "symbol": row.get("symbol"),
                    "instrument_candidate": instrument,
                    "exchange": exchange,
                    "fut_code": row.get("fut_code"),
                    "name": row.get("name"),
                    "reason": reason,
                }
            )
            continue

        product = str(row["fut_code"]).strip().upper()
        delivery_year, delivery_month = parse_d_month(row["d_month"])
        multiplier_source = "multiplier" if exchange == "CFFEX" else "per_unit"
        multiplier = parse_intish(row[multiplier_source])
        out_row = {
            "instrument": instrument,
            "product": product,
            "exchange": exchange,
            "list_date": parse_yyyymmdd(row["list_date"]),
            "last_trade_date": parse_yyyymmdd(row["delist_date"]),
            "delivery_year": delivery_year,
            "delivery_month": delivery_month,
            "end_deliv_date": parse_yyyymmdd(row["last_ddate"]),
            "exchange_inst": format_exchange_inst(row["symbol"], exchange),
            "multiplier": multiplier,
        }

        invalid_reasons: list[str] = []
        if exchange not in EXCHANGES:
            invalid_reasons.append("unsupported_exchange")
        if not instrument.startswith(product):
            invalid_reasons.append("instrument_product_prefix_mismatch")
        for field in [
            "list_date",
            "last_trade_date",
            "delivery_year",
            "delivery_month",
            "exchange_inst",
            "multiplier",
        ]:
            if out_row[field] is None:
                invalid_reasons.append(f"missing_{field}")
        if not out_row["exchange_inst"]:
            invalid_reasons.append("missing_exchange_inst")
        if out_row["delivery_month"] is not None and not (1 <= out_row["delivery_month"] <= 12):
            invalid_reasons.append("invalid_delivery_month")
        if out_row["list_date"] and out_row["last_trade_date"]:
            if out_row["list_date"] > out_row["last_trade_date"]:
                invalid_reasons.append("list_date_after_last_trade_date")
        if out_row["multiplier"] is not None and out_row["multiplier"] <= 0:
            invalid_reasons.append("non_positive_multiplier")

        if invalid_reasons:
            invalid_row = dict(out_row)
            invalid_row["ts_code"] = row.get("ts_code")
            invalid_row["symbol"] = row.get("symbol")
            invalid_row["invalid_reasons"] = ";".join(invalid_reasons)
            invalid_rows.append(invalid_row)
            continue

        rows.append(out_row)
        mapping_rows.append(
            {
                "ts_code": str(row["ts_code"]).strip().upper(),
                "instrument": instrument,
            }
        )

    registry = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    source_to_local = pd.DataFrame(mapping_rows, columns=["ts_code", "instrument"])
    rejected = pd.DataFrame(rejected_rows)
    invalid = pd.DataFrame(invalid_rows)
    if not registry.empty:
        registry = registry.sort_values(["exchange", "product", "delivery_year", "delivery_month", "instrument"])
    if not source_to_local.empty:
        source_to_local = source_to_local.sort_values(["ts_code", "instrument"])
    return registry, source_to_local, rejected, invalid


def write_outputs(registry: pd.DataFrame, source_to_local: pd.DataFrame) -> dict[str, int]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for exchange in EXCHANGES:
        part = registry.loc[registry["exchange"] == exchange, OUTPUT_COLUMNS].copy()
        path = OUTPUT_DIR / f"{exchange}.parquet"
        part.to_parquet(path, index=False)
        counts[exchange] = len(part)
    source_to_local.to_parquet(SOURCE_TO_LOCAL_PATH, index=False)
    return counts


def install_isolated_data_rules_modules() -> None:
    data_mod = types.ModuleType("data")
    data_mod.__path__ = [str(REPO_ROOT / "data")]
    rules_mod = types.ModuleType("data.rules")
    rules_mod.__path__ = [str(REPO_ROOT / "data/rules")]
    sys.modules["data"] = data_mod
    sys.modules["data.rules"] = rules_mod

    models_path = REPO_ROOT / "data/rules/models.py"
    models_spec = importlib.util.spec_from_file_location("data.rules.models", models_path)
    if models_spec is None or models_spec.loader is None:
        raise RuntimeError(f"Cannot load {models_path}")
    models_module = importlib.util.module_from_spec(models_spec)
    sys.modules["data.rules.models"] = models_module
    models_spec.loader.exec_module(models_module)


def validate_with_manager(registry: pd.DataFrame) -> dict[str, Any]:
    install_isolated_data_rules_modules()
    manager_path = REPO_ROOT / "data/rules/instrument_registry_manager.py"
    manager_spec = importlib.util.spec_from_file_location(
        "data.rules.instrument_registry_manager", manager_path
    )
    if manager_spec is None or manager_spec.loader is None:
        raise RuntimeError(f"Cannot load {manager_path}")
    manager_module = importlib.util.module_from_spec(manager_spec)
    sys.modules["data.rules.instrument_registry_manager"] = manager_module
    manager_spec.loader.exec_module(manager_module)
    manager = manager_module.InstrumentRegistryManager(str(OUTPUT_DIR))

    samples: dict[str, dict[str, Any]] = {}
    old_cwd = Path.cwd()
    try:
        os.chdir(OUTPUT_DIR)
        for exchange, part in registry.groupby("exchange"):
            sample_instrument = str(part.iloc[0]["instrument"])
            samples[exchange] = asdict(manager.get_instrument_registry(sample_instrument))
        # 饿汉式加载：manager 构造完成时已加载全部数据，此处直接取行数
        loaded_rows = len(manager._registry_df)
    finally:
        os.chdir(old_cwd)
    return {"loaded_rows": loaded_rows, "sample_objects": samples}


def markdown_table(df: pd.DataFrame, columns: list[str], max_rows: int = 12) -> str:
    if df.empty:
        return "_None._"
    clipped = df.loc[:, columns].head(max_rows).copy()
    return clipped.to_markdown(index=False)


def write_audit(
    raw: pd.DataFrame,
    registry: pd.DataFrame,
    source_to_local: pd.DataFrame,
    rejected: pd.DataFrame,
    invalid: pd.DataFrame,
    output_counts: dict[str, int],
    manager_validation: dict[str, Any],
    static_products: set[str],
) -> None:
    products = set(registry["product"].unique())
    missing_static_products = sorted(products - static_products)
    duplicate_instruments = registry.loc[
        registry["instrument"].duplicated(keep=False), ["instrument", "product", "exchange"]
    ]
    exchange_inst_mismatch = registry.loc[
        registry["instrument"] != registry["exchange_inst"],
        ["instrument", "exchange_inst", "product", "exchange", "delivery_year", "delivery_month"],
    ]
    duplicate_exchange_inst = registry.loc[
        registry.duplicated(["exchange", "exchange_inst"], keep=False),
        ["instrument", "exchange_inst", "product", "exchange", "delivery_year", "delivery_month"],
    ].sort_values(["exchange", "exchange_inst", "instrument"])
    exchange_inst_case_violations = registry.loc[
        (
            registry["exchange"].isin(["CFFEX", "CZCE"])
            & (registry["exchange_inst"] != registry["exchange_inst"].str.upper())
        )
        | (
            registry["exchange"].isin(["SHFE", "DCE", "GFEX", "INE"])
            & (registry["exchange_inst"] != registry["exchange_inst"].str.lower())
        ),
        ["instrument", "exchange_inst", "product", "exchange", "delivery_year", "delivery_month"],
    ]
    symbol_duplicates = raw.assign(instrument_candidate=raw.apply(source_instrument, axis=1))
    raw_symbol_duplicates = symbol_duplicates.loc[
        symbol_duplicates["symbol"].astype(str).str.upper().duplicated(keep=False),
        ["ts_code", "symbol", "exchange", "fut_code", "name"],
    ].sort_values(["symbol", "ts_code"])

    rejected_counts = (
        rejected["reason"].value_counts().rename_axis("reason").reset_index(name="rows")
        if not rejected.empty
        else pd.DataFrame(columns=["reason", "rows"])
    )
    exchange_counts = pd.DataFrame(
        [{"exchange": exchange, "rows": count} for exchange, count in output_counts.items()]
    )

    lines = [
        "# Instrument Registry TIER2 Ingestion Audit",
        "",
        "- Run date: 2026-06-27",
        f"- Raw source: `{RAW_DIR.relative_to(REPO_ROOT)}`",
        f"- Output directory: `{OUTPUT_DIR.relative_to(REPO_ROOT)}`",
        f"- Source-to-local mapping: `{SOURCE_TO_LOCAL_PATH.relative_to(REPO_ROOT)}`",
        "- Script: `db/rules_ingestion/script/instrument_registry/ingest_instrument_registry.py`",
        "",
        "## Scope",
        "",
        "Generated one registry parquet per exchange for the current `InstrumentRegistry` model fields: "
        "`instrument`, `product`, `exchange`, `list_date`, `last_trade_date`, "
        "`delivery_year`, `delivery_month`, `end_deliv_date`, `exchange_inst`, `multiplier`.",
        "",
        "## Inputs And Outputs",
        "",
        f"- Raw rows read: {len(raw)}",
        f"- Accepted registry rows: {len(registry)}",
        f"- Source-to-local mapping rows: {len(source_to_local)}",
        f"- Rejected rows: {len(rejected)}",
        f"- Invalid accepted-candidate rows dropped after field validation: {len(invalid)}",
        "",
        markdown_table(exchange_counts, ["exchange", "rows"]),
        "",
        "## Plan Deviations And Operator Judgement",
        "",
        "1. `instrument` was generated from the `ts_code` prefix before the dot instead of always using "
        "`symbol`. This is required because CZCE `symbol` collapses decade information and creates "
        "duplicate local instruments, for example `CF1001.ZCE` and `CF2001.ZCE` both have `symbol=CF001`. "
        "For most non-CZCE records the `ts_code` prefix equals `symbol`; for CZCE it preserves the full "
        "delivery year and satisfies the manager's all-market uniqueness constraint.",
        "2. `exchange_inst` was derived from Tushare `symbol`, then normalized to the exchange order-code "
        "case convention: CFFEX/CZCE product letters stay uppercase, while SHFE/DCE/GFEX/INE product "
        "letters are lowercase. It is the source exchange/order-code field, while `instrument` remains "
        "the local all-market primary key. Repeated `exchange_inst` values are expected for CZCE "
        "cross-decade contracts and are not treated as validation failures.",
        "3. Plain futures contracts were accepted only when the canonical instrument matched "
        "`^[A-Z]+[0-9]{3,4}$`, with an explicit TAS exclusion because INE TAS contracts such as "
        "`SCTAS2102` otherwise look like ordinary letter-plus-digit instruments after `ts_code` "
        "normalization. This drops main, continuous, index-like, DCE month-average (`*F`) and INE TAS "
        "records from the registry universe.",
        "4. Products present in Tushare ordinary futures contracts but absent from the current TIER1 "
        "static YAML set were retained in TIER2 and listed below for audit. The plan states this as a "
        "validation expectation, but TIER2's modified plan also defines Tushare `fut_basic` as the "
        "universe source. Dropping historical or newly listed ordinary contracts here would make the "
        "registry less faithful to its only source.",
        "",
        "## Rejection Summary",
        "",
        markdown_table(rejected_counts, ["reason", "rows"]),
        "",
        "Rejected-row samples:",
        "",
        markdown_table(
            rejected,
            ["ts_code", "symbol", "instrument_candidate", "exchange", "fut_code", "name", "reason"],
        ),
        "",
        "## Validation",
        "",
        f"- Duplicate output instruments: {len(duplicate_instruments)}",
        f"- Duplicate mapping `ts_code`: {int(source_to_local['ts_code'].duplicated().sum())}",
        f"- Duplicate mapping `instrument`: {int(source_to_local['instrument'].duplicated().sum())}",
        f"- Empty `exchange_inst`: {int(registry['exchange_inst'].isna().sum() + (registry['exchange_inst'] == '').sum())}",
        f"- `exchange_inst` case convention violations: {len(exchange_inst_case_violations)}",
        f"- Rows where `instrument != exchange_inst`: {len(exchange_inst_mismatch)}",
        f"- Rows whose `(exchange, exchange_inst)` is duplicated: {len(duplicate_exchange_inst)}",
        f"- Manager loaded rows: {manager_validation['loaded_rows']}",
        f"- Expected loaded rows: {len(registry)}",
        f"- Output columns: `{', '.join(OUTPUT_COLUMNS)}`",
        f"- Products in output: {len(products)}",
        f"- Products missing current TIER1 static YAML: {', '.join(missing_static_products) if missing_static_products else 'None'}",
        "",
        "Raw `symbol` duplicates were observed before using `ts_code` as the canonical instrument. Sample:",
        "",
        markdown_table(raw_symbol_duplicates, ["ts_code", "symbol", "exchange", "fut_code", "name"]),
        "",
        "`instrument != exchange_inst` samples:",
        "",
        markdown_table(
            exchange_inst_mismatch,
            ["instrument", "exchange_inst", "product", "exchange", "delivery_year", "delivery_month"],
        ),
        "",
        "`exchange_inst` case convention violation samples:",
        "",
        markdown_table(
            exchange_inst_case_violations,
            ["instrument", "exchange_inst", "product", "exchange", "delivery_year", "delivery_month"],
        ),
        "",
        "Duplicated `(exchange, exchange_inst)` samples:",
        "",
        markdown_table(
            duplicate_exchange_inst,
            ["instrument", "exchange_inst", "product", "exchange", "delivery_year", "delivery_month"],
        ),
        "",
        "Invalid accepted-candidate rows:",
        "",
        markdown_table(
            invalid,
            [
                "ts_code",
                "symbol",
                "instrument",
                "product",
                "exchange",
                "exchange_inst",
                "invalid_reasons",
            ],
        ),
        "",
        "Manager sample objects:",
        "",
        "```text",
    ]
    for exchange, sample in manager_validation["sample_objects"].items():
        lines.append(f"{exchange}: {sample}")
    lines.extend(["```", ""])

    AUDIT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    raw = load_raw()
    static_products = load_static_products()
    registry, source_to_local, rejected, invalid = build_registry(raw)

    duplicate_instruments = registry["instrument"].duplicated(keep=False)
    if duplicate_instruments.any():
        duplicated = registry.loc[duplicate_instruments, "instrument"].head(20).tolist()
        raise ValueError(f"Duplicate output instruments remain: {duplicated}")
    if not invalid.empty:
        invalid_preview = invalid.head(20).to_dict("records")
        raise ValueError(f"Invalid accepted-candidate rows remain: {invalid_preview}")

    if source_to_local["ts_code"].duplicated().any():
        duplicated = source_to_local.loc[
            source_to_local["ts_code"].duplicated(keep=False), "ts_code"
        ].head(20).tolist()
        raise ValueError(f"Duplicate source ts_code values remain in mapping: {duplicated}")
    if source_to_local["instrument"].duplicated().any():
        duplicated = source_to_local.loc[
            source_to_local["instrument"].duplicated(keep=False), "instrument"
        ].head(20).tolist()
        raise ValueError(f"Duplicate local instrument values remain in mapping: {duplicated}")

    output_counts = write_outputs(registry, source_to_local)
    manager_validation = validate_with_manager(registry)
    if manager_validation["loaded_rows"] != len(registry):
        raise ValueError(
            f"Manager loaded {manager_validation['loaded_rows']} rows, expected {len(registry)}"
        )

    write_audit(
        raw=raw,
        registry=registry,
        source_to_local=source_to_local,
        rejected=rejected,
        invalid=invalid,
        output_counts=output_counts,
        manager_validation=manager_validation,
        static_products=static_products,
    )

    print(f"Wrote {len(registry)} registry rows to {OUTPUT_DIR}")
    print(f"Wrote audit to {AUDIT_PATH}")


if __name__ == "__main__":
    main()
