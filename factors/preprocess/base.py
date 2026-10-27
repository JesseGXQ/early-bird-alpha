"""因子预处理组件。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Self, TYPE_CHECKING

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
        返回预处理组件的构造参数。

        对于无拟合状态的组件，该方法的返回值应该满足：可以通过返回的参数字典
        构造一个实例，调用 `transform()`（给定同样的 values 作为参数输入），
        可以原样复现出处理后的 `PreprocessingResult`。

        对于 `FittedFactorPreprocessor` 的子类，该字典只包含构造期超参数，
        不包含 fit 数据；复现结果还需要用同一份 fit 数据调用 `fit()`。
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
    """
    符合 Fit -> Transform 流程的可拟合预处理组件的抽象基类。

    Notes
    ------
    实例化时只传入构造期超参数；拟合所需的数据在调用 `fit()` 时传入。
    拟合完成后，拟合状态通过 `states_` 暴露；在未调用 `fit()` 之前，
    `transform()` 与 `states_` 应抛出异常。
    """

    @abstractmethod
    def fit(
        self,
        values: pd.Series,
        **fit_params: Any,
    ) -> Self:
        """
        在给定数据上拟合参数，并将拟合状态保存到内部。

        Parameters
        ----------
        values : pd.Series
            用于拟合的因子值。
        **fit_params : Any
            子类特有的拟合期参数（例如品种池掩码）。
        """
        ...

    @property
    @abstractmethod
    def states_(self) -> Mapping[str, Any]:
        """返回拟合后的状态；未调用 `fit()` 时应抛出异常。"""
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
    
    

            
        
