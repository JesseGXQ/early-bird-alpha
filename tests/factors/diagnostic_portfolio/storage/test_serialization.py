"""诊断组合实验持久化序列化测试。"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from data.config import (
    AdjustmentConfig,
    EnvironmentConfig,
    RolloverConfig,
    SamplingConfig,
)
from factors.diagnostic_portfolio.storage.models import SerializationError
from factors.diagnostic_portfolio.storage.serialization import (
    canonical_json_dumps,
    dataset_spec_from_dict,
    dataset_spec_to_dict,
    portfolio_spec_from_dict,
    portfolio_spec_to_dict,
    read_pandas_artifact,
    semantic_configs,
    sha256_text,
    to_jsonable,
    write_pandas_artifact,
)
from factors.diagnostic_portfolio.models import (
    ConstraintConfig,
    CostConfig,
    DiagnosticPortfolioSpec,
    RebalanceConfig,
)
from factors.profiles import market_rank_ls
from factors.models import DataRequirement, DatasetSpec, UniverseSpec


def _dataset_spec(environment: EnvironmentConfig) -> DatasetSpec:
    return DatasetSpec(
        facade_config=(
            RolloverConfig(),
            AdjustmentConfig(),
            SamplingConfig(),
            environment,
        ),
        products=('RB', 'MA'),
        data_requirements=(
            DataRequirement(
                source='warehouse_receipt',
                columns=('receipt',),
                request_params={'aggregate': True},
            ),
        ),
        start_date=date(2020, 1, 1),
        end_date=date(2024, 12, 31),
        view='realized',
        universe=UniverseSpec(min_average_volume=10_000),
    )


def test_dataset_spec_round_trip() -> None:
    spec = _dataset_spec(EnvironmentConfig())

    restored = dataset_spec_from_dict(dataset_spec_to_dict(spec))

    assert restored == spec


@pytest.mark.parametrize(
    'anchor',
    [date(2024, 1, 2), datetime(2024, 1, 2, 9, 30),
     pd.Timestamp('2024-01-02 09:30:00')],
)
def test_portfolio_spec_preserves_anchor_type(anchor) -> None:
    spec = DiagnosticPortfolioSpec(
        name='anchor_test',
        harvest_profile=market_rank_ls,
        rebalance=RebalanceConfig(interval=5, anchor=anchor),
        constraint=ConstraintConfig(),
        cost=CostConfig(rate=0.0),
        min_cross_section_size=2,
    )

    restored = portfolio_spec_from_dict(portfolio_spec_to_dict(spec))

    assert restored == spec
    assert type(restored.rebalance.anchor) is type(anchor)


def test_environment_path_does_not_change_semantic_fingerprint() -> None:
    left = _dataset_spec(EnvironmentConfig(daily_snapshot_dir='left'))
    right = _dataset_spec(EnvironmentConfig(daily_snapshot_dir='right'))
    left_configs = {'dataset_spec': dataset_spec_to_dict(left)}
    right_configs = {'dataset_spec': dataset_spec_to_dict(right)}

    left_hash = sha256_text(canonical_json_dumps(
        semantic_configs(left_configs)))
    right_hash = sha256_text(canonical_json_dumps(
        semantic_configs(right_configs)))

    assert left_hash == right_hash


def test_canonical_json_ignores_mapping_insertion_order() -> None:
    left = {'b': np.int64(2), 'a': {'y': 2, 'x': 1}}
    right = {'a': {'x': 1, 'y': 2}, 'b': 2}

    assert canonical_json_dumps(left) == canonical_json_dumps(right)


@pytest.mark.parametrize('value', [np.nan, np.inf, -np.inf])
def test_non_finite_json_float_is_rejected(value: float) -> None:
    with pytest.raises(SerializationError, match='非有限浮点值'):
        to_jsonable({'value': value})


def test_unknown_json_object_is_rejected() -> None:
    with pytest.raises(SerializationError, match='不支持的类型'):
        to_jsonable({'value': object()})


def test_series_parquet_round_trip_preserves_multiindex(tmp_path) -> None:
    index = pd.MultiIndex.from_product(
        [pd.date_range('2025-01-01', periods=2), ['A', 'B']],
        names=['timestamp', 'product'],
    )
    series = pd.Series(
        [1.0, np.nan, 3.0, 4.0], index=index, name='signal')
    path = tmp_path / 'signal.parquet'

    record = write_pandas_artifact(
        series, path=path, relative_path='signal.parquet')
    restored = read_pandas_artifact(path, record)

    assert isinstance(restored, pd.Series)
    pd.testing.assert_series_equal(restored, series)


def test_empty_dataframe_round_trip_is_not_absent(tmp_path) -> None:
    frame = pd.DataFrame(
        {'value': pd.Series(dtype='float64')},
        index=pd.DatetimeIndex([], name='timestamp'),
    )
    path = tmp_path / 'empty.parquet'

    record = write_pandas_artifact(
        frame, path=path, relative_path='empty.parquet')
    restored = read_pandas_artifact(path, record)

    assert record.present is True
    assert record.row_count == 0
    assert isinstance(restored, pd.DataFrame)
    pd.testing.assert_frame_equal(restored, frame)
