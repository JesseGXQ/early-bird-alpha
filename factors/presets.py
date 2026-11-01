"""因子研究快捷方式（presets，也称“一键开局”）。

设计约定
--------
- 只放"设定、样板"（配置对象、默认值、构造流程），不放研究结论或实验逻辑。

典型用法
--------
>>> from factors.presets import build_dataset, build_dataset_with_facade
>>> dataset = build_dataset()                      # 主面板，默认品种池与日期
>>> dataset = build_dataset(sources=('term_structure',))   # 加期限结构
>>> dataset, facade = build_dataset_with_facade()  # 还要用 rule_query 时
"""
from __future__ import annotations

import datetime as dt
from dataclasses import replace as dreplace
from collections.abc import Iterable, Sequence
import pandas as pd
from data import DataFacade
from data.config import (
    AdjustmentConfig, EnvironmentConfig, RolloverConfig, SamplingConfig)
from factors.dataset import ResearchDatasetBuilder
from factors.models import DataRequirement, DatasetSpec, ResearchDataset, UniverseSpec
from factors.diagnostic_portfolio import (
    DiagnosticPortfolioExperimentRunner, DiagnosticPortfolioSpec,
    DiagnosticSignal, RebalanceConfig, RiskConfig, CostConfig, ConstraintConfig
)
from factors.profiles import market_rank_ls, ts_inv_vol_gross_one, ts_inv_vol_target_vol
# ============================================================
# 默认设定
# ============================================================

#: 研究默认起始日期；主连/规则数据从 2001 年中期开始可用。
DEFAULT_START_DATE: dt.date = dt.date(2000, 1, 1)

#: 研究默认结束日期；None 表示"最近可用"，做正式记录时建议显式传入。
DEFAULT_END_DATE: dt.date | None = None

#: 默认排除的品种。无数据。
DEFAULT_EXCLUDED_PRODUCTS: tuple[str, ...] = ('L_F', 'PP_F', 'V_F')

#: 默认品种池准入条件：20 日滚动平均成交量不低于 1 万手。
DEFAULT_UNIVERSE: UniverseSpec = UniverseSpec(min_average_volume=10000)

#: 只请求主面板（价格、成交、持仓等）。
MAIN_PANEL_ONLY: tuple[str, ...] = ('main_panel',)

#: 主面板 + 期限结构（展期收益率类因子需要）。
MAIN_PANEL_AND_TERM_STRUCTURE: tuple[str, ...] = ('main_panel', 'term_structure')

# ===============================================
# 组合实验基础配置（boilerplate)
# ===============================================

# 为避开 __post_init__ 对 Series 的格式校验，（麻烦一些）采用 __new__() 实例化
base_signal = object.__new__(DiagnosticSignal)
object.__setattr__(base_signal, 'name', 'base')
object.__setattr__(base_signal, 'values', pd.Series()) 
object.__setattr__(base_signal, 'source_stage', 'raw')
object.__setattr__(base_signal, 'factor_name', 'base')
object.__setattr__(base_signal, 'recipe', {})
object.__setattr__(base_signal, 'warnings', {})

base_rebalance_config = RebalanceConfig(interval=5, anchor=None)
base_constraint_config = ConstraintConfig()
base_cost_config = CostConfig(rate=3e-5)

# profile = mark_rank_ls
mrls_base_spec = DiagnosticPortfolioSpec(
    name='mrls_base', harvest_profile=market_rank_ls, rebalance=base_rebalance_config,
    constraint=base_constraint_config, cost=base_cost_config, 
    min_cross_section_size=5, 
    missing_return_policy='default_to_zero_and_warning'
)
# profile = ts_inv_vol_gross_one
tsgo_base_risk_config = RiskConfig(
    volatility_lookback=30, volatility_min_periods=5)
tsgo_base_spec = DiagnosticPortfolioSpec(
    name='tsgo_base', harvest_profile=ts_inv_vol_gross_one, 
    rebalance=base_rebalance_config, constraint=base_constraint_config,
    cost=base_cost_config, risk=tsgo_base_risk_config, 
    missing_return_policy='default_to_zero_and_warning'
)
# profile = ts_inv_vol_target_vol
tstv_base_risk_config = dreplace(
    tsgo_base_risk_config,
    covariance_lookback=60, covariance_min_periods=10, target_volatility=0.1
    ) 
tstv_base_constraint_config = ConstraintConfig(
    max_gross=2.5, max_volatility_scaler=2.5)
tstv_base_spec = DiagnosticPortfolioSpec(
    name='tstv_base', harvest_profile=ts_inv_vol_target_vol,
    rebalance=base_rebalance_config, constraint=tstv_base_constraint_config,
    risk=tstv_base_risk_config, cost=base_cost_config,
    missing_return_policy='default_to_zero_and_warning'
)






# ============================================================
# 数据引擎
# ============================================================

def default_facade_config() -> tuple[
    RolloverConfig, AdjustmentConfig, SamplingConfig, EnvironmentConfig
]:
    """返回标准数据引擎配置元组（换月 / 复权 / 抽样 / 环境）。

    DatasetSpec.facade_config 与 DataFacade(*config) 都直接接受这个元组。
    """
    return (
        RolloverConfig(),
        AdjustmentConfig(),
        SamplingConfig(),
        EnvironmentConfig(),
    )


def make_facade(
    facade_config: tuple[
        RolloverConfig, AdjustmentConfig, SamplingConfig, EnvironmentConfig
    ] | None = None,
) -> DataFacade:
    """按标准配置（或给定配置）构造一个 DataFacade。"""
    config = facade_config or default_facade_config()
    return DataFacade(*config)


# ============================================================
# 品种池
# ============================================================

def default_products(
    facade: DataFacade | None = None,
    exclude: Iterable[str] = DEFAULT_EXCLUDED_PRODUCTS,
) -> tuple[str, ...]:
    """返回默认研究品种池（全部品种减去排除项），保持规则库返回顺序。

    Parameters
    ----------
    facade : DataFacade, optional
        复用已有 facade；None 时内部临时构造一个。
    exclude : Iterable[str], optional
        需要剔除的品种代码，默认 DEFAULT_EXCLUDED_PRODUCTS。
    """
    facade = facade or make_facade()
    excluded = set(exclude)
    return tuple(
        p for p in facade.rule_query.get_product_universe(return_type='list')
        if p not in excluded
    )


# ============================================================
# 研究数据集
# ============================================================

def _normalize_requirements(
    items: Sequence[str | DataRequirement],
) -> tuple[DataRequirement, ...]:
    """把字符串或 DataRequirement 统一成元组，并保证 main_panel 一定被请求。"""
    requirements: list[DataRequirement] = []
    for item in items:
        if isinstance(item, DataRequirement):
            requirements.append(item)
        elif isinstance(item, str):
            requirements.append(DataRequirement(source=item))
        else:
            raise TypeError(
                'data_requirements/sources 只接受 str 或 DataRequirement，'
                f'收到 {type(item).__name__}。'
            )
    if not any(req.source == 'main_panel' for req in requirements):
        requirements.insert(0, DataRequirement(source='main_panel'))
    return tuple(requirements)


def build_dataset(
    products: Sequence[str] | None = None,
    start_date: dt.date | None = DEFAULT_START_DATE,
    end_date: dt.date | None = DEFAULT_END_DATE,
    sources: Sequence[str | DataRequirement] = MAIN_PANEL_ONLY,
    universe: UniverseSpec = DEFAULT_UNIVERSE,
    facade_config: tuple[
        RolloverConfig, AdjustmentConfig, SamplingConfig, EnvironmentConfig
    ] | None = None,
    exclude_products: Iterable[str] = DEFAULT_EXCLUDED_PRODUCTS,
) -> ResearchDataset:
    """构造研究数据集（DatasetSpec + ResearchDatasetBuilder 的快捷方式）。

    Parameters
    ----------
    products : Sequence[str], optional
        品种代码；None 表示用 default_products() 的默认品种池。
    start_date, end_date : date or None, optional
        研究区间（闭区间）。end_date=None 表示最近可用。
    sources : Sequence[str | DataRequirement], optional
        数据需求，可写数据源名字符串（如 'term_structure'），也可写完整的
        DataRequirement（需要裁剪列或透传参数时）。遗漏 main_panel 时自动补上。
    universe : UniverseSpec, optional
        品种池准入条件，默认 DEFAULT_UNIVERSE。
    facade_config : tuple, optional
        数据引擎配置，默认 default_facade_config()。
    exclude_products : Iterable[str], optional
        仅在 products 为 None（使用默认品种池）时生效。

    Returns
    -------
    ResearchDataset
        含 spec / main_panel / quality / universe / auxiliary 的统一研究数据集。
    """
    config = facade_config or default_facade_config()
    product_tuple = (
        tuple(products) if products is not None
        else default_products(facade=make_facade(config), exclude=exclude_products)
    )

    spec = DatasetSpec(
        facade_config=config,
        products=product_tuple,
        data_requirements=_normalize_requirements(sources),
        start_date=start_date,
        end_date=end_date,
        universe=universe,
    )
    return ResearchDatasetBuilder(spec=spec).build()


def build_dataset_with_facade(
    products: Sequence[str] | None = None,
    start_date: dt.date | None = DEFAULT_START_DATE,
    end_date: dt.date | None = DEFAULT_END_DATE,
    sources: Sequence[str | DataRequirement] = MAIN_PANEL_ONLY,
    universe: UniverseSpec = DEFAULT_UNIVERSE,
    facade_config: tuple[
        RolloverConfig, AdjustmentConfig, SamplingConfig, EnvironmentConfig
    ] | None = None,
    exclude_products: Iterable[str] = DEFAULT_EXCLUDED_PRODUCTS,
) -> tuple[ResearchDataset, DataFacade]:
    """同 build_dataset()，但一并返回 DataFacade。

    研究里经常还要用 facade.rule_query 查板块分类、品种元数据，
    这个版本复用同一个 facade 解析品种池。注意 ResearchDatasetBuilder
    内部会自建 facade，返回的这个实例只用于规则查询与补充取数，
    两者读取同一套本地数据，不共享内存缓存。
    """
    facade = make_facade(facade_config)
    product_tuple = (
        tuple(products) if products is not None
        else default_products(facade=facade, exclude=exclude_products)
    )
    dataset = build_dataset(
        products=product_tuple,
        start_date=start_date,
        end_date=end_date,
        sources=sources,
        universe=universe,
        facade_config=facade_config,
    )
    return dataset, facade


__all__ = [
    'DEFAULT_START_DATE',
    'DEFAULT_END_DATE',
    'DEFAULT_EXCLUDED_PRODUCTS',
    'DEFAULT_UNIVERSE',
    'MAIN_PANEL_ONLY',
    'MAIN_PANEL_AND_TERM_STRUCTURE',
    'default_facade_config',
    'make_facade',
    'default_products',
    'build_dataset',
    'build_dataset_with_facade',
]
