from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any

import pandas as pd

from account.models import AccountSnapshot, Fill
from portfolio.models import Order, TargetPosition


class PerformanceRecorder:
    """记录 V1 交易状态演化过程。"""

    def __init__(self):
        """初始化空的交易状态记录容器。"""
        self.targets: list[TargetPosition] = []
        self.orders: list[Order] = []
        self.fills: list[Fill] = []
        self.snapshots: list[AccountSnapshot] = []

    def record_target(self, target: TargetPosition) -> None:
        """记录策略生成的目标仓位。"""
        self.targets.append(target)

    def record_order(self, order: Order) -> None:
        """记录订单管理器生成的订单。"""
        self.orders.append(order)

    def record_fill(self, fill: Fill) -> None:
        """记录执行模型产生的成交。"""
        self.fills.append(fill)

    def record_snapshot(self, snapshot: AccountSnapshot) -> None:
        """记录某个 Bar 结束后的账户快照。"""
        self.snapshots.append(snapshot)

    def equity_curve(self) -> pd.DataFrame:
        """返回账户权益曲线 DataFrame。"""
        rows = [
            {
                "timestamp": snapshot.timestamp,
                "cash": snapshot.cash,
                "realized_pnl": snapshot.realized_pnl,
                "unrealized_pnl": snapshot.unrealized_pnl,
                "commission": snapshot.commission,
                "equity": snapshot.equity,
            }
            for snapshot in self.snapshots
        ]
        if not rows:
            return pd.DataFrame(columns=["cash", "realized_pnl", "unrealized_pnl", "commission", "equity"])
        return pd.DataFrame(rows).set_index("timestamp")

    def order_log(self) -> pd.DataFrame:
        """返回订单日志 DataFrame。"""
        return pd.DataFrame([asdict(order) for order in self.orders])

    def trade_log(self) -> pd.DataFrame:
        """返回成交日志 DataFrame。"""
        return pd.DataFrame([asdict(fill) for fill in self.fills])

    def position_history(self) -> pd.DataFrame:
        """返回历史持仓记录 DataFrame。"""
        rows = []
        for snapshot in self.snapshots:
            for position in snapshot.positions.values():
                row = asdict(position)
                row["timestamp"] = snapshot.timestamp
                rows.append(row)
        return pd.DataFrame(rows)

    def metrics(self, periods_per_year: int = 252) -> dict[str, float]:
        """计算基础绩效指标。"""
        curve = self.equity_curve()
        if curve.empty:
            return {
                "total_return": 0.0,
                "annualized_return": 0.0,
                "annualized_volatility": 0.0,
                "sharpe": 0.0,
                "max_drawdown": 0.0,
                "trade_count": 0.0,
                "total_commission": 0.0,
                "net_pnl": 0.0,
            }
        equity = curve["equity"]
        returns = equity.pct_change().dropna()
        total_return = equity.iloc[-1] / equity.iloc[0] - 1 if equity.iloc[0] else 0.0
        annualized_return = (1 + total_return) ** (periods_per_year / max(len(equity), 1)) - 1
        annualized_volatility = returns.std(ddof=0) * math.sqrt(periods_per_year) if not returns.empty else 0.0
        sharpe = annualized_return / annualized_volatility if annualized_volatility else 0.0
        drawdown = equity / equity.cummax() - 1
        total_commission = sum(fill.commission for fill in self.fills)
        return {
            "total_return": float(total_return),
            "annualized_return": float(annualized_return),
            "annualized_volatility": float(annualized_volatility),
            "sharpe": float(sharpe),
            "max_drawdown": float(drawdown.min()),
            "trade_count": float(len(self.fills)),
            "total_commission": float(total_commission),
            "net_pnl": float(equity.iloc[-1] - equity.iloc[0]),
        }

    def quantstats_report(
        self,
        report: str = "metrics",
        benchmark: pd.Series | None = None,
        output: str | None = None,
        periods_per_year: int = 252,
        rf: float = 0.0,
        **kwargs: Any,
    ) -> pd.DataFrame | str:
        """使用 quantstats 基于账户收益率生成绩效报告。"""
        try:
            import quantstats as qs
        except ImportError as exc:
            raise ImportError("quantstats is required for quantstats_report(). Install it with `pip install quantstats`.") from exc

        curve = self.equity_curve()
        if curve.empty:
            return pd.DataFrame()

        returns = curve["equity"].pct_change().dropna()
        returns.name = "strategy"
        if returns.empty:
            return pd.DataFrame()

        if report == "metrics":
            return qs.reports.metrics(
                returns,
                benchmark=benchmark,
                rf=rf,
                display=False,
                periods_per_year=periods_per_year,
                **kwargs,
            )
        if report == "html":
            if output is None:
                raise ValueError("output is required when report='html'.")
            qs.reports.html(
                returns,
                benchmark=benchmark,
                output=output,
                rf=rf,
                periods_per_year=periods_per_year,
                **kwargs,
            )
            return output
        raise ValueError("report must be either 'metrics' or 'html'.")
