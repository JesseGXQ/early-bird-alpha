"""单因子回归评估测试。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from factors.evaluation.regression import (
    FactorBetaMeanTestResults,
    REGRESSION_INVALID_CODES,
    regression_details,
)


def _panel(n_times: int = 6, products: tuple[str, ...] = ("A", "B", "C", "D")) -> pd.DataFrame:
    index = pd.MultiIndex.from_product(
        [pd.date_range("2024-01-01", periods=n_times), products],
        names=["timestamp", "product"],
    )
    return pd.DataFrame(index=index)


def test_cross_sectional_recovers_factor_beta_and_does_not_modify_input() -> None:
    data = _panel(4)
    data["factor"] = np.tile([1.0, 2.0, 3.0, 4.0], 4)
    data["label"] = 1.0 + 2.0 * data["factor"]
    data["universe"] = True
    original = data.copy(deep=True)

    result = regression_details(
        data, "cross_sectional", label_horizon=2,
        min_observations=3, bs_reps=20,
    )

    np.testing.assert_allclose(result.factor_coefficients, 2.0)
    assert result.factor_coefficients.name == "factor_beta"
    assert result.factor_beta_summary.n_valid == 4
    assert isinstance(result.factor_beta_mean_test, FactorBetaMeanTestResults)
    assert result.factor_beta_mean_test.requested_max_lags == 1
    assert result.factor_beta_mean_test.effective_max_lags == 1
    pd.testing.assert_frame_equal(data, original)


def test_control_variable_restores_conditional_factor_beta() -> None:
    data = _panel(3, ("A", "B", "C", "D", "E"))
    factor = np.tile(np.arange(1.0, 6.0), 3)
    control = np.tile(np.array([1.0, 1.5, 4.0, 7.0, 11.0]), 3)
    data["factor"] = factor
    data["control"] = control
    data["label"] = 2 * factor + 3 * control
    data["universe"] = True

    uncontrolled = regression_details(
        data, "cross_sectional", 1, 4, bs_reps=10)
    controlled = regression_details(
        data, "cross_sectional", 1, 4,
        control_columns=("control",), bs_reps=10,
    )

    assert not np.isclose(uncontrolled.factor_coefficients.iloc[0], 2.0)
    np.testing.assert_allclose(controlled.factor_coefficients, 2.0)
    assert controlled.control_vars == ("control",)


def test_cross_sectional_hac_matches_statsmodels() -> None:
    data = _panel(5)
    slopes = np.array([1.0, 2.0, 1.5, 3.0, -0.5])
    factor = np.tile(np.arange(4.0), 5)
    data["factor"] = factor
    data["label"] = np.repeat(slopes, 4) * factor + 1
    data["universe"] = True
    result = regression_details(
        data, "cross_sectional", 3, 3,
        confidence_level=0.90, bs_reps=10)

    expected = sm.OLS(slopes, np.ones((5, 1))).fit(
        cov_type="HAC",
        cov_kwds={"maxlags": 2, "use_correction": True},
        use_t=True,
    )
    test = result.factor_beta_mean_test
    assert test is not None
    assert test.estimate == pytest.approx(expected.params[0])
    assert test.standard_error == pytest.approx(expected.bse[0])
    assert test.t_stat == pytest.approx(expected.tvalues[0])
    assert test.p_value == pytest.approx(expected.pvalues[0])
    assert test.inference_dof == expected.df_resid
    assert test.confidence_level == 0.90
    assert result.factor_beta_bootstrap_ci is not None
    assert result.factor_beta_bootstrap_ci.confidence_level == 0.90
    np.testing.assert_allclose(
        test.confidence_interval, expected.conf_int(alpha=0.10)[0])


def test_strict_is_local_and_ignores_missing_outside_universe() -> None:
    data = _panel(3)
    data["factor"] = np.tile(np.arange(4.0), 3)
    data["label"] = 2 * data["factor"] + 1
    data["universe"] = True
    data.loc[(pd.Timestamp("2024-01-02"), "A"), "factor"] = np.nan
    data.loc[(pd.Timestamp("2024-01-03"), "D"), ["factor", "label"]] = np.nan
    data.loc[(pd.Timestamp("2024-01-03"), "D"), "universe"] = False

    result = regression_details(
        data, "cross_sectional", 1, 3, nan_policy="strict", bs_reps=10)

    assert result.regression_metadata.iloc[1]["invalid_code"] == 1
    assert result.regression_metadata.iloc[2]["invalid_code"] is None
    assert np.isnan(result.factor_coefficients.iloc[1])
    assert result.factor_coefficients.iloc[2] == pytest.approx(2.0)


def test_time_series_per_product_and_pooled_fixed_effects() -> None:
    data = _panel(12, ("A", "B", "C"))
    timestamp = data.index.get_level_values("timestamp")
    product = data.index.get_level_values("product")
    t = (timestamp - timestamp.min()).days.to_numpy(dtype=float)
    product_number = pd.Series(product).map({"A": 1.0, "B": 2.0, "C": 4.0}).to_numpy()
    factor = t * product_number + np.sin(t + product_number)
    error = 0.02 * np.cos(2 * t + product_number)
    data["factor"] = factor
    data["label"] = 2 * factor + pd.Series(product).map(
        {"A": 10.0, "B": -5.0, "C": 3.0}).to_numpy() + error
    data["universe"] = True

    result = regression_details(
        data, "time_series", 2, 5, confidence_level=0.90)
    default_confidence_result = regression_details(
        data, "time_series", 2, 5)

    assert set(result.per_product_results) == {"A", "B", "C"}
    assert all(
        item.confidence_level == 0.90
        for item in result.per_product_results.values())
    assert result.factor_beta_summary.n_valid == 3
    assert result.pooled_product_fe.estimate == pytest.approx(2.0, abs=0.03)
    assert result.pooled_product_timestamp_fe.estimate == pytest.approx(2.0, abs=0.03)
    assert result.pooled_product_fe.n_observations == len(data)
    assert result.pooled_product_timestamp_fe.n_observations == len(data)
    assert result.pooled_product_timestamp_fe.regression_metadata["inference_dof"] == 2
    interval_90 = result.pooled_product_timestamp_fe.confidence_interval
    interval_95 = default_confidence_result.pooled_product_timestamp_fe.confidence_interval
    assert interval_90[1] - interval_90[0] < interval_95[1] - interval_95[0]


def test_timestamp_fixed_effect_absorbs_timestamp_only_factor() -> None:
    data = _panel(5, ("A", "B", "C"))
    times = data.index.get_level_values("timestamp")
    factor = (times - times.min()).days.to_numpy(dtype=float)
    data["factor"] = factor
    data["label"] = 2 * factor + np.tile([1.0, 2.0, 4.0], 5)
    data["universe"] = True

    result = regression_details(data, "time_series", 1, 3)

    assert result.pooled_product_timestamp_fe.regression_metadata["invalid_code"] == 4
    assert np.isnan(result.pooled_product_timestamp_fe.estimate)


def test_pooled_requires_both_cluster_dimensions() -> None:
    data = _panel(6, ("A",))
    data["factor"] = np.arange(6.0)
    data["label"] = 2 * data["factor"] + np.sin(data["factor"])
    data["universe"] = True

    result = regression_details(data, "time_series", 1, 3)

    assert result.pooled_product_fe.regression_metadata["invalid_code"] == 5
    assert result.pooled_product_timestamp_fe.regression_metadata["invalid_code"] == 5


def test_unbalanced_two_way_absorption_matches_dummy_regression() -> None:
    data = _panel(6, ("A", "B", "C"))
    timestamp = data.index.get_level_values("timestamp")
    product = data.index.get_level_values("product")
    t = (timestamp - timestamp.min()).days.to_numpy(dtype=float)
    p = pd.Series(product).map({"A": 1.0, "B": 2.0, "C": 4.0}).to_numpy()
    data["factor"] = t * p + np.cos(t + p)
    data["label"] = 1.7 * data["factor"] + 2 * p - 0.4 * t + np.sin(2 * t + p)
    data["universe"] = True
    data.loc[(pd.Timestamp("2024-01-01"), "B"), "universe"] = False
    data.loc[(pd.Timestamp("2024-01-06"), "C"), "universe"] = False

    result = regression_details(data, "time_series", 1, 3)
    sample = data.loc[data["universe"]]
    product_dummies = pd.get_dummies(
        sample.index.get_level_values("product"), drop_first=True, dtype=float)
    timestamp_dummies = pd.get_dummies(
        sample.index.get_level_values("timestamp"), drop_first=True, dtype=float)
    design = np.column_stack([
        sample["factor"].to_numpy(), np.ones(len(sample)),
        product_dummies.to_numpy(), timestamp_dummies.to_numpy(),
    ])
    expected = sm.OLS(sample["label"].to_numpy(), design).fit().params[0]

    assert result.pooled_product_timestamp_fe.estimate == pytest.approx(expected)


def test_time_series_strict_excludes_failed_product_from_both_pooled_models() -> None:
    data = _panel(6, ("A", "B", "C"))
    timestamp = data.index.get_level_values("timestamp")
    product = data.index.get_level_values("product")
    t = (timestamp - timestamp.min()).days.to_numpy(dtype=float)
    p = pd.Series(product).map({"A": 1.0, "B": 2.0, "C": 3.0}).to_numpy()
    data["factor"] = t * p + np.sin(t + p)
    data["label"] = 2 * data["factor"] + p + np.cos(t + p)
    data["universe"] = True
    data.loc[(pd.Timestamp("2024-01-03"), "B"), "factor"] = np.nan

    result = regression_details(
        data, "time_series", 1, 3, nan_policy="strict")

    assert result.per_product_regression_metadata.loc["B", "invalid_code"] == 1
    assert result.pooled_product_fe.n_observations == 12
    assert result.pooled_product_timestamp_fe.n_observations == 12


def test_validation_rejects_bad_contract() -> None:
    data = _panel(2)
    data["factor"] = 1.0
    data["label"] = 2.0
    data["universe"] = True
    with pytest.raises(ValueError, match="保留列"):
        regression_details(data, "cross_sectional", 1, 2,
                           control_columns=("factor",))
    with pytest.raises(ValueError, match="正整数"):
        regression_details(data, "cross_sectional", True, 2)
    with pytest.raises(ValueError, match="不使用 bootstrap"):
        regression_details(data, "time_series", 1, 2, bs_reps=10)
    with pytest.raises(ValueError, match="confidence_level"):
        regression_details(
            data, "cross_sectional", 1, 2, confidence_level=1.0)
    bad = data.copy()
    bad.iloc[0, bad.columns.get_loc("factor")] = np.inf
    with pytest.raises(ValueError, match="正负无穷"):
        regression_details(bad, "cross_sectional", 1, 2)


def test_invalid_code_mapping_is_stable() -> None:
    assert REGRESSION_INVALID_CODES[4] == "rank_deficient_design"
    assert REGRESSION_INVALID_CODES[7] == "invalid_cluster_covariance"
