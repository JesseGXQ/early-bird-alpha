from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class BarMarketEvent:
    """一根已结束、可被策略观察的 Bar。"""

    product: str
    contract: str
    timestamp: pd.Timestamp
    trading_date: date
    adj_open: float
    adj_high: float
    adj_low: float
    adj_close: float
    raw_open: float
    raw_high: float
    raw_low: float
    raw_close: float
    is_roll: bool = False

