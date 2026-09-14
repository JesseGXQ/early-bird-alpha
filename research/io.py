"""探索阶段的轻量评估结果导出工具。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
import json
import math
from pathlib import Path
import shutil
from typing import Any, Mapping
from uuid import uuid4

import numpy as np
import pandas as pd

from factors.evaluation.grouped import (
    CSGroupedEvaluationResults,
    TSGroupedEvaluationResults,
)
from factors.evaluation.hit import HitRateEvaluationResults
from factors.evaluation.ic import (
    CSICEvaluationResults,
    TSICEvaluationResults,
)
from factors.evaluation.regression import (
    CSRegressionEvaluationResults,
    TSRegressionEvaluationResults,
)

ICResult = CSICEvaluationResults | TSICEvaluationResults
ICOutput = ICResult | tuple[ICResult, ICResult]
GroupedResult = CSGroupedEvaluationResults | TSGroupedEvaluationResults
RegressionResult = CSRegressionEvaluationResults | TSRegressionEvaluationResults
ArtifactWriter = Callable[[Path], dict[str, str]]


def _json_value(value: Any) -> Any:
    """将常见研究对象转换为严格 JSON 可编码值。"""
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, pd.Series):
        return _json_value(value.to_dict())
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is pd.NA:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(
            _json_value(value),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ) + "\n",
        encoding="utf-8",
    )


def _write_series(path: Path, series: pd.Series, name: str) -> None:
    series.rename(name).to_frame().to_parquet(path)


def _concat_product_objects(
    values: Mapping[str, pd.DataFrame | pd.Series],
    value_name: str,
) -> pd.DataFrame:
    """将分品种表格或序列合并为带 product 外层索引的长表。"""
    if not values:
        return pd.DataFrame()
    if all(isinstance(value, pd.DataFrame) for value in values.values()):
        frames = {str(key): value for key, value in values.items()}
        return pd.concat(frames, names=["product"])
    if all(isinstance(value, pd.Series) for value in values.values()):
        series = {str(key): value for key, value in values.items()}
        return pd.concat(series, names=["product"]).rename(value_name).to_frame()
    raise TypeError("同一结果字段内不能混合 DataFrame 和 Series。")


def _inference_frame(results: Mapping[str, Any]) -> pd.DataFrame:
    """将分品种推断 dataclass 转换为扁平表格。"""
    rows: dict[str, dict[str, Any]] = {}
    for product, result in results.items():
        row = asdict(result)
        interval = row.pop("confidence_interval", None)
        if interval is not None:
            row["confidence_interval_lower"] = interval[0]
            row["confidence_interval_upper"] = interval[1]
        rows[str(product)] = row
    frame = pd.DataFrame.from_dict(rows, orient="index")
    frame.index.name = "product"
    return frame


def _pooled_frame(result: TSRegressionEvaluationResults) -> pd.DataFrame:
    """将两个 pooled 回归结果及其元数据合并为一张表。"""
    rows: dict[str, dict[str, Any]] = {}
    pooled_results = {
        "product_fe": result.pooled_product_fe,
        "product_timestamp_fe": result.pooled_product_timestamp_fe,
    }
    for specification, pooled in pooled_results.items():
        row = asdict(pooled)
        regression_metadata = row.pop("regression_metadata")
        interval = row.pop("confidence_interval")
        row["confidence_interval_lower"] = interval[0]
        row["confidence_interval_upper"] = interval[1]
        row.update(regression_metadata.to_dict())
        rows[specification] = row
    frame = pd.DataFrame.from_dict(rows, orient="index")
    frame.index.name = "specification"
    return frame


def _save_bundle(
    output_dir: str | Path,
    *,
    result_type: str,
    evaluation: Mapping[str, Any],
    config: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
    write_artifacts: ArtifactWriter,
) -> Path:
    """在临时目录中写完一个结果包，再将其发布到目标路径。"""
    if not isinstance(config, Mapping):
        raise TypeError("config 必须是 Mapping。")
    if metadata is not None and not isinstance(metadata, Mapping):
        raise TypeError("metadata 必须是 Mapping 或 None。")

    target = Path(output_dir)
    if target.exists():
        raise FileExistsError(f"输出目录已存在，不允许覆盖：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.tmp-{uuid4().hex}"
    temporary.mkdir()

    try:
        artifacts = write_artifacts(temporary)
        _write_json(temporary / "config.json", dict(config))
        _write_json(
            temporary / "metadata.json",
            {
                "schema_version": 1,
                "result_type": result_type,
                "evaluation": dict(evaluation),
                **dict(evaluation),
                "context": dict(metadata or {}),
                "artifacts": {"config": "config.json", **artifacts},
            },
        )
        (temporary / "_SUCCESS").write_text("", encoding="utf-8")
        temporary.rename(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target


def save_coverage_result(
    result: Mapping[str, pd.DataFrame],
    output_dir: str | Path,
    *,
    config: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """保存 ``coverage_stats()`` 返回的覆盖率统计。"""
    required = {"overall", "by_timestamp", "by_product"}
    if set(result) != required or not all(
        isinstance(result[key], pd.DataFrame) for key in required
    ):
        raise TypeError("result 必须是 coverage_stats() 返回的三张 DataFrame。")

    def write(path: Path) -> dict[str, str]:
        artifacts = {}
        for key in ("overall", "by_timestamp", "by_product"):
            filename = f"coverage_{key}.parquet"
            result[key].to_parquet(path / filename)
            artifacts[key] = filename
        return artifacts

    return _save_bundle(
        output_dir,
        result_type="CoverageEvaluationResults",
        evaluation={"kind": "coverage"},
        config=config,
        metadata=metadata,
        write_artifacts=write,
    )


def save_ic_result(
    result: ICOutput,
    output_dir: str | Path,
    *,
    config: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """保存 ``ic_details()`` 返回的结果元组或其中单个结果。"""
    allowed_types = (CSICEvaluationResults, TSICEvaluationResults)
    if isinstance(result, allowed_types):
        results = (result,)
    elif (
        isinstance(result, tuple)
        and len(result) == 2
        and all(isinstance(item, allowed_types) for item in result)
        and {item.rank for item in result} == {False, True}
        and type(result[0]) is type(result[1])
    ):
        results = tuple(sorted(result, key=lambda item: item.rank))
    else:
        raise TypeError(
            "result 必须是单个 IC 结果，或 ic_details() 返回的 (IC, RankIC) 元组。"
        )

    first = results[0]
    mode = (
        "cross_sectional"
        if isinstance(first, CSICEvaluationResults)
        else "time_series"
    )

    def summary(item: ICResult) -> dict[str, Any]:
        payload = asdict(item.ic_summary)
        if isinstance(item, CSICEvaluationResults):
            payload.update(
                {
                    "icir": item.icir,
                    "icir_annual": item.icir_annual,
                    "newey_mean_test_results": item.newey_mean_test_results,
                    "bootstrap_confidence_intervals": (
                        item.bootstrap_confidence_intervals
                    ),
                }
            )
        return payload

    def write(path: Path) -> dict[str, str]:
        if len(results) == 1:
            item = results[0]
            metric_name = "rank_ic" if item.rank else "ic"
            _write_series(path / "ic_detail.parquet", item.ic, metric_name)
            item.group_metadata.to_parquet(path / "group_metadata.parquet")
            _write_json(path / "ic_summary.json", summary(item))
            return {
                "ic_detail": "ic_detail.parquet",
                "group_metadata": "group_metadata.parquet",
                "ic_summary": "ic_summary.json",
            }

        by_metric = {
            "rank_ic" if item.rank else "ic": item for item in results
        }
        pd.concat(
            {name: item.ic for name, item in by_metric.items()}, axis=1
        ).to_parquet(path / "ic_detail.parquet")
        for name, item in by_metric.items():
            item.group_metadata.to_parquet(
                path / f"{name}_group_metadata.parquet"
            )
        _write_json(
            path / "ic_summary.json",
            {name: summary(item) for name, item in by_metric.items()},
        )
        return {
            "ic_detail": "ic_detail.parquet",
            "ic_group_metadata": "ic_group_metadata.parquet",
            "rank_ic_group_metadata": "rank_ic_group_metadata.parquet",
            "ic_summary": "ic_summary.json",
        }

    metric = (
        "rank_ic" if first.rank else "ic"
    ) if len(results) == 1 else "ic_and_rank_ic"
    return _save_bundle(
        output_dir,
        result_type=(
            type(first).__name__
            if len(results) == 1
            else f"{type(first).__name__}Pair"
        ),
        evaluation={"kind": "ic", "mode": mode, "metric": metric},
        config=config,
        metadata=metadata,
        write_artifacts=write,
    )


def save_ts_ic_result(
    result: TSICEvaluationResults,
    output_dir: str | Path,
    *,
    config: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """兼容旧入口：保存时序 IC 或 RankIC 结果。"""
    if not isinstance(result, TSICEvaluationResults):
        raise TypeError("result 必须是 TSICEvaluationResults。")
    return save_ic_result(result, output_dir, config=config, metadata=metadata)


def save_grouped_result(
    result: GroupedResult,
    output_dir: str | Path,
    *,
    config: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """保存截面或时序 ``grouped_details()`` 的分组评价结果。"""
    if not isinstance(
        result, (CSGroupedEvaluationResults, TSGroupedEvaluationResults)
    ):
        raise TypeError(
            "result 必须是 CSGroupedEvaluationResults 或 TSGroupedEvaluationResults。"
        )

    evaluation = {
        "kind": "grouped",
        "mode": result.mode,
        "min_observations": result.min_observations,
        "nan_policy": result.nan_policy,
        "n_groups": result.n_groups,
        "grouping_direction": result.grouping_direction,
    }
    if isinstance(result, TSGroupedEvaluationResults):
        evaluation.update(
            {
                "ts_grouping_method": result.ts_grouping_method,
                "rolling_window": result.rolling_window,
            }
        )

    def write(path: Path) -> dict[str, str]:
        filenames = {
            "group_assignment": "group_assignment.parquet",
            "group_metadata": "group_metadata.parquet",
            "group_missing_reason": "group_missing_reason.parquet",
            "grouped_forward_returns": "grouped_forward_returns.parquet",
            "high_minus_low": "high_minus_low.parquet",
        }
        if isinstance(result, CSGroupedEvaluationResults):
            _write_series(
                path / filenames["group_assignment"],
                result.group_assignment,
                "group_assignment",
            )
            result.group_metadata.to_parquet(path / filenames["group_metadata"])
            _write_series(
                path / filenames["group_missing_reason"],
                result.group_missing_reason,
                "group_missing_reason",
            )
            result.grouped_forward_returns.to_parquet(
                path / filenames["grouped_forward_returns"]
            )
            _write_series(
                path / filenames["high_minus_low"],
                result.high_minus_low,
                "high_minus_low",
            )
        else:
            _concat_product_objects(
                result.group_assignment, "group_assignment"
            ).to_parquet(path / filenames["group_assignment"])
            _concat_product_objects(
                result.group_metadata, "group_metadata"
            ).to_parquet(path / filenames["group_metadata"])
            _concat_product_objects(
                result.group_missing_reason, "group_missing_reason"
            ).to_parquet(path / filenames["group_missing_reason"])
            _concat_product_objects(
                result.grouped_forward_returns, "grouped_forward_return"
            ).to_parquet(path / filenames["grouped_forward_returns"])
            pd.Series(
                result.high_minus_low, name="high_minus_low", dtype=float
            ).rename_axis("product").to_frame().to_parquet(
                path / filenames["high_minus_low"]
            )
        return filenames

    return _save_bundle(
        output_dir,
        result_type=type(result).__name__,
        evaluation=evaluation,
        config=config,
        metadata=metadata,
        write_artifacts=write,
    )


def save_regression_result(
    result: RegressionResult,
    output_dir: str | Path,
    *,
    config: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """保存截面或时序 ``regression_details()`` 的回归评价结果。"""
    if not isinstance(
        result, (CSRegressionEvaluationResults, TSRegressionEvaluationResults)
    ):
        raise TypeError(
            "result 必须是 CSRegressionEvaluationResults 或 TSRegressionEvaluationResults。"
        )
    evaluation = {
        "kind": "regression",
        "mode": result.mode,
        "label_horizon": result.label_horizon,
        "min_observations": result.min_observations,
        "nan_policy": result.nan_policy,
        "control_vars": result.control_vars,
        "fit_intercept": result.fit_intercept,
    }

    def write(path: Path) -> dict[str, str]:
        result.group_metadata.to_parquet(path / "group_metadata.parquet")
        if isinstance(result, CSRegressionEvaluationResults):
            _write_series(
                path / "factor_coefficients.parquet",
                result.factor_coefficients,
                "factor_coefficient",
            )
            result.regression_metadata.to_parquet(
                path / "regression_metadata.parquet"
            )
            _write_json(
                path / "regression_summary.json",
                {
                    "factor_beta_summary": result.factor_beta_summary,
                    "factor_beta_mean_test": result.factor_beta_mean_test,
                    "factor_beta_bootstrap_ci": result.factor_beta_bootstrap_ci,
                },
            )
            return {
                "group_metadata": "group_metadata.parquet",
                "factor_coefficients": "factor_coefficients.parquet",
                "regression_metadata": "regression_metadata.parquet",
                "regression_summary": "regression_summary.json",
            }

        result.per_product_regression_metadata.to_parquet(
            path / "per_product_regression_metadata.parquet"
        )
        _inference_frame(result.per_product_results).to_parquet(
            path / "per_product_results.parquet"
        )
        _pooled_frame(result).to_parquet(path / "pooled_results.parquet")
        _write_json(
            path / "regression_summary.json",
            {"factor_beta_summary": result.factor_beta_summary},
        )
        return {
            "group_metadata": "group_metadata.parquet",
            "per_product_regression_metadata": (
                "per_product_regression_metadata.parquet"
            ),
            "per_product_results": "per_product_results.parquet",
            "pooled_results": "pooled_results.parquet",
            "regression_summary": "regression_summary.json",
        }

    return _save_bundle(
        output_dir,
        result_type=type(result).__name__,
        evaluation=evaluation,
        config=config,
        metadata=metadata,
        write_artifacts=write,
    )


def save_hit_rate_result(
    result: HitRateEvaluationResults,
    output_dir: str | Path,
    *,
    config: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """保存 ``hit_rate_details()`` 返回的方向命中率评价结果。"""
    if not isinstance(result, HitRateEvaluationResults):
        raise TypeError("result 必须是 HitRateEvaluationResults。")

    def write(path: Path) -> dict[str, str]:
        statistics = {
            product: item.statistics
            for product, item in result.results.items()
            if item.statistics is not None
        }
        product_metadata = {
            product: item.metadata for product, item in result.results.items()
        }
        _concat_product_objects(statistics, "statistics").to_parquet(
            path / "hit_statistics.parquet"
        )
        _concat_product_objects(product_metadata, "metadata").to_parquet(
            path / "hit_metadata.parquet"
        )
        result.summary.to_parquet(path / "hit_summary.parquet")
        return {
            "hit_statistics": "hit_statistics.parquet",
            "hit_metadata": "hit_metadata.parquet",
            "hit_summary": "hit_summary.parquet",
        }

    return _save_bundle(
        output_dir,
        result_type=type(result).__name__,
        evaluation={
            "kind": "hit_rate",
            "mode": "time_series",
            "label_horizon": result.label_horizon,
            "min_observations": result.min_observations,
            "nan_policy": result.nan_policy,
            "thresholds": result.thresholds,
        },
        config=config,
        metadata=metadata,
        write_artifacts=write,
    )


def save_evaluation_result(
    result: Any,
    output_dir: str | Path,
    *,
    config: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """根据公共评价函数的返回类型选择相应导出器。"""
    common = {"config": config, "metadata": metadata}
    if isinstance(result, tuple):
        return save_ic_result(result, output_dir, **common)
    if isinstance(result, (CSICEvaluationResults, TSICEvaluationResults)):
        return save_ic_result(result, output_dir, **common)
    if isinstance(result, (CSGroupedEvaluationResults, TSGroupedEvaluationResults)):
        return save_grouped_result(result, output_dir, **common)
    if isinstance(
        result, (CSRegressionEvaluationResults, TSRegressionEvaluationResults)
    ):
        return save_regression_result(result, output_dir, **common)
    if isinstance(result, HitRateEvaluationResults):
        return save_hit_rate_result(result, output_dir, **common)
    if isinstance(result, Mapping):
        return save_coverage_result(result, output_dir, **common)
    raise TypeError(f"不支持的评价结果类型：{type(result).__name__}")
