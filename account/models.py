from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from portfolio.models import Side


@dataclass(frozen=True)
class Fill:
    """市场已经确认发生的成交事实。"""

    fill_id: str
    order_id: str
    product: str
    contract: str
    side: Side
    quantity: int
    price: float
    timestamp: pd.Timestamp
    commission: float = 0.0


@dataclass
class Position:
    """V1 不区分今昨仓的单合约净持仓。"""

    product: str
    contract: str
    quantity: int = 0
    average_price: float = 0.0
    multiplier: int = 1


@dataclass(frozen=True)
class PositionSnapshot:
    product: str
    contract: str
    quantity: int
    average_price: float
    multiplier: int


@dataclass(frozen=True)
class AccountSnapshot:
    timestamp: pd.Timestamp
    cash: float
    realized_pnl: float
    unrealized_pnl: float
    commission: float
    equity: float
    positions: dict[str, PositionSnapshot] #key为合约代码


PositionSide = Literal["LONG", "SHORT", "FLAT"]

