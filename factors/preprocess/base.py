"""因子预处理组件。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd
    from factors.models import ResearchDataset

type PreprocessingContext = Mapping[str, pd.Series | pd.DataFrame]

class FactorPreprocessor(ABC):
    """单因子批量预处理组件的抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """返回预处理组件名称。"""
        ...

    @property
    def required_context_keys(self) -> tuple[str, ...]:
        """预处理组件所需 context 的键值。"""
        return ()

    @property
    def parameters(self) -> dict[str, Any]:
        """返回预处理组件的参数字典。"""
        return {}

    @abstractmethod
    def transform(
        self, 
        values: pd.Series, 
        context: PreprocessingContext | None = None
    ) -> PreprocessingResult:
        """
        对因子值(values)做批量预处理。
        """
        ...

class FittedFactorPreprocessor(FactorPreprocessor, ABC):
    """符合 Fit -> Transform 流程的可拟合预处理组件的抽象基类。"""
    @property
    def requires_fit(self) -> bool:
        return True

    @abstractmethod
    def fit(
        self,
        values: pd.Series
    ) -> None:
        """在 fit 窗口上拟合参数 , 并将参数保存到内部状态。"""
        ...
    

    
    
class PreprocessContextConstructor(ABC):
    """预处理的上下文构建器基类。"""

    def __init__(self, dataset: ResearchDataset):
        self.dataset = dataset

    @abstractmethod
    def build(self, context_keys: tuple[str, ...]) -> PreprocessingContext:
        """根据所需的 context 键值，从 ResearchDataset 中构建预处理所需 context data。"""
        return {}

@dataclass(frozen=True)
class PreprocessingResult:
    """预处理结果类。"""
    values: pd.Series
    warnings: list[str]
    metadata: Mapping[str, Any]



# PreprocessingPipeline 类暂缺