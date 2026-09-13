"""时序方向命中率评估测试。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.evaluation.hit import HIT_RATE_INVALID_CODES, hit_rate_details


def _single_product_panel(factor: list[float], label: list[float]) -> pd.DataFrame:
    index = pd.MultiIndex.from_product(
        [pd.date_range("2026-01-01", periods=len(factor)), ["A"]],
        names=["timestamp", "product"],
    )
    return pd.DataFrame(
        {"factor": factor, "label": label, "universe": True},
        index=index,
    )


def test_zero_labels_affect_activation_but_not_direction_statistics() -> None:
    data = _single_product_panel(
        factor=[2.0, 2.0, -2.0, -2.0, 0.0, 0.0],
        label=[1.0, -1.0, -1.0, 0.0, 1.0, -1.0],
    )
    original = data.copy(deep=True)

    result = hit_rate_details(
        data,
        label_horizon=1,
        min_observations=3,
        nan_policy="drop",
        thresholds=(1.0,),
    )

    statistics = result.results["A"].statistics
    assert statistics is not None
    row = statistics.iloc[0]
    assert row["activation_rate"] == pytest.approx(4 / 6)
    assert row["long_opportunity_rate"] == pytest.approx(2 / 6)
    assert row["short_opportunity_rate"] == pytest.approx(2 / 6)
    assert row["long_share_among_active"] == pytest.approx(1 / 2)
    assert row["up_base_rate_global"] == pytest.approx(2 / 5)
    assert row["down_base_rate_global"] == pytest.approx(3 / 5)
    assert row["up_base_rate_active"] == pytest.approx(1 / 3)
    assert row["down_base_rate_active"] == pytest.approx(2 / 3)
    assert row["hit_rate"] == pytest.approx(2 / 3)
    assert row["long_hit_rate"] == pytest.approx(1 / 2)
    assert row["short_hit_rate"] == pytest.approx(1.0)
    assert row["chance_hit_rate"] == pytest.approx(4 / 9)
    assert row["excess_hit_rate"] == pytest.approx(2 / 9)
    assert row["kappa"] == pytest.approx(0.4)

    metadata = result.results["A"].metadata.iloc[0]
    assert metadata["n_direction"] == 5
    assert metadata["n_active"] == 4
    assert metadata["n_hit"] == 3
    assert metadata["invalid_code"] is None
    pd.testing.assert_frame_equal(data, original)


def test_insufficient_hit_sample_uses_registered_invalid_code() -> None:
    data = _single_product_panel(
        factor=[2.0, 2.0, -2.0, -2.0, 0.0, 0.0],
        label=[1.0, -1.0, -1.0, 0.0, 1.0, -1.0],
    )

    result = hit_rate_details(
        data,
        label_horizon=1,
        min_observations=3,
        nan_policy="drop",
        thresholds=(1.0, 3.0),
    )

    statistics = result.results["A"].statistics
    assert statistics is not None
    assert statistics["threshold"].tolist() == [1.0]
    metadata = result.results["A"].metadata.set_index("threshold")
    assert metadata.loc[1.0, "invalid_code"] is None
    assert metadata.loc[3.0, "invalid_code"] == 4
    assert metadata.loc[3.0, "n_active"] == 0
    assert metadata.loc[3.0, "n_hit"] == 0
    assert HIT_RATE_INVALID_CODES[4] == "insufficient_hit_evaluation_obs"


def test_strict_policy_and_one_sided_signal_are_supported() -> None:
    data = _single_product_panel(
        factor=[1.0, 1.0, 1.0],
        label=[1.0, -1.0, 1.0],
    )

    result = hit_rate_details(
        data,
        label_horizon=1,
        min_observations=2,
        nan_policy="strict",
        thresholds=None,
    )

    statistics = result.results["A"].statistics
    assert statistics is not None
    row = statistics.iloc[0]
    assert row["threshold"] == 0.0
    assert row["hit_rate"] == pytest.approx(2 / 3)
    assert row["long_hit_rate"] == pytest.approx(2 / 3)
    assert np.isnan(row["short_hit_rate"])
    assert row["excess_hit_rate"] == pytest.approx(0.0)
    assert row["kappa"] == pytest.approx(0.0)
    assert result.results["A"].metadata.iloc[0]["n_dropped"] is None

