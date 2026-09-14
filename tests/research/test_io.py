import json

import pandas as pd
import pytest

from factors.evaluation.coverage import coverage_stats
from factors.evaluation.grouped import grouped_details
from factors.evaluation.hit import hit_rate_details
from factors.evaluation.ic import ICSummary, TSICEvaluationResults
from factors.evaluation.ic import ic_details
from factors.evaluation.regression import regression_details
from research.io import (
    save_evaluation_result,
    save_grouped_result,
    save_hit_rate_result,
    save_ic_result,
    save_regression_result,
    save_ts_ic_result,
)


def _result(*, rank: bool = False) -> TSICEvaluationResults:
    index = pd.Index(["CU", "RB"], name="product")
    return TSICEvaluationResults(
        rank=rank,
        group_metadata=pd.DataFrame(
            {
                "n_eligible": [120, 118],
                "n_valid": [119, 117],
                "n_dropped": [1, 1],
                "invalid_code": pd.Series(
                    [pd.NA, pd.NA], index=index, dtype="Int64"
                ),
            },
            index=index,
        ),
        ic=pd.Series([0.12, -0.03], index=index),
        ic_summary=ICSummary(
            n_valid=2,
            mean=0.045,
            std=0.106,
            positive_ratio=0.5,
        ),
    )


@pytest.mark.parametrize(
    ("rank", "metric_name"),
    [(False, "ic"), (True, "rank_ic")],
)
def test_save_ts_ic_result_writes_structured_bundle(
    tmp_path, rank, metric_name
):
    output_dir = tmp_path / "experiment-001"
    result = _result(rank=rank)

    saved = save_ts_ic_result(
        result,
        output_dir,
        config={"factor": {"name": "momentum", "lookback": 60}},
        metadata={"experiment_id": "experiment-001"},
    )

    assert saved == output_dir
    assert (output_dir / "_SUCCESS").is_file()
    detail = pd.read_parquet(output_dir / "ic_detail.parquet")
    pd.testing.assert_series_equal(
        detail[metric_name], result.ic.rename(metric_name)
    )
    group_metadata = pd.read_parquet(output_dir / "group_metadata.parquet")
    pd.testing.assert_frame_equal(group_metadata, result.group_metadata)

    summary = json.loads(
        (output_dir / "ic_summary.json").read_text(encoding="utf-8")
    )
    assert summary["mean"] == 0.045
    manifest = json.loads(
        (output_dir / "metadata.json").read_text(encoding="utf-8")
    )
    assert manifest["metric"] == metric_name
    assert manifest["context"]["experiment_id"] == "experiment-001"


def test_save_ts_ic_result_refuses_to_overwrite(tmp_path):
    output_dir = tmp_path / "existing"
    output_dir.mkdir()

    with pytest.raises(FileExistsError, match="不允许覆盖"):
        save_ts_ic_result(_result(), output_dir, config={})


@pytest.fixture
def evaluation_panel():
    timestamps = pd.date_range("2024-01-01", periods=12, freq="D")
    products = ["CU", "RB", "AL"]
    index = pd.MultiIndex.from_product(
        [timestamps, products], names=["timestamp", "product"]
    )
    factor = []
    label = []
    for time_number in range(len(timestamps)):
        for product_number in range(len(products)):
            value = time_number * 0.2 + product_number * 0.7
            factor.append(value)
            label.append(value * 0.4 + ((time_number + product_number) % 3 - 1) * 0.1)
    return pd.DataFrame(
        {"factor": factor, "label": label, "universe": True},
        index=index,
    )


def test_save_coverage_result_from_public_function(
    tmp_path, evaluation_panel
):
    result = coverage_stats(evaluation_panel)
    output = save_evaluation_result(
        result, tmp_path / "coverage", config={"evaluation": "coverage"}
    )

    assert (output / "coverage_overall.parquet").is_file()
    assert (output / "coverage_by_timestamp.parquet").is_file()
    assert (output / "coverage_by_product.parquet").is_file()


@pytest.mark.parametrize("mode", ["cross_sectional", "time_series"])
def test_save_ic_result_from_public_function(
    tmp_path, evaluation_panel, mode
):
    result = ic_details(
        evaluation_panel,
        mode=mode,
        label_horizon=1,
        min_observations=3,
        annualization_factor=252,
        bs_reps=10,
    )

    output = save_evaluation_result(
        result,
        tmp_path / f"{mode}-ic",
        config={"mode": mode},
    )

    assert pd.read_parquet(output / "ic_detail.parquet").columns.tolist() == [
        "ic",
        "rank_ic",
    ]
    assert (output / "ic_group_metadata.parquet").is_file()
    assert (output / "rank_ic_group_metadata.parquet").is_file()


@pytest.mark.parametrize("mode", ["cross_sectional", "time_series"])
def test_save_grouped_result_from_public_function(
    tmp_path, evaluation_panel, mode
):
    result = grouped_details(
        mode,
        evaluation_panel,
        n_groups=2,
        min_observations=3,
        ts_grouping_method="full_sample",
    )
    output = save_grouped_result(
        result, tmp_path / f"{mode}-grouped", config={"mode": mode}
    )

    assert (output / "group_assignment.parquet").is_file()
    assert (output / "group_metadata.parquet").is_file()
    assert (output / "group_missing_reason.parquet").is_file()
    assert (output / "grouped_forward_returns.parquet").is_file()
    assert (output / "high_minus_low.parquet").is_file()


@pytest.mark.parametrize("mode", ["cross_sectional", "time_series"])
def test_save_regression_result_from_public_function(
    tmp_path, evaluation_panel, mode
):
    bootstrap_options = {"bs_reps": 10} if mode == "cross_sectional" else {}
    result = regression_details(
        evaluation_panel,
        mode=mode,
        label_horizon=1,
        min_observations=3,
        **bootstrap_options,
    )
    output = save_regression_result(
        result, tmp_path / f"{mode}-regression", config={"mode": mode}
    )

    assert (output / "group_metadata.parquet").is_file()
    assert (output / "regression_summary.json").is_file()
    if mode == "cross_sectional":
        assert (output / "factor_coefficients.parquet").is_file()
        assert (output / "regression_metadata.parquet").is_file()
    else:
        assert (output / "per_product_results.parquet").is_file()
        assert (output / "pooled_results.parquet").is_file()


def test_save_hit_rate_result_from_public_function(
    tmp_path, evaluation_panel
):
    result = hit_rate_details(
        evaluation_panel,
        label_horizon=1,
        min_observations=3,
        nan_policy="drop",
        thresholds=(0.0, 0.5),
    )
    output = save_hit_rate_result(
        result, tmp_path / "hit-rate", config={"evaluation": "hit_rate"}
    )

    statistics = pd.read_parquet(output / "hit_statistics.parquet")
    product_metadata = pd.read_parquet(output / "hit_metadata.parquet")
    assert statistics.index.names[0] == "product"
    assert product_metadata.index.names[0] == "product"
    assert (output / "hit_summary.parquet").is_file()
