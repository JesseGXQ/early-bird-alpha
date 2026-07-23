from __future__ import annotations

from dataclasses import dataclass

from backtester.models import BarMarketEvent
from portfolio.models import TargetPosition
from strategy.base import Strategy
from strategy.context import StrategyContext


@dataclass(frozen=True)
class MomentumConfig:
    lookback: int = 20
    quantity: int = 1


class TimeSeriesMomentumStrategy(Strategy):
    """使用 Adj_Close 的时间序列动量策略。"""

    def __init__(self, config: MomentumConfig = MomentumConfig()):
        if config.lookback <= 0:
            raise ValueError("lookback 必须为正数。")
        if config.quantity < 0:
            raise ValueError("quantity 不能为负数。")
        self.config = config

    def on_bar(
        self,
        event: BarMarketEvent,
        context: StrategyContext,
    ) -> TargetPosition | None:
        if len(context.history) < self.config.lookback + 1:
            return None
        past = context.history[-self.config.lookback - 1].adj_close
        current = context.history[-1].adj_close
        momentum = current / past - 1
        if momentum > 0:
            target_quantity = self.config.quantity
        elif momentum < 0:
            target_quantity = -self.config.quantity
        else:
            target_quantity = 0
        return TargetPosition(
            product=event.product,
            target_quantity=target_quantity,
            generated_at=event.timestamp,
        )

