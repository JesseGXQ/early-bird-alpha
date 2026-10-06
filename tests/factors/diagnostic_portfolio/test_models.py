"""tests/factors/diagnostic_portfolio/test_models.py — 组合实验层领域对象测试。

覆盖开发方案 §6 与 §24.1：

    - DiagnosticSignal 输入校验
    - RebalanceConfig / RiskConfig / ConstraintConfig / CostConfig 配置校验
    - DiagnosticPortfolioSpec 的 HarvestProfile 适用性与冲突字段
    - frozen dataclass 不可重绑定
    - V1 可执行研究配方注册表

全部使用 synthetic 对象，不依赖真实行情。
"""

from __future__ import annotations

import dataclasses
from datetime import date

import numpy as np
import pandas as pd
import pytest

from factors.diagnostic_portfolio.models import (
    SUPPORTED_PROFILE_NAMES,
    ConstraintConfig,
    CostConfig,
    DiagnosticPortfolioSpec,
    DiagnosticSignal,
    RebalanceConfig,
    RiskConfig,
)
from factors.profiles import (
    MAINSTREAM_HARVEST_PROFILES,
    V1_DIAGNOSTIC_PORTFOLIO_PROFILES,
    market_rank_ls,
    sector_risk_budget_cta,
    ts_inv_vol_gross_one,
    ts_inv_vol_target_vol,
)

INDEX_NAMES = ('timestamp', 'product')


# ============================================================
# 辅助
# ============================================================
def _index(n_ts: int = 3, products: list[str] | None = None) -> pd.MultiIndex:
    products = products or ['A', 'B']
    ts = pd.date_range('2024-01-01', periods=n_ts, freq='D')
    return pd.MultiIndex.from_product([ts, products], names=INDEX_NAMES)


def _valid_signal(**overrides) -> DiagnosticSignal:
    kwargs = dict(
        name='sig', values=pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], index=_index()),
        source_stage='raw', factor_name='fac', recipe={'window': 5})
    kwargs.update(overrides)
    return DiagnosticSignal(**kwargs)


def _base_spec_kwargs() -> dict:
    return dict(
        rebalance=RebalanceConfig(interval=1),
        cost=CostConfig(rate=0.0),
        realized_volatility_lookback=5,
    )


def _gross_one_spec(**overrides) -> DiagnosticPortfolioSpec:
    kwargs = dict(
        name='gross_one', harvest_profile=ts_inv_vol_gross_one,
        constraint=ConstraintConfig(),
        risk=RiskConfig(volatility_lookback=5, volatility_min_periods=3),
        clip_bound=1.0)
    kwargs.update(_base_spec_kwargs())
    kwargs.update(overrides)
    return DiagnosticPortfolioSpec(**kwargs)


def _target_vol_spec(**overrides) -> DiagnosticPortfolioSpec:
    kwargs = dict(
        name='target_vol', harvest_profile=ts_inv_vol_target_vol,
        risk=RiskConfig(
            volatility_lookback=5, volatility_min_periods=3,
            covariance_lookback=5, covariance_min_periods=3,
            target_volatility=0.2),
        clip_bound=1.0,
        constraint=ConstraintConfig(max_gross=2.0, max_volatility_scaler=3.0))
    kwargs.update(_base_spec_kwargs())
    kwargs.update(overrides)
    return DiagnosticPortfolioSpec(**kwargs)


def _market_rank_spec(**overrides) -> DiagnosticPortfolioSpec:
    kwargs = dict(
        name='rank', harvest_profile=market_rank_ls,
        constraint=ConstraintConfig(),
        min_cross_section_size=2)
    kwargs.update(_base_spec_kwargs())
    kwargs.update(overrides)
    return DiagnosticPortfolioSpec(**kwargs)


# ============================================================
# DiagnosticSignal
# ============================================================
def test_signal_valid_construction() -> None:
    sig = _valid_signal()
    assert sig.name == 'sig'
    assert sig.source_stage == 'raw'
    assert tuple(sig.values.index.names) == INDEX_NAMES


@pytest.mark.parametrize('field, value', [
    ('name', ''), ('name', '   '), ('factor_name', '')])
def test_signal_requires_non_empty_names(field: str, value: str) -> None:
    with pytest.raises(ValueError, match=field):
        _valid_signal(**{field: value})


def test_signal_rejects_bad_source_stage() -> None:
    with pytest.raises(ValueError, match='source_stage'):
        _valid_signal(source_stage='unknown')


def test_signal_rejects_non_series_values() -> None:
    with pytest.raises(ValueError, match='pd.Series'):
        _valid_signal(values=[1.0, 2.0])


@pytest.mark.parametrize('values', [
    pd.Series([True, False], index=_index(1)),
    pd.Series(['a', 'b'], index=_index(1)),
])
def test_signal_rejects_non_numeric_values(values: pd.Series) -> None:
    with pytest.raises(ValueError, match='数值型'):
        _valid_signal(values=values)


def test_signal_rejects_non_multiindex() -> None:
    with pytest.raises(ValueError, match='MultiIndex'):
        _valid_signal(values=pd.Series([1.0, 2.0]))


def test_signal_rejects_wrong_level_names() -> None:
    bad = pd.Series(
        [1.0, 2.0],
        index=pd.MultiIndex.from_product(
            [pd.to_datetime(['2024-01-01']), ['A', 'B']], names=['time', 'code']))
    with pytest.raises(ValueError, match='索引层级名称'):
        _valid_signal(values=bad)


def test_signal_rejects_duplicate_index() -> None:
    idx = pd.MultiIndex.from_tuples(
        [(pd.Timestamp('2024-01-01'), 'A'), (pd.Timestamp('2024-01-01'), 'A')],
        names=INDEX_NAMES)
    with pytest.raises(ValueError, match='唯一'):
        _valid_signal(values=pd.Series([1.0, 2.0], index=idx))


def test_signal_rejects_infinity() -> None:
    with pytest.raises(ValueError, match='无穷'):
        _valid_signal(values=pd.Series([1.0, np.inf], index=_index(1)))


def test_signal_allows_nan() -> None:
    sig = _valid_signal(values=pd.Series([1.0, np.nan], index=_index(1)))
    assert sig.values.isna().sum() == 1


def test_signal_rejects_non_mapping_recipe() -> None:
    with pytest.raises(ValueError, match='recipe'):
        _valid_signal(recipe=[('a', 1)])


def test_signal_rejects_non_str_recipe_key() -> None:
    with pytest.raises(ValueError, match='键必须是字符串'):
        _valid_signal(recipe={1: 'a'})


def test_signal_rejects_unknown_warning_stage() -> None:
    with pytest.raises(ValueError, match='warnings'):
        _valid_signal(warnings={'bad_stage': ()})


def test_signal_accepts_known_warning_stages() -> None:
    sig = _valid_signal(warnings={
        'factor_computation': ('w1',), 'preprocess': ()})
    assert set(sig.warnings) == {'factor_computation', 'preprocess'}


def test_signal_is_frozen() -> None:
    sig = _valid_signal()
    with pytest.raises(dataclasses.FrozenInstanceError):
        sig.name = 'other'  # type: ignore[misc]


# ============================================================
# RebalanceConfig
# ============================================================
@pytest.mark.parametrize('interval', [0, -1, 1.5, True])
def test_rebalance_interval_must_be_positive_int(interval) -> None:
    with pytest.raises(ValueError, match='interval'):
        RebalanceConfig(interval=interval)


def test_rebalance_anchor_accepts_none_timestamp_date() -> None:
    assert RebalanceConfig(interval=2).anchor is None
    assert RebalanceConfig(interval=2, anchor=pd.Timestamp('2024-01-01')).anchor \
        is not None
    assert RebalanceConfig(interval=2, anchor=date(2024, 1, 1)).anchor is not None


def test_rebalance_anchor_rejects_str() -> None:
    with pytest.raises(ValueError, match='anchor'):
        RebalanceConfig(interval=2, anchor='2024-01-01')


# ============================================================
# RiskConfig
# ============================================================
@pytest.mark.parametrize('min_periods', [1, 0, -1, True])
def test_risk_min_periods_at_least_two(min_periods) -> None:
    with pytest.raises(ValueError, match='volatility_min_periods'):
        RiskConfig(volatility_lookback=5, volatility_min_periods=min_periods)


def test_risk_min_periods_not_greater_than_lookback() -> None:
    with pytest.raises(ValueError, match='不能大于'):
        RiskConfig(volatility_lookback=5, volatility_min_periods=6)


def test_risk_covariance_lookback_and_min_periods_must_pair() -> None:
    with pytest.raises(ValueError, match='同时提供'):
        RiskConfig(volatility_lookback=5, volatility_min_periods=2,
                   covariance_lookback=5)
    with pytest.raises(ValueError, match='同时提供'):
        RiskConfig(volatility_lookback=5, volatility_min_periods=2,
                   covariance_min_periods=3)


def test_risk_covariance_min_periods_rules() -> None:
    with pytest.raises(ValueError, match='covariance_min_periods'):
        RiskConfig(volatility_lookback=5, volatility_min_periods=2,
                   covariance_lookback=5, covariance_min_periods=1)
    with pytest.raises(ValueError, match='不能大于'):
        RiskConfig(volatility_lookback=5, volatility_min_periods=2,
                   covariance_lookback=5, covariance_min_periods=6)


def test_risk_target_volatility_must_be_positive() -> None:
    with pytest.raises(ValueError, match='target_volatility'):
        RiskConfig(volatility_lookback=5, volatility_min_periods=2,
                   target_volatility=0.0)


# ============================================================
# ConstraintConfig
# ============================================================
def test_constraint_defaults_all_none() -> None:
    cfg = ConstraintConfig()
    assert cfg.max_gross is None
    assert cfg.max_product_weight is None
    assert cfg.max_volatility_scaler is None


@pytest.mark.parametrize('kwargs', [
    {'max_gross': 0.0}, {'max_gross': -1.0},
    {'max_product_weight': 0.0}, {'max_volatility_scaler': -0.5}])
def test_constraint_rejects_non_positive(kwargs) -> None:
    with pytest.raises(ValueError):
        ConstraintConfig(**kwargs)


# ============================================================
# CostConfig
# ============================================================
def test_cost_uniform_rate() -> None:
    cfg = CostConfig(rate=0.0003)
    assert cfg.is_uniform
    assert cfg.rate_for('rb') == pytest.approx(0.0003)


def test_cost_mapping_normalizes_upper() -> None:
    cfg = CostConfig(rate={'rb': 0.001, 'MA': 0.002})
    assert cfg.rate == {'RB': 0.001, 'MA': 0.002}
    assert not cfg.is_uniform
    assert cfg.rate_for('rb') == pytest.approx(0.001)


def test_cost_mapping_duplicate_after_normalization() -> None:
    with pytest.raises(ValueError, match='重复'):
        CostConfig(rate={'rb': 0.001, 'RB': 0.002})


@pytest.mark.parametrize('rate', [-0.1, np.inf, np.nan])
def test_cost_rejects_negative_or_non_finite(rate) -> None:
    with pytest.raises(ValueError):
        CostConfig(rate=rate)


def test_cost_mapping_requires_non_empty_str_keys() -> None:
    with pytest.raises(ValueError, match='键必须是非空字符串'):
        CostConfig(rate={'': 0.001})


def test_cost_validate_product_coverage() -> None:
    cfg = CostConfig(rate={'A': 0.001, 'B': 0.002})
    cfg.validate_product_coverage(('A', 'B'))  # 不抛异常
    with pytest.raises(ValueError, match='不一致'):
        cfg.validate_product_coverage(('A', 'B', 'C'))
    with pytest.raises(ValueError, match='不一致'):
        cfg.validate_product_coverage(('A',))
    # 统一成本率不参与覆盖校验
    CostConfig(rate=0.0).validate_product_coverage(())


# ============================================================
# DiagnosticPortfolioSpec：Profile 适用性
# ============================================================
@pytest.mark.parametrize('spec_factory', [
    _gross_one_spec, _target_vol_spec, _market_rank_spec])
def test_spec_valid_configurations(spec_factory) -> None:
    spec = spec_factory()
    assert spec.harvest_profile.name in SUPPORTED_PROFILE_NAMES


def test_spec_rejects_unsupported_profile() -> None:
    with pytest.raises(ValueError, match='不支持的 HarvestProfile'):
        DiagnosticPortfolioSpec(
            name='bad', harvest_profile=sector_risk_budget_cta,
            constraint=ConstraintConfig(),
            **_base_spec_kwargs(),
            risk=RiskConfig(volatility_lookback=5, volatility_min_periods=3),
            clip_bound=1.0)


def test_spec_gross_one_requires_risk() -> None:
    with pytest.raises(ValueError, match='必须提供 RiskConfig'):
        _gross_one_spec(risk=None)


def test_spec_gross_one_rejects_covariance_and_target() -> None:
    with pytest.raises(ValueError, match='均为 None'):
        _gross_one_spec(risk=RiskConfig(
            volatility_lookback=5, volatility_min_periods=3,
            covariance_lookback=5, covariance_min_periods=3))
    with pytest.raises(ValueError, match='均为 None'):
        _gross_one_spec(risk=RiskConfig(
            volatility_lookback=5, volatility_min_periods=3,
            target_volatility=0.2))


def test_spec_gross_one_requires_clip_bound() -> None:
    with pytest.raises(ValueError, match='clip_bound'):
        _gross_one_spec(clip_bound=None)


def test_spec_gross_one_rejects_min_cs_and_scaler() -> None:
    with pytest.raises(ValueError, match='min_cross_section_size'):
        _gross_one_spec(min_cross_section_size=2)
    with pytest.raises(ValueError, match='max_volatility_scaler'):
        _gross_one_spec(constraint=ConstraintConfig(max_volatility_scaler=2.0))


def test_spec_gross_one_forbids_max_gross() -> None:
    with pytest.raises(ValueError, match='max_gross'):
        _gross_one_spec(constraint=ConstraintConfig(max_gross=1.0))


def test_spec_target_vol_requires_full_risk() -> None:
    with pytest.raises(ValueError, match='covariance_lookback'):
        _target_vol_spec(risk=RiskConfig(
            volatility_lookback=5, volatility_min_periods=3))
    with pytest.raises(ValueError, match='target_volatility'):
        _target_vol_spec(risk=RiskConfig(
            volatility_lookback=5, volatility_min_periods=3,
            covariance_lookback=5, covariance_min_periods=3))


def test_spec_target_vol_requires_clip_scaler_and_rejects_min_cs() -> None:
    with pytest.raises(ValueError, match='clip_bound'):
        _target_vol_spec(clip_bound=None)
    with pytest.raises(ValueError, match='max_volatility_scaler'):
        _target_vol_spec(constraint=ConstraintConfig(max_gross=2.0))
    with pytest.raises(ValueError, match='min_cross_section_size'):
        _target_vol_spec(min_cross_section_size=2)


def test_spec_target_vol_allows_none_max_gross() -> None:
    spec = _target_vol_spec(
        constraint=ConstraintConfig(max_volatility_scaler=3.0))
    assert spec.constraint.max_gross is None


def test_spec_market_rank_rejects_risk_and_clip_and_scaler() -> None:
    with pytest.raises(ValueError, match='不接受 RiskConfig'):
        _market_rank_spec(risk=RiskConfig(
            volatility_lookback=5, volatility_min_periods=3))
    with pytest.raises(ValueError, match='不接受 clip_bound'):
        _market_rank_spec(clip_bound=1.0)
    with pytest.raises(ValueError, match='max_volatility_scaler'):
        _market_rank_spec(
            constraint=ConstraintConfig(max_volatility_scaler=2.0))


def test_spec_market_rank_requires_min_cs_at_least_two() -> None:
    with pytest.raises(ValueError, match='必须提供 min_cross_section_size'):
        _market_rank_spec(min_cross_section_size=None)
    with pytest.raises(ValueError, match='至少为 2'):
        _market_rank_spec(min_cross_section_size=1)


def test_spec_market_rank_forbids_max_gross() -> None:
    with pytest.raises(ValueError, match='max_gross'):
        _market_rank_spec(constraint=ConstraintConfig(max_gross=1.0))


@pytest.mark.parametrize('field, value', [
    ('realized_volatility_lookback', 0),
    ('annualization_factor', 0),
])
def test_spec_requires_positive_lookbacks(field, value) -> None:
    with pytest.raises(ValueError, match=field):
        _market_rank_spec(**{field: value})


def test_spec_rejects_wrong_field_types() -> None:
    with pytest.raises(ValueError, match='rebalance'):
        _market_rank_spec(rebalance=1)
    with pytest.raises(ValueError, match='costs'):
        _market_rank_spec(cost=1)
    with pytest.raises(ValueError, match='harvest_profile'):
        _market_rank_spec(harvest_profile='market_rank_ls')


def test_spec_is_frozen() -> None:
    spec = _market_rank_spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = 'other'  # type: ignore[misc]


# ============================================================
# 注册表
# ============================================================
def test_v1_registry_order_and_content() -> None:
    names = tuple(p.name for p in V1_DIAGNOSTIC_PORTFOLIO_PROFILES)
    assert names == (
        'ts_inv_vol_gross_one', 'ts_inv_vol_target_vol', 'market_rank_ls')
    assert set(names) == set(SUPPORTED_PROFILE_NAMES)


def test_mainstream_registry_unchanged() -> None:
    names = tuple(p.name for p in MAINSTREAM_HARVEST_PROFILES)
    assert names == (
        'market_rank_ls', 'sector_neutral_rv', 'sector_neutral_inv_vol',
        'constrained_alpha_opt', 'ts_inv_vol_target_vol',
        'sector_risk_budget_cta')


def test_public_exports_from_factors_package() -> None:
    """开发方案 §7.2 / §22：公共接口由 factors/__init__.py 稳定导出。"""
    import factors
    import factors.diagnostic_portfolio as dp

    assert factors.ts_inv_vol_gross_one is ts_inv_vol_gross_one
    assert factors.V1_DIAGNOSTIC_PORTFOLIO_PROFILES is \
        V1_DIAGNOSTIC_PORTFOLIO_PROFILES
    for name in (
        'DiagnosticSignal', 'RebalanceConfig', 'RiskConfig',
        'ConstraintConfig', 'CostConfig', 'DiagnosticPortfolioSpec',
        'DiagnosticPortfolioResult', 'DiagnosticPortfolioExperimentRunner',
    ):
        assert name in factors.__all__
        assert hasattr(factors, name)

    # 子包导出运行器与可复用纯函数。
    for name in (
        'DiagnosticPortfolioExperimentRunner', 'align_inputs',
        'build_global_timeline', 'build_rebalance_schedule',
        'MarketRankLSWeightStateMachine', 'TSInverseVolatilityWeightMachine',
    ):
        assert name in dp.__all__
        assert hasattr(dp, name)
