"""
Generate Tier1 product trading sessions YAML files.

Outputs one YAML file per product. Each file contains only:
  - sessions_history
  - session_override_rules
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
import re

import pandas as pd
import yaml


RULES_INGESTION_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]

STATIC_YAML_DIR = RULES_INGESTION_ROOT / "data" / "derived" / "20260624" / "product_meta_static" / "yaml"
OUTPUT_DIR = RULES_INGESTION_ROOT / "data" / "derived" / "20260624" / "product_meta_trading_sessions"
OUTPUT_YAML_DIR = OUTPUT_DIR / "yaml"

LISTING_REPORT = (
    REPO_ROOT
    / "docs"
    / "代码修改笔记"
    / "期货交易规则"
    / "交易时段基础规则"
    / "期货品种上市时间汇总.md"
)
NIGHT_REPORT = (
    REPO_ROOT
    / "docs"
    / "代码修改笔记"
    / "期货交易规则"
    / "交易时段基础规则"
    / "期货品种夜盘开始日期和调整情况汇总报告.md"
)
DAY_REPORT = (
    REPO_ROOT
    / "docs"
    / "代码修改笔记"
    / "期货交易规则"
    / "交易时段基础规则"
    / "期货品种日盘交易时段调整报告.md"
)
SPECIAL_FACTS_REPORT = (
    REPO_ROOT
    / "docs"
    / "代码修改笔记"
    / "期货交易规则"
    / "交易时段规则特殊安排"
    / "中国期货市场交易时段规则特殊安排_历史事实_CodeX.md"
)

BIGQUANT_PRODUCT_INFO = (
    RULES_INGESTION_ROOT / "data" / "raw" / "20260622" / "bigquant" / "cn_future_product_info" / "full.parquet"
)
TRADE_CAL_DIR = RULES_INGESTION_ROOT / "data" / "raw" / "20260622" / "tushare" / "fut_trade_cal"
OFFICIAL_HOLIDAYS = RULES_INGESTION_ROOT / "data" / "raw" / "official_chinese_holidays" / "holiday.csv"


COMMODITY_MORNING = [["09:00", "10:15"], ["10:30", "11:30"]]
COMMODITY_AFTERNOON = [["13:30", "15:00"]]

CFFEX_PRE_MORNING = [["09:15", "11:30"]]
CFFEX_STOCK_MORNING = [["09:30", "11:30"]]
CFFEX_AFTERNOON_1515 = [["13:00", "15:15"]]
CFFEX_AFTERNOON_1500 = [["13:00", "15:00"]]


@dataclass(frozen=True)
class ProductStatic:
    product: str
    exchange: str
    name: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def clean_cell(value: str) -> str:
    value = value.strip()
    value = re.sub(r"<br\s*/?>", " ", value)
    value = value.replace("`", "")
    value = re.sub(r"\*\*(.*?)\*\*", r"\1", value)
    return value.strip()


def parse_markdown_table(path: Path, expected_columns: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if "---" in line:
            continue
        cells = [clean_cell(cell) for cell in line.strip("|").split("|")]
        if len(cells) != len(expected_columns):
            continue
        if cells == expected_columns:
            continue
        rows.append(dict(zip(expected_columns, cells)))
    return rows


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value.strip())


def normalize_product(value: str) -> str:
    return value.strip().upper()


def load_static_products() -> dict[str, ProductStatic]:
    products: dict[str, ProductStatic] = {}
    for path in sorted(STATIC_YAML_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        product = normalize_product(str(raw["product"]))
        products[product] = ProductStatic(
            product=product,
            exchange=str(raw["exchange"]).upper(),
            name=str(raw["name"]),
        )
    return products


def load_listing_dates() -> dict[str, date]:
    rows = parse_markdown_table(
        LISTING_REPORT,
        ["product", "exchange", "list_date", "online_source_checked", "name", "notes"],
    )
    return {normalize_product(row["product"]): parse_iso_date(row["list_date"]) for row in rows}


def load_night_start_rows() -> dict[str, dict[str, str]]:
    rows = parse_markdown_table(
        NIGHT_REPORT,
        ["product", "exchange", "has_night_session", "night_session_start_from", "date_type"],
    )
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        product = normalize_product(row["product"])
        if row["has_night_session"] in {"0", "1"}:
            result[product] = row
    return result


def load_night_adjustment_rows() -> list[dict[str, str]]:
    rows = parse_markdown_table(
        NIGHT_REPORT,
        ["product", "exchange", "before_hours", "after_hours", "adjustment_date", "date_type", "notes"],
    )
    return [row for row in rows if re.match(r"^\d{4}-\d{2}-\d{2}$", row["adjustment_date"])]


def load_day_adjustment_rows() -> list[dict[str, str]]:
    rows = parse_markdown_table(
        DAY_REPORT,
        ["product", "exchange", "before_hours", "after_hours", "adjustment_date", "date_type", "notes"],
    )
    return [row for row in rows if re.match(r"^\d{4}-\d{2}-\d{2}$", row["adjustment_date"])]


def load_trade_calendars() -> dict[str, set[date]]:
    calendars: dict[str, set[date]] = {}
    for path in TRADE_CAL_DIR.glob("exchange=*.parquet"):
        exchange = path.stem.split("=", 1)[1].upper()
        df = pd.read_parquet(path)
        cal_dates = pd.to_datetime(df.loc[df["is_open"] == 1, "cal_date"].astype(str)).dt.date
        calendars[exchange] = set(cal_dates)
    return calendars


def next_trading_date(calendars: dict[str, set[date]], exchange: str, calendar_date: date) -> date:
    dates = calendars[exchange]
    current = calendar_date + timedelta(days=1)
    for _ in range(20):
        if current in dates:
            return current
        current += timedelta(days=1)
    raise ValueError(f"Cannot infer next trading date for {exchange} after {calendar_date}")


def load_bigquant_current_periods() -> dict[str, list[list[str]]]:
    df = pd.read_parquet(BIGQUANT_PRODUCT_INFO)
    result: dict[str, list[list[str]]] = {}
    for _, row in df.iterrows():
        product = normalize_product(str(row["code"]))
        period = str(row["trading_period"])
        intervals: list[list[str]] = []
        for part in period.split(","):
            start_raw, end_raw = part.split("-")
            intervals.append([format_hhmm(start_raw), format_hhmm(end_raw)])
        result[product] = intervals
    return result


def format_hhmm(value: str) -> str:
    value = value.strip()
    if len(value) == 6:
        value = value[:4]
    if len(value) != 4:
        raise ValueError(f"Unsupported time value: {value}")
    return f"{value[:2]}:{value[2:]}"


def parse_hours_interval(value: str) -> list[list[str]]:
    value = value.strip()
    if value in {"N/A", ""}:
        return []
    intervals: list[list[str]] = []
    for part in value.split(","):
        start, end = part.strip().split("-")
        intervals.append([start.strip(), end.strip()])
    return intervals


def make_session(
    effective_date: date,
    morning: list[list[str]],
    afternoon: list[list[str]],
    night: list[list[str]],
) -> dict[str, Any]:
    return {
        "effective_date": effective_date.isoformat(),
        "morning_sessions": [item.copy() for item in morning],
        "afternoon_sessions": [item.copy() for item in afternoon],
        "night_sessions": [item.copy() for item in night],
    }


def initial_day_sessions(product: str, exchange: str) -> tuple[list[list[str]], list[list[str]]]:
    if exchange != "CFFEX":
        return COMMODITY_MORNING, COMMODITY_AFTERNOON
    if product in {"IF", "IH", "IC", "TF", "T", "TS"}:
        return CFFEX_PRE_MORNING, CFFEX_AFTERNOON_1515
    if product in {"IM"}:
        return CFFEX_STOCK_MORNING, CFFEX_AFTERNOON_1500
    if product in {"TL"}:
        return CFFEX_STOCK_MORNING, CFFEX_AFTERNOON_1515
    raise ValueError(f"Unhandled CFFEX product: {product}")


def apply_day_adjustment(
    sessions: list[dict[str, Any]],
    product: str,
    adjustment_date: date,
    after_hours: str,
) -> None:
    parts = parse_hours_interval(after_hours)
    if len(parts) != 2:
        raise ValueError(f"Unexpected CFFEX day adjustment for {product}: {after_hours}")
    sessions.append(
        make_session(
            adjustment_date,
            [parts[0]],
            [parts[1]],
            sessions[-1]["night_sessions"],
        )
    )


def add_or_replace_session_version(
    sessions: list[dict[str, Any]],
    new_session: dict[str, Any],
) -> None:
    for idx, existing in enumerate(sessions):
        if existing["effective_date"] == new_session["effective_date"]:
            sessions[idx] = new_session
            return
    sessions.append(new_session)


def build_sessions_history(
    product: str,
    meta: ProductStatic,
    list_date: date,
    night_row: dict[str, str] | None,
    night_adjustments: list[dict[str, str]],
    day_adjustments: list[dict[str, str]],
    calendars: dict[str, set[date]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    morning, afternoon = initial_day_sessions(product, meta.exchange)
    sessions = [make_session(list_date, morning, afternoon, [])]
    audit_events: list[dict[str, Any]] = [
        {
            "product": product,
            "event": "initial",
            "source_date": list_date.isoformat(),
            "effective_date": list_date.isoformat(),
            "note": "product listing date",
        }
    ]

    for row in sorted(day_adjustments, key=lambda item: item["adjustment_date"]):
        if normalize_product(row["product"]) != product:
            continue
        adjustment_date = parse_iso_date(row["adjustment_date"])
        apply_day_adjustment(sessions, product, adjustment_date, row["after_hours"])
        audit_events.append(
            {
                "product": product,
                "event": "day_adjustment",
                "source_date": adjustment_date.isoformat(),
                "effective_date": adjustment_date.isoformat(),
                "note": f"{row['before_hours']} -> {row['after_hours']}",
            }
        )

    if night_row and night_row["has_night_session"] == "1":
        physical_date = parse_iso_date(night_row["night_session_start_from"])
        effective = next_trading_date(calendars, meta.exchange, physical_date)
        latest = sessions[-1]
        add_or_replace_session_version(
            sessions,
            make_session(
                effective,
                latest["morning_sessions"],
                latest["afternoon_sessions"],
                infer_initial_night_session(product, meta.exchange, physical_date),
            ),
        )
        audit_events.append(
            {
                "product": product,
                "event": "night_start",
                "source_date": physical_date.isoformat(),
                "effective_date": effective.isoformat(),
                "note": "calendar night converted to next trading date",
            }
        )

    for row in sorted(night_adjustments, key=lambda item: item["adjustment_date"]):
        if normalize_product(row["product"]) != product:
            continue
        physical_date = parse_iso_date(row["adjustment_date"])
        effective = next_trading_date(calendars, meta.exchange, physical_date)
        latest = sorted(sessions, key=lambda item: item["effective_date"])[-1]
        add_or_replace_session_version(
            sessions,
            make_session(
                effective,
                latest["morning_sessions"],
                latest["afternoon_sessions"],
                parse_hours_interval(row["after_hours"]),
            ),
        )
        audit_events.append(
            {
                "product": product,
                "event": "night_adjustment",
                "source_date": physical_date.isoformat(),
                "effective_date": effective.isoformat(),
                "note": f"{row['before_hours']} -> {row['after_hours']}",
            }
        )

    sessions = sorted(sessions, key=lambda item: item["effective_date"])
    return sessions, audit_events


def infer_initial_night_session(product: str, exchange: str, physical_date: date) -> list[list[str]]:
    if exchange in {"DCE", "CZCE"}:
        if exchange == "DCE" and physical_date >= date(2019, 3, 29):
            return [["21:00", "23:00"]]
        if exchange == "CZCE" and physical_date >= date(2019, 12, 11):
            return [["21:00", "23:00"]]
        return [["21:00", "23:30"]]
    if exchange == "INE":
        if product == "SC":
            return [["21:00", "02:30"]]
        if product == "BC":
            return [["21:00", "01:00"]]
        return [["21:00", "23:00"]]
    if exchange == "SHFE":
        if product in {"AU", "AG"}:
            return [["21:00", "02:30"]]
        if product in {"CU", "AL", "ZN", "PB", "NI", "SN", "AO", "SS", "AD"}:
            return [["21:00", "01:00"]]
        return [["21:00", "23:00"]]
    raise ValueError(f"Unexpected exchange with night session: {exchange} {product}")


def has_any_night(sessions: list[dict[str, Any]]) -> bool:
    return any(bool(item["night_sessions"]) for item in sessions)


def first_night_effective_date(sessions: list[dict[str, Any]]) -> date | None:
    for item in sorted(sessions, key=lambda value: value["effective_date"]):
        if item["night_sessions"]:
            return parse_iso_date(item["effective_date"])
    return None


def build_session_override_rules(
    product: str,
    meta: ProductStatic,
    sessions: list[dict[str, Any]],
    list_date: date,
) -> dict[str, Any]:
    recurring: list[dict[str, Any]] = []
    scheduled: list[dict[str, Any]] = []
    realized: list[dict[str, Any]] = []

    if product in {"TF", "T", "TS", "TL"}:
        recurring.append(
            {
                "rule_id": f"{product}_LAST_TRADE_DATE_REMOVE_AFTERNOON",
                "name": "国债期货最后交易日仅上午交易",
                "effective_from": list_date.isoformat(),
                "effective_to": None,
                "scope": {"mode": "all_instruments"},
                "trigger": {"type": "instrument_last_trade_date"},
                "actions": [
                    {"operation": "remove_session_group", "session_group": "afternoon"}
                ],
                "sources": [
                    {
                        "url": "http://www.cffex.com.cn/",
                        "note": "中金所国债期货最后交易日仅进行上午交易；具体合约表链接见历史事实文档。",
                    }
                ],
            }
        )

    if product in {"IF", "IH", "IC"}:
        recurring.append(
            {
                "rule_id": f"{product}_LAST_TRADE_DATE_EARLY_CLOSE_2010_2015",
                "name": "股指期货历史上最后交易日提前至15:00收盘",
                "effective_from": list_date.isoformat(),
                "effective_to": "2015-12-31",
                "scope": {"mode": "all_instruments"},
                "trigger": {"type": "instrument_last_trade_date"},
                "actions": [
                    {
                        "operation": "set_last_interval_end",
                        "session_group": "afternoon",
                        "time": "15:00",
                    }
                ],
                "sources": [
                    {
                        "url": "http://www.cffex.com.cn/cn/jystz/20151204/19371.html",
                        "note": "2016-01-01后股指期货基础下午盘已统一收至15:00，本 override 终止。",
                    }
                ],
            }
        )
        realized.extend(make_stock_index_circuit_breaker_events(product))

    first_night = first_night_effective_date(sessions)
    if first_night is not None and meta.exchange in {"SHFE", "INE", "DCE", "CZCE"}:
        scheduled.append(
            {
                "event_id": f"{product}_HOLIDAY_NO_NIGHT_SESSION",
                "name": "法定节假日后首个交易日无夜盘",
                "event_type": "holiday",
                "date_selector": {"type": "exchange_holiday_arrangements"},
                "scope": {"mode": "all_instruments"},
                "actions": [
                    {"operation": "remove_session_group", "session_group": "night"}
                ],
                "sources": [
                    {
                        "url": str(OFFICIAL_HOLIDAYS.as_posix()),
                        "note": "本轮不生成交易所级派生节假日表；后续由官方节假日和交易日历推导目标 trading_date。",
                    }
                ],
            }
        )

    if (
        first_night is not None
        and first_night <= date(2020, 5, 6)
        and meta.exchange in {"SHFE", "INE", "DCE", "CZCE"}
    ):
        scheduled.append(
            {
                "event_id": f"{product}_2020_COVID_NIGHT_SUSPENSION",
                "name": "疫情期间暂停夜盘",
                "event_type": "temporary_policy",
                "date_selector": {
                    "type": "range",
                    "start": "2020-02-04",
                    "end": "2020-05-06",
                },
                "scope": {"mode": "all_instruments"},
                "actions": [
                    {"operation": "remove_session_group", "session_group": "night"}
                ],
                "sources": [
                    {
                        "url": "https://finance.sina.com.cn/money/future/roll/2020-04-27/doc-iircuyvi0000794.shtml",
                        "note": "物理日晚间暂停和恢复已转换为 trading_date 区间。",
                    }
                ],
            }
        )

    if (
        first_night is not None
        and first_night <= date(2019, 12, 26)
        and meta.exchange in {"SHFE", "INE", "DCE", "CZCE"}
    ):
        scheduled.append(
            {
                "event_id": f"{product}_20191226_DELAYED_NIGHT_OPEN",
                "name": "结算系统故障导致夜盘延迟开市",
                "event_type": "system_failure",
                "date_selector": {"type": "dates", "dates": ["2019-12-26"]},
                "scope": {"mode": "all_instruments"},
                "actions": [
                    {
                        "operation": "set_first_interval_start",
                        "session_group": "night",
                        "time": "22:30",
                    }
                ],
                "sources": [
                    {
                        "url": "https://www.guancha.cn/politics/2019_12_25_529522.shtml",
                        "note": "日历日2019-12-25晚转换为交易日2019-12-26；上期能源已确认覆盖。",
                    }
                ],
            }
        )

    return {
        "schema_version": 1,
        "recurring_instrument_lifecycle_rules": recurring,
        "scheduled_dated_overrides": scheduled,
        "realized_session_events": realized,
    }


def make_stock_index_circuit_breaker_events(product: str) -> list[dict[str, Any]]:
    source = {
        "url": "http://www.cffex.com.cn/cn/mtxw/20160105/19578.html",
        "note": "具体触发时间和暂停区间依据历史事实文档的熔断暂停交易时间段表。",
    }
    return [
        {
            "event_id": f"{product}_20160104_CIRCUIT_BREAKER",
            "name": "股指期货熔断实际触发",
            "event_type": "circuit_breaker",
            "trading_date": "2016-01-04",
            "scope": {"mode": "all_instruments"},
            "exception_intervals": [
                {
                    "session_group": "afternoon",
                    "start": "13:13",
                    "end": "13:25",
                    "market_state": "halted",
                },
                {
                    "session_group": "afternoon",
                    "start": "13:25",
                    "end": "13:28",
                    "market_state": "call_auction",
                },
                {
                    "session_group": "afternoon",
                    "start": "13:33",
                    "end": "15:00",
                    "market_state": "halted",
                },
            ],
            "sources": [source],
        },
        {
            "event_id": f"{product}_20160107_CIRCUIT_BREAKER",
            "name": "股指期货熔断实际触发",
            "event_type": "circuit_breaker",
            "trading_date": "2016-01-07",
            "scope": {"mode": "all_instruments"},
            "exception_intervals": [
                {
                    "session_group": "morning",
                    "start": "09:42",
                    "end": "09:54",
                    "market_state": "halted",
                },
                {
                    "session_group": "morning",
                    "start": "09:54",
                    "end": "09:57",
                    "market_state": "call_auction",
                },
                {
                    "session_group": "morning",
                    "start": "09:59",
                    "end": "11:30",
                    "market_state": "halted",
                },
                {
                    "session_group": "afternoon",
                    "start": "13:00",
                    "end": "15:00",
                    "market_state": "halted",
                },
            ],
            "sources": [source],
        },
    ]


def split_current_bigquant(
    intervals: list[list[str]],
) -> tuple[list[list[str]], list[list[str]], list[list[str]]]:
    morning: list[list[str]] = []
    afternoon: list[list[str]] = []
    night: list[list[str]] = []
    for interval in intervals:
        start = interval[0]
        if start >= "20:00":
            night.append(interval)
        elif start < "12:00":
            morning.append(interval)
        else:
            afternoon.append(interval)
    return morning, afternoon, night


def validate_against_bigquant(
    product: str,
    sessions: list[dict[str, Any]],
    bq_periods: dict[str, list[list[str]]],
) -> str:
    if product not in bq_periods:
        return "missing_bigquant"
    latest = sessions[-1]
    bq_morning, bq_afternoon, bq_night = split_current_bigquant(bq_periods[product])
    if (
        latest["morning_sessions"] == bq_morning
        and latest["afternoon_sessions"] == bq_afternoon
        and latest["night_sessions"] == bq_night
    ):
        return "ok"
    if product in {"ME", "TC"} and bool(latest["night_sessions"]) == bool(bq_night):
        return "ok_historical_replaced_code_night_presence_only"
    return (
        "mismatch: "
        f"latest={latest['morning_sessions'] + latest['afternoon_sessions'] + latest['night_sessions']} "
        f"bigquant={bq_periods[product]}"
    )


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    class NoAliasDumper(yaml.SafeDumper):
        def ignore_aliases(self, data: Any) -> bool:
            return True

    path.write_text(
        yaml.dump(
            data,
            Dumper=NoAliasDumper,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=120,
        ),
        encoding="utf-8",
    )


def write_audit(
    product_count: int,
    audit_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
) -> None:
    audit_csv = OUTPUT_DIR / "trading_sessions_audit_events.csv"
    validation_csv = OUTPUT_DIR / "trading_sessions_validation.csv"
    pd.DataFrame(audit_rows).to_csv(audit_csv, index=False, encoding="utf-8-sig")
    pd.DataFrame(validation_rows).to_csv(validation_csv, index=False, encoding="utf-8-sig")

    validation = pd.DataFrame(validation_rows)
    ok_mask = validation["bigquant_validation"].astype(str).str.startswith("ok")
    ok_count = int(ok_mask.sum())
    mismatch = validation[~ok_mask]
    md_lines = [
        "# Tier1 Trading Sessions Audit",
        "",
        "## Summary",
        "",
        f"- Output YAML directory: `{OUTPUT_YAML_DIR.as_posix()}`",
        f"- Product YAML files generated: {product_count}",
        "- Top-level YAML fields: `sessions_history`, `session_override_rules` only.",
        f"- BigQuant latest-session validations passed: {ok_count}/{product_count}",
        f"- BigQuant latest-session validations needing review: {len(mismatch)}",
        "- Exchange-level holiday derived table was not generated in this run by decision.",
        "- Stock index circuit breaker events were written to `realized_session_events` for IF/IH/IC.",
        "",
        "## Source Files",
        "",
        f"- Static universe: `{STATIC_YAML_DIR.as_posix()}`",
        f"- Listing report: `{LISTING_REPORT.as_posix()}`",
        f"- Night-session report: `{NIGHT_REPORT.as_posix()}`",
        f"- Day-session report: `{DAY_REPORT.as_posix()}`",
        f"- Special facts report: `{SPECIAL_FACTS_REPORT.as_posix()}`",
        f"- BigQuant current periods: `{BIGQUANT_PRODUCT_INFO.as_posix()}`",
        f"- Tushare trade calendars: `{TRADE_CAL_DIR.as_posix()}`",
        "",
        "## Generated Audit Tables",
        "",
        f"- Version/event audit CSV: `{audit_csv.as_posix()}`",
        f"- Latest BigQuant validation CSV: `{validation_csv.as_posix()}`",
        "",
        "## Review Notes",
        "",
        "- `night_session_start_from` and night adjustment dates were treated as physical calendar-night dates and converted to the next open trading date for the product exchange.",
        "- Holiday no-night rules are included as action templates for products that have night sessions, but concrete holiday dates are not materialized in this run.",
        "- COVID night suspension is included only for products whose first night-session effective trading date is on or before 2020-05-06.",
        "- The 2019-12-26 delayed night open override covers SHFE, INE, DCE, and CZCE products that already had night sessions on that trading date.",
        "- Remaining BigQuant mismatches, if any, are left for manual review rather than forced to BigQuant when they conflict with the historical night-session report.",
        "",
    ]
    if not mismatch.empty:
        md_lines.extend(
            [
                "## BigQuant Mismatches",
                "",
                "These rows need review before promoting the generated YAML to production data.",
                "",
                "| product | exchange | validation |",
                "|---|---|---|",
            ]
        )
        for _, row in mismatch.iterrows():
            md_lines.append(
                f"| {row['product']} | {row['exchange']} | {str(row['bigquant_validation']).replace('|', '/') } |"
            )
        md_lines.append("")
    else:
        md_lines.extend(["## BigQuant Mismatches", "", "None.", ""])

    (OUTPUT_DIR / "trading_sessions_audit.md").write_text(
        "\n".join(md_lines),
        encoding="utf-8",
    )


def main() -> None:
    OUTPUT_YAML_DIR.mkdir(parents=True, exist_ok=True)
    products = load_static_products()
    listing_dates = load_listing_dates()
    night_rows = load_night_start_rows()
    night_adjustments = load_night_adjustment_rows()
    day_adjustments = load_day_adjustment_rows()
    calendars = load_trade_calendars()
    bq_periods = load_bigquant_current_periods()

    audit_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []

    missing_listing = sorted(set(products) - set(listing_dates))
    missing_night = sorted(set(products) - set(night_rows))
    if missing_listing:
        raise ValueError(f"Missing listing dates for products: {missing_listing}")
    if missing_night:
        raise ValueError(f"Missing night-session rows for products: {missing_night}")

    for product, meta in sorted(products.items()):
        sessions, events = build_sessions_history(
            product=product,
            meta=meta,
            list_date=listing_dates[product],
            night_row=night_rows.get(product),
            night_adjustments=night_adjustments,
            day_adjustments=day_adjustments,
            calendars=calendars,
        )
        rules = build_session_override_rules(
            product=product,
            meta=meta,
            sessions=sessions,
            list_date=listing_dates[product],
        )
        output = {
            "sessions_history": sessions,
            "session_override_rules": rules,
        }
        write_yaml(OUTPUT_YAML_DIR / f"{product}.yaml", output)

        for event in events:
            event["exchange"] = meta.exchange
            audit_rows.append(event)

        validation_rows.append(
            {
                "product": product,
                "exchange": meta.exchange,
                "versions": len(sessions),
                "has_night": has_any_night(sessions),
                "first_night_effective_date": (
                    first_night_effective_date(sessions).isoformat()
                    if first_night_effective_date(sessions)
                    else ""
                ),
                "recurring_rules": len(rules["recurring_instrument_lifecycle_rules"]),
                "scheduled_overrides": len(rules["scheduled_dated_overrides"]),
                "realized_events": len(rules["realized_session_events"]),
                "bigquant_validation": validate_against_bigquant(product, sessions, bq_periods),
            }
        )

    write_audit(len(products), audit_rows, validation_rows)
    print(f"generated {len(products)} YAML files under {OUTPUT_YAML_DIR}")
    print(f"audit written to {OUTPUT_DIR / 'trading_sessions_audit.md'}")


if __name__ == "__main__":
    main()
