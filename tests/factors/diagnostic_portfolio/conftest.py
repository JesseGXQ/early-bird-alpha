"""tests/factors/diagnostic_portfolio/conftest.py — 组合实验层测试公共 fixture。

构造纯 synthetic 的 ``ResearchDataset`` / ``DiagnosticSignal``，不依赖 ``db/``
或仓库外 ``RAW_DATA``。测试目录镜像包结构（``tests/<package>/``）。

本模块只提供“工厂函数”型 fixture：用例自行决定时间戳、品种与收益，避免共享
可变状态。所有面板索引统一为升序的 ``MultiIndex[timestamp, product]``。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pytest

from factors.diagnostic_portfolio.models import DiagnosticSignal
from factors.models import (
    INDEX_NAMES,
    DatasetSpec,
    DataQualitySummary,
    ResearchDataset,
)
from factors.universe import Universe


def build_index(
    timestamps: Sequence[str | pd.Timestamp],
    products: Sequence[str],
) -> pd.MultiIndex:
    """构造升序的 ``MultiIndex[timestamp, product]``（笛卡尔积）。"""
    return pd.MultiIndex.from_product(
        [pd.to_datetime(list(timestamps)), list(products)],
        names=list(INDEX_NAMES),
    )


def build_dataset(
    timestamps: Sequence[str | pd.Timestamp],
    products: Sequence[str],
    true_return: Sequence[float] | np.ndarray | pd.Series,
    eligible: Sequence[bool] | None = None,
    auxiliary: Mapping[str, Any] | None = None,
) -> ResearchDataset:
    """构造 synthetic ``ResearchDataset``。

    Parameters
    ----------
    timestamps, products : sequence
        时间轴与品种列表；面板索引为其笛卡尔积。
    true_return : sequence
        按 ``(timestamp, product)`` 行主序排列的 True_Return。
    eligible : sequence of bool, optional
        品种池准入掩码，长度与面板一致；默认全为 True。
    auxiliary : Mapping, optional
        附属数据集（如 ``{'classification': DataFrame}``）。
    """
    index = build_index(timestamps, products)
    returns = pd.Series(
        np.asarray(true_return, dtype=float), index=index, name='True_Return')
    if eligible is None:
        eligible_series = pd.Series(True, index=index)
    else:
        eligible_series = pd.Series(
            np.asarray(eligible, dtype=bool), index=index)

    n = len(index)
    return ResearchDataset(
        spec=DatasetSpec(
            facade_config=(), products=tuple(products), data_requirements=()),
        main_panel=pd.DataFrame({'True_Return': returns}, index=index),
        quality=DataQualitySummary(
            is_valid=True, total_rows=n,
            product_sample_counts={p: int(n // len(products)) for p in products},
            missing_rate_by_column={}, warnings=(), errors=()),
        universe=Universe(
            eligible=eligible_series,
            exclude_reason=pd.Series('', index=index)),
        auxiliary=auxiliary,
    )


def build_signal(
    values: pd.Series,
    name: str = 'test_signal',
    factor_name: str = 'test_factor',
    source_stage: str = 'raw',
    recipe: Mapping[str, Any] | None = None,
) -> DiagnosticSignal:
    """用给定 Series 包装一个 ``DiagnosticSignal``。"""
    return DiagnosticSignal(
        name=name, values=values, source_stage=source_stage,
        factor_name=factor_name, recipe=recipe if recipe is not None else {})


@pytest.fixture
def dataset_factory():
    """返回 :func:`build_dataset`。"""
    return build_dataset


@pytest.fixture
def signal_factory():
    """返回 :func:`build_signal`。"""
    return build_signal


@pytest.fixture
def index_factory():
    """返回 :func:`build_index`。"""
    return build_index
