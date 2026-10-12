"""诊断组合实验配置与 pandas artifact 的显式序列化。"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from data.config import (
    AdjustmentConfig,
    EnvironmentConfig,
    RolloverConfig,
    SamplingConfig,
)
from factors.diagnostic_portfolio.models import (
    ConstraintConfig,
    CostConfig,
    DiagnosticPortfolioSpec,
    DiagnosticSignal,
    RebalanceConfig,
    RiskConfig,
)
from factors.models import (
    DataQualitySummary,
    DataRequirement,
    DatasetSpec,
    HarvestProfile,
    UniverseSpec,
)

from .models import ArtifactRecord, SerializationError


_FACADE_CONFIG_TYPES = {
    'RolloverConfig': RolloverConfig,
    'AdjustmentConfig': AdjustmentConfig,
    'SamplingConfig': SamplingConfig,
    'EnvironmentConfig': EnvironmentConfig,
}


def to_jsonable(value: Any, *, path: str = '$') -> Any:
    """把值递归转换为稳定、安全的 JSON 基础类型。"""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, np.generic):
        return to_jsonable(value.item(), path=path)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SerializationError(f'{path} 包含非有限浮点值 {value!r}')
        return value
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            raise SerializationError(f'{path} 包含 NaT')
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: to_jsonable(
                getattr(value, item.name), path=f'{path}.{item.name}')
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SerializationError(
                    f'{path} 的 Mapping 键必须是字符串，收到 {key!r}')
            converted[key] = to_jsonable(item, path=f'{path}.{key}')
        return converted
    if isinstance(value, (tuple, list)):
        return [
            to_jsonable(item, path=f'{path}[{index}]')
            for index, item in enumerate(value)
        ]
    raise SerializationError(
        f'{path} 包含不支持的类型 {type(value).__name__}')


def canonical_json_dumps(value: Any) -> str:
    """生成用于展示和哈希的规范 JSON 字符串。"""
    normalized = to_jsonable(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(',', ':'),
    )


def pretty_json_dumps(value: Any) -> str:
    """生成 UTF-8 manifest 使用的可读 JSON 字符串。"""
    normalized = to_jsonable(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    ) + '\n'


def sha256_bytes(data: bytes) -> str:
    """计算带算法前缀的 SHA-256。"""
    return f'sha256:{hashlib.sha256(data).hexdigest()}'


def sha256_text(text: str) -> str:
    """按 UTF-8 计算文本 SHA-256。"""
    return sha256_bytes(text.encode('utf-8'))


def sha256_file(path: Path) -> str:
    """流式计算文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open('rb') as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return f'sha256:{digest.hexdigest()}'


def dataset_spec_to_dict(spec: DatasetSpec) -> dict[str, Any]:
    """序列化 DatasetSpec，并为 facade 子配置保留显式类型。"""
    facade_config: list[dict[str, Any]] = []
    for index, config in enumerate(spec.facade_config):
        type_name = type(config).__name__
        if type_name not in _FACADE_CONFIG_TYPES:
            raise SerializationError(
                f'$.dataset_spec.facade_config[{index}] 包含未知类型 '
                f'{type_name}')
        facade_config.append({
            'type': type_name,
            'values': to_jsonable(asdict(config)),
        })
    return to_jsonable({
        'facade_config': facade_config,
        'products': list(spec.products),
        'data_requirements': [asdict(item) for item in spec.data_requirements],
        'start_date': spec.start_date,
        'end_date': spec.end_date,
        'freq': spec.freq,
        'view': spec.view,
        'universe': asdict(spec.universe),
    })


def dataset_spec_from_dict(data: Mapping[str, Any]) -> DatasetSpec:
    """从 manifest 恢复 DatasetSpec。"""
    facade_items = []
    for index, item in enumerate(data.get('facade_config', ())):
        try:
            cls = _FACADE_CONFIG_TYPES[item['type']]
            facade_items.append(cls(**dict(item['values'])))
        except (KeyError, TypeError) as exc:
            raise SerializationError(
                f'dataset_spec.facade_config[{index}] 无法恢复') from exc

    def _optional_date(value: Any) -> date | None:
        if value is None:
            return None
        return date.fromisoformat(str(value))

    requirements = tuple(
        DataRequirement(
            source=item['source'],
            columns=(None if item.get('columns') is None
                     else tuple(item['columns'])),
            request_params=item.get('request_params'),
        )
        for item in data.get('data_requirements', ())
    )
    return DatasetSpec(
        facade_config=tuple(facade_items),
        products=tuple(data['products']),
        data_requirements=requirements,
        start_date=_optional_date(data.get('start_date')),
        end_date=_optional_date(data.get('end_date')),
        freq=data.get('freq', '1d'),
        view=data.get('view', 'scheduled'),
        universe=UniverseSpec(**dict(data.get('universe', {}))),
    )


def signal_to_dict(signal: DiagnosticSignal) -> dict[str, Any]:
    """序列化信号描述，不在 JSON 中重复保存 values。"""
    return to_jsonable({
        'name': signal.name,
        'source_stage': signal.source_stage,
        'factor_name': signal.factor_name,
        'recipe': signal.recipe,
        'warnings': signal.warnings,
    })


def portfolio_spec_to_dict(spec: DiagnosticPortfolioSpec) -> dict[str, Any]:
    """序列化完整组合实验配置。"""
    anchor = spec.rebalance.anchor
    anchor_type: str | None
    if isinstance(anchor, pd.Timestamp):
        anchor_type = 'timestamp'
    elif isinstance(anchor, datetime):
        anchor_type = 'datetime'
    elif isinstance(anchor, date):
        anchor_type = 'date'
    else:
        anchor_type = None
    return to_jsonable({
        'name': spec.name,
        'harvest_profile': asdict(spec.harvest_profile),
        'rebalance': {
            'interval': spec.rebalance.interval,
            'anchor': anchor,
            'anchor_type': anchor_type,
        },
        'constraint': asdict(spec.constraint),
        'cost': asdict(spec.cost),
        'realized_volatility_lookback': spec.realized_volatility_lookback,
        'risk': None if spec.risk is None else asdict(spec.risk),
        'clip_bound': spec.clip_bound,
        'min_cross_section_size': spec.min_cross_section_size,
        'annualization_factor': spec.annualization_factor,
        'sector_attribution_col': spec.sector_attribution_col,
        'missing_return_policy': spec.missing_return_policy,
    })


def portfolio_spec_from_dict(
        data: Mapping[str, Any]) -> DiagnosticPortfolioSpec:
    """从 manifest 恢复组合实验配置。"""
    rebalance_data = dict(data['rebalance'])
    anchor_value = rebalance_data.get('anchor')
    anchor_type = rebalance_data.get('anchor_type')
    if anchor_value is None:
        anchor = None
    elif anchor_type == 'timestamp':
        anchor = pd.Timestamp(anchor_value)
    elif anchor_type == 'datetime':
        anchor = datetime.fromisoformat(str(anchor_value))
    else:
        anchor = date.fromisoformat(str(anchor_value))
    risk_data = data.get('risk')
    return DiagnosticPortfolioSpec(
        name=data['name'],
        harvest_profile=HarvestProfile(**dict(data['harvest_profile'])),
        rebalance=RebalanceConfig(
            interval=int(rebalance_data['interval']), anchor=anchor),
        constraint=ConstraintConfig(**dict(data['constraint'])),
        cost=CostConfig(**dict(data['cost'])),
        realized_volatility_lookback=data.get(
            'realized_volatility_lookback'),
        risk=None if risk_data is None else RiskConfig(**dict(risk_data)),
        clip_bound=data.get('clip_bound'),
        min_cross_section_size=data.get('min_cross_section_size'),
        annualization_factor=int(data.get('annualization_factor', 252)),
        sector_attribution_col=data.get('sector_attribution_col'),
        missing_return_policy=data.get('missing_return_policy', 'raise'),
    )


def data_quality_to_dict(
        quality: DataQualitySummary | None) -> dict[str, Any] | None:
    """序列化数据质量摘要。"""
    if quality is None:
        return None
    return to_jsonable(asdict(quality))


def data_quality_from_dict(
        data: Mapping[str, Any] | None,
        *,
        fallback_rows: int,
) -> DataQualitySummary:
    """恢复数据质量摘要；旧或空摘要使用保守的最小值。"""
    if data is None:
        return DataQualitySummary(
            is_valid=True,
            total_rows=fallback_rows,
            product_sample_counts=None,
            missing_rate_by_column={},
            warnings=(),
            errors=(),
        )
    return DataQualitySummary(
        is_valid=bool(data['is_valid']),
        total_rows=int(data['total_rows']),
        product_sample_counts=data.get('product_sample_counts'),
        missing_rate_by_column=dict(data.get('missing_rate_by_column', {})),
        warnings=tuple(data.get('warnings', ())),
        errors=tuple(data.get('errors', ())),
    )


def semantic_configs(configs: Mapping[str, Any]) -> dict[str, Any]:
    """返回排除本地物理路径后的语义配置。"""
    normalized = json.loads(canonical_json_dumps(configs))
    facade_config = normalized['dataset_spec'].get('facade_config', [])
    normalized['dataset_spec']['facade_config'] = [
        item for item in facade_config
        if item.get('type') != 'EnvironmentConfig'
    ]
    return normalized


def write_pandas_artifact(
        obj: pd.DataFrame | pd.Series,
        *,
        path: Path,
        relative_path: str,
) -> ArtifactRecord:
    """写入 pandas artifact 并返回完整结构记录。"""
    if isinstance(obj, pd.Series):
        kind = 'series'
        series_name = obj.name
        frame = obj.to_frame(name=(obj.name if obj.name is not None else 0))
    elif isinstance(obj, pd.DataFrame):
        kind = 'dataframe'
        series_name = None
        frame = obj
    else:
        raise SerializationError(
            f'{relative_path} 必须是 Series 或 DataFrame，收到 '
            f'{type(obj).__name__}')
    if isinstance(frame.index, pd.MultiIndex):
        index_dtypes = tuple(
            str(frame.index.get_level_values(index).dtype)
            for index in range(frame.index.nlevels)
        )
    else:
        index_dtypes = (str(frame.index.dtype),)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=True, engine='pyarrow')
    return ArtifactRecord(
        path=relative_path,
        format='parquet',
        kind=kind,
        present=True,
        sha256=sha256_file(path),
        size_bytes=path.stat().st_size,
        row_count=len(frame),
        column_count=len(frame.columns),
        index_names=tuple(frame.index.names),
        columns=tuple(frame.columns.tolist()),
        dtypes=tuple(str(dtype) for dtype in frame.dtypes),
        index_dtypes=index_dtypes,
        series_name=series_name,
    )


def absent_artifact(*, kind: str) -> ArtifactRecord:
    """构造未产生的可选 artifact 记录。"""
    return ArtifactRecord(
        path=None, format='parquet', kind=kind, present=False)


def read_pandas_artifact(
        path: Path,
        record: ArtifactRecord,
) -> pd.DataFrame | pd.Series:
    """读取 artifact，并依据 manifest 恢复原 pandas 对象类型。"""
    frame = pd.read_parquet(path, engine='pyarrow')
    if record.dtypes:
        if len(record.dtypes) != len(frame.columns):
            raise SerializationError(
                f'{record.path} dtype 记录数量与列数不一致')
        for column, expected_dtype in zip(frame.columns, record.dtypes):
            if str(frame[column].dtype) != expected_dtype:
                try:
                    frame[column] = frame[column].astype(expected_dtype)
                except (TypeError, ValueError) as exc:
                    raise SerializationError(
                        f'{record.path} 的列 {column!r} 无法恢复为 '
                        f'{expected_dtype}') from exc
    if record.index_dtypes:
        if isinstance(frame.index, pd.MultiIndex):
            current_index_dtypes = tuple(
                str(frame.index.get_level_values(index).dtype)
                for index in range(frame.index.nlevels)
            )
            if len(record.index_dtypes) != frame.index.nlevels:
                raise SerializationError(
                    f'{record.path} index dtype 记录数量与层级数不一致')
            if current_index_dtypes != record.index_dtypes:
                try:
                    arrays = [
                        frame.index.get_level_values(index).astype(dtype)
                        for index, dtype in enumerate(record.index_dtypes)
                    ]
                    frame.index = pd.MultiIndex.from_arrays(
                        arrays, names=frame.index.names)
                except (TypeError, ValueError) as exc:
                    raise SerializationError(
                        f'{record.path} 无法恢复 MultiIndex dtype') from exc
        else:
            if len(record.index_dtypes) != 1:
                raise SerializationError(
                    f'{record.path} index dtype 记录数量非法')
            expected_index_dtype = record.index_dtypes[0]
            if str(frame.index.dtype) != expected_index_dtype:
                try:
                    frame.index = frame.index.astype(expected_index_dtype)
                except (TypeError, ValueError) as exc:
                    raise SerializationError(
                        f'{record.path} 无法恢复索引 dtype '
                        f'{expected_index_dtype}') from exc
    if record.row_count is not None and len(frame) != record.row_count:
        raise SerializationError(
            f'{record.path} 行数不一致：{len(frame)} != {record.row_count}')
    if record.column_count is not None and \
            len(frame.columns) != record.column_count:
        raise SerializationError(
            f'{record.path} 列数不一致：'
            f'{len(frame.columns)} != {record.column_count}')
    if tuple(frame.index.names) != record.index_names:
        raise SerializationError(
            f'{record.path} 索引名称不一致：'
            f'{tuple(frame.index.names)!r} != {record.index_names!r}')
    if tuple(frame.columns.tolist()) != record.columns:
        raise SerializationError(
            f'{record.path} 列名不一致：'
            f'{tuple(frame.columns.tolist())!r} != {record.columns!r}')
    if record.kind == 'dataframe':
        return frame
    if record.kind == 'series':
        if len(frame.columns) != 1:
            raise SerializationError(
                f'{record.path} 声明为 Series 但包含多列')
        return frame.iloc[:, 0].rename(record.series_name)
    raise SerializationError(
        f'{record.path} 包含未知 artifact kind {record.kind!r}')


__all__ = [
    'to_jsonable',
    'canonical_json_dumps',
    'pretty_json_dumps',
    'sha256_bytes',
    'sha256_text',
    'sha256_file',
    'dataset_spec_to_dict',
    'dataset_spec_from_dict',
    'signal_to_dict',
    'portfolio_spec_to_dict',
    'portfolio_spec_from_dict',
    'data_quality_to_dict',
    'data_quality_from_dict',
    'semantic_configs',
    'write_pandas_artifact',
    'read_pandas_artifact',
    'absent_artifact',
]
