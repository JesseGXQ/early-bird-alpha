from __future__ import annotations

from typing import Literal

from portfolio.models import Side


class CommissionModel:
    """V1 简化手续费模型。"""

    def calculate(self, contract: str, side: Side, quantity: int, price: float) -> float:
        raise NotImplementedError


class FixedPerContractCommissionModel(CommissionModel):
    def __init__(self, commission_per_contract: float = 0.0):
        self.commission_per_contract = float(commission_per_contract)

    def calculate(self, contract: str, side: Side, quantity: int, price: float) -> float:
        return abs(quantity) * self.commission_per_contract


class NotionalRateCommissionModel(CommissionModel):
    def __init__(self, rate: float, multiplier_provider):
        self.rate = float(rate)
        self.multiplier_provider = multiplier_provider

    def calculate(self, contract: str, side: Side, quantity: int, price: float) -> float:
        return abs(quantity) * price * self.multiplier_provider(contract) * self.rate

