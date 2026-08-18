"""预处理组件 —— 平滑类。"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Iterable, Literal, Mapping

import numpy as np
import pandas as pd

from factors.models import INDEX_NAMES, ResearchDataset
from factors.preprocess.base import (
        FactorPreprocessor, PreprocessingResult, 
        PreprocessingContext, clean_multiindex,
        PreprocessContextConstructor)

class SMASmoother(FactorPreprocessor):
    """简单移动平均平滑，支持多品种。"""

    def __init__(self, window: int = 3):
        assert window >= 2
        self._window = window

    @property
    def name(self) -> str:
        return f'sma_smoother_{self._window}'

    @property
    def parameters(self) -> dict[str, Any]:
        return {'window': self._window}

    def transform(self, values: pd.Series) -> PreprocessingResult:
        values = values.copy()

        sma = clean_multiindex(values \
        .groupby('product') \
        .rolling(window=self._window, min_periods=self._window) \
        .agg('mean')
        ).reindex(values.index)

        return PreprocessingResult(
            values = sma,
            warnings = [],
            metadata = {}
        )

class EMASmoother(FactorPreprocessor):
    """EMA平滑，支持多品种。"""
    
    def __init__(
            self, 
            span: int = 3,
            warmup_eps: float = 0.01):
        assert span >=2
        self._span = span
        self._warmup_eps = warmup_eps
        self._warmup_periods = int(
            np.log(warmup_eps) / np.log( (span-1) / (span+1))
        )

        self._metadata = {}
        self._metadata['warmup_periods'] = self._warmup_periods

    @property
    def name(self) -> str:
        return f'ema_smoother_{self._span}_{self._warmup_eps}'

    @property
    def parameters(self) -> dict[str, Any]:
        return {'span': self._span, 'warmup_eps': self._warmup_eps}

    def transform(self, values: pd.Series) -> PreprocessingResult:
        values = values.copy()

        ema = clean_multiindex(values \
        .groupby(level='product') \
        .ewm(span=self._span, adjust=False) \
        .mean()
        ).reindex(values.index)

        return PreprocessingResult(
            values = ema,
            warnings = [],
            metadata = self._metadata
        )

    
        

