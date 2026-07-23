from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


@dataclass(frozen=True)
class BacktestConfig:
    """V1 单品种 Bar 级回测配置。"""

    product: str
    start_date: date
    end_date: date
    freq: str = "1d"
    view: Literal["scheduled", "realized"] = "scheduled"
    initial_cash: float = 1_000_000.0
    commission_per_contract: float = 0.0
    slippage_per_contract: float = 0.0

