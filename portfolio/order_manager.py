from __future__ import annotations

from itertools import count

from account.account import Account
from backtester.models import BarMarketEvent
from portfolio.models import Order, TargetPosition


class OrderManager:
    """根据目标仓位和账户实际仓位生成市价订单。"""

    def __init__(self):
        self._order_seq = count(1)

    def create_orders(
        self,
        target: TargetPosition,
        account: Account,
        market: BarMarketEvent,
    ) -> list[Order]:
        """
        根据目标仓位和账户现有仓位（差异）生成新订单：
        
        * 换月期：先平掉旧主力持仓，再在新主力上根据目标仓位和账户现有仓位差异做开仓。
        
        * 非换月期：根据目标仓位和账户现有仓位差异做开仓。
        """
        product = target.product
        market_contract = market.contract
        orders: list[Order] = []

        # 最小换月：先把同品种非当前主力的持仓平掉。
        for pos in account.positions_for_product(product):
            if pos.contract == market_contract:
                continue
            side = "SELL" if pos.quantity > 0 else "BUY"
            orders.append(
                self._new_order(
                    product=product,
                    contract=pos.contract,
                    side=side,
                    quantity=abs(pos.quantity),
                    created_at=market.timestamp,
                )
            )

        current_market_qty = account.get_position(market_contract).quantity if account.get_position(market_contract) else 0
        delta = target.target_quantity - current_market_qty
        if delta > 0:
            orders.append(
                self._new_order(product, market_contract, "BUY", delta, market.timestamp)
            )
        elif delta < 0:
            orders.append(
                self._new_order(product, market_contract, "SELL", abs(delta), market.timestamp)
            )
        return orders

    def create_order(
        self,
        target: TargetPosition,
        account: Account,
        market: BarMarketEvent,
    ) -> Order | None:
        """非换月期生成订单。内部直接调用换月期生成订单方法。"""
        orders = self.create_orders(target=target, account=account, market=market)
        if len(orders) > 1:
            raise ValueError("create_order 仅适用于单订单场景；换月场景请使用 create_orders。")
        return orders[0] if orders else None

    def _new_order(
        self,
        product: str,
        contract: str,
        side: str,
        quantity: int,
        created_at,
    ) -> Order:
        return Order(
            order_id=f"O{next(self._order_seq):08d}",
            product=product,
            contract=contract,
            side=side,  # type: ignore[arg-type]
            quantity=quantity,
            created_at=created_at,
        )

