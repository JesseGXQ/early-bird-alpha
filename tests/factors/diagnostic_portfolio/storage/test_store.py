"""DiagnosticPortfolioStore 的保存、加载、复跑与查询测试。"""

from __future__ import annotations

from dataclasses import replace
import json
import logging
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest

from factors.diagnostic_portfolio import (
    ConstraintConfig,
    CostConfig,
    DiagnosticPortfolioExperimentRunner,
    DiagnosticPortfolioSpec,
    RebalanceConfig,
    RiskConfig,
)
from factors.diagnostic_portfolio.storage import (
    ArtifactIntegrityError,
    DiagnosticPortfolioStore,
    ManifestValidationError,
    SerializationError,
    UnsupportedSchemaVersionError,
    replay_stored_run,
)
from factors.profiles import (
    market_rank_ls,
    ts_inv_vol_gross_one,
    ts_inv_vol_target_vol,
)


def _market_spec(*, sector_col: str | None = None) \
        -> DiagnosticPortfolioSpec:
    return DiagnosticPortfolioSpec(
        name='market_test',
        harvest_profile=market_rank_ls,
        rebalance=RebalanceConfig(interval=1),
        constraint=ConstraintConfig(),
        cost=CostConfig(rate=3e-5),
        min_cross_section_size=2,
        sector_attribution_col=sector_col,
        missing_return_policy='default_to_zero_and_warning',
    )


def _gross_one_spec() -> DiagnosticPortfolioSpec:
    return DiagnosticPortfolioSpec(
        name='gross_one_test',
        harvest_profile=ts_inv_vol_gross_one,
        rebalance=RebalanceConfig(interval=1),
        constraint=ConstraintConfig(),
        cost=CostConfig(rate=3e-5),
        risk=RiskConfig(
            volatility_lookback=3,
            volatility_min_periods=2,
        ),
        clip_bound=2.0,
        missing_return_policy='default_to_zero_and_warning',
    )


def _target_vol_spec() -> DiagnosticPortfolioSpec:
    return DiagnosticPortfolioSpec(
        name='target_vol_test',
        harvest_profile=ts_inv_vol_target_vol,
        rebalance=RebalanceConfig(interval=1),
        constraint=ConstraintConfig(
            max_gross=2.5,
            max_volatility_scaler=2.5,
        ),
        cost=CostConfig(rate=3e-5),
        risk=RiskConfig(
            volatility_lookback=3,
            volatility_min_periods=2,
            covariance_lookback=3,
            covariance_min_periods=2,
            target_volatility=0.15,
        ),
        clip_bound=2.0,
        missing_return_policy='default_to_zero_and_warning',
    )


def _build_case(dataset_factory, signal_factory, spec, *, auxiliary=None):
    timestamps = pd.date_range('2025-01-02', periods=8, freq='D')
    products = ('A', 'B', 'C')
    returns = np.array([
        0.010, -0.005, 0.002,
        0.004, 0.003, -0.002,
        -0.006, 0.008, 0.001,
        0.007, -0.004, 0.005,
        0.002, 0.006, -0.003,
        -0.001, 0.004, 0.007,
        0.003, -0.002, 0.006,
        0.005, 0.001, -0.004,
    ])
    dataset = dataset_factory(
        timestamps, products, returns, auxiliary=auxiliary)
    signal_values = pd.Series(
        np.tile([-1.0, 0.25, 1.0], len(timestamps)),
        index=dataset.main_panel.index,
        name='stored_signal',
    )
    signal = signal_factory(
        signal_values,
        name='stored_signal',
        factor_name='synthetic_factor',
        recipe={
            'factor': {
                'class': 'tests.SyntheticFactor',
                'params': {'lookback': 3},
            },
            'preprocess': [],
        },
    )
    result = DiagnosticPortfolioExperimentRunner(
        signal=signal, dataset=dataset, spec=spec).run()
    return dataset, signal, result


def _assert_result_tables_equal(left, right) -> None:
    excluded = {'signal_name', 'spec', 'warnings', 'metadata'}
    for field_name in left.__dataclass_fields__:
        if field_name in excluded:
            continue
        left_value = getattr(left, field_name)
        right_value = getattr(right, field_name)
        if left_value is None or right_value is None:
            assert left_value is right_value
        elif isinstance(left_value, pd.Series):
            pd.testing.assert_series_equal(left_value, right_value)
        else:
            pd.testing.assert_frame_equal(left_value, right_value)


@pytest.mark.parametrize(
    'spec_factory', [_market_spec, _gross_one_spec, _target_vol_spec],
)
def test_save_load_and_replay_all_profiles(
        tmp_path,
        dataset_factory,
        signal_factory,
        spec_factory,
) -> None:
    spec = spec_factory()
    dataset, signal, result = _build_case(
        dataset_factory, signal_factory, spec)
    store = DiagnosticPortfolioStore(tmp_path / 'artifacts')

    run_id = store.save(
        dataset, signal, spec, result,
        note='存储集成测试', tags=('synthetic', spec.harvest_profile.name),
        data_provenance={'main_panel': '2025-01-09'},
    )
    stored = store.load(run_id)

    assert stored.manifest.status == 'complete'
    assert stored.manifest.data_provenance['source_as_of'] == {
        'main_panel': '2025-01-09'}
    assert tuple(stored.experiment_panel.columns) == (
        'true_return', 'eligible', 'signal')
    assert stored.result.signal_name == result.signal_name
    assert stored.result.spec == result.spec
    assert stored.result.warnings == result.warnings
    _assert_result_tables_equal(stored.result, result)

    replayed = replay_stored_run(stored)
    _assert_result_tables_equal(replayed, result)


def test_table_valued_result_metadata_round_trip(
        tmp_path,
        dataset_factory,
        signal_factory,
) -> None:
    spec = _target_vol_spec()
    dataset, signal, result = _build_case(
        dataset_factory, signal_factory, spec)
    assert isinstance(result.metadata['covariance_estimation'], pd.DataFrame)
    store = DiagnosticPortfolioStore(tmp_path / 'artifacts')

    stored = store.load(store.save(dataset, signal, spec, result))

    restored_metadata = stored.result.metadata['covariance_estimation']
    assert isinstance(restored_metadata, pd.DataFrame)
    pd.testing.assert_frame_equal(
        restored_metadata, result.metadata['covariance_estimation'])
    assert any(
        key.startswith('output.metadata')
        for key in stored.manifest.artifacts)


def test_sector_classification_is_saved_only_when_consumed(
        tmp_path,
        dataset_factory,
        signal_factory,
) -> None:
    classification = pd.DataFrame({
        'product': ['A', 'B', 'C'],
        'sector': ['metal', 'metal', 'agri'],
        'unused': [1, 2, 3],
    })
    spec = _market_spec(sector_col='sector')
    dataset, signal, result = _build_case(
        dataset_factory, signal_factory, spec,
        auxiliary={'classification': classification},
    )
    store = DiagnosticPortfolioStore(tmp_path / 'artifacts')

    stored = store.load(store.save(dataset, signal, spec, result))

    restored = stored.auxiliary_inputs['classification']
    assert list(restored.columns) == ['product', 'sector']
    pd.testing.assert_frame_equal(
        restored, classification[['product', 'sector']])
    replay_stored_run(stored)


def test_find_scans_manifest_without_loading_parquet(
        tmp_path,
        dataset_factory,
        signal_factory,
) -> None:
    spec = _market_spec()
    dataset, signal, result = _build_case(
        dataset_factory, signal_factory, spec)
    store = DiagnosticPortfolioStore(tmp_path / 'artifacts')
    run_id = store.save(
        dataset, signal, spec, result, tags=('baseline', 'momentum'))

    matches = store.find(
        run_id=run_id,
        signal_name='stored_signal',
        factor_name='synthetic_factor',
        harvest_profile='market_rank_ls',
        products=('A', 'B', 'C'),
        start_timestamp='2025-01-02',
        end_timestamp='2025-01-09',
        tags=('baseline',),
    )
    misses = store.find(signal_name='does_not_exist')

    assert [item.run_id for item in matches] == [run_id]
    assert misses == ()


def test_optional_artifact_is_distinct_from_empty_artifact(
        tmp_path,
        dataset_factory,
        signal_factory,
) -> None:
    spec = _market_spec()
    dataset, signal, result = _build_case(
        dataset_factory, signal_factory, spec)
    store = DiagnosticPortfolioStore(tmp_path / 'artifacts')

    stored = store.load(store.save(dataset, signal, spec, result))

    risk = stored.manifest.artifacts['output.risk']
    assert risk.present is False
    assert risk.path is None
    assert stored.result.risk is None


def test_two_equal_runs_have_distinct_ids_and_equal_fingerprints(
        tmp_path,
        dataset_factory,
        signal_factory,
) -> None:
    spec = _market_spec()
    dataset, signal, result = _build_case(
        dataset_factory, signal_factory, spec)
    store = DiagnosticPortfolioStore(tmp_path / 'artifacts')

    first = store.save(dataset, signal, spec, result)
    second = store.save(dataset, signal, spec, result)
    first_manifest = store.load(first).manifest
    second_manifest = store.load(second).manifest

    assert first != second
    assert first_manifest.identity['config_fingerprint'] == \
        second_manifest.identity['config_fingerprint']
    assert first_manifest.identity['input_fingerprint'] == \
        second_manifest.identity['input_fingerprint']
    assert first_manifest.identity['experiment_fingerprint'] == \
        second_manifest.identity['experiment_fingerprint']


def test_tampered_artifact_is_rejected(
        tmp_path,
        dataset_factory,
        signal_factory,
) -> None:
    spec = _market_spec()
    dataset, signal, result = _build_case(
        dataset_factory, signal_factory, spec)
    store = DiagnosticPortfolioStore(tmp_path / 'artifacts')
    run_id = store.save(dataset, signal, spec, result)
    stored = store.load(run_id)
    nav_record = stored.manifest.artifacts['output.nav']
    nav_path = stored.path / Path(nav_record.path)
    with nav_path.open('ab') as file:
        file.write(b'tampered')

    with pytest.raises(ArtifactIntegrityError, match='文件大小不一致'):
        store.load(run_id)


def test_serialization_failure_does_not_publish_run(
        tmp_path,
        dataset_factory,
        signal_factory,
) -> None:
    spec = _market_spec()
    dataset, signal, result = _build_case(
        dataset_factory, signal_factory, spec)
    invalid = replace(result, metadata={'unsupported': object()})
    store = DiagnosticPortfolioStore(tmp_path / 'artifacts')

    with pytest.raises(SerializationError, match='不支持的类型'):
        store.save(dataset, signal, spec, invalid)

    assert list(store.runs_dir.iterdir()) == []


def test_path_traversal_run_id_is_rejected(tmp_path) -> None:
    store = DiagnosticPortfolioStore(tmp_path / 'artifacts')

    with pytest.raises(ValueError, match='非法 run_id'):
        store.load('../outside')


def test_unknown_schema_version_is_rejected(
        tmp_path,
        dataset_factory,
        signal_factory,
) -> None:
    spec = _market_spec()
    dataset, signal, result = _build_case(
        dataset_factory, signal_factory, spec)
    store = DiagnosticPortfolioStore(tmp_path / 'artifacts')
    run_id = store.save(dataset, signal, spec, result)
    manifest_path = store.runs_dir / run_id / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['schema_version'] = 999
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    with pytest.raises(UnsupportedSchemaVersionError):
        store.load(run_id)


def test_artifact_path_cannot_escape_run_directory(
        tmp_path,
        dataset_factory,
        signal_factory,
) -> None:
    spec = _market_spec()
    dataset, signal, result = _build_case(
        dataset_factory, signal_factory, spec)
    store = DiagnosticPortfolioStore(tmp_path / 'artifacts')
    run_id = store.save(dataset, signal, spec, result)
    manifest_path = store.runs_dir / run_id / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['artifacts']['output.nav']['path'] = '../outside.parquet'
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    with pytest.raises(ManifestValidationError, match='越出运行目录'):
        store.load(run_id)


def test_missing_git_provenance_lowers_reproducibility_level(
        tmp_path,
        dataset_factory,
        signal_factory,
        monkeypatch,
) -> None:
    spec = _market_spec()
    dataset, signal, result = _build_case(
        dataset_factory, signal_factory, spec)
    store = DiagnosticPortfolioStore(tmp_path / 'artifacts')
    monkeypatch.setattr(
        store,
        '_collect_code_provenance',
        lambda: ({
            'git_commit': None,
            'git_dirty': None,
            'git_diff_sha256': None,
            'untracked_source_files': [],
        }, ('Git 不可用',)),
    )

    stored = store.load(store.save(dataset, signal, spec, result))

    assert stored.manifest.reproducibility_level == 'incomplete'
    assert 'Git 不可用' in stored.manifest.storage_warnings


def _init_git_repo(path: Path, **files: str) -> None:
    """在 path 初始化一个只含一次提交的 Git 仓库，可附加额外文件。"""
    def run(*args: str) -> None:
        subprocess.run(
            ['git', *args], cwd=path, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    run('init', '-q')
    for name, content in {'placeholder.py': 'x = 1\n', **files}.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    run('add', '-A')
    run('-c', 'user.email=test@example.com', '-c', 'user.name=test',
        'commit', '-q', '-m', 'init')


def _store_warnings(caplog):
    """只取 Store 模块发出的警告文本，避免混入其他库的日志。"""
    return [record.getMessage() for record in caplog.records
            if record.name == 'factors.diagnostic_portfolio.storage.store']


_NEEDS_GIT = pytest.mark.skipif(
    shutil.which('git') is None, reason='需要 Git 命令')


@_NEEDS_GIT
def test_init_warns_when_runs_dir_inside_repo_and_not_ignored(
        tmp_path, caplog) -> None:
    repo = tmp_path / 'repo'
    repo.mkdir()
    _init_git_repo(repo)

    with caplog.at_level(logging.WARNING):
        DiagnosticPortfolioStore(root_dir=repo / 'artifacts', repo_root=repo)

    messages = _store_warnings(caplog)
    assert any('未被忽略' in message for message in messages)
    assert not any('工作区不干净' in message for message in messages)
    assert not (repo / 'artifacts').exists()


@_NEEDS_GIT
def test_init_warns_when_worktree_dirty(tmp_path, caplog) -> None:
    repo = tmp_path / 'repo'
    repo.mkdir()
    _init_git_repo(repo)
    (repo / 'untracked.py').write_text('y = 2\n', encoding='utf-8')

    with caplog.at_level(logging.WARNING):
        DiagnosticPortfolioStore(
            root_dir=tmp_path / 'storage', repo_root=repo)

    messages = _store_warnings(caplog)
    assert any('工作区不干净' in message for message in messages)
    assert not any('未被忽略' in message for message in messages)


@_NEEDS_GIT
def test_init_is_silent_for_ignored_runs_dir_and_clean_worktree(
        tmp_path, caplog) -> None:
    repo = tmp_path / 'repo'
    repo.mkdir()
    _init_git_repo(repo, **{'.gitignore': 'artifacts/\n'})

    with caplog.at_level(logging.WARNING):
        DiagnosticPortfolioStore(root_dir=repo / 'artifacts', repo_root=repo)

    assert _store_warnings(caplog) == []


@_NEEDS_GIT
def test_init_is_silent_when_repo_root_is_not_a_git_repo(
        tmp_path, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        DiagnosticPortfolioStore(
            root_dir=tmp_path / 'artifacts', repo_root=tmp_path)

    assert _store_warnings(caplog) == []
