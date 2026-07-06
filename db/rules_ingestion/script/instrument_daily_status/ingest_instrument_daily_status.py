from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
RAW_ROOT = REPO_ROOT / "db/rules_ingestion/data/raw/exchange_settlement_data_scraping"
REGISTRY_DIR = REPO_ROOT / "db/rules_ingestion/data/derived/20260624/instrument_registry"
CALENDAR_DIR = REPO_ROOT / "db/rules_ingestion/data/raw/20260622/tushare/fut_trade_cal"
OUTPUT_DIR = REPO_ROOT / "db/rules_ingestion/data/derived/20260624/instrument_daily_status"
AUDIT_PATH = OUTPUT_DIR / "AUDIT.md"
UNMAPPED_PATH = OUTPUT_DIR / "unmapped_or_skipped_rows.csv"

EXCHANGES = ["CFFEX", "CZCE", "DCE", "GFEX", "SHFE"]
OUTPUT_COLUMNS = [
    "trading_date",
    "instrument",
    "is_tradable",
    "inst_life_phase",
    "long_margin_ratio",
    "short_margin_ratio",
    "commission_type",
    "commission_open",
    "commission_open_today",
    "commission_close",
    "commission_close_today",
]


def parse_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    if pd.isna(parsed):
        parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def parse_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "nan", "None"}:
        return None
    if text.endswith("%"):
        text = text[:-1]
        value = pd.to_numeric(text, errors="coerce")
        return None if pd.isna(value) else float(value) / 100.0
    value = pd.to_numeric(text, errors="coerce")
    return None if pd.isna(value) else float(value)


def is_nonzero(value: Any) -> bool:
    parsed = parse_float(value)
    return parsed is not None and abs(parsed) > 1e-15


def parse_percent_or_ratio(value: Any) -> float | None:
    return parse_float(value)


def normalize_exchange_inst(value: Any, exchange: str) -> str:
    text = str(value).strip()
    if not text:
        return ""
    text = re.sub(r"\s+", "", text)
    if exchange in {"CFFEX", "CZCE"}:
        return text.upper()
    return text.lower()


def product_from_exchange_inst(exchange_inst: str) -> str:
    match = re.match(r"([A-Za-z]+)", str(exchange_inst).strip())
    return match.group(1).upper() if match else ""


def first_present(row: dict[str, Any], names: list[str]) -> Any:
    for name in names:
        if name in row and row[name] is not None and not pd.isna(row[name]):
            return row[name]
    return None


def first_present_pair(row: dict[str, Any], names: list[str]) -> tuple[str | None, Any]:
    for name in names:
        if name in row and row[name] is not None and not pd.isna(row[name]):
            return name, row[name]
    return None, None


def read_records_json(day_dir: Path) -> list[dict[str, Any]]:
    record_files = sorted(day_dir.glob("*_records.json"))
    if not record_files:
        return []
    data = json.loads(record_files[0].read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        rows = data.get("records") or data.get("data") or []
        return rows if isinstance(rows, list) else []
    return []


def read_csv_records(day_dir: Path) -> list[dict[str, Any]]:
    csv_files = sorted(day_dir.glob("*.csv"))
    if not csv_files:
        return []
    parsed = [p for p in csv_files if "parsed" in p.name.lower()]
    csv_path = parsed[0] if parsed else csv_files[0]
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return pd.read_csv(csv_path, encoding=encoding).to_dict("records")
        except Exception as exc:  # pragma: no cover - audit path
            last_error = exc
    raise RuntimeError(f"Failed to read CSV {csv_path}: {last_error}")


def read_day_records(exchange: str, day_dir: Path) -> list[dict[str, Any]]:
    if exchange in {"CFFEX", "DCE", "GFEX"}:
        rows = read_records_json(day_dir)
        if rows:
            return rows
    return read_csv_records(day_dir)


class RegistryMapper:
    def __init__(self, registry_dir: Path):
        dfs: list[pd.DataFrame] = []
        for path in sorted(registry_dir.glob("*.parquet")):
            if path.name.startswith("tushare_"):
                continue
            df = pd.read_parquet(path)
            dfs.append(df)
        if not dfs:
            raise FileNotFoundError(f"No registry parquet files found under {registry_dir}")
        self.df = pd.concat(dfs, ignore_index=True)
        self.df["list_date"] = pd.to_datetime(self.df["list_date"]).dt.date
        self.df["last_trade_date"] = pd.to_datetime(self.df["last_trade_date"]).dt.date
        self.df["exchange_inst"] = self.df["exchange_inst"].astype(str)
        self.df["instrument"] = self.df["instrument"].astype(str).str.upper()
        self.df["product"] = self.df["product"].astype(str).str.upper()
        self.df["exchange"] = self.df["exchange"].astype(str).str.upper()
        self.by_instrument = self.df.set_index("instrument", drop=False)
        self.by_source_exchange_inst: dict[tuple[str, str], list[pd.Series]] = defaultdict(list)
        for _, row in self.df.iterrows():
            exchange = str(row["exchange"])
            exchange_inst = str(row["exchange_inst"])
            self.by_source_exchange_inst[(exchange, exchange_inst)].append(row)
            if exchange == "INE":
                self.by_source_exchange_inst[("SHFE", exchange_inst)].append(row)

    def translate(self, source_exchange: str, exchange_inst: str, trading_date: date) -> pd.Series | None:
        candidates = self.by_source_exchange_inst.get((source_exchange, exchange_inst), [])
        matched = [
            row
            for row in candidates
            if row["list_date"] <= trading_date <= row["last_trade_date"]
        ]
        if len(matched) == 1:
            return matched[0]
        return None

    def cffex_registry(self) -> pd.DataFrame:
        return self.df[self.df["exchange"].eq("CFFEX")].copy()


def parse_cffex_commission(text: Any) -> tuple[str | None, float | None]:
    value = str(text).strip()
    if not value or value.lower() == "nan":
        return None, None
    number_match = re.search(r"[-+]?[0-9]+(?:\.[0-9]+)?", value)
    if not number_match:
        if "免" in value:
            return "flat", 0.0
        return None, None
    number = float(number_match.group(0))
    if "万分" in value:
        return "ratio", number / 10000.0
    if "%" in value:
        return "ratio", number / 100.0
    if "元" in value:
        return "flat", number
    return "flat", number


def parse_cffex_close_today(base: float, rate_text: Any) -> float | None:
    if rate_text is None or pd.isna(rate_text):
        return None
    text = str(rate_text).strip()
    if not text:
        return None
    if "免" in text:
        return 0.0
    value = parse_float(text)
    if value is None:
        return None
    return base * value


def parse_style_fee(value: Any, style: Any) -> tuple[str | None, float | None]:
    amount = parse_float(value)
    if amount is None:
        return None, None
    style_text = str(style).strip()
    if "比例" in style_text or "ratio" in style_text.lower():
        return "ratio", amount / 10000.0
    if "绝对" in style_text or "absolute" in style_text.lower():
        return "flat", amount
    return None, None


def parse_czce_fee(value: Any, fee_type: Any) -> tuple[str | None, float | None]:
    amount = parse_float(value)
    if amount is None:
        return None, None
    fee_type_text = "" if fee_type is None or pd.isna(fee_type) else str(fee_type).strip()
    if "比例" in fee_type_text:
        return "ratio", amount / 10000.0
    if "绝对" in fee_type_text:
        return "flat", amount
    return "flat", amount


def parse_czce_margin(row: dict[str, Any], names: list[str]) -> float | None:
    name, value = first_present_pair(row, names)
    parsed = parse_float(value)
    if parsed is None:
        return None
    if name and "(%)" in name:
        return parsed / 100.0
    return parsed


def parse_shfe_fee(row: dict[str, Any]) -> tuple[str | None, float | None]:
    if is_nonzero(row.get("TRADEFEERATION")):
        return "ratio", parse_float(row.get("TRADEFEERATION"))
    if is_nonzero(row.get("TRADEFEEUNIT")):
        return "flat", parse_float(row.get("TRADEFEEUNIT"))
    return None, None


def output_row(
    trading_date: date,
    instrument: str,
    long_margin_ratio: float | None,
    short_margin_ratio: float | None,
    commission_type: str | None,
    commission_open: float | None,
    commission_open_today: float | None,
    commission_close: float | None,
    commission_close_today: float | None,
) -> dict[str, Any] | None:
    required = [
        long_margin_ratio,
        short_margin_ratio,
        commission_type,
        commission_open,
        commission_close,
    ]
    if any(x is None for x in required):
        return None
    if not (0 < float(long_margin_ratio) < 1 and 0 < float(short_margin_ratio) < 1):
        return None
    if commission_type not in {"flat", "ratio"}:
        return None
    return {
        "trading_date": trading_date,
        "instrument": instrument,
        "is_tradable": None,
        "inst_life_phase": None,
        "long_margin_ratio": float(long_margin_ratio),
        "short_margin_ratio": float(short_margin_ratio),
        "commission_type": commission_type,
        "commission_open": float(commission_open),
        "commission_open_today": None if commission_open_today is None else float(commission_open_today),
        "commission_close": float(commission_close),
        "commission_close_today": None if commission_close_today is None else float(commission_close_today),
    }


def parse_non_cffex_record(
    source_exchange: str,
    trading_date: date,
    raw: dict[str, Any],
    mapper: RegistryMapper,
) -> tuple[dict[str, Any] | None, str, str]:
    if source_exchange == "CZCE":
        raw_inst = raw.get("合约代码")
        exchange_inst = normalize_exchange_inst(raw_inst, source_exchange)
    elif source_exchange in {"DCE", "GFEX"}:
        raw_inst = raw.get("contractId")
        exchange_inst = normalize_exchange_inst(raw_inst, source_exchange)
    elif source_exchange == "SHFE":
        raw_inst = raw.get("INSTRUMENTID")
        exchange_inst = normalize_exchange_inst(raw_inst, source_exchange)
    else:
        return None, "", "unsupported_exchange"

    if not exchange_inst:
        return None, "", "missing_contract_code"
    if not re.fullmatch(r"[A-Za-z]+[0-9]{3,4}", exchange_inst):
        return None, exchange_inst, "non_contract_row"

    registry_row = mapper.translate(source_exchange, exchange_inst, trading_date)
    if registry_row is None:
        return None, exchange_inst, "unmapped_contract"

    if source_exchange == "CZCE":
        long_margin_ratio = parse_czce_margin(
            raw, ["买交易保证金率(%)", "交易保证金率(%)", "买交易保证金率", "交易保证金率"]
        )
        short_margin_ratio = parse_czce_margin(
            raw, ["卖交易保证金率(%)", "交易保证金率(%)", "卖交易保证金率", "交易保证金率"]
        )
        fee_type = raw.get("手续费收取方式")
        commission_type, commission_open = parse_czce_fee(raw.get("交易手续费"), fee_type)
        commission_close = commission_open
        close_today_raw = first_present(raw, ["日内平今仓交易手续费", "日内平今仓手续费", "平今仓手续费"])
        _, commission_close_today = parse_czce_fee(close_today_raw, fee_type)
        commission_open_today = None
    elif source_exchange in {"DCE", "GFEX"}:
        long_margin_ratio = parse_float(raw.get("specBuyRate"))
        short_margin_ratio = parse_float(raw.get("specSellRate"))
        commission_type, commission_open = parse_style_fee(raw.get("openFee"), raw.get("style"))
        _, commission_close = parse_style_fee(raw.get("offsetFee"), raw.get("style"))
        _, commission_open_today = parse_style_fee(raw.get("shortOpenFee"), raw.get("style"))
        _, commission_close_today = parse_style_fee(raw.get("shortOffsetFee"), raw.get("style"))
    else:
        long_margin_ratio = parse_float(raw.get("SPEC_LONGMARGINRATIO"))
        short_margin_ratio = parse_float(raw.get("SPEC_SHORTMARGINRATIO"))
        commission_type, commission_open = parse_shfe_fee(raw)
        commission_close = commission_open
        discount = parse_float(raw.get("DISCOUNTRATE"))
        commission_close_today = None if discount is None or commission_open is None else commission_open * discount
        commission_open_today = None

    row = output_row(
        trading_date=trading_date,
        instrument=str(registry_row["instrument"]),
        long_margin_ratio=long_margin_ratio,
        short_margin_ratio=short_margin_ratio,
        commission_type=commission_type,
        commission_open=commission_open,
        commission_open_today=commission_open_today,
        commission_close=commission_close,
        commission_close_today=commission_close_today,
    )
    if row is None:
        return None, exchange_inst, "missing_required_values"
    row["_exchange"] = str(registry_row["exchange"])
    row["_product"] = str(registry_row["product"])
    return row, exchange_inst, "ok"


def load_calendar(exchange: str) -> list[date]:
    path = CALENDAR_DIR / f"exchange={exchange}.parquet"
    df = pd.read_parquet(path)
    df = df[df["is_open"].eq(1)].copy()
    return sorted(parse_date(x) for x in df["cal_date"] if parse_date(x) is not None)


def ingest_cffex(mapper: RegistryMapper, skipped: list[dict[str, Any]], stats: Counter) -> list[dict[str, Any]]:
    source_exchange = "CFFEX"
    raw_rows: list[dict[str, Any]] = []
    for day_dir in sorted((RAW_ROOT / "exchange=CFFEX").glob("trading_day=*")):
        trading_date = parse_date(day_dir.name.split("=", 1)[1])
        if trading_date is None:
            continue
        for raw in read_day_records(source_exchange, day_dir):
            stats["raw_rows_CFFEX"] += 1
            exchange_inst = normalize_exchange_inst(raw.get("期货合约"), source_exchange)
            if not re.fullmatch(r"[A-Za-z]+[0-9]{3,4}", exchange_inst):
                skipped.append(skip_record(source_exchange, trading_date, exchange_inst, "non_contract_row"))
                stats["skipped_non_contract_row"] += 1
                continue
            registry_row = mapper.translate(source_exchange, exchange_inst, trading_date)
            if registry_row is None:
                skipped.append(skip_record(source_exchange, trading_date, exchange_inst, "unmapped_contract"))
                stats["skipped_unmapped_contract"] += 1
                continue
            commission_type, commission_open = parse_cffex_commission(raw.get("交易手续费标准"))
            row = output_row(
                trading_date=trading_date,
                instrument=str(registry_row["instrument"]),
                long_margin_ratio=parse_float(
                    first_present(raw, ["合约多头保证金标准", "合约多头保证金"])
                ),
                short_margin_ratio=parse_float(
                    first_present(raw, ["合约空头保证金标准", "合约空头保证金"])
                ),
                commission_type=commission_type,
                commission_open=commission_open,
                commission_open_today=None,
                commission_close=commission_open,
                commission_close_today=None
                if commission_open is None
                else parse_cffex_close_today(commission_open, raw.get("平今仓收取率")),
            )
            if row is None:
                skipped.append(skip_record(source_exchange, trading_date, exchange_inst, "missing_required_values"))
                stats["skipped_missing_required_values"] += 1
                continue
            row["_effective_date"] = trading_date
            row["_exchange"] = str(registry_row["exchange"])
            row["_product"] = str(registry_row["product"])
            raw_rows.append(row)

    if not raw_rows:
        return []

    params = pd.DataFrame(raw_rows)
    params["trading_date"] = pd.to_datetime(params["trading_date"]).dt.date
    calendar = load_calendar("CFFEX")
    registry = mapper.cffex_registry()
    max_param_date = max(params["trading_date"])
    expanded: list[dict[str, Any]] = []
    params_by_inst = {
        inst: group.sort_values("trading_date").to_dict("records")
        for inst, group in params.groupby("instrument")
    }
    for _, reg in registry.iterrows():
        inst = str(reg["instrument"])
        inst_params = params_by_inst.get(inst)
        if not inst_params:
            stats["cffex_registry_without_params"] += 1
            continue
        dates = [
            d
            for d in calendar
            if reg["list_date"] <= d <= reg["last_trade_date"] and d <= max_param_date
        ]
        param_i = 0
        current: dict[str, Any] | None = None
        for d in dates:
            while param_i < len(inst_params) and inst_params[param_i]["trading_date"] <= d:
                current = inst_params[param_i]
                param_i += 1
            if current is None:
                continue
            out = {col: current[col] for col in OUTPUT_COLUMNS}
            out["trading_date"] = d
            out["_exchange"] = str(reg["exchange"])
            out["_product"] = str(reg["product"])
            expanded.append(out)
    stats["cffex_parameter_rows"] = len(raw_rows)
    stats["cffex_expanded_rows"] = len(expanded)
    return expanded


def skip_record(exchange: str, trading_date: date | None, exchange_inst: str, reason: str) -> dict[str, Any]:
    return {
        "exchange": exchange,
        "trading_date": trading_date,
        "exchange_inst": exchange_inst,
        "reason": reason,
    }


def ingest_non_cffex(mapper: RegistryMapper, skipped: list[dict[str, Any]], stats: Counter) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source_exchange in ["CZCE", "DCE", "GFEX", "SHFE"]:
        exdir = RAW_ROOT / f"exchange={source_exchange}"
        for day_dir in sorted(exdir.glob("trading_day=*")):
            trading_date = parse_date(day_dir.name.split("=", 1)[1])
            if trading_date is None:
                continue
            for raw in read_day_records(source_exchange, day_dir):
                stats[f"raw_rows_{source_exchange}"] += 1
                row, exchange_inst, reason = parse_non_cffex_record(
                    source_exchange, trading_date, raw, mapper
                )
                if row is None:
                    skipped.append(skip_record(source_exchange, trading_date, exchange_inst, reason))
                    stats[f"skipped_{reason}"] += 1
                    continue
                output.append(row)
    return output


def write_outputs(rows: list[dict[str, Any]]) -> dict[str, int]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for old_path in OUTPUT_DIR.glob("*.parquet"):
        old_path.unlink()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        exchange = row.pop("_exchange")
        product = row.pop("_product")
        row.pop("_effective_date", None)
        grouped[(exchange, product)].append(row)

    file_rows: dict[str, int] = {}
    for (exchange, product), records in sorted(grouped.items()):
        df = pd.DataFrame(records)
        df = df[OUTPUT_COLUMNS]
        df = df.drop_duplicates(["trading_date", "instrument"], keep="last")
        df = df.sort_values(["trading_date", "instrument"]).reset_index(drop=True)
        path = OUTPUT_DIR / f"{exchange}_{product}.parquet"
        df.to_parquet(path, index=False)
        file_rows[path.name] = len(df)
    return file_rows


def write_audit(stats: Counter, file_rows: dict[str, int], skipped: list[dict[str, Any]]) -> None:
    skipped_counter = Counter(row["reason"] for row in skipped)
    lines = [
        "# Instrument Daily Status TIER3 Ingestion Audit",
        "",
        "- Raw source: `db/rules_ingestion/data/raw/exchange_settlement_data_scraping/exchange={EXCHANGE}`",
        "- Registry source: `db/rules_ingestion/data/derived/20260624/instrument_registry`",
        "- Output directory: `db/rules_ingestion/data/derived/20260624/instrument_daily_status`",
        "- Script: `db/rules_ingestion/script/instrument_daily_status/ingest_instrument_daily_status.py`",
        "",
        "## Scope",
        "",
        "Generated product-level parquet files named `{EXCHANGE}_{PRODUCT}.parquet` for `InstrumentDailyStatus` fields. "
        "The ingestion used raw exchange settlement directories only; unfinished `cleaned/` files were not used.",
        "",
        "## Raw Rows",
        "",
    ]
    for key in sorted(k for k in stats if k.startswith("raw_rows_")):
        lines.append(f"- {key.removeprefix('raw_rows_')}: {stats[key]}")
    lines.extend(
        [
            "",
            "## Output",
            "",
            f"- Output files: {len(file_rows)}",
            f"- Output rows: {sum(file_rows.values())}",
            "",
            "| file | rows |",
            "|---|---:|",
        ]
    )
    for name, count in sorted(file_rows.items()):
        lines.append(f"| `{name}` | {count} |")
    lines.extend(["", "## Skipped Rows", ""])
    if skipped_counter:
        lines.extend(["| reason | rows |", "|---|---:|"])
        for reason, count in sorted(skipped_counter.items()):
            lines.append(f"| {reason} | {count} |")
        lines.append("")
        lines.append(f"Detailed skipped rows are saved to `{UNMAPPED_PATH.name}`.")
    else:
        lines.append("_None._")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `instrument` was generated by mapping exchange-format settlement contract codes through the TIER2 registry `exchange_inst -> instrument` mapping, constrained by `trading_date` and contract lifecycle.",
            "- For SHFE raw settlement files, rows mapping to INE registry products were written under `INE_{PRODUCT}.parquet`.",
            "- CFFEX settlement parameters were treated as effective-date rows and forward-filled over CFFEX open trading days within each contract lifecycle.",
            "- Rows missing required margin or ordinary commission fields were skipped instead of backfilled with guessed values.",
        ]
    )
    AUDIT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if skipped:
        pd.DataFrame(skipped).to_csv(UNMAPPED_PATH, index=False, encoding="utf-8-sig")
    elif UNMAPPED_PATH.exists():
        UNMAPPED_PATH.unlink()


def main() -> None:
    mapper = RegistryMapper(REGISTRY_DIR)
    stats: Counter = Counter()
    skipped: list[dict[str, Any]] = []
    rows = ingest_non_cffex(mapper, skipped, stats)
    rows.extend(ingest_cffex(mapper, skipped, stats))
    file_rows = write_outputs(rows)
    write_audit(stats, file_rows, skipped)
    print(f"output_files={len(file_rows)} output_rows={sum(file_rows.values())}")
    print(f"skipped_rows={len(skipped)} audit={AUDIT_PATH}")


if __name__ == "__main__":
    main()
