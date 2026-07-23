from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from account.models import AccountSnapshot, Fill, Position, PositionSnapshot
from backtester.models import BarMarketEvent


MultiplierProvider = Callable[[str], int]


class Account:
    """V1 账户是现金、持仓和 PnL 的唯一权威来源。"""

    def __init__(
        self,
        initial_cash: float,
        multiplier_provider: MultiplierProvider | None = None,
    ):
        """初始化 V1 最小账户状态。"""
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.commission = 0.0
        self._positions: dict[str, Position] = {} # key为合约代码
        self._multiplier_provider = multiplier_provider or (lambda contract: 1)

    @property
    def equity(self) -> float:
        """返回当前账户权益。"""
        return self.cash + self.unrealized_pnl

    def position_quantity(self, product: str) -> int:
        """返回指定品种的净持仓数量。"""
        return sum(pos.quantity for pos in self._positions.values() if pos.product == product.upper())

    def positions_for_product(self, product: str) -> list[Position]:
        """返回指定品种下所有非零物理合约持仓。"""
        product = product.upper()
        return [pos for pos in self._positions.values() if pos.product == product and pos.quantity != 0]

    def get_position(self, contract: str) -> Position | None:
        """返回指定物理合约的当前持仓。"""
        return self._positions.get(contract.upper())

    def apply_fill(self, fill: Fill) -> None:
        """将一笔成交应用到账户并更新持仓（数量和平均成本）、现金和已实现盈亏。"""
        if fill.quantity <= 0:
            raise ValueError("Fill quantity 必须为正数。")

        product = fill.product.upper()
        contract = fill.contract.upper()
        signed_qty = fill.quantity if fill.side == "BUY" else -fill.quantity
        multiplier = self._multiplier_provider(contract)
        pos = self._positions.get(contract)
        if pos is None:
            pos = Position(product=product, contract=contract, multiplier=multiplier)
            self._positions[contract] = pos

        old_qty = pos.quantity
        old_avg = pos.average_price
        new_qty = old_qty + signed_qty

        self.commission += fill.commission
        self.cash -= fill.commission
        
        # 若该笔成交的合约的旧持仓为零，或旧持仓和新成交同向（旧头寸为多头+新开多仓；旧头寸为空头+新开空仓）
        if old_qty == 0 or old_qty * signed_qty > 0:
            self._apply_increase(pos, old_qty, old_avg, signed_qty, fill.price)
            return
        
        # 若旧持仓和新成交反向
        closing_qty = min(abs(old_qty), abs(signed_qty)) # 平调的仓位
        pnl_per_unit = fill.price - old_avg if old_qty > 0 else old_avg - fill.price # 每手的 PnL
        realized = pnl_per_unit * closing_qty * multiplier # 该笔成交后，实现的PnL
        self.realized_pnl += realized # 更新 PnL
        self.cash += realized # 更新现金账户余额

        if new_qty == 0: # （该笔成交后）净头寸归零
            pos.quantity = 0
            pos.average_price = 0.0
        elif old_qty * new_qty > 0: # 净头寸方向保持不变（即部分减仓），此时成本价不变
            pos.quantity = new_qty
        else: # 净头寸变为反向
            pos.quantity = new_qty
            pos.average_price = fill.price 

    def mark_to_market(self, bar: BarMarketEvent) -> None:
        """使用当前 Bar 的原始收盘价更新未实现盈亏。"""
        total = 0.0
        for pos in self._positions.values():
            if pos.quantity == 0:
                continue
            if pos.contract != bar.contract:
                continue
            if pos.quantity > 0: # 多头
                total += (bar.raw_close - pos.average_price) * pos.quantity * pos.multiplier
            else: # 空头
                total += (pos.average_price - bar.raw_close) * abs(pos.quantity) * pos.multiplier
        self.unrealized_pnl = total

    def snapshot(self, timestamp: pd.Timestamp) -> AccountSnapshot:
        """生成当前账户状态的不可变快照。"""
        positions = {
            contract: PositionSnapshot(
                product=pos.product,
                contract=pos.contract,
                quantity=pos.quantity,
                average_price=pos.average_price,
                multiplier=pos.multiplier,
            )
            for contract, pos in self._positions.items()
            if pos.quantity != 0
        }
        return AccountSnapshot(
            timestamp=timestamp,
            cash=self.cash,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            commission=self.commission,
            equity=self.equity,
            positions=positions,
        )

    @staticmethod
    def _apply_increase(
        pos: Position,
        old_qty: int,
        old_avg: float,
        signed_qty: int,
        price: float,
    ) -> None:
        """处理同方向增仓或空仓开仓的持仓成本+仓位更新。"""
        new_qty = old_qty + signed_qty
        if old_qty == 0:
            pos.quantity = new_qty
            pos.average_price = price
            return
        pos.average_price = (
            old_avg * abs(old_qty) + price * abs(signed_qty)
        ) / abs(new_qty)
        pos.quantity = new_qty
