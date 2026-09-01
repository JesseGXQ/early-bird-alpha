"""
增量因子计算接口 — 最小用例演示

演示 FactorState / IncrementalFactorResult / update / reset / to_dict / from_dict
的完整生命周期。使用一个单产品、lookback=3 的简单时间序列动量因子。

用法：
    python factors/demo_incremental.py

"""
from __future__ import annotations

from collections import deque
from typing import Any
import pandas as pd

from factors.models import FactorState, IncrementalFactorResult


# ============================================================
# 具体因子状态：保存每个品种的滚动价格窗口
# ============================================================

class MomentumState(FactorState):
    """
    时间序列动量因子的增量计算状态。

    Attributes
    ----------
    lookback : int
        动量回看期数。
    _price_windows : dict[str, deque[float]]
        每个品种的滚动收盘价窗口（最近 lookback 期）。
        使用 deque(maxlen=lookback) 自动维持窗口大小。
    """

    __slots__ = ('lookback', '_price_windows')

    def __init__(self, name: str = '', lookback: int = 20):
        super().__init__(name)
        self.lookback = lookback
        self._price_windows: dict[str, deque[float]] = {}

    def reset(self) -> None:
        """清空所有品种的滚动窗口，回到初始状态。"""
        self._price_windows.clear()

    def to_dict(self) -> dict[str, Any]:
        """序列化：将 deque 转为 list 以便 JSON 兼容。"""
        return {
            'name': self._name,
            'lookback': self.lookback,
            'price_windows': {
                product: list(window)
                for product, window in self._price_windows.items()
            },
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'MomentumState':
        """反序列化：从字典恢复 MomentumState，重建 deque。"""
        state = cls(name=d['name'], lookback=d['lookback'])
        state._price_windows = {
            product: deque(prices, maxlen=d['lookback'])
            for product, prices in d['price_windows'].items()
        }
        return state


# ============================================================
# 因子 update 逻辑
# ============================================================

def momentum_update(
    state: MomentumState,
    timestamp_df: pd.DataFrame,
    timestamp: Any,
) -> IncrementalFactorResult:
    """
    单步增量计算。

    对当前时间戳的每个品种：
    1. 将该品种的收盘价追加到滚动窗口。
    2. 若窗口已满（长度==lookback），计算动量值 = 今价/前lookback期价 - 1。
    3. 窗口不足则返回 NaN。

    Parameters
    ----------
    state : MomentumState
        因子当前状态（会被原地更新）。
    timestamp_df : pd.DataFrame
        当前时间戳的品种截面数据，索引为 product，
        必须包含 'Adj_Close' 列。
    timestamp : Any
        当前时间戳。

    Returns
    -------
    IncrementalFactorResult
    """
    price_column = 'Adj_Close'
    values: dict[str, float] = {}

    for product, row in timestamp_df.iterrows():
        close = row[price_column]

        # 追加新价格到该品种的滚动窗口；若首见该品种，自动创建 deque
        if product not in state._price_windows:
            state._price_windows[product] = deque(maxlen=state.lookback)
        state._price_windows[product].append(close)

        window = state._price_windows[product]
        if len(window) < state.lookback:
            values[product] = float('nan')   # 窗口不足，因子不可计算
        else:
            values[product] = close / window[0] - 1.0  # window[0] 是 lookback 期前的价格

    return IncrementalFactorResult(
        values=pd.Series(values, name=f'{state._name}_value'),
        state=state,
        timestamp=timestamp,
    )


# ============================================================
# 演示脚本
# ============================================================

if __name__ == '__main__':
    #
    # ---- 准备测试数据 ----
    # 单品种 RB，6 天的市场数据
    #
    prices = [100.0, 102.0, 105.0, 103.0, 108.0, 110.0]
    dates = pd.date_range('2026-07-01', periods=6, freq='B')

    print("=" * 60)
    print("1. 初始化状态（initialize_state）")
    print("=" * 60)
    state = MomentumState(name='ts_momentum_3', lookback=3)
    print(f"   因子名称: {state._name}")
    print(f"   回看窗口: {state.lookback}")
    print(f"   当前窗口: {state._price_windows}  ← 空字典")

    print()
    print("=" * 60)
    print("2. 逐时间戳调用 update（模拟日频扫描）")
    print("=" * 60)

    for i, (date, price) in enumerate(zip(dates, prices)):
        # 构造单时间戳的截面 DataFrame
        df_t = pd.DataFrame(
            {'Adj_Close': [price]},
            index=pd.Index(['RB'], name='product'),
        )
        result = momentum_update(state, df_t, timestamp=date)

        print(f"\n   t{i+1} = {date.date()}  |  价格 = {price}")
        print(f"   滚动窗口: {list(state._price_windows['RB'])}")
        print(f"   窗口长度: {len(state._price_windows['RB'])}/{state.lookback}")
        print(f"   因子值: {result.values['RB']:.4f}" if not pd.isna(result.values['RB']) else "   因子值: NaN (窗口不足)")
        print(f"   时间戳 : {result.timestamp}")

    print()
    print("=" * 60)
    print("3. 序列化（to_dict）—— 保存当前状态")
    print("=" * 60)
    saved = state.to_dict()
    print(f"   序列化结果: price_windows['RB'] = {saved['price_windows']['RB']}")
    print(f"   （deque 被转为 list，实现 JSON 兼容）")

    print()
    print("=" * 60)
    print("4. 重置状态（reset）—— 热身结束，重新开始")
    print("=" * 60)
    state.reset()
    print(f"   重置后窗口: {state._price_windows}")

    print()
    print("=" * 60)
    print("5. 反序列化（from_dict）—— 从序列化快照恢复")
    print("=" * 60)
    state = MomentumState.from_dict(saved)
    print(f"   恢复后 lookback: {state.lookback}")
    print(f"   恢复后窗口: list({state._price_windows['RB']}) = {list(state._price_windows['RB'])}")

    # 验证：用恢复后的状态继续增量计算，应与原先一致
    print()
    print("=" * 60)
    print("6. 验证：用恢复后的状态追加新一天数据")
    print("=" * 60)
    df_next = pd.DataFrame(
        {'Adj_Close': [112.0]},
        index=pd.Index(['RB'], name='product'),
    )
    result = momentum_update(state, df_next, timestamp=pd.Timestamp('2026-07-10'))
    print(f"   新价格: 112.0")
    print(f"   滚动窗口: {list(state._price_windows['RB'])}")
    # 恢复后的窗口为 [103, 108, 110]，加入 112 后 window[0]=108
    expected = 112.0 / 108.0 - 1.0
    print(f"   因子值: {expected:.4f}  ← 手工验算: 112 / 108 - 1 (window[0]=108)")
    print(f"   程序输出: {result.values['RB']:.4f}")

    print()
    print("=" * 60)
    print("演示完毕。")
    print("=" * 60)
