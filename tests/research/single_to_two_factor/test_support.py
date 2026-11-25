"""研究关键不变量：时序隔离、公开记账一致性与成分净额结算。"""
from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from factors.diagnostic_portfolio import DiagnosticPortfolioExperimentRunner
from factors.diagnostic_portfolio.models import DiagnosticSignal, DiagnosticPortfolioSpec, RebalanceConfig, RiskConfig, ConstraintConfig, CostConfig
from factors.profiles import market_rank_ls, ts_inv_vol_target_vol
from factors.models import LabelSpec
from factors.labels import LabelBuilder
from factors.preprocess.scale import FittedPooledScalar
from research.experiments.single_to_two_factor.support import (
    MatrixPanel, WeightRun, simulate_signal, combine_positions, evaluate_target_weights,
    paired_block_bootstrap, assert_development_only, save_result, verify_snapshot, to_long,
)

# 直接测试 notebook 中的唯一选择实现，不复制研究规则，也不执行任何取数单元格。
import ast
import json
from pathlib import Path
_notebook_path=Path(__file__).resolve().parents[3]/'research/notebooks/202611/single-to-two-factor_research_standard.ipynb'
_notebook=json.loads(_notebook_path.read_text(encoding='utf-8-sig'))
_selection_node=next(node for cell in _notebook['cells'] if cell['cell_type']=='code'
    for node in ast.parse(''.join(cell['source'])).body if isinstance(node,ast.FunctionDef) and node.name=='choose_candidate')
_selection_namespace={'pd':pd,'np':np}
exec(compile(ast.Module(body=[_selection_node],type_ignores=[]),str(_notebook_path),'exec'),_selection_namespace)
choose_candidate=_selection_namespace['choose_candidate']

@pytest.fixture
def inputs():
    rng = np.random.default_rng(19)
    dates = pd.bdate_range('2010-10-01', periods=85)
    products = pd.Index([f'P{x:02}' for x in range(12)], name='product')
    ret = pd.DataFrame(rng.normal(0.0002, 0.01, (len(dates), len(products))), index=dates, columns=products)
    elig = pd.DataFrame(True, index=dates, columns=products)
    sig = pd.DataFrame(rng.normal(size=ret.shape), index=dates, columns=products)
    dataset = SimpleNamespace(main_panel=to_long(ret, 'True_Return').to_frame(),
        universe=SimpleNamespace(eligible=to_long(elig, 'eligible')), auxiliary={})
    return MatrixPanel(ret, elig), sig, dataset


def make_spec(ts=False):
    return DiagnosticPortfolioSpec(name='test', harvest_profile=ts_inv_vol_target_vol if ts else market_rank_ls,
        rebalance=RebalanceConfig(5), constraint=ConstraintConfig(max_gross=2.5 if ts else None,
            max_product_weight=0.2, max_volatility_scaler=2.5 if ts else None), cost=CostConfig(0.0001),
        risk=RiskConfig(5, 5, 10, 8, 0.1) if ts else None,
        min_cross_section_size=None if ts else 10, clip_bound=3.0 if ts else None,
        missing_return_policy='raise')


@pytest.mark.parametrize('ts', [False, True])
def test_public_accounting_matches_existing_runner(inputs, ts):
    panel, sig, dataset = inputs
    spec = make_spec(ts)
    signal = DiagnosticSignal('same', to_long(sig, 'signal'), 'preprocessed', 'synthetic')
    original = DiagnosticPortfolioExperimentRunner(signal, dataset, spec).run()
    run = simulate_signal(panel, sig, spec, panel.returns.index[1], panel.returns.index[-1])
    adapted = evaluate_target_weights(panel, run, spec, name='same')
    np.testing.assert_allclose(adapted.target_weights, original.target_weights, atol=1e-10, rtol=0)
    np.testing.assert_allclose(adapted.portfolio_returns, original.portfolio_returns, atol=1e-10, rtol=0)
    np.testing.assert_allclose(adapted.nav, original.nav, atol=1e-10, rtol=0)


@pytest.mark.parametrize('ts', [False, True])
def test_prefix_invariance_and_annual_state(inputs, ts):
    panel, sig, _ = inputs
    spec = make_spec(ts)
    full = simulate_signal(panel, sig, spec, panel.returns.index[15], panel.returns.index[-1])
    truncated = MatrixPanel(panel.returns.iloc[:65], panel.eligible.iloc[:65])
    short = simulate_signal(truncated, sig.iloc[:65], spec, panel.returns.index[15], panel.returns.index[64])
    pd.testing.assert_frame_equal(full.weights.loc[short.weights.index], short.weights)
    # 跨年时若不调仓，旧持仓必须保持，不能在每个年度重新开仓。
    boundary = full.weights.index[full.weights.index.year == 2011][0]
    if not full.flags.loc[boundary]:
        pos = full.weights.index.get_loc(boundary)
        np.testing.assert_allclose(full.weights.iloc[pos], full.weights.iloc[pos-1])


@pytest.mark.parametrize('ts', [False, True])
def test_offsetting_positions_have_zero_cost(inputs, ts):
    panel, sig, _ = inputs
    spec = make_spec(ts)
    first = simulate_signal(panel, sig, spec, panel.returns.index[15], panel.returns.index[-1])
    opposite = replace(first, weights=-first.weights)
    merged = combine_positions(panel, first, opposite, 0.5, spec)
    result = evaluate_target_weights(panel, merged, spec, name='cancelled')
    np.testing.assert_allclose(merged.weights, 0, atol=1e-12)
    np.testing.assert_allclose(result.portfolio_returns, 0, atol=1e-12)
    assert result.performance.loc['proxy_net', 'sharpe'] is np.nan or pd.isna(result.performance.loc['proxy_net', 'sharpe'])


def test_target_vol_overlay_does_not_apply_inverse_vol_again(inputs):
    panel, _, _ = inputs
    spec = replace(make_spec(True), constraint=ConstraintConfig(max_gross=100, max_product_weight=None, max_volatility_scaler=100))
    idx = panel.returns.index[20:-1]
    weights = pd.DataFrame(np.tile(np.linspace(-0.2, 0.3, 12), (len(idx), 1)), index=idx, columns=panel.returns.columns)
    flags = pd.Series(True, index=idx)
    run = WeightRun(weights, flags, pd.DataFrame(index=idx))
    merged = combine_positions(panel, run, run, 0.5, spec, audit=True)
    for ts in idx:
        ratios = merged.weights.loc[ts]/weights.loc[ts]
        np.testing.assert_allclose(ratios, ratios.iloc[0], atol=1e-12)
    np.testing.assert_allclose(merged.risk.ex_ante_final_volatility, 0.1, atol=1e-10)


def test_missing_active_return_raises(inputs):
    panel, sig, _ = inputs
    spec = make_spec()
    run = simulate_signal(panel, sig, spec, panel.returns.index[15], panel.returns.index[-1])
    decision = run.weights.abs().sum(axis=1).gt(0).idxmax()
    realized = panel.returns.index[panel.returns.index.get_loc(decision)+1]
    broken = panel.returns.copy()
    broken.loc[realized, run.weights.columns[0]] = np.nan
    with pytest.raises(ValueError, match='缺失'):
        evaluate_target_weights(replace(panel, returns=broken), run, spec, name='missing')


def test_holdout_guard_and_full_horizon_labels(inputs):
    panel, _, dataset = inputs
    with pytest.raises(ValueError, match='保留样本'):
        assert_development_only(pd.Series([1.0], index=pd.to_datetime(['2025-01-02'])))
    labels = LabelBuilder(LabelSpec('y', 5, min_valid_obs=5), dataset).build().labels.y.unstack('product')
    assert labels.iloc[-5:].isna().all().all()
    dense = panel.returns.copy()
    dense.iloc[20, 0] = np.nan
    dataset.main_panel = to_long(dense, 'True_Return').to_frame()
    labels = LabelBuilder(LabelSpec('y', 5, min_valid_obs=5), dataset).build().labels.y.unstack('product')
    assert labels.iloc[15:20, 0].isna().all()


def test_rms_preserves_zero_sign_and_uses_only_training(inputs):
    panel, sig, _ = inputs
    sig.iloc[0, 0] = 0
    values, eligible = to_long(sig, 'factor'), to_long(panel.eligible, 'eligible')
    train = values.index.get_level_values('timestamp') < panel.returns.index[40]
    fitted = FittedPooledScalar().fit(values.loc[train], eligible.loc[train])
    transformed = fitted.transform(values, eligible).values
    np.testing.assert_array_equal(np.sign(values), np.sign(transformed))
    changed = values.copy()
    changed.loc[~train] *= 1000
    second = FittedPooledScalar().fit(changed.loc[train], eligible.loc[train])
    assert second.scaling_coef_ == fitted.scaling_coef_


def test_choice_determinism_and_no_fallback():
    table = pd.DataFrame([
        dict(candidate_id='b', status='ok', n_observations=240, sharpe=1.0, turnover=0.1, reference_distance=1),
        dict(candidate_id='a', status='ok', n_observations=240, sharpe=1.0, turnover=0.1, reference_distance=0),
        dict(candidate_id='bad', status='error', n_observations=0, sharpe=100, turnover=0, reference_distance=0)])
    assert choose_candidate(table, min_observations=200, tolerance=1e-12).candidate_id == 'a'
    with pytest.raises(ValueError, match='回退'):
        choose_candidate(table.iloc[2:], min_observations=200, tolerance=1e-12)


def test_paired_bootstrap_and_family_interval():
    rng = np.random.default_rng(5)
    base = rng.normal(size=120)
    returns = pd.DataFrame({'a': base, 'b': base, 'flat': np.zeros(120)})
    args = dict(block_size=20, reps=60, seed=3, annualization=252, family_size=4, practical_delta=0.1)
    one = paired_block_bootstrap(returns[['a', 'b']], [('a', 'b')], **args)
    two = paired_block_bootstrap(returns[['a', 'b']], [('a', 'b')], **args)
    pd.testing.assert_frame_equal(one, two)
    assert one.loc[0, 'ci_low'] == one.loc[0, 'ci_high'] == 0
    assert one.loc[0, 'confidence_level'] == 0.9875


def test_snapshot_replay_and_tamper_detection(inputs, tmp_path):
    panel, sig, _ = inputs
    spec = make_spec()
    run = simulate_signal(panel, sig, spec, panel.returns.index[15], panel.returns.index[-1])
    result = evaluate_target_weights(panel, run, spec, name='replay')
    save_result(tmp_path, result, panel, run, {'protocol': 'synthetic'})
    assert verify_snapshot(tmp_path)['max_absolute_error'] < 1e-10
    with (tmp_path/'returns.parquet').open('ab') as stream:
        stream.write(b'tampered')
    with pytest.raises(ValueError, match='完整性'):
        verify_snapshot(tmp_path)



def test_fast_rolling_groups_match_core_and_ignore_future_labels():
    from factors.evaluation.grouped import grouped_details
    from research.experiments.single_to_two_factor.diagnostics import rolling_time_series_groups
    rng=np.random.default_rng(22)
    dates=pd.bdate_range('2010-01-01',periods=65)
    index=pd.MultiIndex.from_product([dates,['A','B']],names=['timestamp','product'])
    frame=pd.DataFrame({'factor':np.clip(rng.normal(size=len(index)),-1,1),
                        'label':rng.normal(size=len(index)),'universe':rng.random(len(index))>0.15},index=index)
    frame.loc[index[8:15],'factor']=np.nan
    frame.loc[index[70:120],'factor']=0.5
    frame.loc[index[30:45],'label']=np.nan
    core=grouped_details('time_series',frame,5,8,nan_policy='drop',ts_grouping_method='rolling',rolling_window=20)
    fast=rolling_time_series_groups(frame,window=20,minimum=8,n_groups=5)
    for product in ['A','B']:
        pd.testing.assert_series_equal(core.group_assignment[product],fast.group_assignment[product])
        pd.testing.assert_series_equal(core.group_missing_reason[product],fast.group_missing_reason[product])
        pd.testing.assert_frame_equal(core.group_metadata[product],fast.group_metadata[product],check_dtype=False)
        pd.testing.assert_series_equal(core.grouped_forward_returns[product],fast.grouped_forward_returns[product])
    changed=frame.copy()
    changed['label']=np.nan
    again=rolling_time_series_groups(changed,window=20,minimum=8,n_groups=5)
    for product in ['A','B']:
        pd.testing.assert_series_equal(fast.group_assignment[product],again.group_assignment[product])


def test_position_snapshot_preserves_actual_components(inputs,tmp_path):
    panel,sig,_=inputs
    spec=make_spec()
    first=simulate_signal(panel,sig,spec,panel.returns.index[15],panel.returns.index[-1])
    second=simulate_signal(panel,-sig+0.2,spec,panel.returns.index[15],panel.returns.index[-1])
    run=combine_positions(panel,first,second,0.25,spec,audit=True)
    result=evaluate_target_weights(panel,run,spec,name='positions')
    save_result(tmp_path,result,panel,run,{'protocol':'synthetic'})
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path/'component_first.parquet'),first.weights,check_freq=False)
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path/'component_second.parquet'),second.weights,check_freq=False)
    assert verify_snapshot(tmp_path)['max_absolute_error'] < 1e-10




def test_alpha_switch_only_on_scheduled_rebalance(inputs):
    panel,_,_=inputs
    spec=make_spec()
    idx=panel.returns.index[20:-1]
    values=np.r_[np.repeat(1/12,6),np.repeat(-1/12,6)]
    first_weights=pd.DataFrame(np.tile(values,(len(idx),1)),index=idx,columns=panel.returns.columns)
    second_weights=first_weights.iloc[:,::-1].copy()
    second_weights.columns=first_weights.columns
    flags=pd.Series(np.arange(len(idx))%5==0,index=idx)
    first=WeightRun(first_weights,flags,pd.DataFrame(index=idx))
    second=WeightRun(second_weights,flags,pd.DataFrame(index=idx))
    alpha=pd.Series(0.25,index=idx)
    alpha.iloc[2:]=0.75
    run=combine_positions(panel,first,second,alpha,spec,audit=True)
    assert (run.risk.alpha.iloc[:5]==0.25).all()
    assert run.risk.alpha.iloc[5]==0.75
    np.testing.assert_allclose(run.weights.iloc[1:5],np.tile(run.weights.iloc[0].to_numpy(),(4,1)))
    np.testing.assert_allclose(run.weights.iloc[5],-run.weights.iloc[0])
    result=evaluate_target_weights(panel,run,spec,name='annual_switch')
    assert result.turnover.loc[idx[5]] > 0
    expected_cost=result.turnover*spec.cost.rate
    np.testing.assert_allclose(result.portfolio_returns.cost,expected_cost)

