from __future__ import annotations
from dataclasses import asdict, dataclass
import pandas as pd
import numpy as np

from typing import TYPE_CHECKING, Any, Iterable, Mapping

from data.calendar.product_calendar import ProductCalendar
from data.factory import _create_trading_rule_components
from factors import universe
from factors.diagnostic_portfolio.weights import MarketRankLSWeightStateMachine, TSInverseVolatilityWeightMachine, WeightStateMachine
from factors.models import INDEX_NAMES
from factors.diagnostic_portfolio.models import DiagnosticPortfolioResult, RebalanceConfig
from factors.diagnostic_portfolio.accounting import (
    DecisionRecord, DecisionLedger, compute_attribution_direction, 
    compute_attribution_product, compute_attribution_sector, compute_contribution, 
    compute_exposure, compute_nav, compute_performance, 
    compute_portfolio_returns, compute_weight_changes_turnover_cost
)
if TYPE_CHECKING:
    from factors.diagnostic_portfolio.models import DiagnosticSignal, DiagnosticPortfolioSpec
    from factors.models import ResearchDataset, DatasetSpec
    from data.rules.product_meta_manager import ProductMetaManager
    from data import EnvironmentConfig
    import datetime



_ACTIVE_WEIGHT_THRESHOLD = 1e-3

def _verify_signal_index(signal_index: pd.MultiIndex, 
                         main_panel_index: pd.MultiIndex):
    """校验：信号索引全部在主面板索引集以内。"""
    extra = set(signal_index) - set(main_panel_index)
    if extra:
        raise ValueError(f'信号的索引中存在不在主面板索引中的值: {extra} 。')

def align_inputs(
        signal: DiagnosticSignal, 
        dataset: ResearchDataset
    ) -> pd.DataFrame:
    """将研究主面板（main_panel）、品种池准入序列和交易信号序列
    拼接起来，合成一个 DataFrame 返回，Index[timestamp, product],
    columns 包含如下列: true_return, eligible, signal。

    Notes
    ------
        返回的数据是 ragged long panel（不是 full panel —— 索引为 [时间戳，品种] 
        的笛卡尔积）。因为品种上市时间有早有晚，早期必然存在品种不全，这是必然现象。
    """
    true_return_series = dataset.main_panel['True_Return'].rename('true_return')
    eligible = dataset.universe.eligible.copy().rename('eligible')
    signal_series = signal.values.copy().rename('signal')
    
    assert true_return_series.index.equals(eligible.index)
    _verify_signal_index(signal_series.index, true_return_series.index) # pyright: ignore[reportArgumentType]

    # 以主面板行集为左表，按 MultiIndex[timestamp, product] 对齐拼接；
    # 信号未覆盖的行保留为 NaN，信号多出的行被丢弃。
    # 这里日频是用的权威时间轴（因为构建日频主面板时，保留了Dominant_Contract 为缺失的行，实际的时间轴来自官方交易日历）；但更高频率（例如分钟级）数据由于丢弃了Dominant_Contract 为缺失的行，所以可能还需要建立一条更高频率下的权威时间轴？
    experiment_panel = pd.concat(
        [true_return_series, eligible, signal_series], axis=1
    ).reindex(true_return_series.index)
    return experiment_panel.sort_index(level=['timestamp','product'])

def build_global_timeline(panel: pd.DataFrame) -> pd.DatetimeIndex:
    """构造全局时间轴。""" # 注意，下面的实现目前不严谨：如果panel在某个日期，所有品种都没有数据，那么依然得不到完整的时间轴。真正的权威时间轴，要向交易日历 ProductCalendar 索取。
    timeline = panel.index.get_level_values('timestamp')\
        .drop_duplicates().sort_values()
    return pd.to_datetime(timeline)

def _build_global_timeline(
        products: Iterable[str],
        start_date:  pd.Timestamp,
        end_date: pd.Timestamp,
        freq: str,
        env_config: EnvironmentConfig
    ) -> pd.DatetimeIndex:
    """
    构建组合实验的全局时间轴。
    品种时间轴 = [start_date, end_date] 和 [上市时间，退市时间] 的交集 。

    全局时间轴= 品种1时间轴 ∪ 品种2时间轴 ∪ 品种3时间轴 ...
    """
    ...


def build_rebalance_schedule(timeline: pd.DatetimeIndex,
                             config: RebalanceConfig) -> pd.Series[bool]:
    """构造再平衡标记序列——每个时间戳是否为调仓时点。
    返回 pd.Series[bool]，with DatetimeIndex。"""
    anchor = config.anchor
    interval = config.interval
    if anchor is None:
        anchor_loc = 0
    else:
        anchor_loc: int = timeline\
            .get_indexer([pd.Timestamp(anchor)], method='bfill')[0]
        if anchor_loc == -1:
            raise ValueError(f'所输入的再平衡配置锚点时间(anchor: {anchor})'
                             f'晚于全局时间轴最后一个时间戳 :{timeline[-1]}。')
        if anchor_loc > len(timeline) - 2:
            raise ValueError(f'再平衡配置中的锚点时间(anchor : {anchor})太晚'
                             f'其在 timeline 中的位置为 {anchor_loc}。'
                             f'目前只接受从第1:{timeline[0]}，至倒数第2: {timeline[-2]}。')
    _mask1 = np.arange(len(timeline)) >= anchor_loc
    _mask2 = (np.arange(len(timeline)) - anchor_loc) % interval == 0
    flags =  pd.Series(_mask1 & _mask2, index=timeline, name='rebalance_flags')
    flags.iloc[-1] = False # 将整条时间轴上最后一个时点设为“非调仓时点”。
    return flags

@dataclass(frozen=True)
class AccountingArtifacts:
    weight_changes: pd.Series
    turnover: pd.Series
    decision_cost: pd.Series
    product_contributions: pd.Series
    portfolio_returns: pd.DataFrame
    nav: pd.DataFrame
    exposures: pd.DataFrame
    risk: pd.DataFrame | None
    product_attribution: pd.DataFrame
    direction_attribution: pd.DataFrame
    sector_attribution: pd.Series | None
    performance: pd.DataFrame
    status: pd.DataFrame | None
    warnings: tuple[str, ...]
    metadata: Mapping[str, Any]



class DiagnosticPortfolioExperimentRunner:
    """
    诊断性组合实验运行器。

    将单列信号按指定的收获范式转换为连续研究权重，并在不引入订单、
    成交、保证金和账户状态的前提下，计算下一期研究收益、换手、成本、
    净值、暴露、风险和归因。

    Parameters
    ----------
    signal : DiagnosticSignal
        进入组合实验的单列信号。
    dataset : ResearchDataset
        因子研究统一数据集，提供主面板、真实收益和动态品种池。
    spec : DiagnosticPortfolioSpec
        本次组合实验的完整不可变配置。

    Methods
    -------
    run()
        执行一次完整组合实验并返回 DiagnosticPortfolioResult。

    """

    def __init__(self, signal: DiagnosticSignal, 
                 dataset: ResearchDataset, spec: DiagnosticPortfolioSpec) :
        self._signal = signal
        self._dataset = dataset
        self._spec = spec

    def run(self, ) -> DiagnosticPortfolioResult:
        panel = align_inputs(self._signal, self._dataset) # 包含 ture_return, eligible, signal 列
        products = panel.index.get_level_values('product')\
            .unique().sort_values().tolist()
        timeline = build_global_timeline(panel)
        rebalance_flags = build_rebalance_schedule(
            timeline, self._spec.rebalance)
        # 只保留全局决策时间轴：最后一个全局时点仅作为收益实现时点，
        # 不在该日建立新的可评价调仓决策。
        rebalance_flags = rebalance_flags.iloc[:-1]
        assert len(timeline) - 1 == len(rebalance_flags)
        # 启动权重状态机
        machine = self._start_machine(products=products)
        # 新建决策记账本
        ledger = DecisionLedger()

        for timestamp in timeline[:-1]:
            rebalance: bool = rebalance_flags.at[timestamp]
            _data = panel.xs(
                timestamp, level='timestamp', drop_level=False)
            eligible = _data.reset_index(level='timestamp', drop=True)['eligible'] # Index[product]
            signal = _data.droplevel(level='timestamp')['signal'] # Index[product]
            ret = _data['true_return'] # MultiIndex[timestamp, product]
            # 更新权重状态机，得到新一期权重
            current_weights = machine.update(
                rebalance=rebalance, 
                eligible=eligible,
                signal=signal,
                ret=ret
            )
            weights, warnings, weight_meta = (
                current_weights.weights, 
                current_weights.warnings, 
                current_weights.metadata
            )
            # 记录该时间戳的权重、再平衡标记和其他数据
            record = DecisionRecord(
                timestamp=timestamp, weights=weights, rebalance=rebalance,
                weight_warnings=warnings, weight_meta=weight_meta
            )
            ledger.append(record)

        weights = ledger.to_weights_series()
        warnings_ledger = ledger.warnings
        metadata_ledger = ledger.metadata

        artifacts = self._compute_accounting_artifacts(
            weights=weights, panel=panel, timeline=timeline
        )
        return self._build_result(
            target_weights=weights,
            rebalance_flags=rebalance_flags,
            artifacts=artifacts,
            warnings_ledger=warnings_ledger,
            metadata_ledger=metadata_ledger
        )
        

    def _align_weight_return_cost(
            self, 
            weights: pd.Series, 
            panel: pd.DataFrame, 
            cost: pd.Series,
            timeline: pd.DatetimeIndex
    ) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
        """将收益、权重和成本按全局时间轴对齐。

        对齐口径为：收益实现时点 t 对应决策时点 t-1 的权重与成本
        （方案 §9.2 / §15.4）。决策时点为 t1..t_{N-1}，收益实现时点为
        t2..t_{N}；首期没有对应的上一期决策，因此从结果中剔除。

        Parameters
        ----------
        weights : pd.Series
            决策时点上的有效权重，Index[timestamp, product]。
        panel : pd.DataFrame
            研究主面板，Index[timestamp, product]，含 `true_return`。
        cost : pd.Series
            决策时点上的决策成本，Index[timestamp]。
        timeline: pd.DatetimeIndex
            全局时间轴。

        Returns
        -------
            tuple[pd.DataFrame, pd.Series, pd.Series]

            - aligned_df : pd.DataFrame
                收益实现时点上的对齐数据集，Index[timestamp, product]，
                columns 含 true_return、eligible、weight、cost。
            - weights_shifted : pd.Series
                对齐到收益实现时点的权重，Index[timestamp, product]。
            - costs_shifted : pd.Series
                对齐到收益实现时点的成本，Index[timestamp]。

        Notes
        -----
        * 权重与成本都必须先补齐到全局时间轴、再向后平移一期，否则最后
        一个收益实现时点会拿不到 t_{N-1} 期的权重与成本。
        """
        assert isinstance(weights.index, pd.MultiIndex)
        assert weights.notna().all()
        assert panel.index.is_unique

        # 权重：先补齐到全局时间轴（最后一期收益需要上一期权重），再向后
        # 平移一期，使实现时点 t 对应决策时点 t-1 的权重。
        products = weights.index.get_level_values('product')\
            .unique().sort_values()
        timeline_product_grid = pd.MultiIndex.from_product(
            [timeline, products], names=('timestamp', 'product')
        )    # cartesian product
        weights_all_prod_full_timeline = weights.reindex(timeline_product_grid)
        weights_all_prod_full_timeline_shifted = \
        weights_all_prod_full_timeline.groupby(level='product').shift(1)
        weights_shifted = weights_all_prod_full_timeline_shifted\
        .reindex(panel.index).rename('weight')
        
        # 成本：同样先补齐到全局时间轴再平移，避免最后一个实现时点缺成本。
        costs_shifted = cost.reindex(timeline).shift(1).rename('cost')

        aligned_df = panel.join(weights_shifted, how='left')
        aligned_df['cost'] = aligned_df.index\
            .get_level_values('timestamp').map(costs_shifted)

        # 剔除第1期：其没有对应的上一期决策，不产生收益。
        realization_mask = aligned_df.index\
            .get_level_values('timestamp') != timeline[0] 
        aligned_df = aligned_df.loc[realization_mask]

        aligned_df.drop(columns=['signal'], errors='ignore', inplace=True)

        weights_shifted = aligned_df['weight']
        costs_shifted = aligned_df['cost']\
            .groupby(level='timestamp').first().rename('cost')

        return aligned_df, weights_shifted, costs_shifted
    
    def _compute_accounting_artifacts(
        self,
        weights: pd.Series,
        panel: pd.DataFrame,
        timeline: pd.DatetimeIndex  
    ):
        """计算各实验结果统计量 artifacts 作为中间结果。"""
        warnings_list = []

        weight_changes, turnover, cost = compute_weight_changes_turnover_cost(
        weights, cost_config=self._spec.cost
        )
        # timeline alignment：收益实现时点 t 对应决策时点 t-1 的权重与成本
        aligned_df, weights_shifted, costs_shifted = \
            self._align_weight_return_cost(weights, panel, cost, timeline)

        contributions = compute_contribution(aligned_df)
        portfolio_returns = compute_portfolio_returns(
        contributions=contributions, costs_shifted=costs_shifted
        )
        nav = compute_nav(
            gross_returns=portfolio_returns['gross_return'],
            net_returns=portfolio_returns['proxy_net_return']
        )
        exposure = compute_exposure(weights, _ACTIVE_WEIGHT_THRESHOLD)
        attribution_prod = compute_attribution_product(
            contributions=contributions, weights_shifted=weights_shifted,
            active_weight_threshold=_ACTIVE_WEIGHT_THRESHOLD
        )
        attribution_direction = compute_attribution_direction(
            contributions=contributions,
            weights_shifted=weights_shifted
        )
        # 若有sector分类数据，计算sector attribution
        if isinstance(self._dataset.auxiliary, Mapping) and \
        'classification' in self._dataset.auxiliary.keys() and \
        self._spec.sector_attribution_col is not None:
            attribution_sector = compute_attribution_sector(
                contributions=contributions,
                classification=self._dataset.auxiliary['classification'],
                sector_col=self._spec.sector_attribution_col
            )
        else:
            attribution_sector = None
        
        performance, warnings = compute_performance(
            gross_returns=portfolio_returns['gross_return'], 
            net_returns=portfolio_returns['proxy_net_return'],
            annualization_factor=self._spec.annualization_factor
        )
        warnings_list.extend(warnings)

        return AccountingArtifacts(
            weight_changes=weight_changes, 
            turnover=turnover,
            decision_cost=cost,
            product_contributions=contributions,
            portfolio_returns=portfolio_returns,
            nav=nav,
            exposures=exposure,
            risk=None,
            product_attribution=attribution_prod,
            direction_attribution=attribution_direction,
            sector_attribution=attribution_sector,
            performance=performance,
            status=None,
            warnings=tuple(warnings_list),
            metadata={}
        )

    def _build_result(
            self, 
            target_weights: pd.Series,
            rebalance_flags: pd.Series,
            artifacts: AccountingArtifacts,
            warnings_ledger: tuple[str,...],
            metadata_ledger: Mapping[str, Any]
    ) -> DiagnosticPortfolioResult:
        """
        工厂函数：组装目标权重、再平衡flags、实验结果的artifacts
        （accounting阶段的警告和元数据）、记录册上的警告和元数据到实验结果类返回。
        """
        artifacts_dict = asdict(artifacts)
        warnings_artifacts: tuple[str] = artifacts_dict.pop('warnings', ()) # pyright: ignore[reportAssignmentType]
        metadata_artifacts: Mapping[str, Any] = artifacts_dict.pop('metadata', {})
        
        warnings_for_result = tuple([*warnings_ledger, *warnings_artifacts])
        metadata_for_result = {
            **dict(metadata_ledger), **dict(metadata_artifacts)
        }
        
        return DiagnosticPortfolioResult(
            signal_name=self._signal.name,
            spec=self._spec,
            target_weights=target_weights,
            rebalance_flags=rebalance_flags,
            **artifacts_dict, # 此时不包含 warnings和metadata 两个keys
            warnings=warnings_for_result,
            metadata=metadata_for_result
        )


    def _start_machine(self, products: list[str]) -> WeightStateMachine:
        """"""
        if self._spec.harvest_profile.name == 'market_rank_ls':
            return MarketRankLSWeightStateMachine(
                spec=self._spec,
                products=products
            )
        elif self._spec.harvest_profile.name in (
            'ts_inv_vol_gross_one', 'ts_inv_vol_target_vol'
        ):
            return TSInverseVolatilityWeightMachine(
                spec=self._spec,
                products=products
            )
        raise ValueError(
            f'HarvestProfile :{self._spec.harvest_profile.name} \
            目前无对应的权重状态机。'
        )


# ===========================================
# inline smoke test
# ===========================================
if __name__ == '__main__':
    from factors.models import ResearchDataset, DatasetSpec, UniverseSpec
    from factors.universe import Universe
    from factors.diagnostic_portfolio.models import DiagnosticSignal, DiagnosticPortfolioSpec
    from factors.diagnostic_portfolio.models import (
        RebalanceConfig, CostConfig, ConstraintConfig, RiskConfig
    )
    from data import RolloverConfig, AdjustmentConfig, SamplingConfig, EnvironmentConfig
    from datetime import date
    from factors.profiles import market_rank_ls


    facade_config = RolloverConfig(), AdjustmentConfig(), SamplingConfig(), EnvironmentConfig()
    uni_spec = UniverseSpec()
    data_spec = DatasetSpec(
        facade_config=facade_config,
        products=('A','B','C'),
        data_requirements=(),
        start_date=date(2025, 1, 5),
        end_date=date(2025, 1, 10),
        view='scheduled',
        universe=uni_spec
    )
    main_panel = pd.DataFrame( # products=A, B, C, A from 1-5 to 1-8, B from 1-5 to 1-10, C from 1-7 to 1-10
        {
            'timestamp':[*pd.date_range('2025-01-05','2025-01-08'), *pd.date_range('2025-01-05','2025-01-10'), *pd.date_range('2025-01-07','2025-01-10')],
            'product': ['A']* 4 + ['B']*6 + ['C']*4,
            'True_Return': np.random.randn(14)
        }
    ).set_index(['timestamp','product'])
    universe = Universe(
        eligible=pd.Series(True, index=main_panel.index),
        exclude_reason=pd.Series()
    )
    dataset = ResearchDataset(
        spec=data_spec,
        main_panel=main_panel,
        quality=None,
        universe=universe
    )

    signal_values=pd.Series(
        np.random.rand(len(main_panel)),
        index=main_panel.index
    )
    signal = DiagnosticSignal(name='momentum_20', values=signal_values,
                              source_stage='raw', factor_name='momentum_20', 
                              recipe={})
    rebalance_config = RebalanceConfig(interval=1, anchor=None)
    constraint_config = ConstraintConfig()
    cost_config = CostConfig(rate=3e-5)
    spec = DiagnosticPortfolioSpec(
        name='momentum_20', harvest_profile=market_rank_ls, 
        rebalance=rebalance_config, constraint=constraint_config, 
        cost=cost_config, min_cross_section_size=2,
    )
    runner = DiagnosticPortfolioExperimentRunner(
        signal=signal, dataset=dataset, spec=spec
    )
    result = runner.run()

    # ---------------------# 
    