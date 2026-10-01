"""

因子研究模块（Factor Research Module）。

封装从研究数据到候选因子的完整日频因子研究流程。
研究链路：

    DataFacade / ResearchFeed
        -> ResearchDataset
        -> DynamicUniverse
        -> FactorDefinition
        -> PreprocessingPipeline
        -> LabelDefinition
        -> FactorEvaluator
        -> ResearchResult
        -> CandidateFactor

V1 仅支持日频数据。依赖方向：data <- factors <- strategy <- backtester。
factors/ 可以调用 data.DataFacade，但不得依赖 strategy/、backtester/、
portfolio/、executor/ 或 account/。

"""

from factors.models import (
    # 模块常量
    INDEX_NAMES,
    REQUIRED_COLUMNS,
    # 数据集模型
    UniverseSpec,
    DatasetSpec,
    DataQualitySummary,
    ResearchDataset,
    # 因子模型
    FactorState,
    IncrementalFactorResult,
    FactorResult,
    # 标签模型
    LabelSpec,
    LabelResult,
    # 评价模型
    EvaluationConfig,
    FactorEvaluationResult,
    DecayResult,
    HarvestProfile,
    # 实验模型
    ExperimentIdentity,
    CandidateFactor,
)

from factors.universe import Universe

from factors.profiles import (
    # 六个主流组合收获范式
    market_rank_ls,
    sector_neutral_rv,
    sector_neutral_inv_vol,
    constrained_alpha_opt,
    ts_inv_vol_target_vol,
    sector_risk_budget_cta,
    MAINSTREAM_HARVEST_PROFILES,
)

__all__ = [
    'INDEX_NAMES',
    'REQUIRED_COLUMNS',
    'UniverseSpec',
    'DatasetSpec',
    'DataQualitySummary',
    'ResearchDataset',
    'Universe',
    'FactorState',
    'IncrementalFactorResult',
    'FactorResult',
    'LabelSpec',
    'LabelResult',
    'EvaluationConfig',
    'FactorEvaluationResult',
    'DecayResult',
    'HarvestProfile',
    # 实验模型
    'ExperimentIdentity',
    'CandidateFactor',
    # 主流组合收获范式
    'market_rank_ls',
    'sector_neutral_rv',
    'sector_neutral_inv_vol',
    'constrained_alpha_opt',
    'ts_inv_vol_target_vol',
    'sector_risk_budget_cta',
    'MAINSTREAM_HARVEST_PROFILES',
]
