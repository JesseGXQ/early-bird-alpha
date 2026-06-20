from dataclasses import dataclass
from typing import Literal

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
        复权方向。'BACKWARD'（默认）表示锚定最新价格并调整历史价格（前向调整法）；
        'FORWARD' 表示后向调整法。
    method : {'ADDITIVE', 'MULTIPLICATIVE'}, optional
        复权计算方法。'ADDITIVE'（默认）表示等差调整；'MULTIPLICATIVE' 表示等比调整。

    """
    direction: Literal['FORWARD','BACKWARD'] = 'BACKWARD'     # 复权方向，默认前向调整法：锚定最新价格不变，调整历史合约价格。英文为 Backward Adjustment。
    method: Literal['ADDITIVE','MULTIPLICATIVE'] = 'ADDITIVE' # 复权计算方法，默认等差法。

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
        是否包含夜盘数据（默认：True）。

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
    
    data_dir_1min : str, optional
        存放 1 分钟 K 线数据的目录路径。
    data_dir_tick: str, optinal
        存放原始 TICK 数据的目录路径。
    daily_snapshot_dir : str, optional
        存放日频快照数据的目录路径。
    
    master_table_dir : str, optional
        存放换月 master table 的目录路径。
    calendar_dir : str, optional
        存放交易日历文件的目录路径。
    
    bar_loader_max_cache_size : int, optional
        `BarLoader` 的最大 LRU 缓存大小（条目数）。

    """
    # ── 三层期货交易规则数据库路径 ──────────────────────────────
    # tier1
    product_meta_dir: str = './db/reference/product_meta'
    # tier2
    instrument_registry_dir: str = './db/reference/instrument_registry'
    # tier3 
    daily_status_dir: str = './db/reference/instrument_daily_status'
    # exchange-level holiday/no-night arrangements
    exchange_holiday_dir: str = './db/reference/exchange_holiday'

    # ── 行情数据路径 ──────────────────────────────────────────
    data_dir_1min: str = './db/market/bar_1min'              # 1min 数据储存路径
    data_dir_tick: str = './db/market/tick'                  # TICK 数据储存路径
    daily_snapshot_dir: str = './db/market/daily_snapshots' # 日频快照数据储存位置
    
    # ── 其他数据路径 ──────────────────────────────────────────
    master_table_dir: str = './db/derived/master_tables'     # 换月总表储存位置
    calendar_dir: str = './db/calendar/trading_calendars'     # 交易日历储存位置
    
    # ── tool attribute ───────────────────────────────────────
    bar_loader_max_cache_size: int = 200                        # BarLoader 最大 LRU 缓存数


