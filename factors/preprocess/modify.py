"""预处理组件 —— 信号改性"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Literal, Mapping

import pandas as pd
from factors.preprocess.base import (
        FactorPreprocessor, PreprocessingResult, PreprocessingContext)

class RollingTimeSeriesZscore(FactorPreprocessor):
    pass