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

@dataclass(frozen=True)
class PanelArrays:
    """把 ragged long panel 展开成的三个 ``(T, P)`` 矩阵。

    行对应 ``timestamps``，列对应 ``products``；品种在该时点无数据的位置为
    缺失值（``eligible`` 为 False）。
    """

    signal: np.ndarray      # (T, P) float64
    eligible: np.ndarray    # (T, P) bool
    true_return: np.ndarray # (T, P) float64
    timestamps: pd.DatetimeIndex
    products: pd.Index

    @property
    def n_timestamps(self) -> int:
        return self.signal.shape[0]

    @property
    def n_products(self) -> int:
        return self.signal.shape[1]


def build_panel_arrays(
        panel: pd.DataFrame,
        timeline: pd.DatetimeIndex,
        products: list[str],
) -> PanelArrays:
    """把 ragged long panel 一次性展开成 ``(timestamp, product)`` 矩阵。

    展开后每个时点的一行就是全品种对齐好的切片，与权重机逐期 reindex 的
    结果一致；差异仅在于原本「不存在的 (时点, 品种) 组合」在这里是显式的
    缺失值。这样实验主循环里就不必逐期做 ``xs`` / ``reset_index`` /
    ``droplevel``，也不必逐期 reindex，主循环内只做 numpy 切片。

    Parameters
    ----------
    panel : pd.DataFrame
        研究主面板，Index[timestamp, product]，含 true_return / eligible /
        signal 三列。
    timeline : pd.DatetimeIndex
        全局时间轴；矩阵的行顺序与之完全一致。
    products : list of str
        参与组合的全部品种代码；矩阵的列顺序与之完全一致。

    Returns
    -------
    PanelArrays
        三个矩阵视图。

    Raises
    ------
    ValueError
        当 panel 索引不唯一、或索引层级名不是 timestamp/product 时抛出。

    Notes
    -----
    ``eligible`` 经 unstack 后缺失格为 NaN，使 bool 列退化为 object dtype，
    这里直接取成 bool 数组，以满足下游把它当掩码使用的要求。

    """
    if not panel.index.is_unique:
        raise ValueError('研究主面板索引必须唯一，才能展开成矩阵。')
    if list(panel.index.names) != list(INDEX_NAMES):
        raise ValueError(
            f'研究主面板索引层级名必须为 {list(INDEX_NAMES)}，'
            f'实际为 {list(panel.index.names)}。')

    def _matrix(column: str) -> np.ndarray:
        return panel[column].unstack(level='product')\
            .reindex(index=timeline, columns=products).to_numpy()

    return PanelArrays(
        signal=_matrix('signal'),
        eligible=_matrix('eligible').astype(bool),
        true_return=_matrix('true_return'),
        timestamps=timeline,
        products=pd.Index(products, name=INDEX_NAMES[1]),
    )


def build_weights_series(
        weights_matrix: np.ndarray,
        timestamps: pd.DatetimeIndex,
        products: pd.Index,
) -> pd.Series:
    """把 ``(T, P)`` 权重矩阵一次性展开成 ``Index[timestamp, product]`` 的 Series。

    等价于逐期构造 MultiIndex 再 ``concat``，但只做一次 levels/codes 组装，
    不经过 MultiIndex 与 Series 的反复重建。

    Parameters
    ----------
    weights_matrix : np.ndarray
        ``(T, P)`` 的权重矩阵。
    timestamps : pd.DatetimeIndex
        行时间戳。
    products : pd.Index
        列品种。

    Returns
    -------
    pd.Series
        dtype float64、name 为 ``'weight'`` 的权重序列。

    """
    n_t, n_p = weights_matrix.shape
    multi_index = pd.MultiIndex(
        levels=[pd.DatetimeIndex(timestamps, name=INDEX_NAMES[0]), products],
        codes=[np.repeat(np.arange(n_t, dtype=np.intp), n_p),
               np.tile(np.arange(n_p, dtype=np.intp), n_t)],
        names=list(INDEX_NAMES),
        verify_integrity=False,
    )
    return pd.Series(
        weights_matrix.ravel(), index=multi_index, name='weight', copy=False)


def build_global_timeline(panel: pd.DataFrame) -> pd.DatetimeIndex:
    """构造全局时间轴。""" # 注意，下面的实现目前不严谨：如果panel在某个日期，所有品种都没有数据，那么依然得不到完整的时间轴。真正的权威时间轴，要向交易日历索取。
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
    严谨版（待实现）：
    构建组合实验的全局时间轴。
    品种时间轴 = [start_date, end_date] 和 [上市时间，退市时间] 的交集，再取交易日。

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

        # 一次性把 ragged long panel 展开成 (timestamp, product) 矩阵，
        # 使主循环内只需按行做 numpy 切片，不产生任何 pandas 索引开销。
        arrays = build_panel_arrays(
            panel=panel, timeline=timeline, products=products)
        n_decisions = arrays.n_timestamps - 1

        weights_matrix = np.empty(
            (n_decisions, arrays.n_products), dtype=float)
        ledger = DecisionLedger()

        for i, timestamp in enumerate(timeline[:-1]):
            rebalance: bool = bool(rebalance_flags.iat[i])
            current_weights = machine.update(
                rebalance=rebalance,
                eligible=arrays.eligible[i],
                signal=arrays.signal[i],
                ret=arrays.true_return[i],
                timestamp=timestamp,
            )
            weights_matrix[i] = current_weights.weights
            # 只记账警告与元数据；权重已进矩阵，无需逐期留 Series
            ledger.append_warnings(current_weights.warnings,
                                   timestamp=timestamp)
            ledger.append_meta(current_weights.metadata, timestamp=timestamp)

        weights = build_weights_series(
            weights_matrix, arrays.timestamps[:-1], arrays.products)
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
            metadata_ledger=metadata_ledger,
        )

    def _collect_date_range_metadata(
            self, target_weights: pd.Series,
            experiment_timeline: pd.DatetimeIndex) -> Mapping[str, pd.Series]:
        """收集本次实验相关的时间范围信息。包括：
        1. 信号的第一次和最后一次非全 NaN 时期（信号的时间范围），找不到则为 None。
        2. 目标权重的第一次、最后一次非零 gross_exposure 时期（实际策略运作的时间范围），
        找不到同为None。
        3. 实验运行的时间轴起点和终点。
        """
        # 1. signal
        first_signal_valid_ts = self._signal.values.first_valid_index()[0] # pyright: ignore[reportIndexIssue]
        last_signal_valid_ts = self._signal.values.last_valid_index()[0] # pyright: ignore[reportIndexIssue]
        # 2. 权重
        _w = target_weights.where(target_weights!=0)
        first_nonzero_gross_expsoure_ts = _w.first_valid_index()[0] # pyright: ignore[reportIndexIssue]
        last_nonzero_gross_expsoure_ts = _w.last_valid_index()[0] # pyright: ignore[reportIndexIssue]
        # 3. 实验时间轴
        first_experiment_timeline_ts = experiment_timeline[0]
        last_experiment_timeline_ts = experiment_timeline[-1]

        date_range_meta = pd.Series(
            {
                'first_signal_valid_timestamp': first_signal_valid_ts,
                'last_signal_valid_timestamp': last_signal_valid_ts,
                'first_nonzero_gross_exposure_timestamp': first_nonzero_gross_expsoure_ts,
                'last_nonzero_gross_exposure_timestamp': last_nonzero_gross_expsoure_ts,
                'experiment_timeline_first': first_experiment_timeline_ts,
                'experiment_timeline_last': last_experiment_timeline_ts
            },
            name='date_range_meta'
        )
        return {'date_range_meta': date_range_meta}

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
        warnings_facts: list[str] = []
        metadata_facts: Mapping[str, Any] = {}

        date_range_meta = self._collect_date_range_metadata(
            target_weights=weights,
            experiment_timeline=timeline
        )
        metadata_facts.update(date_range_meta)

        weight_changes, turnover, cost = compute_weight_changes_turnover_cost(
        weights, cost_config=self._spec.cost
        )
        # timeline alignment：收益实现时点 t 对应决策时点 t-1 的权重与成本
        aligned_df, weights_shifted, costs_shifted = \
            self._align_weight_return_cost(weights, panel, cost, timeline)

        contributions, warnings, metadata = compute_contribution(
            aligned_df, missing_return_policy=self._spec.missing_return_policy)
        warnings_facts.extend(warnings)
        metadata_facts.update(metadata)

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
            first_nonzero_gross_exposure_timestamp = \
                date_range_meta['date_range_meta']\
                .loc['first_nonzero_gross_exposure_timestamp'],
            annualization_factor=self._spec.annualization_factor
        )
        warnings_facts.extend(warnings)

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
            warnings=tuple(warnings_facts),
            metadata=metadata_facts
        )

    def _build_result(
            self, 
            target_weights: pd.Series,
            rebalance_flags: pd.Series,
            artifacts: AccountingArtifacts,
            warnings_ledger: tuple[str,...],
            metadata_ledger: Mapping[str, Any],
    ) -> DiagnosticPortfolioResult:
        """
        工厂函数：组装目标权重、再平衡flags、实验结果的artifacts
        （accounting阶段的警告和元数据）、记录册上的警告和元数据、
        日期范围元数据到实验结果类返回。
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
    