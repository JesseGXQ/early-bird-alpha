"""因子定义抽象基类和校验函数。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Literal, Mapping

from factors.models import DataRequirement, FactorState, IncrementalFactorResult

if TYPE_CHECKING:
    import pandas as pd

class Factor(ABC):
    """因子定义的抽象基类。"""

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
    @abstractmethod
    def warmup_periods(self) -> int | None:
        ...

    @abstractmethod
    def compute(self, data: Any) -> pd.Series:
        """因子计算批量接口"""
        ...

    @abstractmethod
    def initialize_state(self) -> FactorState:
        """初始化因子运行状态"""
        ...

    @abstractmethod
    def update(self, state: FactorState, timestamp_data: Any) -> IncrementalFactorResult:
        """根据新的时间戳数据，更新因子状态。"""
        ...

    @property
    @abstractmethod
    def parameters(self) -> Mapping[str, Any]:
        """返回因子构建的参数映射。"""
        ...
