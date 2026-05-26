# SimulationFeed 设计规划

**模块定位**：Data Engine 面向回测引擎（Execution 模块）的唯一实盘流式接口，负责按时间顺序分发 tick 数据，并在换月期同时下发新旧主力合约的快照。

---

## 一、核心数据结构

### ContractTick
单个合约的单笔 tick 数据。

```python
@dataclass(frozen=True)
class ContractTick:
    """
    单个合约的单笔 tick 数据。

    Parameters
    ----------
    contract : str
        合约代码，例如 "RB2505"。
    timestamp : pd.Timestamp
        tick 的日历时间戳。
    last : float
        最新价。
    bid : float
        买一价。
    ask : float
        卖一价。
    volume : int
        成交量。
    open_interest : float
        持仓量。
    """
    contract: str
    timestamp: pd.Timestamp
    last: float
    bid: float
    ask: float
    volume: int
    open_interest: float
```

### MarketSnapshot
某一时刻向回测引擎下发的完整市场快照，包含新旧主力合约的状态。

```python
@dataclass(frozen=True)
class MarketSnapshot:
    """
    某一时刻的市场快照，向回测引擎下发。

    Parameters
    ----------
    timestamp : pd.Timestamp
        快照的日历时间戳，严格单调递增。
    trading_date : datetime.date
        该快照归属的 Trading Date。
    dominant : ContractTick
        当前主力合约的 tick（ZOH 填充）。
    outgoing : ContractTick or None
        换月期间的旧主力合约 tick（ZOH 填充）。
        非换月期为 None。
    is_roll_period : bool
        是否处于换月期。
    """
    timestamp: pd.Timestamp
    trading_date: datetime.date
    dominant: ContractTick
    outgoing: ContractTick | None
    is_roll_period: bool
```

---

## 二、核心算法：Min-Heap 多流调度 + ZOH

### 算法概述

```
输入：
  - 旧主力合约的 tick Generator（换月期存在）
  - 新主力合约的 tick Generator

核心循环：
  1. 将两个 Generator 的当前头部 tick 压入 Min-Heap
     排序键：(timestamp, priority)
     priority：旧主力 = 0，新主力 = 1（同时刻时旧主力优先弹出）

  2. 弹出时间戳最小的 tick，暂存

  3. 检查堆顶：若下一个 tick 时间戳与当前相同，继续弹出并暂存
     （合并同一时刻的多个 tick，避免重复下发同一时刻的快照）

  4. ZOH 填充：
     对于本时刻未产生新 tick 的合约，保留其上一笔快照数据

  5. 组装 MarketSnapshot，下发给回测引擎

  6. 对已弹出的每个 Generator，推进一步（取下一个 tick 压回堆）

  7. 重复，直到堆为空
```

### 伪代码

```python
import heapq
from typing import Generator

def _stream(
    self,
    dominant_gen: Generator[ContractTick, None, None],
    outgoing_gen: Generator[ContractTick, None, None] | None,
    is_roll_period: bool,
) -> Generator[MarketSnapshot, None, None]:

    heap = []
    zoh: dict[str, ContractTick] = {}  # 各合约的 ZOH 状态

    # 初始化堆：将两个 Generator 的第一个 tick 压入
    # 堆元素：(timestamp, priority, contract_label, tick)
    # priority：旧主力 = 0，新主力 = 1
    def push_next(gen, priority, label):
        try:
            tick = next(gen)
            heapq.heappush(heap, (tick.timestamp, priority, label, tick, gen))
        except StopIteration:
            pass  # Generator 耗尽，不压入

    push_next(dominant_gen, priority=1, label="dominant")
    if outgoing_gen:
        push_next(outgoing_gen, priority=0, label="outgoing")

    while heap:
        # 弹出最小时间戳的 tick
        ts, _, label, tick, gen = heapq.heappop(heap)
        batch = {label: tick}

        # 检查同一时刻是否有另一合约的 tick，合并
        while heap and heap[0][0] == ts:
            _, _, label2, tick2, gen2 = heapq.heappop(heap)
            batch[label2] = tick2
            push_next(gen2, ...)  # 推进该 Generator

        # ZOH 更新
        zoh.update(batch)

        # 组装快照
        snapshot = MarketSnapshot(
            timestamp=ts,
            trading_date=self._calendar.get_trading_date(ts),
            dominant=zoh["dominant"],
            outgoing=zoh.get("outgoing"),
            is_roll_period=is_roll_period,
        )

        yield snapshot

        # 推进本次弹出的 Generator
        push_next(gen, ...)
```

---

## 三、需要注意的细节

1. **双流边界的精确定义**
   换月日的双流应从该 Trading Session 的第一个 tick 开始，即换月日夜盘的第一个 tick（日历日为换月日前一天）。回测引擎需要在换月日的完整价格信息基础上做换仓决策，因此不能推迟到日盘才开始双流。

2. **同一时刻双 tick 的合并逻辑**
   当两个合约在同一时间戳都有 tick 时，必须合并成一个 `MarketSnapshot` 一次性下发，而不是分两次下发。分两次下发会导致回测引擎在同一时刻收到两个快照，破坏时间单调性语义。

3. **时间戳严格单调递增**
   `MarketSnapshot` 的 `timestamp` 必须严格单调递增，绝对不允许出现 T+1 的快照先于 T 的快照下发。Min-Heap 在单交易日内自动保证这一点，但需要额外注意跨交易日边界（夜盘第一个 tick 的日历时间戳）不发生跳变或乱序。

4. **旧主力 Generator 耗尽后的优雅降级**
   换月结束后旧主力的 Generator 会自然耗尽（`StopIteration`）。此时应将其从堆中移除，后续快照的 `outgoing` 字段置为 `None`，`is_roll_period` 置为 `False`，切换回单流模式，不抛出异常。

5. **ZOH 的初始状态处理**
   在第一个 tick 到达之前，ZOH 状态为空。需要定义初始快照的处理策略：
   - 选项 A：在第一个 tick 到达之前不下发任何快照（推荐）
   - 选项 B：用前一交易日的收盘价初始化 ZOH
   
   选项 A 更简单，回测引擎应能处理"数据从第一个 tick 开始"的语义。

6. **Trading Date 标签的正确映射**
   每个 `MarketSnapshot` 需要附上正确的 `trading_date`。若有需要，应通过 `TradingCalendar.get_trading_date(calendar_timestamp)` 转换。夜盘 tick 的日历时间戳属于前一自然日，但 `trading_date` 应归属下一个交易日，此处必须经过 `TradingCalendar` 转换，不能直接用 `timestamp.date()`。

---

## 四、SimulationFeed 类接口设计

```python
class SimulationFeed:
    """
    回测事件分发器，Data Engine 面向回测引擎的唯一流式接口。

    按时间顺序分发 MarketSnapshot，换月期同时下发新旧主力合约数据。

    Parameters
    ----------
    symbol_root : str
        期货品种代码，例如 "RB"。
    calendar : TradingCalendar
        交易日历，用于 Trading Date 映射和交易时段过滤。
    bar_loader : BarLoader
        底层 tick/bar 数据读取器。
    contract_manager : ContinuousContractManager
        换月总表管理器，提供每日主力合约和换月信息。

    Methods
    -------
    stream(start_date, end_date)
        返回指定交易日范围内的 MarketSnapshot Generator。
    """

    def __init__(
        self,
        symbol_root: str,
        calendar: TradingCalendar,
        bar_loader: BarLoader,
        contract_manager: ContinuousContractManager,
    ): ...

    def stream(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> Generator[MarketSnapshot, None, None]:
        """
        按时间顺序下发 MarketSnapshot。

        Parameters
        ----------
        start_date : datetime.date
            起始交易日（Trading Date，含）。
        end_date : datetime.date
            结束交易日（Trading Date，含）。

        Yields
        ------
        MarketSnapshot
            包含当前时刻所有主力合约状态的市场快照，
            timestamp 严格单调递增。
        """
        ...
```

---

## 五、文件位置

```
data/
└── feed/
    ├── feed.py          # ResearchFeed（已有）
    ├── aggregator.py    # BarAggregator（已有）
    └── simulation.py    # SimulationFeed ← 新建
```

`SimulationFeed` 与 `ResearchFeed` 同属 `feed/` 子模块，共享 `BarLoader`、`TradingCalendar` 等基础设施依赖，通过 `factory.py` 组装后由 `facade.py` 对外暴露。
