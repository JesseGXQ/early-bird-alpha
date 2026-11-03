from pathlib import Path

from data.config import EnvironmentConfig


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_default_paths_are_anchored_to_repository(monkeypatch, tmp_path):
    """默认路径由仓库位置推导，不受当前工作目录影响。"""
    monkeypatch.delenv('EARLY_BIRD_RAW_DATA_ROOT', raising=False)
    monkeypatch.chdir(tmp_path)

    config = EnvironmentConfig()

    assert Path(config.product_meta_dir) == (
        REPOSITORY_ROOT / 'db/rules/product_meta')
    assert Path(config.daily_snapshot_dir) == (
        REPOSITORY_ROOT
        / 'db/market/daily_snapshots/bar_1d/bar_1d_tushare/cleaned')
    assert Path(config.data_dir_1min) == (
        REPOSITORY_ROOT.parent / 'RAW_DATA/1min')
    assert Path(config.data_dir_tick) == (
        REPOSITORY_ROOT.parent / 'RAW_DATA/TICK')


def test_raw_data_root_environment_variable_overrides_default(
        monkeypatch, tmp_path):
    """环境变量可把原始行情根目录放到任意位置。"""
    raw_data_root = tmp_path / 'custom_raw_data'
    monkeypatch.setenv('EARLY_BIRD_RAW_DATA_ROOT', str(raw_data_root))

    config = EnvironmentConfig()

    assert Path(config.data_dir_1min) == raw_data_root / '1min'
    assert Path(config.data_dir_tick) == raw_data_root / 'TICK'


def test_explicit_path_has_priority_over_environment_variable(
        monkeypatch, tmp_path):
    """构造函数显式传入的路径优先于环境变量推导值。"""
    raw_data_root = tmp_path / 'environment_raw_data'
    explicit_1min = tmp_path / 'explicit_1min'
    monkeypatch.setenv('EARLY_BIRD_RAW_DATA_ROOT', str(raw_data_root))

    config = EnvironmentConfig(data_dir_1min=str(explicit_1min))

    assert Path(config.data_dir_1min) == explicit_1min
    assert Path(config.data_dir_tick) == raw_data_root / 'TICK'
