"""因子预处理组件。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Self, TYPE_CHECKING, Literal
from factors.common import walk_forward_split
import copy

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
        eligible: pd.Series | None 
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
        eligible: pd.Series,
    ) -> Self:
        """
        在给定数据上应用品种池，然后拟合参数，并将拟合状态保存到内部。

        Parameters
        ----------
        values : pd.Series
            用于拟合的因子值。
        eligible: pd.Series 
            品种池准入掩码。
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

    def process(self, values: pd.Series, eligible: pd.Series | None) \
        -> PreprocessingPipelineResult:
        """
        按顺序运行预处理流水线，同时收集 warnings 和 metadata。
        """
        warnings: dict[str, list[str]] = {}
        metadata: dict[str, Mapping[str, Any]] = {}
        for processor in self._processors:
            result = processor.transform(values=values, eligible=eligible)
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

# TODO 后续若想要该类能加入预处理流水线，必须先继承FactorPreprocess
# 这里的形态其实我没有特别想清楚，目前 `transform()` 方法接受 values, eligible，
# 实际上是因为 FittedFactorPreprocessor 的 `fit()` 方法要接受 eligible。
# 但如果改成继承 FactorPreprocessor，那么 `transform()` 又不应该接受 eligible。
# 这类的关系仍待理顺。
class WalkForwardTransformer:
    """推进型（拟合）变换预处理工具。
    
    Parameters
    ------
    inner: FittedFactorPreprocessor
        内部包装的可拟合变换类预处理组件。
    fit_size: int or pd.DateOffset
        拟合窗口大小，即walk-forward语境下的训练集大小(`train_size`)。
    transform_size: int or pd.DateOffset
        变换窗口大小， walk-forward 语境下的测试集大小(`test_size`)。
    mode: {'expanding', 'rolling'}
        滚动窗口推进或扩展窗口推进。为 `expanding` 时，拟合窗口起点固定，终点
        不断扩张。变换窗口大小保持恒定。
    """

    def __init__(
            self,
            inner: FittedFactorPreprocessor,
            fit_size: int | pd.DateOffset,
            transform_size: int | pd.DateOffset,
            mode: Literal['rolling', 'expanding'] = 'rolling'
    ):
        self._processor = inner
        self._train_size = fit_size
        self._test_size = transform_size
        self._mode: Literal['rolling', 'expanding'] = mode

    def _add_warnings(
            self, 
            warnings: list[str], 
            existing: list[str],
            fold: int) -> None:
        warnings_with_fold_info = [f'[fold{fold}] ' + w for w in warnings]
        existing.extend(warnings_with_fold_info)

    def _add_metadata(
            self, 
            metadata: Mapping[str, Any], 
            existing: dict[str, Any],
            fold: int
        ) -> None:
        existing[f'fold{fold}'] = metadata


    def transform(self, values: pd.Series, eligible: pd.Series) -> PreprocessingResult:
        """推进拟合->变换预处理。
        先利用 `walk_forward_split`函数 将 values 的时间轴按 `fit_size`, `transform_size`
        进行切分。随后对于每个fold，利用 `inner` 带进来的可拟合变换类预处理组件，
        在 fit window 上应用品种池，拟合（并保存状态），然后对 transform window 
        上的数据做变换。最后拼接返回一个 `PreprocessingResult`，包含预处理后的数据，警告和
        元数据。
        """
        timeline = values.index.get_level_values('timestamp').unique().sort_values()
        assert isinstance(timeline, pd.DatetimeIndex)

        splits = walk_forward_split(timeline, 
            train_size=self._train_size, 
            validation_size=None,
            test_size=self._test_size,
            mode=self._mode,
            embargo=0)
        self._splits = splits

        fitted_states: dict[str, Any] = {}
        warnings_collection: list[str] = []
        metadata_collection: dict[str, Any] = {}
        transed_values_list: list[pd.Series] = []

        for i, fold in enumerate(splits):
            fit_data = values.loc[fold.train]
            to_trans_data = values.loc[fold.test]
            # fit data
            self._processor.fit(fit_data, eligible)
            # collect fitted states
            states = self._processor.states_
            fitted_states[f'fold{i}'] = states
            # transform 
            result = self._processor.transform(to_trans_data, eligible)
            transed_values_list.append(result.values)

            self._add_warnings(
                result.warnings, warnings_collection, i)
            self._add_metadata(
                result.metadata, metadata_collection, i)
        
        self._fitted_states = fitted_states

        transformed_values = pd.concat(transed_values_list, axis=0)
        return PreprocessingResult(
            values=transformed_values,
            warnings=warnings_collection,
            metadata=metadata_collection
        )

    @property
    def states_(self) -> Mapping[str, Any]:
        return {
            'splits': self._splits.copy(),
            'fitted_states': copy.deepcopy(self._fitted_states)
        }    

            
        
