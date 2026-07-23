from __future__ import annotations

from abc import ABC, abstractmethod

from backtester.models import BarMarketEvent
from portfolio.models import TargetPosition
from strategy.context import StrategyContext


class Strategy(ABC):
    """交易策略基类。必须实现其下的 on_bar 方法。"""

    @abstractmethod
    def on_bar(
        self,
        event: BarMarketEvent,
        context: StrategyContext,
    ) -> TargetPosition | None:
        raise NotImplementedError

