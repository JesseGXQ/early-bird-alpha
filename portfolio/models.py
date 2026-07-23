from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class TargetPosition:
    """策略希望最终持有的品种级目标仓位。"""

    product: str
    target_quantity: int
    generated_at: pd.Timestamp


@dataclass(frozen=True)
class Order:
    """系统准备向市场表达的交易意图。"""

    order_id: str
    product: str
    contract: str
    side: Side
    quantity: int
    created_at: pd.Timestamp

