"""诊断组合实验持久化层的数据模型与异常。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from factors.diagnostic_portfolio.models import DiagnosticPortfolioResult


class DiagnosticPortfolioStorageError(Exception):
    """诊断组合实验持久化错误基类。"""


class SerializationError(DiagnosticPortfolioStorageError):
    """对象无法按存储格式安全序列化。"""


class ManifestValidationError(DiagnosticPortfolioStorageError):
    """Manifest 缺失字段、字段非法或与 artifact 不一致。"""


class ArtifactIntegrityError(DiagnosticPortfolioStorageError):
    """Artifact 缺失、损坏或复跑结果不一致。"""


class UnsupportedSchemaVersionError(DiagnosticPortfolioStorageError):
    """加载器不支持目标存储 schema。"""


class RunNotFoundError(DiagnosticPortfolioStorageError):
    """请求的运行不存在。"""


class RunAlreadyExistsError(DiagnosticPortfolioStorageError):
    """目标运行目录已经存在。"""


@dataclass(frozen=True)
class ArtifactRecord:
    """单个输入或结果文件在 manifest 中的结构记录。"""

    path: str | None
    format: str
    kind: str
    present: bool
    sha256: str | None = None
    size_bytes: int | None = None
    row_count: int | None = None
    column_count: int | None = None
    index_names: tuple[str | None, ...] = ()
    columns: tuple[Any, ...] = ()
    dtypes: tuple[str, ...] = ()
    index_dtypes: tuple[str, ...] = ()
    series_name: Any = None

    def to_dict(self) -> dict[str, Any]:
        """转换为可继续规范化的普通字典。"""
        return {
            'path': self.path,
            'format': self.format,
            'kind': self.kind,
            'present': self.present,
            'sha256': self.sha256,
            'size_bytes': self.size_bytes,
            'row_count': self.row_count,
            'column_count': self.column_count,
            'index_names': list(self.index_names),
            'columns': list(self.columns),
            'dtypes': list(self.dtypes),
            'index_dtypes': list(self.index_dtypes),
            'series_name': self.series_name,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'ArtifactRecord':
        """从 manifest 字典恢复记录。"""
        required = {'path', 'format', 'kind', 'present'}
        missing = required - set(data)
        if missing:
            raise ManifestValidationError(
                f'ArtifactRecord 缺少字段：{sorted(missing)}')
        if not isinstance(data['present'], bool):
            raise ManifestValidationError(
                'ArtifactRecord.present 必须是布尔值')
        return cls(
            path=data['path'],
            format=str(data['format']),
            kind=str(data['kind']),
            present=data['present'],
            sha256=data.get('sha256'),
            size_bytes=data.get('size_bytes'),
            row_count=data.get('row_count'),
            column_count=data.get('column_count'),
            index_names=tuple(data.get('index_names', ())),
            columns=tuple(data.get('columns', ())),
            dtypes=tuple(data.get('dtypes', ())),
            index_dtypes=tuple(data.get('index_dtypes', ())),
            series_name=data.get('series_name'),
        )


@dataclass(frozen=True)
class RunManifest:
    """一次完整诊断组合实验的自描述清单。"""

    schema_version: int
    run_id: str
    created_at: str
    status: str
    reproducibility_level: str
    identity: Mapping[str, Any]
    configs: Mapping[str, Any]
    resolved_input: Mapping[str, Any]
    code_provenance: Mapping[str, Any]
    data_provenance: Mapping[str, Any]
    runtime: Mapping[str, Any]
    artifacts: Mapping[str, ArtifactRecord]
    warnings: tuple[str, ...] = ()
    storage_warnings: tuple[str, ...] = ()
    result_metadata: Mapping[str, Any] = field(default_factory=dict)
    user_metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为 JSON manifest 使用的普通字典。"""
        return {
            'schema_version': self.schema_version,
            'run_id': self.run_id,
            'created_at': self.created_at,
            'status': self.status,
            'reproducibility_level': self.reproducibility_level,
            'identity': dict(self.identity),
            'configs': dict(self.configs),
            'resolved_input': dict(self.resolved_input),
            'code_provenance': dict(self.code_provenance),
            'data_provenance': dict(self.data_provenance),
            'runtime': dict(self.runtime),
            'artifacts': {
                key: value.to_dict()
                for key, value in sorted(self.artifacts.items())
            },
            'warnings': list(self.warnings),
            'storage_warnings': list(self.storage_warnings),
            'result_metadata': dict(self.result_metadata),
            'user_metadata': dict(self.user_metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'RunManifest':
        """从 JSON 字典恢复并完成顶层字段校验。"""
        required = {
            'schema_version', 'run_id', 'created_at', 'status',
            'reproducibility_level', 'identity', 'configs',
            'resolved_input', 'code_provenance', 'data_provenance',
            'runtime', 'artifacts', 'warnings', 'result_metadata',
            'user_metadata',
        }
        missing = required - set(data)
        if missing:
            raise ManifestValidationError(
                f'manifest 缺少字段：{sorted(missing)}')
        artifacts_data = data['artifacts']
        if not isinstance(artifacts_data, Mapping):
            raise ManifestValidationError('manifest.artifacts 必须是对象')
        return cls(
            schema_version=int(data['schema_version']),
            run_id=str(data['run_id']),
            created_at=str(data['created_at']),
            status=str(data['status']),
            reproducibility_level=str(data['reproducibility_level']),
            identity=dict(data['identity']),
            configs=dict(data['configs']),
            resolved_input=dict(data['resolved_input']),
            code_provenance=dict(data['code_provenance']),
            data_provenance=dict(data['data_provenance']),
            runtime=dict(data['runtime']),
            artifacts={
                str(key): ArtifactRecord.from_dict(value)
                for key, value in artifacts_data.items()
            },
            warnings=tuple(data.get('warnings', ())),
            storage_warnings=tuple(data.get('storage_warnings', ())),
            result_metadata=dict(data.get('result_metadata', {})),
            user_metadata=dict(data.get('user_metadata', {})),
        )


@dataclass(frozen=True)
class StoredDiagnosticPortfolioRun:
    """从持久化仓库完整加载的一次运行。"""

    path: Path
    manifest: RunManifest
    result: DiagnosticPortfolioResult
    experiment_panel: pd.DataFrame = field(repr=False)
    auxiliary_inputs: Mapping[str, pd.DataFrame | pd.Series] = field(
        default_factory=dict, repr=False)


@dataclass(frozen=True)
class RunSummary:
    """只读取 manifest 得到的轻量运行摘要。"""

    run_id: str
    created_at: str
    signal_name: str
    factor_name: str
    harvest_profile: str
    products: tuple[str, ...]
    start_timestamp: str | None
    end_timestamp: str | None
    git_commit: str | None
    config_fingerprint: str
    experiment_fingerprint: str
    tags: tuple[str, ...]
    reproducibility_level: str


__all__ = [
    'DiagnosticPortfolioStorageError',
    'SerializationError',
    'ManifestValidationError',
    'ArtifactIntegrityError',
    'UnsupportedSchemaVersionError',
    'RunNotFoundError',
    'RunAlreadyExistsError',
    'ArtifactRecord',
    'RunManifest',
    'StoredDiagnosticPortfolioRun',
    'RunSummary',
]
