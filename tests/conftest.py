"""公开测试套件的分层入口和外部数据门控。"""

from __future__ import annotations

from pathlib import Path

import pytest

from data.config import EnvironmentConfig


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup('early-bird-alpha')
    group.addoption(
        '--run-real-data',
        action='store_true',
        default=False,
        help='运行依赖完整 db 和仓库外 RAW_DATA 的测试。',
    )
    group.addoption(
        '--run-slow',
        action='store_true',
        default=False,
        help='运行标记为 slow 的耗时测试。',
    )


def _has_matching_file(directory: str, pattern: str) -> bool:
    path = Path(directory)
    return path.is_dir() and next(path.glob(pattern), None) is not None


def _missing_real_data() -> list[str]:
    env = EnvironmentConfig()
    checks = (
        ('合约注册库', env.instrument_registry_dir, '*.parquet'),
        ('合约日频状态库', env.daily_status_dir, '*.parquet'),
        ('交易日历', env.calendar_dir, '*.csv'),
        ('日频快照', env.daily_snapshot_dir, '*.parquet'),
        ('换月主表', env.master_table_dir, '*.parquet'),
        ('1分钟行情', env.data_dir_1min, '*'),
        ('TICK行情', env.data_dir_tick, '*'),
    )
    return [
        f'{name}（{directory}）'
        for name, directory, pattern in checks
        if not _has_matching_file(directory, pattern)
    ]


def pytest_collection_modifyitems(
        config: pytest.Config, items: list[pytest.Item]) -> None:
    run_real_data = config.getoption('--run-real-data')
    run_slow = config.getoption('--run-slow')
    missing_real_data = _missing_real_data() if run_real_data else []

    skip_real_data = pytest.mark.skip(
        reason='需要完整行情库；使用 --run-real-data 显式运行。')
    skip_missing_data = pytest.mark.skip(
        reason='完整行情库不齐全：' + '；'.join(missing_real_data))
    skip_slow = pytest.mark.skip(
        reason='耗时测试默认不运行；使用 --run-slow 显式运行。')

    for item in items:
        if item.get_closest_marker('real_data') is not None:
            if not run_real_data:
                item.add_marker(skip_real_data)
            elif missing_real_data:
                item.add_marker(skip_missing_data)
        if item.get_closest_marker('slow') is not None and not run_slow:
            item.add_marker(skip_slow)
