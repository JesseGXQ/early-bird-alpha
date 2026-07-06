from __future__ import annotations

import argparse
import csv
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[5]
CALENDAR_DIR = REPO_ROOT / "db" / "rules_ingestion" / "data" / "raw" / "20260622" / "tushare" / "fut_trade_cal"
CREDENTIAL_FILE = REPO_ROOT / "db" / "rules_ingestion" / "supplements" / "API凭证.txt"
DEFAULT_EXCHANGE = "SHFE"
DEFAULT_SLEEP_SECONDS = 0.35
DEFAULT_MAX_RETRIES = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pull Tushare futures daily bars by exchange and trade date.")
    parser.add_argument(
        "--exchange",
        default=DEFAULT_EXCHANGE,
        help="Exchange code, e.g. SHFE, DCE, CZCE, CFFEX, INE, GFEX; use 'all' for all local calendars.",
    )
    parser.add_argument("--start-date", default=None, help="Start trade date in YYYYMMDD. Defaults to calendar min.")
    parser.add_argument(
        "--end-date",
        default=None,
        help="End trade date in YYYYMMDD. Defaults to min(calendar max, today). Future dates are always capped.",
    )
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS, help="Sleep after each API call.")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Max retries per trade date after the initial request.",
    )
    parser.add_argument(
        "--retry-sleep-seconds",
        type=float,
        default=5.0,
        help="Base sleep before retrying failed API calls; multiplied by retry number.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Ignore existing per-date CSV checkpoints and overwrite local files.",
    )
    parser.add_argument(
        "--fields",
        default=None,
        help="Optional comma-separated Tushare fields passed to pro.fut_daily.",
    )
    return parser.parse_args()


def looks_like_tushare_token(text: str) -> bool:
    token = text.strip()
    return len(token) >= 32 and bool(re.fullmatch(r"[0-9A-Za-z]+", token))


def load_tushare_token() -> str | None:
    if not CREDENTIAL_FILE.exists():
        return None

    text = CREDENTIAL_FILE.read_text(encoding="utf-8-sig")
    candidates: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = re.split(r"[:=：]", line, maxsplit=1)
        value = parts[-1].strip().strip("'\"")
        key = parts[0].lower()
        if "tushare" in key or key == "ts" or "token" in key:
            candidates.append(value)
        elif looks_like_tushare_token(value):
            candidates.append(value)

    for candidate in candidates:
        if looks_like_tushare_token(candidate):
            return candidate
    return None


def build_tushare_client() -> Any:
    import tushare as ts

    token = load_tushare_token()
    if token:
        return ts.pro_api(token)
    return ts.pro_api()


def log(message: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)


def normalize_yyyymmdd(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("-", "")
    if not re.fullmatch(r"\d{8}", text):
        raise ValueError(f"Invalid date: {value!r}. Expected YYYYMMDD or YYYY-MM-DD.")
    pd.to_datetime(text, format="%Y%m%d")
    return text


def calendar_path(exchange: str) -> Path:
    return CALENDAR_DIR / f"exchange={exchange}.parquet"


def available_exchanges() -> list[str]:
    exchanges = []
    for path in CALENDAR_DIR.glob("exchange=*.parquet"):
        exchanges.append(path.stem.split("=", 1)[-1].upper())
    return sorted(exchanges)


def trade_dates_for_exchange(exchange: str, start_date: str | None, end_date: str | None) -> list[str]:
    path = calendar_path(exchange)
    if not path.exists():
        raise FileNotFoundError(f"Local trade calendar not found: {path}")

    cal = pd.read_parquet(path)
    if "cal_date" not in cal.columns or "is_open" not in cal.columns:
        raise ValueError(f"Calendar missing required columns cal_date/is_open: {path}")

    cal = cal.copy()
    cal["cal_date"] = cal["cal_date"].astype(str)
    cal = cal.loc[cal["is_open"].astype(str).eq("1")]
    if cal.empty:
        return []

    today = datetime.now().strftime("%Y%m%d")
    effective_start = start_date or cal["cal_date"].min()
    requested_end = end_date or cal["cal_date"].max()
    effective_end = min(requested_end, today)

    dates = cal.loc[cal["cal_date"].between(effective_start, effective_end), "cal_date"]
    return sorted(dates.dropna().astype(str).unique().tolist())


def output_path(exchange: str, trade_date: str) -> Path:
    return SCRIPT_DIR / "fut_daily" / f"exchange={exchange}" / f"trade_date={trade_date}.csv"


def fetch_one_day(pro: Any, exchange: str, trade_date: str, fields: str | None) -> pd.DataFrame:
    params: dict[str, Any] = {"trade_date": trade_date, "exchange": exchange}
    if fields:
        params["fields"] = fields
    return pro.fut_daily(**params)


def pull_with_retries(
    pro: Any,
    exchange: str,
    trade_date: str,
    fields: str | None,
    max_retries: int,
    retry_sleep_seconds: float,
) -> tuple[bool, pd.DataFrame | None, str | None, str | None, int]:
    total_attempts = max_retries + 1
    for attempt in range(1, total_attempts + 1):
        try:
            df = fetch_one_day(pro, exchange, trade_date, fields)
            if not isinstance(df, pd.DataFrame):
                df = pd.DataFrame(df)
            return True, df, None, None, attempt
        except Exception as exc:  # noqa: BLE001 - keep the full run moving after date-level failures.
            error_type = type(exc).__name__
            error = str(exc)
            if attempt >= total_attempts:
                return False, None, error_type, error, attempt
            log(
                f"retry exchange={exchange} trade_date={trade_date} "
                f"attempt={attempt}/{max_retries} error={error_type}: {error}"
            )
            time.sleep(retry_sleep_seconds * attempt)
    return False, None, "UnknownError", "unreachable retry state", total_attempts


def save_failure_list(failures: list[dict[str, Any]], run_id: str) -> Path:
    path = SCRIPT_DIR / "failures" / f"fut_daily_failures_{run_id}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["exchange", "trade_date", "error_type", "error", "attempts", "finished_at"],
        )
        writer.writeheader()
        writer.writerows(failures)
    return path


def save_run_summary(summary: dict[str, Any], run_id: str) -> Path:
    path = SCRIPT_DIR / "summaries" / f"fut_daily_summary_{run_id}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([summary]).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def main() -> None:
    args = parse_args()
    start_date = normalize_yyyymmdd(args.start_date)
    end_date = normalize_yyyymmdd(args.end_date)
    if start_date and end_date and start_date > end_date:
        raise ValueError(f"start-date must be <= end-date: {start_date} > {end_date}")

    requested_exchange = args.exchange.upper()
    exchanges = available_exchanges() if requested_exchange == "ALL" else [requested_exchange]
    if not exchanges:
        raise ValueError(f"No local calendars found under {CALENDAR_DIR}")

    pro = build_tushare_client()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    failures: list[dict[str, Any]] = []
    skipped = 0
    succeeded = 0
    empty = 0
    total_rows = 0
    failed = 0

    exchange_dates: list[tuple[str, str]] = []
    for exchange in exchanges:
        dates = trade_dates_for_exchange(exchange, start_date, end_date)
        log(
            f"plan exchange={exchange} trade_dates={len(dates)} "
            f"start={dates[0] if dates else 'NA'} end={dates[-1] if dates else 'NA'}"
        )
        exchange_dates.extend((exchange, trade_date) for trade_date in dates)

    total = len(exchange_dates)
    log(
        f"fut_daily pull started exchange={requested_exchange} total_trade_dates={total} "
        f"overwrite={args.overwrite} sleep_seconds={args.sleep_seconds}"
    )

    for idx, (exchange, trade_date) in enumerate(exchange_dates, start=1):
        pct = (idx / total * 100) if total else 100.0
        out = output_path(exchange, trade_date)
        prefix = f"[{idx}/{total}; {pct:.2f}%] exchange={exchange} trade_date={trade_date}"

        if out.exists() and not args.overwrite:
            skipped += 1
            log(f"{prefix} skip existing file={out}")
            continue

        log(f"{prefix} request start")
        ok, df, error_type, error, attempts = pull_with_retries(
            pro=pro,
            exchange=exchange,
            trade_date=trade_date,
            fields=args.fields,
            max_retries=args.max_retries,
            retry_sleep_seconds=args.retry_sleep_seconds,
        )

        if ok and df is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(out, index=False, encoding="utf-8-sig")
            rows = len(df)
            total_rows += rows
            succeeded += 1
            if rows == 0:
                empty += 1
            log(f"{prefix} success rows={rows} attempts={attempts} file={out}")
        else:
            failed += 1
            failures.append(
                {
                    "exchange": exchange,
                    "trade_date": trade_date,
                    "error_type": error_type,
                    "error": error,
                    "attempts": attempts,
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            log(f"{prefix} failed attempts={attempts} error={error_type}: {error}")

        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    failure_file = save_failure_list(failures, run_id)
    summary = {
        "run_id": run_id,
        "exchange": requested_exchange,
        "start_date": start_date,
        "end_date": end_date,
        "total_trade_dates": total,
        "succeeded": succeeded,
        "skipped_existing": skipped,
        "failed": failed,
        "empty": empty,
        "total_rows": total_rows,
        "overwrite": args.overwrite,
        "sleep_seconds": args.sleep_seconds,
        "failure_file": str(failure_file),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    summary_file = save_run_summary(summary, run_id)
    log(f"fut_daily pull finished summary={summary} summary_file={summary_file}")


if __name__ == "__main__":
    main()
