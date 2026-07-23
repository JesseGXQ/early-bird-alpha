from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence

from account.account import Account
from backtester.models import BarMarketEvent


class HistoryView:
    """只暴露已经进入系统的历史 Bar。"""

    def __init__(self):
        """初始化空的历史 Bar 容器。"""
        self._bars: deque[BarMarketEvent] = deque()

    def append(self, bar: BarMarketEvent) -> None:
        """追加一根已经进入系统的 Bar。"""
        self._bars.append(bar)

    def last(self, n: int) -> list[BarMarketEvent]:
        """返回最近 n 根历史 Bar。"""
        if n <= 0:
            raise ValueError("n 必须为正数。")
        return list(self._bars)[-n:]

    def __len__(self) -> int:
        """返回历史 Bar 数量。"""
        return len(self._bars)

    def __getitem__(self, index: int) -> BarMarketEvent:
        """按索引读取一根历史 Bar。"""
        return list(self._bars)[index]


class ReadOnlyAccountView:
    """
    策略可读的账户视图。
    
    仅能查询权益、品种净持仓。
    """

    def __init__(self, account: Account):
        """绑定策略可读的账户对象。"""
        self._account = account

    @property
    def equity(self) -> float:
        """返回当前账户权益。"""
        return self._account.equity

    def position_quantity(self, product: str) -> int:
        """返回指定品种的当前净持仓数量。"""
        return self._account.position_quantity(product)


class StrategyContext:
    """控制策略可见的信息边界。"""

    def __init__(self, account: Account):
        """初始化策略历史视图和账户只读视图。"""
        self.history = HistoryView()
        self.account = ReadOnlyAccountView(account)

    def on_bar(self, bar: BarMarketEvent) -> None:
        """将当前 Bar 纳入策略可见历史。"""
        self.history.append(bar)
