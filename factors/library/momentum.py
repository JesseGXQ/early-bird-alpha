"""动量因子。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Literal

import numpy as np
import pandas as pd

from factors.library.base import Factor
from factors.models import DataRequirement, FactorState, IncrementalFactorResult

class MomentumState(FactorState):
    """增量接口，动量因子状态类。"""

    __slots__ = ('lookback', 'product_returns')

    def __init__(self, lookback: int, name: str):
        super().__init__(name=name)
        self.lookback = lookback
        self.product_returns: dict[str, tuple[float, ...]] = {} # key is product

    def push(self, product: str, ret: float) -> None:
        """将最新收益率值追加到品种历史窗口。"""
        if product not in self.product_returns: # 若是第一期，则初始化
            self.product_returns[product] = (ret,)
            return
        rets = self.product_returns[product]
        rets += (ret, ) # 追加最新的一期收益率值
        if len(rets) > self.lookback: # 历史数据个数超出窗口长度
            rets = rets[-self.lookback:] # 取最新 lookback 个值
        self.product_returns[product] = rets

    def ready(self, product: str) -> bool:
        """品种历史窗口内数据是否足够产出因子值。"""
        return len(self.product_returns.get(product, ())) == self.lookback

    def compute_window(self, product: str) -> float:
        """计算品种窗口内的动量因子值（累计收益率）。"""
        rets = self.product_returns.get(product)
        if rets is None or len(rets) != self.lookback:
            return float('nan')
        return float(np.prod(1.0 + np.array(rets)) - 1.0)

    def reset(self) -> None:
        self.product_returns.clear()

    def to_dict(self) -> dict[str, Any]:
        re_dict = super().to_dict() # 父类包含一个 name 属性
        re_dict['lookback'] = self.lookback
        re_dict['product_returns'] = self.product_returns
        return re_dict

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MomentumState:
        momstate = cls(lookback=d['lookback'], name=d['name'])
        momstate.product_returns = d['product_returns']
        return momstate
    

class MomentumFactor(Factor):
    """动量因子类。"""

    def __init__(self, lookback: int):
        self.lookback = lookback

    @property
    def name(self, ) -> str:
        return f'momentum_{self.lookback}'

    @property
    def computation_scope(self,) -> Literal['per_product']:
        return 'per_product'

    @property
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        return (
    DataRequirement(source='main_panel', columns=('True_Return',)),
    )

    @property
    def warmup_periods(self) -> int | None:
        return None # 窗口类因子，窗口填满即收敛，故暖机参数不适用

    def compute(self, data: pd.DataFrame) -> pd.Series:
        """
        批量计算接口。

        Parameters
        ---
        data: pd.DataFrame
            MultiIndex[timetamp, product]，包含 `True_Return` 列。

        Returns
        ---
        pd.Series
            因子Series。
        """
        def cumulative_ret_window(ret_series: pd.Series) -> pd.Series:
            """内部辅助函数：根据收益率序列计算窗口内的累计收益率。"""
            return (
                (1 + ret_series).rolling(self.lookback).
                apply(np.prod, raw=True) -1 # 此处的rolling方法的min_periods默认为窗口长度，即第 1 个直到 lookback-1 个值都为nan
            )

        ret_series = data['True_Return']
        momentum = (ret_series
                .groupby('product', group_keys=False)
                .apply(cumulative_ret_window))
        momentum.name = self.name
        return momentum

    def initialize_state(self) -> MomentumState:
        """增量接口，初始化动量因子状态。"""
        return MomentumState(lookback=self.lookback, name=self.name)

    def update(self, state: FactorState, 
               timestamp_data: pd.DataFrame) -> IncrementalFactorResult:
        """增量接口，根据最新一期数据，计算最新因子值。返回增量因子结果。"""
        state: MomentumState = state 
        timestamp_df = timestamp_data

        # 初始化{品种：（新）因子值} 的字典
        product_value: dict[str, float] = {}
        # 获取 timestamp
        timestamp = timestamp_df.index.get_level_values('timestamp')[0]

        # 获取每个品种的新一期数据（True_Return），并计算（新）因子值
        products: list[str] = timestamp_df.index.get_level_values('product').tolist()
        for product in products:
            ret: float = timestamp_df.at[(timestamp, product), 'True_Return']
            state.push(product, ret) # 追加新一期收益率数据
            value = state.compute_window(product) if state.ready(product) else np.nan # 若准备就绪，则计算新一期因子值
            product_value[product] = value

        values = pd.Series(product_value, name=self.name)
        return IncrementalFactorResult(values=values, state=state, timestamp=timestamp)

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {'lookback':self.lookback}


    





        


    
    
    
    
        
    
    
    

