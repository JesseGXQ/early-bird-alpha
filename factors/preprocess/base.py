"""因子预处理组件。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
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
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """
        返回预处理组件的参数。
        
        该方法的返回值应该满足：可以通过返回的参数字典，构造一个实例，
        调用`transform()`（给定同样的values作为参数输入），
        可以原样复现出处理后的 `PreprocessingResult`。
        """
        return {}

    @abstractmethod
    def transform(
        self, 
        values: pd.Series, 
    ) -> PreprocessingResult:
        """
        对因子值(values)做批量预处理。
        """
        ...

class FittedFactorPreprocessor(FactorPreprocessor, ABC):
    """符合 Fit -> Transform 流程的可拟合预处理组件的抽象基类。"""

    def __init__(self, values_for_fit: pd.Series):
        self._values_for_fit = values_for_fit

    @abstractmethod
    def fit(self) -> None:
        """在 fit 窗口上拟合参数 , 并将参数保存到内部状态。"""
        ...
    
class PreprocessContextConstructor(ABC):
    """预处理的上下文构建器基类。"""

    def __init__(self, dataset: ResearchDataset):
        self._dataset = dataset

    @abstractmethod
    def build(self, *args: Any, **kwargs: Any) -> PreprocessingContext:
        """从 ResearchDataset 中构建预处理所需的 context data。"""
        ...

@dataclass(frozen=True)
class PreprocessingResult:
    """预处理结果类。"""
    values: pd.Series
    warnings: list[str]
    metadata: Mapping[str, Any]

@dataclass(frozen=True)
class PreprocessingPipelineResult:
    """预处理流水线处理后结果。"""
    values: pd.Series
    warnings: Mapping[str, Any]
    metadata: Mapping[str, Any]

def clean_multiindex(s: pd.Series) -> pd.Series:
    """将索引清理为MultiIndex[timestamp, product]。"""
    assert isinstance(s.index, pd.MultiIndex)
    if list(s.index.names) == ['product', 'timestamp', 'product']:
        # 丢弃最左边一层的 product
        return s.droplevel(level=0).copy()
    elif list(s.index.names) == ['timestamp', 'product']:
        return s.copy()
    raise ValueError(f'Expect MultiIndex[product, timestamp, product]'
                     'or MultiIndex[timestamp, product],'
                     f'got {s.index.names} ')
    

# PreprocessingPipeline 类：
# 根据目前的设计， pipeline 有 `add_preprocessor()` 和 `process()`；
# `process()` 里直接对每个 preprocessor 调用其 `transform()`即可。

class PreprocessingPipeline:
    """预处理流水线。"""

    def __init__(
            self, 
            processors: list[FactorPreprocessor] | None = None):
        self._processors = list(processors) if processors else []

    def add_processor(self, processor: FactorPreprocessor):
        """在流水线尾部加入一个预处理组件。"""
        self._processors.append(processor)

    def process(self, values: pd.Series) \
        -> PreprocessingPipelineResult:
        """
        按顺序运行预处理流水线，同时收集 warnings 和 metadata。
        """
        warnings: dict[str, list[str]] = {}
        metadata: dict[str, Mapping[str, Any]] = {}
        for processor in self._processors:
            result = processor.transform(values=values)
            values = result.values
            warnings[processor.name] = result.warnings
            metadata[processor.name] = result.metadata
        return PreprocessingPipelineResult(
            values=values, 
            warnings=warnings, 
            metadata=metadata
        )

    @property
    def config(self) -> dict[str, Any]:
        """返回预处理流水线配置（即所有预处理组件的参数）。"""
        all_config = {}
        for processor in self._processors:
            name = processor.name
            params = processor.parameters
            all_config[name] = params
        return all_config
    
    

            
        
