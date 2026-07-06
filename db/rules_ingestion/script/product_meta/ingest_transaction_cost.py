"""
Generate TIER1 TransactionCostRule candidates from local raw snapshots.

The first rule of each product is backfilled to the product's first listed
trading date when earlier fee observations are unavailable. This is an explicit
weak assumption requested for the first ingestion pass and is written as a YAML
comment plus audit metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[4]
RAW_ROOT = ROOT / "db" / "rules_ingestion" / "data" / "raw" / "20260622"
STATIC_BATCH = ROOT / "db" / "rules_ingestion" / "data" / "derived" / "20260624" / "product_meta_static"
OUT_ROOT = ROOT / "db" / "rules_ingestion" / "data" / "derived" / "20260624" / "product_meta_transaction_cost"
YAML_DIR = OUT_ROOT / "yaml"

EXCHANGE_ALIASES = {
    "CFE": "CFFEX",
    "CFX": "CFFEX",
    "CFFEX": "CFFEX",
    "CZC": "CZCE",
    "ZCE": "CZCE",
    "CZCE": "CZCE",
    "DCE": "DCE",
    "GFE": "GFEX",
    "GFEX": "GFEX",
    "INE": "INE",
    "SHF": "SHFE",
    "SHFE": "SHFE",
}
BIGQUANT_COMMISSION_TYPE = {1: "flat", 2: "ratio"}


@dataclass(frozen=True)
class FeeTuple:
    commission_type: str
    open: float
    close_yesterday: float
    close_today: float

    def as_key(self) -> tuple[str, float, float, float]:
        return (
            self.commission_type,
            round(float(self.open), 12),
            round(float(self.close_yesterday), 12),
            round(float(self.close_today), 12),
        )


def normalize_product(value: Any) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text.upper() if text else None


def normalize_exchange(value: Any) -> str | None:
    if pd.isna(value):
        return None
    return EXCHANGE_ALIASES.get(str(value).strip().upper())


def iso_from_yyyymmdd(value: Any) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nat", "none", "nan"}:
        return None
    if len(text) >= 10 and text[4] == "-":
        return text[:10]
    text = text.split(".")[0]
    if len(text) != 8:
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def numeric(value: Any, default: float | None = None) -> float | None:
    out = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(out):
        return default
    return float(out)


def read_static_candidates() -> pd.DataFrame:
    path = STATIC_BATCH / "candidate_static_meta.csv"
    df = pd.read_csv(path)
    return df[df["status"].isin(["ok", "needs_review"])].copy()


def read_product_list_dates() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for path in sorted((RAW_ROOT / "tushare" / "fut_basic").glob("exchange=*.parquet")):
        df = pd.read_parquet(path, columns=["exchange", "fut_code", "list_date"])
        df["exchange_local"] = df["exchange"].map(normalize_exchange)
        df["product"] = df["fut_code"].map(normalize_product)
        df["list_date_iso"] = df["list_date"].map(iso_from_yyyymmdd)
        for (exchange, product), group in df.dropna(subset=["exchange_local", "product", "list_date_iso"]).groupby(
            ["exchange_local", "product"]
        ):
            rows.append(
                {
                    "exchange": exchange,
                    "product": product,
                    "source": "tushare.fut_basic",
                    "first_list_date": min(group["list_date_iso"]),
                }
            )

    for path in sorted((RAW_ROOT / "bigquant" / "cn_future_basic_info").glob("exchange=*.parquet")):
        df = pd.read_parquet(path, columns=["exchange", "product_code", "list_date"])
        df["exchange_local"] = df["exchange"].map(normalize_exchange)
        df["product"] = df["product_code"].map(normalize_product)
        df["list_date_iso"] = df["list_date"].map(iso_from_yyyymmdd)
        df = df[df["list_date_iso"].notna() & (df["list_date_iso"] > "1980-01-01")]
        for (exchange, product), group in df.dropna(subset=["exchange_local", "product"]).groupby(
            ["exchange_local", "product"]
        ):
            rows.append(
                {
                    "exchange": exchange,
                    "product": product,
                    "source": "bigquant.cn_future_basic_info",
                    "first_list_date": min(group["list_date_iso"]),
                }
            )

    all_dates = pd.DataFrame(rows)
    if all_dates.empty:
        return all_dates
    priority = {"tushare.fut_basic": 0, "bigquant.cn_future_basic_info": 1}
    all_dates["source_priority"] = all_dates["source"].map(priority)
    all_dates = all_dates.sort_values(["exchange", "product", "source_priority", "first_list_date"])
    return all_dates.groupby(["exchange", "product"], as_index=False).first()[
        ["exchange", "product", "first_list_date", "source"]
    ]


def read_bigquant_current() -> pd.DataFrame:
    path = RAW_ROOT / "bigquant" / "cn_future_product_info" / "full.parquet"
    df = pd.read_parquet(path)
    df["exchange"] = df["exchange"].map(normalize_exchange)
    df["product"] = df["code"].map(normalize_product)
    df["bq_commission_type"] = pd.to_numeric(df["commission_type"], errors="coerce").map(
        lambda x: BIGQUANT_COMMISSION_TYPE.get(int(x)) if pd.notna(x) else None
    )
    return df[
        [
            "exchange",
            "product",
            "bq_commission_type",
            "commission_open",
            "commission_yesterday",
            "commission_today",
        ]
    ].dropna(subset=["exchange", "product"])


def settle_paths() -> dict[tuple[str, str], Path]:
    paths: dict[tuple[str, str], Path] = {}
    for path in sorted((RAW_ROOT / "tushare" / "fut_settle").glob("exchange=*/product=*.parquet")):
        exchange = normalize_exchange(path.parent.name.split("=", 1)[1])
        product = normalize_product(path.stem.split("=", 1)[1] if "=" in path.stem else path.stem)
        if exchange and product:
            paths[(exchange, product)] = path
    return paths


def normalize_rate(value: float) -> float:
    if value >= 0.001:
        return value / 1000.0
    return value


def fee_tuple_from_row(row: Any, bq_commission_type: str | None = None) -> FeeTuple:
    rate = numeric(row.trading_fee_rate, 0.0) or 0.0
    fee = numeric(row.trading_fee, 0.0) or 0.0
    offset_today = numeric(row.offset_today_fee, None)
    if rate > 0:
        commission_type = "ratio"
        open_fee = normalize_rate(rate)
    elif bq_commission_type == "ratio":
        commission_type = "ratio"
        open_fee = fee / 10000.0 if fee >= 0.001 else fee
    else:
        commission_type = "flat"
        open_fee = fee
    close_yesterday = open_fee
    close_today = open_fee if offset_today is None else offset_today
    return FeeTuple(
        commission_type=commission_type,
        open=float(open_fee),
        close_yesterday=float(close_yesterday),
        close_today=float(close_today),
    )


def instrument_to_local(ts_code: str) -> str:
    if not isinstance(ts_code, str) or "." not in ts_code:
        return str(ts_code).upper()
    body, suffix = ts_code.split(".", 1)
    exchange = normalize_exchange(suffix) or suffix.upper()
    return f"{body.upper()}.{exchange}"


def build_product_history(
    exchange: str,
    product: str,
    settle_path: Path,
    first_list_date: str | None,
    bq_commission_type: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    df = pd.read_parquet(settle_path)
    if df.empty:
        return [], [], {"status": "no_settle_rows"}

    df["trade_date_iso"] = df["trade_date"].map(iso_from_yyyymmdd)
    df = df.dropna(subset=["trade_date_iso"]).copy()
    for col in ["trading_fee_rate", "trading_fee", "offset_today_fee"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["has_open_fee_evidence"] = (df["trading_fee_rate"].fillna(0.0) > 0) | (df["trading_fee"].fillna(0.0) > 0)
    rows_without_open_fee_evidence = int((~df["has_open_fee_evidence"]).sum())
    df = df[df["has_open_fee_evidence"]].copy()
    if df.empty:
        return [], [], {
            "status": "no_fee_evidence_rows",
            "rows_without_open_fee_evidence": rows_without_open_fee_evidence,
        }
    df["fee_key"] = [fee_tuple_from_row(row, bq_commission_type).as_key() for row in df.itertuples(index=False)]
    df["instrument_local"] = df["ts_code"].map(instrument_to_local)

    daily_rows: list[dict[str, Any]] = []
    conflict_rows: list[dict[str, Any]] = []
    for trade_date, group in df.groupby("trade_date_iso"):
        keys = sorted(set(group["fee_key"]))
        if len(keys) == 1:
            key = keys[0]
            daily_rows.append(
                {
                    "trade_date": trade_date,
                    "commission_type": key[0],
                    "open": key[1],
                    "close_yesterday": key[2],
                    "close_today": key[3],
                    "instrument_count": int(group["instrument_local"].nunique(dropna=True)),
                    "row_count": int(len(group)),
                }
            )
        else:
            for key, sub in group.groupby("fee_key"):
                conflict_rows.append(
                    {
                        "exchange": exchange,
                        "product": product,
                        "trade_date": trade_date,
                        "commission_type": key[0],
                        "open": key[1],
                        "close_yesterday": key[2],
                        "close_today": key[3],
                        "instrument_count": int(sub["instrument_local"].nunique(dropna=True)),
                        "sample_instruments": "|".join(sorted(sub["instrument_local"].dropna().unique())[:20]),
                    }
                )

    if not daily_rows:
        return [], conflict_rows, {
            "status": "no_product_level_compressible_dates",
            "earliest_observed_fee_date": min(df["trade_date_iso"]) if not df.empty else None,
            "latest_observed_fee_date": max(df["trade_date_iso"]) if not df.empty else None,
            "conflict_days": len(set(r["trade_date"] for r in conflict_rows)),
        }

    daily = pd.DataFrame(daily_rows).sort_values("trade_date")
    rules: list[dict[str, Any]] = []
    last_key: tuple[str, float, float, float] | None = None
    earliest_product_level_date = str(daily.iloc[0]["trade_date"])
    effective_start = first_list_date or earliest_product_level_date
    for row in daily.itertuples(index=False):
        key = (
            row.commission_type,
            float(row.open),
            float(row.close_yesterday),
            float(row.close_today),
        )
        if last_key == key:
            continue
        effective_date = effective_start if not rules else row.trade_date
        rules.append(
            {
                "effective_date": effective_date,
                "commission_type": row.commission_type,
                "open": float(row.open),
                "close_yesterday": float(row.close_yesterday),
                "close_today": float(row.close_today),
                "scope": [],
                "condition": None,
                "observed_trade_date": row.trade_date,
                "is_backfilled_first_rule": len(rules) == 0 and effective_date != row.trade_date,
            }
        )
        last_key = key

    metadata = {
        "status": "ok",
        "first_list_date": first_list_date,
        "earliest_observed_fee_date": min(df["trade_date_iso"]),
        "earliest_product_level_fee_date": earliest_product_level_date,
        "latest_observed_fee_date": max(df["trade_date_iso"]),
        "observed_days": int(df["trade_date_iso"].nunique()),
        "product_level_days": int(daily.shape[0]),
        "conflict_days": len(set(r["trade_date"] for r in conflict_rows)),
        "rules": len(rules),
        "instruments": int(df["instrument_local"].nunique(dropna=True)),
        "rows_without_open_fee_evidence": rows_without_open_fee_evidence,
    }
    return rules, conflict_rows, metadata


def float_text(value: float) -> str:
    return f"{float(value):.12g}"


def write_product_yaml(static_payload: dict[str, Any], rules: list[dict[str, Any]]) -> None:
    YAML_DIR.mkdir(parents=True, exist_ok=True)
    product = static_payload["product"]
    lines = [
        f"product: {product}",
        f"exchange: {static_payload['exchange']}",
        f"name: {static_payload['name']}",
        f"position_date_type: {static_payload['position_date_type']}",
        f"max_margin_side_algorithm: {str(bool(static_payload['max_margin_side_algorithm'])).lower()}",
        f"combination_type: {int(static_payload['combination_type'])}",
        "transaction_cost_history:",
    ]
    for index, rule in enumerate(rules):
        lines.append("  -")
        if index == 0:
            lines.append(
                f"    effective_date: '{rule['effective_date']}'  # inferred by backfilling from earliest "
                f"product-level observed fee date {rule['observed_trade_date']}; very weak assumption"
            )
        else:
            lines.append(f"    effective_date: '{rule['effective_date']}'")
        lines.extend(
            [
                f"    commission_type: {rule['commission_type']}",
                f"    open: {float_text(rule['open'])}",
                f"    close_yesterday: {float_text(rule['close_yesterday'])}",
                f"    close_today: {float_text(rule['close_today'])}",
                "    scope: []",
                "    condition: null",
            ]
        )
    (YAML_DIR / f"{product}.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def compare_bigquant(latest_rule: dict[str, Any], bq_row: pd.Series | None) -> dict[str, Any]:
    if bq_row is None:
        return {
            "bq_available": False,
            "bq_type_match": None,
            "bq_open_match": None,
            "bq_yesterday_match": None,
            "bq_today_match": None,
        }

    def close(a: Any, b: Any) -> bool | None:
        aa = numeric(a, None)
        bb = numeric(b, None)
        if aa is None or bb is None:
            return None
        return abs(aa - bb) <= max(1e-12, abs(bb) * 1e-8)

    return {
        "bq_available": True,
        "bq_type": bq_row.get("bq_commission_type"),
        "bq_open": bq_row.get("commission_open"),
        "bq_yesterday": bq_row.get("commission_yesterday"),
        "bq_today": bq_row.get("commission_today"),
        "bq_type_match": latest_rule["commission_type"] == bq_row.get("bq_commission_type"),
        "bq_open_match": close(latest_rule["open"], bq_row.get("commission_open")),
        "bq_yesterday_match": close(latest_rule["close_yesterday"], bq_row.get("commission_yesterday")),
        "bq_today_match": close(latest_rule["close_today"], bq_row.get("commission_today")),
    }


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    if YAML_DIR.exists():
        for old_yaml in YAML_DIR.glob("*.yaml"):
            old_yaml.unlink()
    candidates = read_static_candidates()
    list_dates = read_product_list_dates()
    bq_current = read_bigquant_current()
    paths = settle_paths()

    list_date_map = {
        (row.exchange, row.product): row.first_list_date for row in list_dates.itertuples(index=False)
    }
    bq_map = {
        (row.exchange, row.product): row
        for row in bq_current.dropna(subset=["exchange", "product"]).itertuples(index=False)
    }

    summary_rows: list[dict[str, Any]] = []
    rule_rows: list[dict[str, Any]] = []
    all_conflicts: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []

    for static in candidates.sort_values(["exchange", "product"]).itertuples(index=False):
        key = (static.exchange, static.product)
        settle_path = paths.get(key)
        if settle_path is None:
            summary_rows.append(
                {
                    "exchange": static.exchange,
                    "product": static.product,
                    "status": "missing_fut_settle",
                    "first_list_date": list_date_map.get(key),
                    "rules": 0,
                }
            )
            continue

        bq_row = bq_map.get(key)
        bq_type = bq_row.bq_commission_type if bq_row is not None else None
        rules, conflicts, metadata = build_product_history(
            static.exchange, static.product, settle_path, list_date_map.get(key), bq_type
        )
        all_conflicts.extend(conflicts)
        summary = {"exchange": static.exchange, "product": static.product, **metadata}
        summary_rows.append(summary)
        if not rules:
            continue

        static_payload = {
            "product": static.product,
            "exchange": static.exchange,
            "name": static.name,
            "position_date_type": static.position_date_type,
            "max_margin_side_algorithm": static.max_margin_side_algorithm,
            "combination_type": static.combination_type,
        }
        write_product_yaml(static_payload, rules)
        for idx, rule in enumerate(rules):
            rule_rows.append(
                {
                    "exchange": static.exchange,
                    "product": static.product,
                    "rule_index": idx,
                    **rule,
                }
            )
        validation_rows.append(
            {
                "exchange": static.exchange,
                "product": static.product,
                "latest_rule_effective_date": rules[-1]["effective_date"],
                "latest_rule_observed_trade_date": rules[-1]["observed_trade_date"],
                "latest_commission_type": rules[-1]["commission_type"],
                "latest_open": rules[-1]["open"],
                "latest_close_yesterday": rules[-1]["close_yesterday"],
                "latest_close_today": rules[-1]["close_today"],
                **compare_bigquant(rules[-1], pd.Series(bq_row._asdict()) if bq_row is not None else None),
            }
        )

    summary_df = pd.DataFrame(summary_rows)
    rules_df = pd.DataFrame(rule_rows)
    conflicts_df = pd.DataFrame(all_conflicts)
    validation_df = pd.DataFrame(validation_rows)
    summary_df.to_csv(OUT_ROOT / "transaction_cost_product_summary.csv", index=False, encoding="utf-8-sig")
    rules_df.to_csv(OUT_ROOT / "transaction_cost_rules.csv", index=False, encoding="utf-8-sig")
    conflicts_df.to_csv(OUT_ROOT / "transaction_cost_conflicts.csv", index=False, encoding="utf-8-sig")
    validation_df.to_csv(OUT_ROOT / "transaction_cost_bigquant_current_validation.csv", index=False, encoding="utf-8-sig")

    yaml_count = len(list(YAML_DIR.glob("*.yaml"))) if YAML_DIR.exists() else 0
    ok_products = int((summary_df["status"] == "ok").sum()) if "status" in summary_df else 0
    missing = int((summary_df["status"] == "missing_fut_settle").sum()) if "status" in summary_df else 0
    conflict_products = int(conflicts_df[["exchange", "product"]].drop_duplicates().shape[0]) if not conflicts_df.empty else 0
    lines = [
        "# TransactionCostRule Ingestion Audit",
        "",
        "- Batch: 20260624",
        "- Raw snapshot: `db/rules_ingestion/data/raw/20260622`",
        "- Source of generated rules: Tushare `fut_settle`",
        "- BigQuant `cn_future_product_info` is used only for current-value validation.",
        f"- Target products from StaticMeta candidates: {len(candidates)}",
        f"- Products with generated candidate histories: {ok_products}",
        f"- Candidate YAML files: {yaml_count}",
        f"- Products missing local `fut_settle` data: {missing}",
        f"- Products with at least one same-day tuple conflict: {conflict_products}",
        f"- Generated rule rows: {len(rules_df)}",
        "",
        "## Assumptions",
        "",
        "- The first generated fee rule is backfilled to the product's first listed trading date when earlier fee observations are unavailable.",
        "- The YAML comment on the first `effective_date` records the earliest product-level observed fee date and labels the rule as a very weak assumption.",
        "- `trading_fee_rate > 0` maps to `commission_type: ratio`; Tushare rate-like values such as `0.1` are normalized to local decimal ratios such as `0.0001`.",
        "- If BigQuant current product metadata says a product is ratio-based but Tushare reports the value in `trading_fee`, the value is interpreted as per-ten-thousand and divided by 10000.",
        "- Rows where both `trading_fee_rate` and `trading_fee` are zero are treated as missing open-fee evidence, not as real zero-fee rules.",
        "- `close_yesterday` is assumed equal to `open` because Tushare `fut_settle` does not provide a separate close-yesterday fee field.",
        "- Missing `offset_today_fee` is filled from `open`; non-missing `offset_today_fee` is used as `close_today`.",
        "- `scope` is written as `[]` for generated product-level rules; same-day contract-level tuple conflicts are reported and not compressed into product-level YAML.",
        "- `condition` defaults to `null`.",
        "",
        "## Output Files",
        "",
        "- `transaction_cost_product_summary.csv`",
        "- `transaction_cost_rules.csv`",
        "- `transaction_cost_conflicts.csv`",
        "- `transaction_cost_bigquant_current_validation.csv`",
        "- `yaml/*.yaml`",
        "",
    ]
    (OUT_ROOT / "audit_report.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"Target products: {len(candidates)}")
    print(f"Generated histories: {ok_products}")
    print(f"YAML files written: {yaml_count}")
    print(f"Missing fut_settle products: {missing}")
    print(f"Conflict products: {conflict_products}")
    print(f"Rule rows: {len(rules_df)}")
    print(f"Output: {OUT_ROOT}")


if __name__ == "__main__":
    main()
