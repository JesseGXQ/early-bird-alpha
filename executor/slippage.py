from __future__ import annotations

from portfolio.models import Side


class FixedSlippageModel:
    """固定价格单位滑点。"""

    def __init__(self, slippage_per_contract: float = 0.0):
        self.slippage_per_contract = float(slippage_per_contract)

    def calculate(self, side: Side, raw_open: float) -> float:
        return self.slippage_per_contract

