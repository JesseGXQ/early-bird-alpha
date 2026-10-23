"""因子定义抽象基类和校验函数。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Literal, Mapping

from factors.models import DataRequirement, FactorState, IncrementalFactorResult

if TYPE_CHECKING:
    import pandas as pd

class Factor(ABC):
    """因子定义的抽象基类。
    必须实现的方法： `name`(property), `computation_scope`(property),
    `data_requirements(property)` , `compute`, `parameters`(property)。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def computation_scope(self) -> Literal['per_product', 'cross_sectional']:
        ...

    @property
    @abstractmethod
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        ...

    @property
    def warmup_periods(self) -> int | None:
        """暖机窗口大小。这里的暖机，指的是能产出数值但未至稳态。"""
        return None

    @abstractmethod
    def compute(self, data: Any) -> pd.Series:
        """因子计算批量接口"""
        ...

    def initialize_state(self) -> FactorState:
        """初始化因子运行状态"""
        raise NotImplementedError()

    def update(self, state: FactorState, timestamp_data: Any) -> IncrementalFactorResult:
        """根据新的时间戳数据，更新因子状态。"""
        raise NotImplementedError()

    @property
    @abstractmethod
    def parameters(self) -> Mapping[str, Any]:
        """返回因子构建的参数映射。"""
        ...
