"""本目录公共配置。

补数任务：SHFE 日频快照缺失 2025-05-26，重新拉取并重写 SHFE 各品种日频快照表。
"""

from __future__ import annotations

import re
from pathlib import Path


# 本文件位于 <repo>/db/market/daily_snapshots/bar_1d/bar_1d_tushare/raw/<TASK_DIR>/
# parents[0]=raw, [1]=bar_1d_tushare, [2]=bar_1d, [3]=daily_snapshots, [4]=market, [5]=db, [6]=repo root
TASK_DIR = Path(__file__).resolve().parent
REPO_ROOT = TASK_DIR.parents[6]
TUSHARE_ROOT = TASK_DIR.parents[1]
BAR_1D_ROOT = TASK_DIR.parents[2]

# Tushare 原始落地目录（分区格式 exchange=XXX/trade_date=YYYYMMDD.csv）
TUSHARE_RAW_DIR = TASK_DIR.parent / "fut_daily"
# 清洗输出目录
CLEANED_DIR = TUSHARE_ROOT / "cleaned"

CREDENTIAL_FILE = REPO_ROOT / "db" / "rules_ingestion" / "supplements" / "API凭证.txt"
TUSHARE_ENDPOINT = "http://api.tushare.pro"

# 上期所结算参数历史爬取结果（用于交叉校验结算价）
SHFE_SETTLEMENT_DIR = (
    REPO_ROOT
    / "db"
    / "rules_ingestion"
    / "data"
    / "raw"
    / "exchange_settlement_data_scraping"
    / "exchange=SHFE"
)

TARGET_EXCHANGE = "SHFE"
TARGET_TRADE_DATE = "20250526"
TARGET_DATE = "2025-05-26"

# Tushare fut_daily 返回字段（与历史 raw csv 表头一致）
TUSHARE_FIELDS = [
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


def repo_relative(path: Path) -> str:
    """把路径转成相对仓库根目录的 posix 风格字符串，便于写报告。"""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def load_tushare_token() -> str:
    """从凭证文件中解析 Tushare token。"""
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
        elif len(value) >= 32 and re.fullmatch(r"[0-9A-Za-z]+", value):
            candidates.append(value)

    for candidate in candidates:
        if len(candidate) >= 32 and re.fullmatch(r"[0-9A-Za-z]+", candidate):
            return candidate
    raise RuntimeError(f"未能在 {CREDENTIAL_FILE} 中解析出 Tushare token")


def raw_csv_path(exchange: str, trade_date: str) -> Path:
    """Tushare raw 分区文件的落地路径。"""
    return TUSHARE_RAW_DIR / f"exchange={exchange}" / f"trade_date={trade_date}.csv"


def log(message: str) -> None:
    from datetime import datetime

    print(f"[{datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)