"""单因子预处理模块。

包含数据清洗、缩尾、特征缩放、中性化、平滑、信号改性等预处理组件，
以及串联各组件构成完整预处理流程的 PreprocessingPipeline。

典型用法：

>>>    from factors.preprocess import (
        CrossSectionalMissingHandler,
        CrossSectionalWinsorizer,
        PreprocessingPipeline,
    )

>>>    pipeline = PreprocessingPipeline([
        CrossSectionalMissingHandler(policy='impute'),
        CrossSectionalWinsorizer(),
    ])
>>>    result = pipeline.process(values)

各组件/管线骨架定义于子模块，这里统一导出稳定公共接口。
"""

from factors.preprocess.base import PreprocessingPipeline
from factors.preprocess.clean import (
    CrossSectionalMissingHandler, RollingTimeSeriesMissingHandler,
)
from factors.preprocess.winsorize import (
    CrossSectionalWinsorizer, RollingTimeseriesWinsorizer,
)
from factors.preprocess.scale import (
    CrossSectionalZscore, CrossSectionalRankNormalizer, FittedTimeSeriesZscore,
    RollingTimeSeriesZscore, FittedPooledScalar, ClipPreprocessor, Clip, Clamp
)
from factors.preprocess.neutralize import (
    LiquidityDataConstructor, LiquidityExposureNeutralizer,
    SectorDataConstructor, SectorExposureNeutralizer,
    VolatilityDataConstructor, VolatilityExposureNeutralizer,
)
from factors.preprocess.smooth import EMASmoother, SMASmoother

__all__ = [
    'PreprocessingPipeline',
    'CrossSectionalMissingHandler',
    'RollingTimeSeriesMissingHandler',
    'CrossSectionalWinsorizer',
    'RollingTimeseriesWinsorizer',
    'CrossSectionalZscore',
    'CrossSectionalRankNormalizer',
    'FittedTimeSeriesZscore',
    'LiquidityDataConstructor',
    'LiquidityExposureNeutralizer',
    'SectorDataConstructor',
    'SectorExposureNeutralizer',
    'VolatilityDataConstructor',
    'VolatilityExposureNeutralizer',
    'EMASmoother',
    'SMASmoother',
    'RollingTimeSeriesZscore',
    'FittedPooledScalar',
    'ClipPreprocessor', 'Clip', 'Clamp',
]