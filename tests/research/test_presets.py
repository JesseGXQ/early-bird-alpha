"""research/presets.py 的单元测试。

这里只校验"设定"是否被正确组装成 DatasetSpec，不触发真实数据加载：
ResearchDatasetBuilder 被替换成只记录 spec 的假构造器。
"""
import datetime as dt

import pytest

import research.presets as presets
from data.config import (
    AdjustmentConfig,
    EnvironmentConfig,
    RolloverConfig,
    SamplingConfig,
)
from factors.models import DataRequirement, UniverseSpec


class _RecordingBuilder:
    """只记录 spec 的假构造器，避免测试依赖 db/ 真实数据。"""

    captured: list = []

    def __init__(self, spec):
        type(self).captured.append(spec)

    def build(self):
        return object()


@pytest.fixture
def recording_builder(monkeypatch):
    _RecordingBuilder.captured = []
    monkeypatch.setattr(presets, 'ResearchDatasetBuilder', _RecordingBuilder)
    return _RecordingBuilder


def test_default_facade_config_returns_four_configs():
    config = presets.default_facade_config()
    assert isinstance(config, tuple) and len(config) == 4
    assert isinstance(config[0], RolloverConfig)
    assert isinstance(config[1], AdjustmentConfig)
    assert isinstance(config[2], SamplingConfig)
    assert isinstance(config[3], EnvironmentConfig)


def test_build_dataset_builds_expected_spec(recording_builder):
    start = dt.date(2020, 1, 1)
    end = dt.date(2020, 12, 31)
    presets.build_dataset(
        products=('RB', 'MA'),
        start_date=start,
        end_date=end,
        sources=('term_structure',),
        universe=UniverseSpec(min_average_volume=5000),
    )

    spec = recording_builder.captured[-1]
    assert spec.products == ('RB', 'MA')
    assert spec.start_date == start
    assert spec.end_date == end
    assert spec.universe == UniverseSpec(min_average_volume=5000)
    # sources 只给了 term_structure，main_panel 应被自动补齐且排在最前
    assert [req.source for req in spec.data_requirements] == [
        'main_panel', 'term_structure',
    ]


def test_build_dataset_keeps_given_data_requirement(recording_builder):
    requirement = DataRequirement(
        source='warehouse_receipt',
        columns=('trading_date', 'product'),
        request_params={'aggregate': True},
    )
    presets.build_dataset(products=('RB',), sources=(requirement,))

    spec = recording_builder.captured[-1]
    assert requirement in spec.data_requirements
    assert spec.data_requirements[0].source == 'main_panel'


def test_build_dataset_does_not_duplicate_main_panel(recording_builder):
    presets.build_dataset(
        products=('RB',), sources=('main_panel', 'term_structure'),
    )
    spec = recording_builder.captured[-1]
    assert [req.source for req in spec.data_requirements] == [
        'main_panel', 'term_structure',
    ]


def test_build_dataset_rejects_unknown_requirement_type(recording_builder):
    with pytest.raises(TypeError):
        presets.build_dataset(products=('RB',), sources=(123,))
