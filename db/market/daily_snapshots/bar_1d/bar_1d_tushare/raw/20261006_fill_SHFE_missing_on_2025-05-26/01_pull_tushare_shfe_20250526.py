"""步骤 01：重新从 Tushare 拉取 SHFE 20250526 的 fut_daily 日频数据。

背景：历史入库时 `trade_date=20250526.csv` 落盘为空文件（BOM + 空行），导致
清洗阶段被跳过，SHFE 各品种日频快照缺失 2025-05-26。

本脚本：
1. 直连 Tushare HTTP 接口（`http://api.tushare.pro`）重新拉取当天 SHFE 全量日频；
2. 原样保存接口响应 JSON，便于追溯；
3. 按历史 raw 分区格式写回 `exchange=SHFE/trade_date=20250526.csv`（utf-8-sig，与历史一致）；
4. 若 Tushare 返回空表（非 0 行），以非零退出码提示需要走交易所官网爬虫兜底。

用法（在仓库根目录执行）：
    python db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/20261006_fill_SHFE_missing_on_2025-05-26/01_pull_tushare_shfe_20250526.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from task_common import (  # noqa: E402
    TUSHARE_ENDPOINT,
    TUSHARE_FIELDS,
    TARGET_EXCHANGE,
    TARGET_TRADE_DATE,
    TASK_DIR,
    load_tushare_token,
    log,
    raw_csv_path,
    repo_relative,
)


RAW_JSON_PATH = TASK_DIR / f"tushare_fut_daily_{TARGET_EXCHANGE}_{TARGET_TRADE_DATE}.json"
RAW_CSV_COPY_PATH = TASK_DIR / f"tushare_fut_daily_{TARGET_EXCHANGE}_{TARGET_TRADE_DATE}.csv"
PULL_META_PATH = TASK_DIR / "01_pull_tushare_result.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-pull SHFE 20250526 futures daily bars from Tushare.")
    parser.add_argument("--exchange", default=TARGET_EXCHANGE)
    parser.add_argument("--trade-date", default=TARGET_TRADE_DATE)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-sleep-seconds", type=float, default=5.0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只落盘到任务目录，不覆盖 raw 分区文件。",
    )
    return parser.parse_args()


def call_tushare(token: str, exchange: str, trade_date: str) -> dict[str, Any]:
    """调用 Tushare fut_daily 接口，返回原始响应体。"""
    response = requests.post(
        TUSHARE_ENDPOINT,
        json={
            "api_name": "fut_daily",
            "token": token,
            "params": {"exchange": exchange, "trade_date": trade_date},
            "fields": ",".join(TUSHARE_FIELDS),
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def fetch_with_retries(
    token: str,
    exchange: str,
    trade_date: str,
    max_retries: int,
    retry_sleep_seconds: float,
) -> tuple[dict[str, Any] | None, str | None, int]:
    total_attempts = max_retries + 1
    for attempt in range(1, total_attempts + 1):
        try:
            payload = call_tushare(token, exchange, trade_date)
            return payload, None, attempt
        except Exception as exc:  # noqa: BLE001 - 单日失败不应中断流程
            log(f"retry exchange={exchange} trade_date={trade_date} attempt={attempt} {type(exc).__name__}: {exc}")
            if attempt < total_attempts:
                time.sleep(retry_sleep_seconds * attempt)
    return None, "max_retries_exhausted", total_attempts


def payload_to_frame(payload: dict[str, Any]) -> tuple[pd.DataFrame, list[str]]:
    """把 Tushare data 段还原成 DataFrame。"""
    data = payload.get("data") or {}
    fields = list(data.get("fields") or [])
    items = data.get("items") or []
    if not fields or not items:
        return pd.DataFrame(), fields
    return pd.DataFrame(items, columns=fields), fields


def main() -> None:
    args = parse_args()
    exchange = args.exchange.upper()
    trade_date = args.trade_date

    token = load_tushare_token()
    log(f"拉取 Tushare fut_daily exchange={exchange} trade_date={trade_date}")

    payload, error, attempts = fetch_with_retries(
        token, exchange, trade_date, args.max_retries, args.retry_sleep_seconds
    )
    if payload is None:
        raise RuntimeError(f"Tushare 请求失败：{error}")

    code = payload.get("code")
    msg = payload.get("msg")
    frame, fields = payload_to_frame(payload)
    log(f"Tushare 响应 code={code} msg={msg!r} fields={len(fields)} rows={len(frame)} attempts={attempts}")

    # 原样保存接口响应
    RAW_JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    record: dict[str, Any] = {
        "step": "01_pull_tushare",
        "exchange": exchange,
        "trade_date": trade_date,
        "endpoint": TUSHARE_ENDPOINT,
        "api_name": "fut_daily",
        "requested_fields": list(TUSHARE_FIELDS),
        "returned_fields": fields,
        "tushare_code": code,
        "tushare_msg": msg,
        "attempts": attempts,
        "rows": int(len(frame)),
        "fetched_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "raw_response_json": repo_relative(RAW_JSON_PATH),
        "previous_raw_file": repo_relative(raw_csv_path(exchange, trade_date)),
    }

    if code != 0:
        record.update({"status": "failed", "reason": f"tushare_code={code}", "msg": msg})
        PULL_META_PATH.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError(f"Tushare 返回错误 code={code} msg={msg}")

    if frame.empty:
        record.update({"status": "empty", "reason": "tushare returned 0 rows"})
        PULL_META_PATH.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(
            "Tushare 返回空表：需要改用上期所官网爬虫兜底（见 02_scrape_shfe_kx_fallback.py）。"
        )

    # 列顺序对齐历史 raw 表头
    frame = frame[list(TUSHARE_FIELDS)]
    frame["trade_date"] = frame["trade_date"].astype(str)

    # 任务目录内留一份副本
    frame.to_csv(RAW_CSV_COPY_PATH, index=False, encoding="utf-8-sig")

    if args.dry_run:
        record.update({"status": "ok_dry_run", "written_to": repo_relative(RAW_CSV_COPY_PATH)})
        PULL_META_PATH.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"dry-run 完成，未覆盖 raw 分区文件。记录：{PULL_META_PATH}")
        return

    # 覆盖 raw 分区文件（历史脚本使用 utf-8-sig + index=False，此处保持一致）
    target = raw_csv_path(exchange, trade_date)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.exists():
        backup = TASK_DIR / f"backup_trade_date={trade_date}.csv"
        backup.write_bytes(target.read_bytes())
    frame.to_csv(target, index=False, encoding="utf-8-sig")

    record.update(
        {
            "status": "ok",
            "written_to": repo_relative(target),
            "backup_of_empty_file": repo_relative(backup) if backup else None,
            "rows_by_ts_code_suffix": {
                str(k): int(v)
                for k, v in frame["ts_code"].astype(str).str.rsplit(".", n=1).str[-1].value_counts().items()
            },
        }
    )
    PULL_META_PATH.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"已写入 {target}（{len(frame)} 行）；记录：{PULL_META_PATH}")


if __name__ == "__main__":
    main()