from __future__ import annotations

from itertools import count

from account.models import Fill
from backtester.models import BarMarketEvent
from executor.commission import CommissionModel, FixedPerContractCommissionModel
from executor.slippage import FixedSlippageModel
from portfolio.models import Order


class NextBarOpenExecutionModel:
    """市价单在下一根 Bar 开盘，以开盘价加上手续费和滑点，做全部成交。"""

    def __init__(
        self,
        slippage_model: FixedSlippageModel | None = None,
        commission_model: CommissionModel | None = None,
    ):
        self.slippage_model = slippage_model or FixedSlippageModel()
        self.commission_model = commission_model or FixedPerContractCommissionModel()
        self._fill_seq = count(1)

    def execute(self, order: Order, bar: BarMarketEvent) -> Fill:
        slippage = self.slippage_model.calculate(order.side, bar.raw_open)
        if order.side == "BUY":
            price = bar.raw_open + slippage
        else:
            price = bar.raw_open - slippage
        commission = self.commission_model.calculate(
            contract=order.contract,
            side=order.side,
            quantity=order.quantity,
            price=price,
        )
        return Fill(
            fill_id=f"F{next(self._fill_seq):08d}",
            order_id=order.order_id,
            product=order.product,
            contract=order.contract,
            side=order.side,
            quantity=order.quantity,
            price=price,
            timestamp=bar.timestamp,
            commission=commission,
        )

    def execute_pending_orders(
        self,
        pending_orders: list[Order],
        bar: BarMarketEvent,
    ) -> list[Fill]:
        return [self.execute(order, bar) for order in pending_orders]

