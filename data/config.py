import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ALL_EXCHANGES = ['CFFEX', 'CZCE', 'DCE', 'GFEX', 'INE', 'SHFE']

_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
_DATABASE_ROOT = _REPOSITORY_ROOT / 'db'
_DEFAULT_HIGH_FREQ_RAW_DATA_ROOT = _REPOSITORY_ROOT.parent / 'RAW_DATA'
_HIGH_FREQ_RAW_DATA_ROOT_ENV_VAR = 'EARLY_BIRD_HIGH_FREQ_RAW_DATA_ROOT'


def _database_path(*parts: str) -> str:
    """返回相对仓库内 ``db`` 根目录定位的绝对路径。"""
    return str(_DATABASE_ROOT.joinpath(*parts))


def _raw_data_root() -> Path:
    """返回原始行情根目录，环境变量优先于仓库同级默认目录。"""
    configured = os.environ.get(_HIGH_FREQ_RAW_DATA_ROOT_ENV_VAR)
    if configured:
        return Path(configured).expanduser().resolve()
    return _DEFAULT_HIGH_FREQ_RAW_DATA_ROOT


def _raw_data_path(directory: str) -> str:
    """返回原始行情根目录下指定子目录的绝对路径。"""
    return str(_raw_data_root() / directory)


@dataclass(frozen=True)
class RolloverConfig:
    """
    换月配置类。

    Parameters
    ----------
    buffer_days : int, optional
        换月缓冲天数，用于判断合约更换的缓冲窗口（默认：3）。
    ratio_threshold : float, optional
        比例阈值；新合约的持仓量（OI）必须超过旧合约多少倍才视为有效超越（默认：1.1）。
    transition_days : int, optional
        换月过渡期天数，在该窗口内两个月份合约可能同时用于计算（默认：1）。

    Attributes
    ----------
    signature : str
        基于配置生成的唯一指纹字符串，用于命名持久化的 master_table 文件，例如 'b3_r110_t1'。

    """
    buffer_days: int = 3         # 换月缓冲天数
    ratio_threshold: float = 1.1 # 比例阈值，新合约 OI 必须大于旧合约多少倍才记为有效超越
    transition_days: int = 1     # 换月过渡期窗口

    @property
    def signature(self) -> str:
        """
        生成参数唯一指纹。
        用于匹配本地持久化的 master_table 文件名，如: RB_b5_r80_t0.parquet
        """
        r_int = int(self.ratio_threshold * 100)
        return f'b{self.buffer_days}_r{r_int}_t{self.transition_days}'
    
@dataclass(frozen=True)
class AdjustmentConfig:
    """
    复权配置类。

    Parameters
    ----------
    direction : {'FORWARD', 'BACKWARD'}, optional
        复权方向。'BACKWARD' 表示锚定最新价格并调整历史价格；
        'FORWARD' 表示锚定上市日价格不变，向较新日期方向调整价格，中文
        称“后复权(hfq)”。
        默认后复权，即 'FORWARD' 。
    method : {'ADDITIVE', 'MULTIPLICATIVE'}, optional
        复权计算方法。'ADDITIVE' 表示等差调整；'MULTIPLICATIVE' 表示等比调整。
        默认 'ADDITIVE'。

    """
    direction: Literal['FORWARD','BACKWARD'] = 'FORWARD' 
    method: Literal['ADDITIVE','MULTIPLICATIVE'] = 'ADDITIVE'

@dataclass(frozen=True)
class SamplingConfig:
    """
    抽样配置类。

    Parameters
    ----------
    imputation_method : {'ffill', 'drop'}, optional
        缺失值处理方法。'ffill' 使用前一个收盘价填充在该时间段内无成交的 K 线；
        'drop' 则删除该行（默认：'ffill'）。
    alignment : {'strict', 'lazy'}, optional
        时间轴对齐模式。'strict' 强制对齐为标准交易时间戳以避免因提前收盘导致的时间错位；
        'lazy' 则更宽松（默认：'strict'）。
    include_night_session : bool, optional
        是否包含夜盘数据(默认: True)。

    """
    imputation_method: Literal['ffill','drop'] = 'ffill' # 缺失值处理，如果某一根 15min K线全市场无成交（涨跌停时常见），是填补上一根收盘价 (ffill)，还是直接丢弃这行 (drop))
    alignment: Literal['strict','lazy'] = 'strict'       # 时间轴对齐模式，是否严格对齐标准物理时间轴（防止因某天提早收盘导致时间戳错位）
    include_night_session: bool = True                   # 保留夜盘开关

@dataclass(frozen=True)
class EnvironmentConfig:
    """
    环境配置类。

    Parameters
    ----------
    product_meta_dir : str, optional
        规则数据库tier1——品种元数据的目录路径。
    instrument_registry_dir: str, optional
        规则数据库tier2——合约注册层数据的目录路径。
    daily_status_dir: str, optional
        规则数据库tier3——日频动态状态层数据的目录路径。
    exchange_holiday_dir: str, optional
        交易所节假日安排数据的路径。
    
    data_dir_1min : str, optional
        存放 1 分钟 K 线数据的目录路径。默认使用仓库同级目录
        ``RAW_DATA/1min``；设置环境变量
        ``EARLY_BIRD_HIGH_FREQ_RAW_DATA_ROOT`` 后，使用该根目录下的
        ``1min`` 子目录。
    data_dir_tick: str, optinal
        存放原始 TICK 数据的目录路径。默认使用仓库同级目录
        ``RAW_DATA/TICK``；设置环境变量
        ``EARLY_BIRD_HIGH_FREQ_RAW_DATA_ROOT`` 后，使用该根目录下的
        ``TICK`` 子目录。
    daily_snapshot_dir : str, optional
        存放日频快照数据的目录路径。
    warehouse_receipt_dir : str, optional
        存放仓单日报基本面数据的目录路径。
    
    master_table_dir : str, optional
        存放换月 master table 的目录路径。
    calendar_dir : str, optional
        存放交易日历文件的目录路径。
    
    bar_loader_max_cache_size : int, optional
        `BarLoader` 的最大 LRU 缓存大小（条目数）。

    """
    # ── 三层期货交易规则数据库路径 ──────────────────────────────
    # tier1
    product_meta_dir: str = field(
        default_factory=lambda: _database_path('rules', 'product_meta'))
    # tier2
    instrument_registry_dir: str = field(
        default_factory=lambda: _database_path('rules', 'instrument_registry'))
    # tier3 
    daily_status_dir: str = field(
        default_factory=lambda: _database_path('rules', 'instrument_daily_status'))
    # exchange-level holiday/no-night arrangements
    exchange_holiday_dir: str = field(
        default_factory=lambda: _database_path('rules', 'exchange_holiday'))
    # product classification
    product_classification_dir: str = field(
        default_factory=lambda: _database_path('rules', 'product_classification'))
    
    # ── 行情数据路径 ──────────────────────────────────────────
    data_dir_1min: str = field(
        default_factory=lambda: _raw_data_path('1min'))
    data_dir_tick: str = field(
        default_factory=lambda: _raw_data_path('TICK'))
    daily_snapshot_dir: str = field(default_factory=lambda: _database_path(
        'market', 'daily_snapshots', 'bar_1d', 'bar_1d_tushare', 'cleaned'))

    # ── 基本面数据路径 ────────────────────────────────────────
    warehouse_receipt_dir: str = field(
        default_factory=lambda: _database_path(
            'fundamental', 'warehouse_receipts'))
    
    # ── 其他数据路径 ──────────────────────────────────────────
    master_table_dir: str = field(
        default_factory=lambda: _database_path('derived', 'master_tables'))
    calendar_dir: str = field(
        default_factory=lambda: _database_path(
            'calendar', 'trading_calendars'))
    
    # ── tool attribute ───────────────────────────────────────
    bar_loader_max_cache_size: int = 200                        # BarLoader 最大 LRU 缓存数


__all__ = [
    'RolloverConfig',
    'SamplingConfig',
    'AdjustmentConfig',
    'EnvironmentConfig'
]
