"""时序正交化 P1-3 及其衍生问题的研究语义回归测试。

覆盖样本变化后的基底重选、因子优先级、零列与常数列、计量单位不变性、
截距及拟合失败分支，并验证公共接口和推进变换能够交付研究结果。

所有数据均为合成数据。期望值来自明确的线性关系、手算投影或正交性质，
不要求组件采用特定的分解算法、缩放方法或原因码内部布局。
已知问题保留为正常失败，不使用 xfail，以便后续修改直接以此为验收基准。

内部数值用例通过 _core_residuals 隔离当前私有接口；若后续拟合状态改为
保存 gamma、尺度和保留列位置，只需更新该适配函数，保留语义断言。
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
import pytest

from factors.preprocess.orthogonalize import (
    TSGramSchmidtOrthogonalizer,
    _seq_lstsq_beta_safe,
    _seq_res_with_fitted_betas,
    walk_forward_orthogonalize,
)

ATOL = 1e-10
TOL = 1e-8


def _core_residuals(
    train: np.ndarray,
    transform: np.ndarray | None = None,
    *,
    fit_intercept: bool = False,
    sample_policy: Literal['common', 'own'] = 'own',
) -> np.ndarray:
    """仅适配内部拟合和变换入口，避免公共接口错误遮蔽数值回归问题。"""
    train = np.asarray(train, dtype=float)
    transform = train if transform is None else np.asarray(transform, dtype=float)
    fit_oks, keeps, scales, gammas, codes, ratios = _seq_lstsq_beta_safe(
        train, tol=TOL, intercept=fit_intercept, policy=sample_policy,
    )
    return _seq_res_with_fitted_betas(
        F = transform, 
        fit_oks = fit_oks,
        keeps = keeps,
        scales = scales,
        gammas = gammas, 
        intercept=fit_intercept
    )


def _factor_series(
    matrix: np.ndarray,
    *,
    start: str,
) -> tuple[tuple[pd.Series, ...], pd.Series]:
    """将单品种合成矩阵转成公共接口接受的因子序列和准入掩码。"""
    matrix = np.asarray(matrix, dtype=float)
    index = pd.MultiIndex.from_product(
        [pd.date_range(start, periods=len(matrix), freq='D'), ['A']],
        names=['timestamp', 'product'],
    )
    factors = tuple(
        pd.Series(matrix[:, j], index=index, name=f'f{j}')
        for j in range(matrix.shape[1])
    )
    return factors, pd.Series(True, index=index, name='eligible')


@pytest.mark.parametrize('fit_intercept', [False, True], ids=['no_intercept', 'intercept'])
@pytest.mark.parametrize('sample_policy', ['own', 'common'])
def test_clean_full_rank_residuals_are_orthogonal(fit_intercept, sample_policy):
    """完整样本的基准路径满足顺序正交性质，不应因边界修复而退化。"""
    train = np.random.default_rng(2026).normal(size=(40, 4))
    residuals = _core_residuals(
        train, fit_intercept=fit_intercept, sample_policy=sample_policy,
    )

    assert residuals.shape == train.shape
    assert np.isfinite(residuals).all()
    gram = residuals.T @ residuals
    np.testing.assert_allclose(gram - np.diag(np.diag(gram)), 0.0, atol=ATOL)
    expected_first = train[:, 0] - train[:, 0].mean() if fit_intercept else train[:, 0]
    np.testing.assert_allclose(residuals[:, 0], expected_first, atol=ATOL)
    if fit_intercept:
        np.testing.assert_allclose(residuals.mean(axis=0), 0.0, atol=ATOL)


@pytest.mark.parametrize('sample_policy', ['own', 'common'])
def test_rank_change_on_current_samples_preserves_the_independent_later_column(sample_policy):
    """原始 P1-3：前两列在目标有效样本上共线，不能误删独立的第三列。"""
    train = np.array([
        [1, 1, 0, 1],
        [2, 2, 1, 0],
        [3, 3, 0, 1],
        [4, 4, 1, 0],
        [5, 8, 2, np.nan],
        [6, 1, 3, np.nan],
    ], dtype=float)
    # 手算当前有效样本上的目标投影：预测值 = 第一列 / 3 - 第三列。
    transformed = np.array([[10, 100, 2, 5], [10, -999, 2, 5]], dtype=float)

    in_sample = _core_residuals(train, sample_policy=sample_policy)
    out_of_sample = _core_residuals(train, transformed, sample_policy=sample_policy)

    np.testing.assert_allclose(in_sample[:4, -1], [2 / 3, 1 / 3, 0, -1 / 3], atol=ATOL)
    assert np.isnan(in_sample[4:, -1]).all()
    np.testing.assert_allclose(out_of_sample[:, -1], [11 / 3, 11 / 3], atol=ATOL)


@pytest.mark.parametrize('fit_intercept', [False, True], ids=['no_intercept', 'intercept'])
def test_multiple_redundant_predictors_do_not_displace_an_independent_predictor(fit_intercept):
    """同次回归有多个冗余前序列时，仍须保留后面的独立方向。"""
    x = np.arange(1., 9.)
    z = np.array([1., 0., -1., 2., 0., 1., -2., 1.])
    train = np.column_stack([x, 2 * x, 3 * x, z, 4 * x - z])
    transformed = np.array([[10, 99, -55, 2, 7]], dtype=float)

    residuals = _core_residuals(train, transformed, fit_intercept=fit_intercept)

    np.testing.assert_allclose(residuals[:, -1], [-31.], atol=ATOL)


def test_affine_predictors_are_independent_without_an_intercept():
    """无截距时 x 与 x+1 都需保留，后续完全重复因子的残差必须为零。"""
    x = np.arange(1., 6.)
    train = np.column_stack([x, x + 1, x + 1])

    residuals = _core_residuals(train)

    slope = np.dot(x, x + 1) / np.dot(x, x)
    np.testing.assert_allclose(residuals[:, 1], x + 1 - slope * x, atol=ATOL)
    np.testing.assert_allclose(residuals[:, 2], 0.0, atol=ATOL)


def test_explicit_intercept_participates_in_priority_selection():
    """截距、x、x+1 整体共线；样本外须沿用截距和高优先级 x。"""
    x = np.arange(1., 6.)
    train = np.column_stack([x, x + 1, x])
    transformed = np.array([[10, 30, 10]], dtype=float)

    residuals = _core_residuals(train, transformed, fit_intercept=True)

    # 第三个目标始终重复第一列；低优先级第二列的样本外变化不能影响它。
    np.testing.assert_allclose(residuals[:, 2], 0.0, atol=ATOL)


def test_late_constant_factor_does_not_override_earlier_affine_basis():
    """无截距时 x 和 x+1 已提供常数方向，后来的常数因子应服从顺序。"""
    x = np.arange(1., 7.)
    train = np.column_stack([x, x + 1, np.ones_like(x), x])
    transformed = np.array([[10, 11, 99, 10], [10, 11, -99, 10]], dtype=float)

    residuals = _core_residuals(train, transformed)

    np.testing.assert_allclose(residuals[:, -1], 0.0, atol=ATOL)


@pytest.mark.parametrize('new_zero_predictor', [0., 42., np.nan], ids=['zero', 'changed', 'missing'])
def test_empty_basis_is_a_success_and_returns_the_target(new_zero_predictor):
    """全零解释变量不提供暴露；被剔除列的样本外变化或缺失不应污染目标。"""
    x = np.arange(1., 6.)
    train = np.column_stack([np.zeros_like(x), x])
    transformed = np.array([[new_zero_predictor, 10], [new_zero_predictor, 20]], dtype=float)

    residuals = _core_residuals(train, transformed)

    np.testing.assert_allclose(residuals[:, 1], [10., 20.], atol=ATOL)


def test_zero_column_before_nonzero_constants_does_not_remove_the_constant_direction():
    """零列不能挤掉非零常数方向；两个相同常数信号不得同时保留。"""
    train = np.column_stack([np.zeros(5), np.ones(5), np.ones(5)])

    residuals = _core_residuals(train)

    np.testing.assert_allclose(residuals[:, 1], 1.0, atol=ATOL)
    np.testing.assert_allclose(residuals[:, 2], 0.0, atol=ATOL)


def test_nonzero_constant_predictors_preserve_the_first_constant_direction():
    """正负非零常数互相冗余，应由第一个非零常数承担投影。"""
    x = np.arange(1., 6.)
    train = np.column_stack([np.full(5, 2.), np.full(5, -3.), x])
    transformed = np.array([[2, 999, 10], [4, -999, 20]], dtype=float)

    residuals = _core_residuals(train, transformed)

    # 训练目标均值为 3，第一常数列系数为 3/2，第二常数列应被剔除。
    np.testing.assert_allclose(residuals[:, -1], [7., 14.], atol=ATOL)


@pytest.mark.parametrize(
    'magnitude', [1., 1e-20, -1e-20, 1e20, -1e20],
    ids=['unit', 'tiny', 'negative_tiny', 'huge', 'negative_huge'],
)
def test_predictor_units_do_not_change_explained_information(magnitude):
    """两个独立解释方向只改变计量单位时，可完全解释的目标仍须残差为零。"""
    x = np.arange(1., 6.)
    z = np.array([1., 0., 1., 0., 1.])
    train = np.column_stack([x, magnitude * z, 2 * x - 3 * z])

    residuals = _core_residuals(train)

    np.testing.assert_allclose(residuals[:, -1], 0.0, atol=ATOL)


def test_tiny_scaled_duplicate_does_not_create_spurious_residual_information():
    """保留讨论中的精确反例：[x, 1e-20*z, z] 的最后一列没有新增信息。"""
    x = np.arange(1., 6.)
    z = np.array([1., 0., 1., 0., 1.])
    train = np.column_stack([x, 1e-20 * z, z])

    residuals = _core_residuals(train)

    np.testing.assert_allclose(residuals[:, -1], 0.0, atol=ATOL)


def test_out_of_sample_projection_reuses_the_training_mapping():
    """变换数据量级发生变化时，仍须使用训练阶段拟合出的线性映射。"""
    x = np.arange(1., 7.)
    z = np.array([1., 0., 1., 0., 1., 0.])
    train = np.column_stack([x, .1 * z, 2 * x - 3 * z])
    transformed = np.array([[100, 20, 7], [-20, .3, 5]], dtype=float)

    residuals = _core_residuals(train, transformed)

    # 原单位下的两个系数为 2、-30；不能在新窗口重算尺度后沿用旧系数。
    np.testing.assert_allclose(residuals[:, -1], [407., 54.], atol=ATOL)


@pytest.mark.parametrize('fit_intercept', [False, True], ids=['no_intercept', 'intercept'])
def test_failed_target_fit_is_not_confused_with_a_successful_empty_basis(fit_intercept):
    """目标训练值全缺失时应输出缺失，同时保留其他目标的有效拟合结果。"""
    x = np.arange(1., 6.)
    train = np.column_stack([x, np.full_like(x, np.nan)])
    transformed = np.array([[10, 20], [20, 40]], dtype=float)

    residuals = _core_residuals(train, transformed, fit_intercept=fit_intercept)

    expected_first = transformed[:, 0] - x.mean() if fit_intercept else transformed[:, 0]
    np.testing.assert_allclose(residuals[:, 0], expected_first, atol=ATOL)
    assert np.isnan(residuals[:, 1]).all(), '拟合失败不能返回看似正常的原始目标信号'


def test_intercept_with_insufficient_input_samples_returns_a_failed_fit():
    """进入总样本不足分支时，包含截距的原因码也须能正常交付失败结果。"""
    train = np.array([[np.nan, np.nan]])
    transformed = np.array([[10., 20.]])

    residuals = _core_residuals(train, transformed, fit_intercept=True)

    assert residuals.shape == transformed.shape
    assert np.isnan(residuals).all()


@pytest.mark.parametrize('fit_intercept', [False, True], ids=['no_intercept', 'intercept'])
def test_public_fit_transform_delivers_values_and_metadata(fit_intercept):
    """公共接口须支持当前原因码结构，并在交付 metadata 时保持结果可用。"""
    x = np.arange(1., 6.)
    train = np.column_stack([x, 2 * x + 1])
    transformed = np.array([[6, 16], [7, 20]], dtype=float)
    train_factors, eligible = _factor_series(train, start='2026-01-01')
    new_factors, _ = _factor_series(transformed, start='2026-02-01')
    orth = TSGramSchmidtOrthogonalizer(fit_intercept=fit_intercept)

    orth.fit(*train_factors, eligible=eligible)
    result = orth.transform(*new_factors, eligible=None)

    assert len(result.values) == 2
    for j, values in enumerate(result.values):
        pd.testing.assert_index_equal(values.index, new_factors[j].index)
        assert values.name == new_factors[j].name
    expected_first = transformed[:, 0] - x.mean() if fit_intercept else transformed[:, 0]
    if fit_intercept:
        expected_second = transformed[:, 1] - (2 * transformed[:, 0] + 1)
    else:
        slope = np.dot(x, 2 * x + 1) / np.dot(x, x)
        expected_second = transformed[:, 1] - slope * transformed[:, 0]
    np.testing.assert_allclose(result.values[0].to_numpy(), expected_first, atol=ATOL)
    np.testing.assert_allclose(result.values[1].to_numpy(), expected_second, atol=ATOL)
    assert {'gammas', 'fit_residual_std_ratios', 'fit_reason_codes'} <= result.metadata.keys()


def test_public_redundant_predictor_diagnostics_do_not_prevent_transform():
    """存在剔除原因时，公共接口仍须完成警告、诊断信息及残差的交付。"""
    x = np.arange(1., 6.)
    train_factors, eligible = _factor_series(
        np.column_stack([x, 2 * x, 3 * x]), start='2026-01-01',
    )
    new_factors, _ = _factor_series(
        np.array([[10., 999., 30.]]), start='2026-02-01',
    )
    orth = TSGramSchmidtOrthogonalizer()

    orth.fit(*train_factors, eligible=eligible)
    result = orth.transform(*new_factors, eligible=None)

    np.testing.assert_allclose(result.values[1].to_numpy(), [979.], atol=ATOL)
    np.testing.assert_allclose(result.values[2].to_numpy(), 0.0, atol=ATOL)
    assert result.metadata['fit_reason_codes'] is not None
    assert all(isinstance(message, str) for message in result.warnings)


def test_walk_forward_delivers_all_test_folds_with_intercepts():
    """推进拟合须贯通截距、重复拟合和原因码交付，并拼接全部测试窗口。"""
    x = np.arange(1., 10.)
    factors, eligible = _factor_series(
        np.column_stack([x, 2 * x + 1]), start='2026-01-01',
    )

    result = walk_forward_orthogonalize(
        *factors,
        eligible=eligible,
        ts_orthogonalizer=TSGramSchmidtOrthogonalizer(fit_intercept=True),
        fit_size=3,
        transform_size=2,
    )

    assert len(result.values) == 2
    pd.testing.assert_index_equal(result.values[0].index, factors[0].iloc[3:].index)
    pd.testing.assert_index_equal(result.values[1].index, factors[1].iloc[3:].index)
    np.testing.assert_allclose(result.values[0].to_numpy(), [2., 3., 2., 3., 2., 3.], atol=ATOL)
    np.testing.assert_allclose(result.values[1].to_numpy(), 0.0, atol=ATOL)
    assert set(result.metadata) == {'fold0', 'fold1', 'fold2'}
