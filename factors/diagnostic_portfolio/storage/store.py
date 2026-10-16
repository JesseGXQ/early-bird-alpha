"""诊断组合实验的文件式持久化仓库。"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import secrets
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import fields
from datetime import date, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from factors.diagnostic_portfolio.experiment import (
    DiagnosticPortfolioExperimentRunner,
    align_inputs,
)
from factors.diagnostic_portfolio.models import (
    DiagnosticPortfolioResult,
    DiagnosticPortfolioSpec,
    DiagnosticSignal,
)
from factors.models import ResearchDataset
from factors.universe import Universe

from .models import (
    ArtifactIntegrityError,
    ArtifactRecord,
    ManifestValidationError,
    RunAlreadyExistsError,
    RunManifest,
    RunNotFoundError,
    RunSummary,
    SerializationError,
    StoredDiagnosticPortfolioRun,
    UnsupportedSchemaVersionError,
)
from .serialization import (
    absent_artifact,
    canonical_json_dumps,
    data_quality_from_dict,
    data_quality_to_dict,
    dataset_spec_from_dict,
    dataset_spec_to_dict,
    portfolio_spec_from_dict,
    portfolio_spec_to_dict,
    pretty_json_dumps,
    read_pandas_artifact,
    semantic_configs,
    sha256_bytes,
    sha256_file,
    sha256_text,
    signal_to_dict,
    to_jsonable,
    write_pandas_artifact,
)


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_ROOT_DIR = Path('research/artifacts/diagnostic_portfolio')
_SAFE_RUN_ID = re.compile(r'^[A-Za-z0-9_-]+$')
_RESULT_TABLE_FIELDS = tuple(
    item.name for item in fields(DiagnosticPortfolioResult)
    if item.name not in {'signal_name', 'spec', 'warnings', 'metadata'}
)
_OPTIONAL_RESULT_FIELDS = {'risk', 'sector_attribution', 'status'}


class DiagnosticPortfolioStore:
    """诊断组合实验的文件式持久化仓库。

    Store 使用“一个运行一个不可变目录”的方式保存实际输入、完整结果和
    manifest，并提供带完整性校验的加载、轻量查询与复跑所需的数据入口。
    计算仍由 :class:`DiagnosticPortfolioExperimentRunner` 负责；本类不会在
    保存或加载过程中重新计算组合结果。

    Parameters
    ----------
    root_dir : str or os.PathLike, optional
        存储模块根目录。每次运行实际保存在 ``root_dir / 'runs' / run_id``；
        默认使用 ``research/artifacts/diagnostic_portfolio``。
    repo_root : str or os.PathLike or None, optional
        用于获取 Git commit、工作区状态和 diff 指纹的仓库目录。为 None 时
        使用当前工作目录。

    Attributes
    ----------
    root_dir : pathlib.Path
        解析为绝对路径后的存储模块根目录。
    runs_dir : pathlib.Path
        最终运行目录的父目录，即 ``root_dir / 'runs'``。
    repo_root : pathlib.Path
        获取代码来源信息时使用的仓库目录。

    Notes
    -----
    最终运行目录一经发布即视为不可变。本类不提供覆盖已有运行的选项，
    也不把持久化隐式集成进实验 Runner。

    """

    def __init__(
            self,
            root_dir: str | os.PathLike[str] = DEFAULT_ROOT_DIR,
            *,
            repo_root: str | os.PathLike[str] | None = None,
    ) -> None:
        """初始化诊断组合实验存储仓库。

        Parameters
        ----------
        root_dir : str or os.PathLike, optional
            存储模块根目录。目录可以尚不存在，首次保存时会自动创建。
        repo_root : str or os.PathLike or None, optional
            Git 仓库目录。为 None 时使用当前工作目录。

        Returns
        -------
        None

        Notes
        -----
        实例化时会检查两项会降低复现等级的环境问题，并在命中时输出
        ``logging`` 警告：运行目录落在 ``repo_root`` 内却未被 Git 忽略（此时
        Store 自己的保存产物会被判定为未提交改动），以及 ``repo_root`` 当前
        工作区不干净。两项检查都只提示，不阻止后续保存；它们判断的是实例化
        时点的状态，保存时仍以实际采集的 Git 来源为准。

        """
        self.root_dir = Path(root_dir).resolve()
        self.runs_dir = self.root_dir / 'runs'
        self.repo_root = Path(repo_root or Path.cwd()).resolve()
        self._warn_environment()

    def save(
            self,
            dataset: ResearchDataset,
            signal: DiagnosticSignal,
            spec: DiagnosticPortfolioSpec,
            result: DiagnosticPortfolioResult,
            *,
            note: str | None = None,
            tags: Sequence[str] = (),
            data_provenance: Mapping[str, Any] | None = None,
    ) -> str:
        """原子保存一次已经成功完成的组合实验。

        保存内容包括规范化配置、组合层实际输入、必要附属输入、完整结果、
        结果 metadata、文件校验和、Git 来源和最小运行环境。所有文件先写入
        临时目录并回读验证，最后才发布为不可变的最终运行目录。

        Parameters
        ----------
        dataset : ResearchDataset
            本次实验使用的研究数据集。Store 只持久化组合实验实际消费的
            ``true_return``、``eligible`` 和必要附属输入，不复制完整主面板。
        signal : DiagnosticSignal
            本次实验的信号描述与实际信号值。
        spec : DiagnosticPortfolioSpec
            本次诊断组合实验的完整配置。
        result : DiagnosticPortfolioResult
            使用同一 ``dataset``、``signal`` 和 ``spec`` 已经计算完成的结果。
        note : str or None, optional
            研究者备注，仅用于说明实验目的，不参与配置或实验指纹。
        tags : sequence of str, optional
            用于后续查询的非空字符串标签，不参与配置或实验指纹。
        data_provenance : mapping of str to Any or None, optional
            调用方能够确认的数据源截止信息，例如
            ``{'main_panel': '2026-10-09'}``。它用于审计，不替代实际输入快照。

        Returns
        -------
        str
            本次保存事件的唯一 ``run_id``。最终目录位于
            ``runs_dir / run_id``。

        Raises
        ------
        TypeError
            输入对象、note 或 tags 的类型不符合要求。
        ValueError
            ``result`` 与 ``signal`` 或 ``spec`` 不匹配，或标签内容非法。
        SerializationError
            配置、recipe、metadata 或 pandas artifact 无法安全序列化。
        ArtifactIntegrityError
            临时 artifact 写入后无法通过回读或完整性校验。
        RunAlreadyExistsError
            极低概率下生成的最终运行目录已经存在。
        OSError
            创建目录、写文件或发布最终目录失败。

        Notes
        -----
        工作区不干净不会阻止保存，但 manifest 的复现等级会降为
        ``input_replayable_code_dirty``。保存失败时不会发布最终运行目录。

        """
        self._validate_save_inputs(dataset, signal, spec, result, note, tags)
        experiment_panel = align_inputs(signal, dataset)
        created_at = datetime.now().astimezone()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = Path(tempfile.mkdtemp(
            prefix='.tmp-', dir=self.runs_dir))
        try:
            artifacts: dict[str, ArtifactRecord] = {}
            panel_relative = 'inputs/experiment_panel.parquet'
            artifacts['input.experiment_panel'] = write_pandas_artifact(
                experiment_panel,
                path=temporary_path / panel_relative,
                relative_path=panel_relative,
            )
            self._write_classification_if_used(
                temporary_path, dataset, spec, artifacts)
            self._write_result_artifacts(
                temporary_path, result, artifacts)

            encoded_result_metadata = self._encode_metadata(
                result.metadata,
                temporary_path=temporary_path,
                artifacts=artifacts,
                artifact_key='output.metadata',
                relative_stem='outputs/metadata/root',
            )

            configs = {
                'dataset_spec': dataset_spec_to_dict(dataset.spec),
                'signal': signal_to_dict(signal),
                'diagnostic_portfolio_spec': portfolio_spec_to_dict(spec),
            }
            config_fingerprint = sha256_text(canonical_json_dumps(
                semantic_configs(configs)))
            input_fingerprint = self._input_fingerprint(artifacts)
            code_provenance, code_warnings = self._collect_code_provenance()
            reproducibility_level = self._reproducibility_level(
                code_provenance)
            experiment_fingerprint = sha256_text(canonical_json_dumps({
                'schema_version': SCHEMA_VERSION,
                'config_fingerprint': config_fingerprint,
                'input_fingerprint': input_fingerprint,
                'git_commit': code_provenance.get('git_commit'),
                'git_dirty': code_provenance.get('git_dirty'),
                'git_diff_sha256': code_provenance.get('git_diff_sha256'),
            }))
            run_id = self._new_run_id(created_at, experiment_fingerprint)
            resolved_input = self._resolved_input(
                experiment_panel, dataset)
            source_as_of = to_jsonable(
                dict(data_provenance or {}), path='$.data_provenance')
            data_info = {
                'captured_at': created_at.isoformat(),
                'logical_data_cutoff': resolved_input['end_timestamp'],
                'source_as_of': source_as_of,
            }
            storage_warnings = list(code_warnings)
            if not signal.recipe:
                storage_warnings.append(
                    '信号配方为空，上游信号不可仅靠配置重建。')
            manifest = RunManifest(
                schema_version=SCHEMA_VERSION,
                run_id=run_id,
                created_at=created_at.isoformat(),
                status='complete',
                reproducibility_level=reproducibility_level,
                identity={
                    'config_fingerprint': config_fingerprint,
                    'input_fingerprint': input_fingerprint,
                    'experiment_fingerprint': experiment_fingerprint,
                },
                configs=configs,
                resolved_input=resolved_input,
                code_provenance=code_provenance,
                data_provenance=data_info,
                runtime=self._runtime_info(),
                artifacts=artifacts,
                warnings=tuple(result.warnings),
                storage_warnings=tuple(storage_warnings),
                result_metadata=encoded_result_metadata,
                user_metadata={
                    'note': note,
                    'tags': list(tags),
                },
            )
            manifest_path = temporary_path / 'manifest.json'
            manifest_path.write_text(
                pretty_json_dumps(manifest.to_dict()), encoding='utf-8')
            self._verify_temporary_run(temporary_path, manifest)

            final_path = self.runs_dir / run_id
            if final_path.exists():
                raise RunAlreadyExistsError(
                    f'运行目录已经存在：{final_path}')
            temporary_path.rename(final_path)
            return run_id
        except Exception:
            if temporary_path.exists():
                shutil.rmtree(temporary_path)
            raise

    def load(
            self,
            run_id: str,
            *,
            verify: bool = True,
    ) -> StoredDiagnosticPortfolioRun:
        """加载一次完整的已保存运行。

        Parameters
        ----------
        run_id : str
            ``save()`` 返回的运行标识，只允许字母、数字、下划线和连字符。
        verify : bool, optional
            是否在读取前重新验证每个 artifact 的文件大小和 SHA-256。
            默认为 True。无论该值如何，manifest schema 和表格结构都会校验。

        Returns
        -------
        StoredDiagnosticPortfolioRun
            包含 manifest、恢复后的 ``DiagnosticPortfolioResult``、实际输入面板、
            必要附属输入和运行目录路径的结构化容器。

        Raises
        ------
        ValueError
            ``run_id`` 格式非法。
        RunNotFoundError
            运行目录或 manifest 不存在。
        UnsupportedSchemaVersionError
            manifest 的存储 schema 高于或不同于当前加载器支持的版本。
        ManifestValidationError
            manifest 缺少必需字段、引用越界路径或与运行目录不一致。
        ArtifactIntegrityError
            artifact 缺失、校验和不一致或表格结构无法按 manifest 恢复。

        Notes
        -----
        日常研究和审计应保留 ``verify=True``。``verify=False`` 只适合已经由
        其他可信流程验证过、且对加载延迟敏感的场景。

        """
        run_path = self._run_path(run_id)
        manifest = self._read_manifest(run_path / 'manifest.json')
        if manifest.run_id != run_id:
            raise ManifestValidationError(
                f'manifest.run_id={manifest.run_id!r} 与目录名 {run_id!r} 不一致')
        self._validate_manifest(manifest)
        loaded: dict[str, pd.DataFrame | pd.Series | None] = {}
        for key, record in manifest.artifacts.items():
            if not record.present:
                loaded[key] = None
                continue
            artifact_path = self._artifact_path(run_path, record)
            if verify:
                self._verify_artifact_file(artifact_path, record)
            try:
                loaded[key] = read_pandas_artifact(artifact_path, record)
            except SerializationError as exc:
                raise ArtifactIntegrityError(str(exc)) from exc

        panel = loaded['input.experiment_panel']
        if not isinstance(panel, pd.DataFrame):
            raise ManifestValidationError(
                'input.experiment_panel 必须恢复为 DataFrame')
        spec = portfolio_spec_from_dict(
            manifest.configs['diagnostic_portfolio_spec'])
        result_values: dict[str, Any] = {}
        for field_name in _RESULT_TABLE_FIELDS:
            result_values[field_name] = loaded[f'output.{field_name}']
        metadata = self._decode_metadata(
            manifest.result_metadata, loaded=loaded)
        if not isinstance(metadata, Mapping):
            raise ManifestValidationError(
                'DiagnosticPortfolioResult.metadata 必须恢复为 Mapping')
        result = DiagnosticPortfolioResult(
            signal_name=manifest.configs['signal']['name'],
            spec=spec,
            **result_values,
            warnings=tuple(manifest.warnings),
            metadata=metadata,
        )
        auxiliary: dict[str, pd.DataFrame | pd.Series] = {}
        classification = loaded.get('input.classification')
        if classification is not None:
            auxiliary['classification'] = classification
        return StoredDiagnosticPortfolioRun(
            path=run_path,
            manifest=manifest,
            result=result,
            experiment_panel=panel,
            auxiliary_inputs=auxiliary,
        )

    def find(
            self,
            *,
            run_id: str | None = None,
            signal_name: str | None = None,
            factor_name: str | None = None,
            harvest_profile: str | None = None,
            products: Sequence[str] | None = None,
            start_timestamp: str | datetime | date | None = None,
            end_timestamp: str | datetime | date | None = None,
            created_after: str | datetime | None = None,
            created_before: str | datetime | None = None,
            git_commit: str | None = None,
            config_fingerprint: str | None = None,
            experiment_fingerprint: str | None = None,
            tags: Sequence[str] | None = None,
    ) -> tuple[RunSummary, ...]:
        """扫描 manifest 并按常用字段查询历史运行。

        查询只读取 ``runs/*/manifest.json``，不会加载输入和结果 Parquet。
        所有非 None 条件同时生效。

        Parameters
        ----------
        run_id : str or None, optional
            精确匹配某次保存事件的运行标识。
        signal_name : str or None, optional
            精确匹配 ``DiagnosticSignal.name``。
        factor_name : str or None, optional
            精确匹配 ``DiagnosticSignal.factor_name``。
        harvest_profile : str or None, optional
            精确匹配 ``HarvestProfile.name``。
        products : sequence of str or None, optional
            精确匹配实际输入的完整品种集合。比较前会按品种代码排序，调用方
            传入顺序不影响结果。
        start_timestamp : str, datetime, date or None, optional
            精确匹配实际输入的首个时间戳。
        end_timestamp : str, datetime, date or None, optional
            精确匹配实际输入的最后一个时间戳。
        created_after : str, datetime or None, optional
            只返回创建时间大于等于该时点的运行。无时区值按运行记录的时区解释。
        created_before : str, datetime or None, optional
            只返回创建时间小于等于该时点的运行。无时区值按运行记录的时区解释。
        git_commit : str or None, optional
            精确匹配完整 Git commit。
        config_fingerprint : str or None, optional
            精确匹配规范化语义配置指纹。
        experiment_fingerprint : str or None, optional
            精确匹配配置、实际输入和代码来源共同形成的实验指纹。
        tags : sequence of str or None, optional
            标签包含条件。候选运行必须包含这里给出的全部标签，但可以有额外标签。

        Returns
        -------
        tuple of RunSummary
            满足条件的轻量运行摘要，按 ``created_at`` 和 ``run_id`` 稳定升序排列。
            存储根目录不存在或没有匹配项时返回空元组。

        Raises
        ------
        ValueError
            时间过滤条件无法由 pandas 解析。

        Notes
        -----
        临时目录、非完整运行、损坏 manifest 和当前加载器不支持的 schema 会被
        查询跳过。需要读取结果或验证 artifact 时，应继续调用 :meth:`load`。

        """
        if not self.runs_dir.exists():
            return ()
        after = pd.Timestamp(created_after) if created_after is not None else None
        before = (
            pd.Timestamp(created_before) if created_before is not None else None)
        expected_products = (
            None if products is None
            else tuple(sorted(str(item) for item in products)))
        expected_start = (
            None if start_timestamp is None
            else pd.Timestamp(start_timestamp))
        expected_end = (
            None if end_timestamp is None
            else pd.Timestamp(end_timestamp))
        expected_tags = None if tags is None else set(tags)
        summaries: list[RunSummary] = []
        for manifest_path in self.runs_dir.glob('*/manifest.json'):
            if manifest_path.parent.name.startswith('.tmp-'):
                continue
            try:
                manifest = self._read_manifest(manifest_path)
                self._validate_manifest(manifest)
                summary = self._summary(manifest)
            except (OSError, json.JSONDecodeError,
                    ManifestValidationError,
                    UnsupportedSchemaVersionError):
                continue
            created = pd.Timestamp(summary.created_at)
            if run_id is not None and summary.run_id != run_id:
                continue
            if signal_name is not None and summary.signal_name != signal_name:
                continue
            if factor_name is not None and summary.factor_name != factor_name:
                continue
            if harvest_profile is not None and \
                    summary.harvest_profile != harvest_profile:
                continue
            if expected_products is not None and \
                    summary.products != expected_products:
                continue
            if expected_start is not None and \
                    pd.Timestamp(summary.start_timestamp) != expected_start:
                continue
            if expected_end is not None and \
                    pd.Timestamp(summary.end_timestamp) != expected_end:
                continue
            comparable_after = self._align_query_timestamp(after, created)
            comparable_before = self._align_query_timestamp(before, created)
            if comparable_after is not None and created < comparable_after:
                continue
            if comparable_before is not None and created > comparable_before:
                continue
            if git_commit is not None and summary.git_commit != git_commit:
                continue
            if config_fingerprint is not None and \
                    summary.config_fingerprint != config_fingerprint:
                continue
            if experiment_fingerprint is not None and \
                    summary.experiment_fingerprint != experiment_fingerprint:
                continue
            if expected_tags is not None and not expected_tags.issubset(
                    set(summary.tags)):
                continue
            summaries.append(summary)
        return tuple(sorted(
            summaries, key=lambda item: (item.created_at, item.run_id)))

    def _validate_save_inputs(
            self,
            dataset: ResearchDataset,
            signal: DiagnosticSignal,
            spec: DiagnosticPortfolioSpec,
            result: DiagnosticPortfolioResult,
            note: str | None,
            tags: Sequence[str],
    ) -> None:
        if not isinstance(dataset, ResearchDataset):
            raise TypeError('dataset 必须是 ResearchDataset')
        if not isinstance(signal, DiagnosticSignal):
            raise TypeError('signal 必须是 DiagnosticSignal')
        if not isinstance(spec, DiagnosticPortfolioSpec):
            raise TypeError('spec 必须是 DiagnosticPortfolioSpec')
        if not isinstance(result, DiagnosticPortfolioResult):
            raise TypeError('result 必须是 DiagnosticPortfolioResult')
        if result.signal_name != signal.name:
            raise ValueError(
                'result.signal_name 与 signal.name 不一致：'
                f'{result.signal_name!r} != {signal.name!r}')
        if result.spec != spec:
            raise ValueError('result.spec 与传入 spec 不一致')
        if note is not None and not isinstance(note, str):
            raise TypeError('note 必须是 str 或 None')
        if isinstance(tags, (str, bytes)) or \
                any(not isinstance(item, str) or not item for item in tags):
            raise ValueError('tags 必须是非空字符串序列')

    def _write_classification_if_used(
            self,
            temporary_path: Path,
            dataset: ResearchDataset,
            spec: DiagnosticPortfolioSpec,
            artifacts: dict[str, ArtifactRecord],
    ) -> None:
        key = 'input.classification'
        auxiliary = dataset.auxiliary
        if spec.sector_attribution_col is None or \
                not isinstance(auxiliary, Mapping) or \
                'classification' not in auxiliary:
            artifacts[key] = absent_artifact(kind='dataframe')
            return
        classification = auxiliary['classification']
        if not isinstance(classification, pd.DataFrame):
            raise SerializationError(
                '实际使用的 classification 必须是 DataFrame')
        required = ['product', spec.sector_attribution_col]
        missing = set(required) - set(classification.columns)
        if missing:
            raise SerializationError(
                f'classification 缺少字段：{sorted(missing)}')
        consumed = classification.loc[:, required].copy()
        relative = 'inputs/auxiliary/classification.parquet'
        artifacts[key] = write_pandas_artifact(
            consumed,
            path=temporary_path / relative,
            relative_path=relative,
        )

    def _write_result_artifacts(
            self,
            temporary_path: Path,
            result: DiagnosticPortfolioResult,
            artifacts: dict[str, ArtifactRecord],
    ) -> None:
        for field_name in _RESULT_TABLE_FIELDS:
            value = getattr(result, field_name)
            key = f'output.{field_name}'
            if value is None:
                if field_name not in _OPTIONAL_RESULT_FIELDS:
                    raise SerializationError(
                        f'DiagnosticPortfolioResult.{field_name} 不是可选字段')
                artifacts[key] = absent_artifact(kind='dataframe')
                continue
            if not isinstance(value, (pd.DataFrame, pd.Series)):
                raise SerializationError(
                    f'DiagnosticPortfolioResult.{field_name} 必须是 pandas '
                    f'对象或允许的 None，收到 {type(value).__name__}')
            relative = f'outputs/{field_name}.parquet'
            artifacts[key] = write_pandas_artifact(
                value,
                path=temporary_path / relative,
                relative_path=relative,
            )

    def _encode_metadata(
            self,
            value: Any,
            *,
            temporary_path: Path,
            artifacts: dict[str, ArtifactRecord],
            artifact_key: str,
            relative_stem: str,
    ) -> Any:
        """递归编码 metadata，表格值拆成独立 artifact。"""
        if isinstance(value, (pd.DataFrame, pd.Series)):
            key = artifact_key
            suffix = 'parquet'
            relative = f'{relative_stem}.{suffix}'
            counter = 1
            while key in artifacts:
                counter += 1
                key = f'{artifact_key}.{counter}'
                relative = f'{relative_stem}_{counter}.{suffix}'
            artifacts[key] = write_pandas_artifact(
                value,
                path=temporary_path / relative,
                relative_path=relative,
            )
            return {'__storage_type__': 'artifact', 'key': key}
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, np.generic):
            return self._encode_metadata(
                value.item(), temporary_path=temporary_path,
                artifacts=artifacts, artifact_key=artifact_key,
                relative_stem=relative_stem)
        if isinstance(value, float):
            return to_jsonable(value, path='$.result_metadata')
        if isinstance(value, pd.Timestamp):
            if pd.isna(value):
                raise SerializationError(
                    'DiagnosticPortfolioResult.metadata 包含 NaT')
            return {
                '__storage_type__': 'timestamp',
                'value': value.isoformat(),
            }
        if isinstance(value, datetime):
            return {
                '__storage_type__': 'datetime',
                'value': value.isoformat(),
            }
        if isinstance(value, date):
            return {
                '__storage_type__': 'date',
                'value': value.isoformat(),
            }
        if isinstance(value, Path):
            return {
                '__storage_type__': 'path',
                'value': str(value),
            }
        if isinstance(value, tuple):
            return {
                '__storage_type__': 'tuple',
                'items': [
                    self._encode_metadata(
                        item,
                        temporary_path=temporary_path,
                        artifacts=artifacts,
                        artifact_key=f'{artifact_key}.{index}',
                        relative_stem=f'{relative_stem}_{index}',
                    )
                    for index, item in enumerate(value)
                ],
            }
        if isinstance(value, list):
            return [
                self._encode_metadata(
                    item,
                    temporary_path=temporary_path,
                    artifacts=artifacts,
                    artifact_key=f'{artifact_key}.{index}',
                    relative_stem=f'{relative_stem}_{index}',
                )
                for index, item in enumerate(value)
            ]
        if isinstance(value, Mapping):
            items: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise SerializationError(
                        'DiagnosticPortfolioResult.metadata 的键必须是字符串')
                safe_key = re.sub(r'[^A-Za-z0-9_-]+', '_', key).strip('_') \
                    or 'item'
                items[key] = self._encode_metadata(
                    item,
                    temporary_path=temporary_path,
                    artifacts=artifacts,
                    artifact_key=f'{artifact_key}.{safe_key}',
                    relative_stem=f'{relative_stem}_{safe_key}',
                )
            return {'__storage_type__': 'mapping', 'items': items}
        raise SerializationError(
            'DiagnosticPortfolioResult.metadata 包含不支持的类型 '
            f'{type(value).__name__}')

    def _decode_metadata(
            self,
            value: Any,
            *,
            loaded: Mapping[str, pd.DataFrame | pd.Series | None],
    ) -> Any:
        if isinstance(value, list):
            return [self._decode_metadata(item, loaded=loaded) for item in value]
        if not isinstance(value, Mapping):
            return value
        storage_type = value.get('__storage_type__')
        if storage_type == 'artifact':
            key = value.get('key')
            if not isinstance(key, str) or key not in loaded or \
                    loaded[key] is None:
                raise ManifestValidationError(
                    f'result_metadata 引用了不存在的 artifact：{key!r}')
            return loaded[key]
        if storage_type == 'timestamp':
            return pd.Timestamp(value['value'])
        if storage_type == 'datetime':
            return datetime.fromisoformat(value['value'])
        if storage_type == 'date':
            return date.fromisoformat(value['value'])
        if storage_type == 'path':
            return Path(value['value'])
        if storage_type == 'tuple':
            return tuple(
                self._decode_metadata(item, loaded=loaded)
                for item in value.get('items', ()))
        if storage_type == 'mapping':
            return {
                key: self._decode_metadata(item, loaded=loaded)
                for key, item in value.get('items', {}).items()
            }
        return {
            key: self._decode_metadata(item, loaded=loaded)
            for key, item in value.items()
        }

    def _resolved_input(
            self,
            panel: pd.DataFrame,
            dataset: ResearchDataset,
    ) -> dict[str, Any]:
        timestamps = pd.DatetimeIndex(
            panel.index.get_level_values('timestamp').unique()).sort_values()
        products = tuple(sorted(
            str(item) for item in
            panel.index.get_level_values('product').unique()))
        return to_jsonable({
            'products': products,
            'start_timestamp': (
                None if timestamps.empty else timestamps[0].isoformat()),
            'end_timestamp': (
                None if timestamps.empty else timestamps[-1].isoformat()),
            'row_count': len(panel),
            'timestamp_count': len(timestamps),
            'product_count': len(products),
            'signal_non_null_count': int(panel['signal'].notna().sum()),
            'eligible_true_count': int(panel['eligible'].sum()),
            'index_names': list(panel.index.names),
            'columns': list(panel.columns),
            'dataset_quality': data_quality_to_dict(dataset.quality),
        })

    def _input_fingerprint(
            self,
            artifacts: Mapping[str, ArtifactRecord],
    ) -> str:
        inputs = {
            key: record.sha256
            for key, record in sorted(artifacts.items())
            if key.startswith('input.') and record.present
        }
        return sha256_text(canonical_json_dumps(inputs))

    def _collect_code_provenance(
            self,
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        warnings: list[str] = []
        try:
            commit = self._git_text('rev-parse', 'HEAD').strip()
            status = self._git_text(
                'status', '--porcelain=v1', '--untracked-files=all')
            dirty = bool(status.strip())
            diff = subprocess.run(
                ['git', 'diff', 'HEAD', '--binary'],
                cwd=self.repo_root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout
            diff_sha = sha256_bytes(diff) if dirty else None
            untracked = []
            for line in status.splitlines():
                if line.startswith('?? '):
                    relative = line[3:].strip().strip('"')
                    if relative.lower().endswith('.py'):
                        untracked.append(relative)
            return {
                'git_commit': commit,
                'git_dirty': dirty,
                'git_diff_sha256': diff_sha,
                'untracked_source_files': sorted(untracked),
            }, tuple(warnings)
        except (OSError, subprocess.CalledProcessError) as exc:
            warnings.append(f'无法获取完整 Git 来源信息：{exc}')
            return {
                'git_commit': None,
                'git_dirty': None,
                'git_diff_sha256': None,
                'untracked_source_files': [],
            }, tuple(warnings)

    def _git_text(self, *args: str) -> str:
        completed = subprocess.run(
            ['git', *args],
            cwd=self.repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return completed.stdout.decode('utf-8', errors='replace')

    def _warn_environment(self) -> None:
        """实例化时提示会降低复现等级的环境问题。"""
        relative = self._inside_repo_path(self.runs_dir)
        if relative is not None and self._is_ignored(relative) is False:
            logger.warning(
                f'运行目录 {self.runs_dir} 位于 Git 仓库 {self.repo_root} 内'
                f'且未被忽略（仓库内相对路径 {relative.as_posix()}）。保存实验'
                '时，Store 自己的保存产物会被判定为未提交改动，使 manifest 的 '
                'git_dirty=True、复现等级降为 input_replayable_code_dirty。'
                '建议把存储根移出仓库，或将运行目录加入 .gitignore。')
        if self._worktree_dirty() is True:
            logger.warning(
                f'Git 仓库 {self.repo_root} 当前工作区不干净，保存实验时 '
                'manifest 的 git_dirty=True、复现等级将为 '
                'input_replayable_code_dirty。建议提交代码后再运行正式实验。')

    def _inside_repo_path(self, path: Path) -> Path | None:
        """返回 path 在仓库内的相对路径，不在 repo_root 内时返回 None。"""
        try:
            return path.relative_to(self.repo_root)
        except ValueError:
            return None

    def _is_ignored(self, relative: Path) -> bool | None:
        """判断仓库内相对路径是否被 Git 忽略，无法判定时返回 None。"""
        try:
            completed = subprocess.run(
                ['git', 'check-ignore', '-q', '--', relative.as_posix()],
                cwd=self.repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError:
            return None
        if completed.returncode == 0:
            return True
        if completed.returncode == 1:
            return False
        return None

    def _worktree_dirty(self) -> bool | None:
        """判断工作区是否存在未提交改动，无法判定时返回 None。"""
        try:
            status = self._git_text(
                'status', '--porcelain=v1', '--untracked-files=all')
        except (OSError, subprocess.SubprocessError):
            return None
        return bool(status.strip())

    def _reproducibility_level(
            self, code_provenance: Mapping[str, Any]) -> str:
        if not code_provenance.get('git_commit'):
            return 'incomplete'
        if code_provenance.get('git_dirty'):
            return 'input_replayable_code_dirty'
        return 'strict'

    def _runtime_info(self) -> dict[str, Any]:
        packages: dict[str, str | None] = {}
        for name in ('numpy', 'pandas', 'pyarrow'):
            try:
                packages[name] = version(name)
            except PackageNotFoundError:
                packages[name] = None
        return {
            'python': platform.python_version(),
            'platform': platform.platform(),
            'packages': packages,
        }

    def _new_run_id(
            self,
            created_at: datetime,
            experiment_fingerprint: str,
    ) -> str:
        prefix = created_at.strftime('%Y%m%dT%H%M%S_%f')
        short_hash = experiment_fingerprint.split(':', 1)[-1][:8]
        candidate = f'{prefix}_{short_hash}'
        if not (self.runs_dir / candidate).exists():
            return candidate
        return f'{candidate}_{secrets.token_hex(2)}'

    def _verify_temporary_run(
            self,
            temporary_path: Path,
            manifest: RunManifest,
    ) -> None:
        for record in manifest.artifacts.values():
            if not record.present:
                continue
            artifact_path = self._artifact_path(temporary_path, record)
            self._verify_artifact_file(artifact_path, record)
            try:
                read_pandas_artifact(artifact_path, record)
            except SerializationError as exc:
                raise ArtifactIntegrityError(str(exc)) from exc
        parsed = self._read_manifest(temporary_path / 'manifest.json')
        if parsed.run_id != manifest.run_id:
            raise ManifestValidationError('临时 manifest 回读失败')

    def _run_path(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not _SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError(f'非法 run_id：{run_id!r}')
        run_path = self.runs_dir / run_id
        if not run_path.is_dir():
            raise RunNotFoundError(f'运行不存在：{run_id}')
        return run_path

    def _read_manifest(self, path: Path) -> RunManifest:
        if not path.is_file():
            raise RunNotFoundError(f'manifest 不存在：{path}')
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise ManifestValidationError(
                f'manifest 不是合法 JSON：{path}') from exc
        if not isinstance(data, Mapping):
            raise ManifestValidationError('manifest 顶层必须是对象')
        return RunManifest.from_dict(data)

    def _validate_manifest(self, manifest: RunManifest) -> None:
        if manifest.schema_version != SCHEMA_VERSION:
            raise UnsupportedSchemaVersionError(
                f'不支持 schema_version={manifest.schema_version}，'
                f'当前仅支持 {SCHEMA_VERSION}')
        if manifest.status != 'complete':
            raise ManifestValidationError(
                f'运行状态不是 complete：{manifest.status!r}')
        if not _SAFE_RUN_ID.fullmatch(manifest.run_id):
            raise ManifestValidationError(
                f'manifest.run_id 非法：{manifest.run_id!r}')
        allowed_levels = {
            'strict', 'input_replayable_code_dirty', 'incomplete'}
        if manifest.reproducibility_level not in allowed_levels:
            raise ManifestValidationError(
                'manifest.reproducibility_level 非法：'
                f'{manifest.reproducibility_level!r}')
        required_identity = {
            'config_fingerprint', 'input_fingerprint',
            'experiment_fingerprint'}
        missing_identity = required_identity - set(manifest.identity)
        if missing_identity:
            raise ManifestValidationError(
                f'manifest.identity 缺少：{sorted(missing_identity)}')
        required_configs = {
            'dataset_spec', 'signal', 'diagnostic_portfolio_spec'}
        missing_configs = required_configs - set(manifest.configs)
        if missing_configs:
            raise ManifestValidationError(
                f'manifest.configs 缺少：{sorted(missing_configs)}')
        required_artifacts = {'input.experiment_panel'} | {
            f'output.{field_name}' for field_name in _RESULT_TABLE_FIELDS}
        missing_artifacts = required_artifacts - set(manifest.artifacts)
        if missing_artifacts:
            raise ManifestValidationError(
                f'manifest.artifacts 缺少：{sorted(missing_artifacts)}')
        for key, record in manifest.artifacts.items():
            if record.format != 'parquet':
                raise ManifestValidationError(
                    f'artifact {key!r} 的格式不受支持：{record.format!r}')
            if record.kind not in {'series', 'dataframe'}:
                raise ManifestValidationError(
                    f'artifact {key!r} 的 kind 非法：{record.kind!r}')
            if record.present:
                if not record.path or not record.sha256 or \
                        record.size_bytes is None:
                    raise ManifestValidationError(
                        f'artifact {key!r} 的完整性字段不完整')
            elif record.path is not None:
                raise ManifestValidationError(
                    f'未产生的 artifact {key!r} 不应包含 path')
        if not manifest.artifacts['input.experiment_panel'].present:
            raise ManifestValidationError(
                'input.experiment_panel 必须存在')
        for field_name in set(_RESULT_TABLE_FIELDS) - \
                _OPTIONAL_RESULT_FIELDS:
            if not manifest.artifacts[f'output.{field_name}'].present:
                raise ManifestValidationError(
                    f'非可选结果 output.{field_name} 必须存在')

    def _artifact_path(
            self,
            run_path: Path,
            record: ArtifactRecord,
    ) -> Path:
        if not record.present or not record.path:
            raise ManifestValidationError('存在的 artifact 必须提供 path')
        relative = Path(record.path)
        if relative.is_absolute():
            raise ManifestValidationError(
                f'artifact 路径不能是绝对路径：{record.path}')
        candidate = (run_path / relative).resolve()
        try:
            candidate.relative_to(run_path.resolve())
        except ValueError as exc:
            raise ManifestValidationError(
                f'artifact 路径越出运行目录：{record.path}') from exc
        return candidate

    def _verify_artifact_file(
            self,
            path: Path,
            record: ArtifactRecord,
    ) -> None:
        if not path.is_file():
            raise ArtifactIntegrityError(f'artifact 不存在：{record.path}')
        if record.size_bytes is not None and \
                path.stat().st_size != record.size_bytes:
            raise ArtifactIntegrityError(
                f'artifact 文件大小不一致：{record.path}')
        if record.sha256 is None or sha256_file(path) != record.sha256:
            raise ArtifactIntegrityError(
                f'artifact SHA-256 不一致：{record.path}')

    def _summary(self, manifest: RunManifest) -> RunSummary:
        signal = manifest.configs['signal']
        portfolio_spec = manifest.configs['diagnostic_portfolio_spec']
        identity = manifest.identity
        user_metadata = manifest.user_metadata
        return RunSummary(
            run_id=manifest.run_id,
            created_at=manifest.created_at,
            signal_name=signal['name'],
            factor_name=signal['factor_name'],
            harvest_profile=portfolio_spec['harvest_profile']['name'],
            products=tuple(manifest.resolved_input.get('products', ())),
            start_timestamp=manifest.resolved_input.get('start_timestamp'),
            end_timestamp=manifest.resolved_input.get('end_timestamp'),
            git_commit=manifest.code_provenance.get('git_commit'),
            config_fingerprint=identity['config_fingerprint'],
            experiment_fingerprint=identity['experiment_fingerprint'],
            tags=tuple(user_metadata.get('tags', ())),
            reproducibility_level=manifest.reproducibility_level,
        )

    def _align_query_timestamp(
            self,
            value: pd.Timestamp | None,
            reference: pd.Timestamp,
    ) -> pd.Timestamp | None:
        """让查询时间与 manifest 时间具有可比较的时区语义。"""
        if value is None:
            return None
        if reference.tzinfo is not None and value.tzinfo is None:
            return value.tz_localize(reference.tzinfo)
        if reference.tzinfo is None and value.tzinfo is not None:
            return value.tz_localize(None)
        return value


def build_replay_inputs(
        stored: StoredDiagnosticPortfolioRun,
) -> tuple[DiagnosticSignal, ResearchDataset, DiagnosticPortfolioSpec]:
    """从已保存实际输入构造 Runner 可直接消费的最小输入。"""
    panel = stored.experiment_panel
    required_columns = {'true_return', 'eligible', 'signal'}
    missing = required_columns - set(panel.columns)
    if missing:
        raise ManifestValidationError(
            f'experiment_panel 缺少字段：{sorted(missing)}')
    configs = stored.manifest.configs
    signal_config = configs['signal']
    signal = DiagnosticSignal(
        name=signal_config['name'],
        values=panel['signal'].copy().rename(signal_config['name']),
        source_stage=signal_config['source_stage'],
        factor_name=signal_config['factor_name'],
        recipe=signal_config.get('recipe', {}),
        warnings=signal_config.get('warnings', {}),
    )
    dataset_spec = dataset_spec_from_dict(configs['dataset_spec'])
    quality = data_quality_from_dict(
        stored.manifest.resolved_input.get('dataset_quality'),
        fallback_rows=len(panel),
    )
    eligible = panel['eligible'].astype(bool).copy().rename(None)
    dataset = ResearchDataset(
        spec=dataset_spec,
        main_panel=pd.DataFrame({
            'True_Return': panel['true_return'].copy(),
        }, index=panel.index),
        quality=quality,
        universe=Universe(
            eligible=eligible,
            exclude_reason=pd.Series('', index=panel.index),
        ),
        auxiliary=(dict(stored.auxiliary_inputs)
                   if stored.auxiliary_inputs else None),
    )
    return signal, dataset, stored.result.spec


def replay_stored_run(
        stored: StoredDiagnosticPortfolioRun,
        *,
        compare: bool = True,
        rtol: float = 1e-12,
        atol: float = 1e-12,
) -> DiagnosticPortfolioResult:
    """只使用保存输入复跑组合实验，并可与保存结果逐字段比对。"""
    signal, dataset, spec = build_replay_inputs(stored)
    replayed = DiagnosticPortfolioExperimentRunner(
        signal=signal, dataset=dataset, spec=spec).run()
    if compare:
        _assert_results_equal(
            stored.result, replayed, rtol=rtol, atol=atol)
    return replayed


def _assert_results_equal(
        expected: DiagnosticPortfolioResult,
        actual: DiagnosticPortfolioResult,
        *,
        rtol: float,
        atol: float,
) -> None:
    """按持久化方案的精确结构和浮点容差比较两个结果。"""
    if expected.signal_name != actual.signal_name:
        raise ArtifactIntegrityError('复跑结果 signal_name 不一致')
    for field_name in _RESULT_TABLE_FIELDS:
        left = getattr(expected, field_name)
        right = getattr(actual, field_name)
        if left is None or right is None:
            if left is not None or right is not None:
                raise ArtifactIntegrityError(
                    f'复跑结果 {field_name} 的 None 状态不一致')
            continue
        try:
            if isinstance(left, pd.Series) and isinstance(right, pd.Series):
                pd.testing.assert_series_equal(
                    left, right, check_exact=False, rtol=rtol, atol=atol)
            elif isinstance(left, pd.DataFrame) and \
                    isinstance(right, pd.DataFrame):
                pd.testing.assert_frame_equal(
                    left, right, check_exact=False, rtol=rtol, atol=atol)
            else:
                raise AssertionError(
                    f'对象类型不同：{type(left).__name__} / '
                    f'{type(right).__name__}')
        except AssertionError as exc:
            raise ArtifactIntegrityError(
                f'复跑结果字段 {field_name} 不一致：{exc}') from exc
    if expected.warnings != actual.warnings:
        raise ArtifactIntegrityError('复跑结果 warnings 不一致')
    _assert_metadata_equal(
        expected.metadata, actual.metadata, path='metadata',
        rtol=rtol, atol=atol)


def _assert_metadata_equal(
        expected: Any,
        actual: Any,
        *,
        path: str,
        rtol: float,
        atol: float,
) -> None:
    if isinstance(expected, pd.DataFrame) and isinstance(actual, pd.DataFrame):
        try:
            pd.testing.assert_frame_equal(
                expected, actual, check_exact=False, rtol=rtol, atol=atol)
        except AssertionError as exc:
            raise ArtifactIntegrityError(f'{path} 不一致：{exc}') from exc
        return
    if isinstance(expected, pd.Series) and isinstance(actual, pd.Series):
        try:
            pd.testing.assert_series_equal(
                expected, actual, check_exact=False, rtol=rtol, atol=atol)
        except AssertionError as exc:
            raise ArtifactIntegrityError(f'{path} 不一致：{exc}') from exc
        return
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        if set(expected) != set(actual):
            raise ArtifactIntegrityError(f'{path} 的键不一致')
        for key in expected:
            _assert_metadata_equal(
                expected[key], actual[key], path=f'{path}.{key}',
                rtol=rtol, atol=atol)
        return
    if isinstance(expected, (list, tuple)) and \
            isinstance(actual, (list, tuple)):
        if len(expected) != len(actual):
            raise ArtifactIntegrityError(f'{path} 的长度不一致')
        for index, (left, right) in enumerate(zip(expected, actual)):
            _assert_metadata_equal(
                left, right, path=f'{path}[{index}]',
                rtol=rtol, atol=atol)
        return
    if isinstance(expected, (float, np.floating)) and \
            isinstance(actual, (float, np.floating)):
        if not np.isclose(expected, actual, rtol=rtol, atol=atol,
                          equal_nan=True):
            raise ArtifactIntegrityError(f'{path} 的浮点值不一致')
        return
    if expected != actual:
        raise ArtifactIntegrityError(
            f'{path} 不一致：{expected!r} != {actual!r}')


__all__ = [
    'SCHEMA_VERSION',
    'DEFAULT_ROOT_DIR',
    'DiagnosticPortfolioStore',
    'build_replay_inputs',
    'replay_stored_run',
]
