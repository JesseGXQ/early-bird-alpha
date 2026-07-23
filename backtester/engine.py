from __future__ import annotations

from collections.abc import Iterable

from account.account import Account
from analytics.recorder import PerformanceRecorder
from backtester.models import BarMarketEvent
from executor.execution_model import NextBarOpenExecutionModel
from portfolio.models import Order
from portfolio.order_manager import OrderManager
from strategy.base import Strategy
from strategy.context import StrategyContext


class BacktestEngine:
    """V1 Bar 级最小闭环调度器。"""

    def __init__(
        self,
        market_feed: Iterable[BarMarketEvent],
        strategy: Strategy,
        account: Account,
        execution_model: NextBarOpenExecutionModel,
        order_manager: OrderManager,
        recorder: PerformanceRecorder | None = None,
    ):
        self.market_feed = market_feed
        self.strategy = strategy
        self.account = account
        self.execution_model = execution_model
        self.order_manager = order_manager
        self.recorder = recorder or PerformanceRecorder()
        self.pending_orders: list[Order] = []
        self.strategy_context = StrategyContext(account)

    def run(self) -> PerformanceRecorder:
        for bar in self.market_feed:
            # 1. 执行待处理订单
            fills = self.execution_model.execute_pending_orders(self.pending_orders, bar)
            # 2. 账户执行成交；记录器记录成交
            for fill in fills:
                self.account.apply_fill(fill)
                self.recorder.record_fill(fill)
            # 3. 清除所有待处理订单
            self.pending_orders.clear()
            # 4. 将账户根据新 Bar （市场状态）更新 
            self.account.mark_to_market(bar)
            # 5. 根据 Bar 更新策略上下文
            self.strategy_context.on_bar(bar)
            # 6. 策略生成目标仓位
            target = self.strategy.on_bar(bar, self.strategy_context)
            if target is not None:
                self.recorder.record_target(target) # 记录器记录目标仓位
                # 7. 创建新订单
                orders = self.order_manager.create_orders(
                    target=target,
                    account=self.account,
                    market=bar,
                )
                # 8. 将新订单合并到待处理订单队列；记录器记录订单
                for order in orders:
                    self.pending_orders.append(order)
                    self.recorder.record_order(order)
            
            # 9. 根据该 Bar 生成账户快照，并在记录器里记录
            snapshot = self.account.snapshot(timestamp=bar.timestamp)
            self.recorder.record_snapshot(snapshot)

        return self.recorder

