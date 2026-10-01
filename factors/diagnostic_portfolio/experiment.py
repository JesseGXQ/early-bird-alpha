from __future__ import annotations
from dataclasses import asdict, dataclass
import pandas as pd
import numpy as np

from typing import TYPE_CHECKING, Any, Mapping

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
    from factors.diagnostic_portfolio.models import DiagnosticSignal
    from factors.diagnostic_portfolio.models import DiagnosticPortfolioSpec
    from factors.models import ResearchDataset


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
    返回的数据为 full panel，即索引为 [时间戳，品种] 的笛卡尔积。
    """
    true_return_series = dataset.main_panel['True_Return'].rename('true_return')
    universe = dataset.universe.eligible.copy().rename('eligible')
    signal_series = signal.values.copy().rename('signal')
    _verify_signal_index(signal_series.index, true_return_series.index) # pyright: ignore[reportArgumentType]

    # 以主面板行集为左表，按 MultiIndex[timestamp, product] 对齐拼接；
    # 信号未覆盖的行保留为 NaN，信号多出的行被丢弃。
    # TODO 这里日频是用的权威时间轴（因为构建日频主面板时，保留了Dominant_Contract 为缺失的行，实际的时间轴来自官方交易日历）；但更高频率（例如分钟级）数据由于丢弃了Dominant_Contract 为缺失的行，所以可能还需要建立一条更高频率下的权威时间轴？
    experiment_panel = pd.concat(
        [true_return_series, universe, signal_series], axis=1
    ).reindex(true_return_series.index)
    # Make full panel
    timestamps = experiment_panel.index\
        .get_level_values('timestamp').unique().sort_values()
    products = experiment_panel.index\
        .get_level_values('product').unique().sort_values()
    full_index = pd.MultiIndex.from_product(
        [timestamps, products], names=['timestamp', 'product']
    )
    return experiment_panel.reindex(full_index)

def build_global_timeline(panel: pd.DataFrame) -> pd.DatetimeIndex:
    """构造全局时间轴。"""
    timeline = panel.index.get_level_values('timestamp')\
        .drop_duplicates().sort_values()
    return pd.to_datetime(timeline)

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
    warnings: tuple[str]
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

        machine = self._start_machine(products=products)

        ledger = DecisionLedger()

        for timestamp in timeline[:-1]:
            rebalance: bool = rebalance_flags.at[timestamp]
            _data = panel.xs(
                timestamp, level='timestamp')
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
            weights=weights, panel=panel,
        )
        return self._build_result(
            target_weights=weights,
            rebalance_flags=rebalance_flags,
            artifacts=artifacts,
            warnings_ledger=warnings_ledger,
            metadata_ledger=metadata_ledger
        )
        

    def _align_weight_return_cost(
            self, weights: pd.Series, 
            panel: pd.DataFrame, cost: pd.Series
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

        Returns
        -------
        aligned_df : pd.DataFrame
            收益实现时点上的对齐数据集，Index[timestamp, product]，
            columns 含 true_return、eligible、weight、cost。
        weights_shifted : pd.Series
            对齐到收益实现时点的权重，Index[timestamp, product]。
        costs_shifted : pd.Series
            对齐到收益实现时点的成本，Index[timestamp]。

        Notes
        -----
        权重与成本都必须先补齐到全局时间轴、再向后平移一期，否则最后
        一个收益实现时点会拿不到 t_{N-1} 期的权重与成本。
        """
        assert isinstance(weights.index, pd.MultiIndex)
        assert weights.notna().all()
        assert panel.index.is_unique

        timeline = build_global_timeline(panel)

        # 权重：先补齐到全局时间轴（最后一期收益需要上一期权重），再向后
        # 平移一期，使实现时点 t 对应决策时点 t-1 的权重。
        weights_shifted_full = weights.reindex(panel.index)\
            .groupby(level='product').shift(1).rename('weight')
        # 成本：同样先补齐到全局时间轴再平移，避免最后一个实现时点缺成本。
        cost_shifted_ts = cost.reindex(timeline).shift(1).rename('cost')

        aligned_df = panel.join(weights_shifted_full, how='left')
        aligned_df['cost'] = aligned_df.index\
            .get_level_values('timestamp').map(cost_shifted_ts)

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
        panel: pd.DataFrame        
    ):
        """计算各实验结果统计量 artifacts 作为中间结果。"""
        warnings_list = []

        weight_changes, turnover, cost = compute_weight_changes_turnover_cost(
        weights, cost_config=self._spec.costs
        )
        # timeline alignment：收益实现时点 t 对应决策时点 t-1 的权重与成本
        aligned_df, weights_shifted, costs_shifted = \
            self._align_weight_return_cost(weights, panel, cost)

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
            f'Harvest profile :{self._spec.harvest_profile.name} \
            目前无对应的权重状态机。'
        )

        
        
        

        
        


            

            







# =====================
# inline smoke test
# =====================
if __name__ == '__main__':
    from factors.models import DatasetSpec, DataQualitySummary, ResearchDataset
    from factors.universe import Universe
    from factors.diagnostic_portfolio.models import DiagnosticSignal

    timestamps = pd.to_datetime(['2024-01-01', '2024-01-02'])
    products = ['RB', 'MA']
    panel_index = pd.MultiIndex.from_product(
        [timestamps, products], names=INDEX_NAMES)
    main_panel = pd.DataFrame(
        {'True_Return': [0.1, 0.2, 0.3, 0.4]}, index=panel_index)

    # ---- Test 1: align_inputs ----
    print('Test 1: align_inputs — 三列按 MultiIndex 对齐，信号缺失行为 NaN')
    universe = Universe(
        eligible=pd.Series([True, False, True, True], index=panel_index),
        exclude_reason=pd.Series('', index=panel_index),
    )
    signal = DiagnosticSignal(
        name='smoke_signal',
        values=pd.Series([1.0, -1.0], index=panel_index[:2]),
        source_stage='raw',
        factor_name='smoke_factor',
        recipe={},
    )
    dataset = ResearchDataset(
        spec=DatasetSpec(
            facade_config=(), products=tuple(products), data_requirements=()),
        main_panel=main_panel,
        quality=DataQualitySummary(
            is_valid=True, total_rows=len(panel_index),
            product_sample_counts=None, missing_rate_by_column={},
            warnings=(), errors=()),
        universe=universe,
    )
    panel = align_inputs(signal, dataset)
    # 全面板按 (timestamp, product) 升序，产品按代码升序（MA < RB）。
    expected_index = pd.MultiIndex.from_product(
        [timestamps, sorted(products)], names=INDEX_NAMES)
    assert tuple(panel.columns) == ('true_return', 'eligible', 'signal')
    assert tuple(panel.index.names) == INDEX_NAMES
    assert panel.index.equals(expected_index)
    assert panel['true_return'].tolist() == [0.2, 0.1, 0.4, 0.3]
    assert panel['eligible'].tolist() == [False, True, True, True]
    assert panel['signal'].isna().tolist() == [False, False, True, True]
    print('  → PASS')

    # ---- Test 2: build_global_timeline ----
    print('Test 2: build_global_timeline — 乱序面板去重、排序、转 DatetimeIndex')
    shuffled = panel.sample(frac=1.0, random_state=0)
    timeline = build_global_timeline(shuffled)
    assert isinstance(timeline, pd.DatetimeIndex)
    assert timeline.is_unique and timeline.is_monotonic_increasing
    assert timeline.tolist() == timestamps.tolist()
    print('  → PASS')

    # ---- Test 3: build_rebalance_schedule ----
    print('Test 3: build_rebalance_schedule — 无锚点/有锚点/锚点越界')
    schedule = pd.date_range('2024-01-01', periods=5, freq='D')
    flags = build_rebalance_schedule(schedule, RebalanceConfig(interval=2))
    assert flags.name == 'rebalance_flags'
    assert flags.dtype == bool and flags.index.equals(schedule)
    assert flags.tolist() == [True, False, True, False, False]

    flags = build_rebalance_schedule(
        schedule, RebalanceConfig(interval=2, anchor=pd.Timestamp('2024-01-02')))
    assert flags.tolist() == [False, True, False, True, False]

    flags = build_rebalance_schedule(
        schedule,
        RebalanceConfig(interval=2, anchor=pd.Timestamp('2024-01-02 12:00')))
    assert flags.tolist() == [False, False, True, False, False]

    try:
        build_rebalance_schedule(
            schedule, RebalanceConfig(interval=2,
                                      anchor=pd.Timestamp('2024-01-05')))
    except ValueError as exc:
        assert 'anchor' in str(exc)
    else:
        raise AssertionError('anchor 落在倒数第一应抛出 ValueError')
    print('  → PASS')

    # ---- Test 4: DiagnosticPortfolioExperimentRunner.run()（market_rank_ls）----
    print('Test 4: run() — market_rank_ls 端到端结构与手工复算')
    from factors.profiles import market_rank_ls
    from factors.diagnostic_portfolio.models import (
        DiagnosticPortfolioSpec, ConstraintConfig, CostConfig,
    )
    from factors.diagnostic_portfolio.experiment import (
        DiagnosticPortfolioExperimentRunner,
    )

    run_timestamps = pd.to_datetime(
        ['2024-03-01', '2024-03-04', '2024-03-05', '2024-03-06',
         '2024-03-07'])
    run_products = ['A', 'B', 'C', 'D']
    run_index = pd.MultiIndex.from_product(
        [run_timestamps, run_products], names=INDEX_NAMES)

    # 手工设定下一期 True_Return：t1、t3 有明确收益，t0/t2/t4 为 0 或弃用。
    run_returns = pd.Series(0.0, index=run_index, name='True_Return')
    run_returns.loc[pd.IndexSlice['2024-03-01', :]] = [0.05] * 4
    run_returns.loc[pd.IndexSlice['2024-03-04', :]] = \
        [0.01, 0.02, -0.01, 0.03]
    run_returns.loc[pd.IndexSlice['2024-03-05', :]] = [0.0, 0.0, 0.0, 0.0]
    run_returns.loc[pd.IndexSlice['2024-03-06', :]] = \
        [0.02, -0.01, 0.03, -0.02]
    run_returns.loc[pd.IndexSlice['2024-03-07', :]] = [0.0, 0.0, 0.0, 0.0]

    # 只在两个计划调仓日 t0(03-01)、t2(03-05) 给出信号，其余为 NaN。
    run_signal = pd.Series(np.nan, index=run_index, name='signal')
    run_signal.loc[pd.IndexSlice['2024-03-01', :]] = [1.0, 2.0, 3.0, 4.0]
    run_signal.loc[pd.IndexSlice['2024-03-05', :]] = [4.0, 3.0, 2.0, 1.0]

    run_dataset = ResearchDataset(
        spec=DatasetSpec(
            facade_config=(), products=tuple(run_products),
            data_requirements=()),
        main_panel=pd.DataFrame({'True_Return': run_returns},
                                index=run_index),
        quality=DataQualitySummary(
            is_valid=True, total_rows=len(run_index),
            product_sample_counts=None, missing_rate_by_column={},
            warnings=(), errors=()),
        universe=Universe(
            eligible=pd.Series(True, index=run_index),
            exclude_reason=pd.Series('', index=run_index)),
    )
    run_signal_obj = DiagnosticSignal(
        name='run_smoke_signal', values=run_signal, source_stage='raw',
        factor_name='run_smoke_factor', recipe={})
    run_spec = DiagnosticPortfolioSpec(
        name='run_smoke_market_rank_ls',
        harvest_profile=market_rank_ls,
        rebalance=RebalanceConfig(interval=2),
        constraints=ConstraintConfig(max_gross=1.0),
        costs=CostConfig(rate=0.0003),
        realized_volatility_lookback=2,
        min_cross_section_size=2,
    )
    result = DiagnosticPortfolioExperimentRunner(
        run_signal_obj, run_dataset, run_spec).run()

    decision_index = run_timestamps[:-1]
    realization_index = run_timestamps[1:]

    assert result.signal_name == 'run_smoke_signal'
    assert result.spec is run_spec
    assert result.sector_attribution is None

    # 调仓标志只覆盖全局决策时间轴（最后一个时点被裁掉）。
    pd.testing.assert_series_equal(
        result.rebalance_flags,
        pd.Series([True, False, True, False], index=decision_index),
        check_names=False)

    # 截面 rank 权重：4 品种去中心化 rank / |sum|，gross=1、net=0。
    w0 = pd.Series([-0.375, -0.125, 0.125, 0.375], index=run_products)
    w2 = pd.Series([0.375, 0.125, -0.125, -0.375], index=run_products)
    target_weights = result.target_weights
    assert target_weights.index.names == INDEX_NAMES
    for ts, expected in (
        (run_timestamps[0], w0), (run_timestamps[1], w0),
        (run_timestamps[2], w2), (run_timestamps[3], w2),
    ):
        pd.testing.assert_series_equal(
            target_weights.xs(ts, level='timestamp'), expected,
            check_names=False, atol=1e-12)

    # 换手与决策成本：初始建仓 1.0；t2 完全反向 2.0；冻结日 0。
    expected_turnover = pd.Series([1.0, 0.0, 2.0, 0.0], index=decision_index)
    pd.testing.assert_series_equal(
        result.turnover, expected_turnover, check_names=False)
    pd.testing.assert_series_equal(
        result.decision_cost, 0.0003 * expected_turnover, check_names=False)

    # 收益、成本与净值：实现时点为 t1..t4，成本 t 对应上一决策时点。
    returns = result.portfolio_returns
    assert returns.index.tolist() == realization_index.tolist()
    assert returns['gross_return'].notna().all()
    np.testing.assert_allclose(
        returns['gross_return'].to_numpy(),
        [0.00375, 0.0, 0.01, 0.0], atol=1e-12)
    np.testing.assert_allclose(
        returns['cost'].to_numpy(), [0.0003, 0.0, 0.0006, 0.0], atol=1e-12)
    np.testing.assert_allclose(
        returns['proxy_net_return'].to_numpy(),
        [0.00345, 0.0, 0.0094, 0.0], atol=1e-12)

    nav = result.nav
    assert nav.index.tolist() == realization_index.tolist()
    np.testing.assert_allclose(
        nav['gross_nav'].to_numpy(),
        np.cumprod([1.00375, 1.0, 1.01, 1.0]), atol=1e-12)
    np.testing.assert_allclose(
        nav['proxy_net_nav'].to_numpy(),
        np.cumprod([1.00345, 1.0, 1.0094, 1.0]), atol=1e-12)

    # 暴露：截面多空 gross=1、net=0、多空各 0.5，活跃 4 个品种。
    exposure = result.exposures
    assert exposure.index.tolist() == decision_index.tolist()
    np.testing.assert_allclose(
        exposure['gross_exposure'].to_numpy(), 1.0, atol=1e-12)
    np.testing.assert_allclose(
        exposure['net_exposure'].to_numpy(), 0.0, atol=1e-12)
    np.testing.assert_allclose(
        exposure['long_exposure'].to_numpy(), 0.5, atol=1e-12)
    np.testing.assert_allclose(
        exposure['short_exposure'].to_numpy(), -0.5, atol=1e-12)
    np.testing.assert_allclose(
        exposure['active_count'].to_numpy(), 4, atol=0)

    # 归因：分品种贡献加总 = 组合毛收益；多空方向贡献加总 = 组合毛收益。
    attribution = result.product_attribution
    assert attribution.index.tolist() == run_products
    assert np.isclose(
        attribution['gross_contribution_sum'].sum(),
        returns['gross_return'].sum(), atol=1e-12)
    direction = result.direction_attribution
    assert direction.index.tolist() == realization_index.tolist()
    np.testing.assert_allclose(
        (direction['long_contribution']
         + direction['short_contribution']).to_numpy(),
        returns['gross_return'].to_numpy(), atol=1e-12)

    # 绩效表结构与期数。
    performance = result.performance
    assert performance.index.tolist() == ['gross', 'proxy_net']
    assert performance['n_periods'].tolist() == [4, 4]
    print('  → PASS')

    print('ALL SMOKE TESTS PASSED')