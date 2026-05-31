from __future__ import annotations
from typing import TYPE_CHECKING

from data.config import (
    AdjustmentConfig, EnvironmentConfig, 
    RolloverConfig, SamplingConfig
)
from data.factory import create_research_feed, create_simulation_feed
from data.feed.models import MarketSnapshot

if TYPE_CHECKING:
    from data.feed.research_feed import ResearchFeed 
    from data.feed.simulation_feed import SimulationFeed
    from data.feed.models import MarketSnapshot
    from datetime import date
    import pandas as pd
    from typing import Generator

class DataFacade:
    """
    Data Engine 的统一对外接口。

    Parameters
    ------
    rollover_config: RolloverConfig, optional
        换月配置，默认标准配置。
    adj_config: AdjustmentConfig, optional
        复权配置，默认标准配置。
    sample_config: SamplingConfig, optional
        抽样配置，默认标准配置。
    env_config: EnvironmentConfig, optional
        环境配置，默认标准配置。
    
    Methods
    ------
    get_research_feed(symbol_root)
        获取 ResearchFeed 实例。
    get_simulation_feed(symbol_root)
        获取 SimulationFeed 实例。
    get_historical_vector(symbol_root, start_date, end_date, freq)
        获取历史复权价格向量。
    get_snapshot_stream(symbol_root, start_date, end_date)
        获取逐笔市场快照数据流。
    
    """

    def __init__(
            self,
            rollover_config: RolloverConfig = RolloverConfig(),
            adj_config: AdjustmentConfig = AdjustmentConfig(),
            sample_config: SamplingConfig = SamplingConfig(),
            env_config: EnvironmentConfig = EnvironmentConfig(),
    ):
        self._rollover_config = rollover_config
        self._adj_config = adj_config
        self._sample_config = sample_config
        self._env_config = env_config

        self._research_feeds: dict[str, ResearchFeed] = {} # 内部懒加载的 ResearchFeed 实例缓存
        self._simulation_feeds: dict[str, SimulationFeed] = {} # 内部懒加载的 SimulationFeed 实例缓存

    def get_research_feed(self, symbol_root: str) -> ResearchFeed: 
        """
        返回一个 ResearchFeed 实例，供获取历史复权价格向量。
        """
        if symbol_root not in self._research_feeds:
            self._research_feeds[symbol_root] = create_research_feed(
                symbol_root=symbol_root,
                rollover_config=self._rollover_config,
                adj_config=self._adj_config,
                sample_config=self._sample_config,
                env_config=self._env_config
            )
        return self._research_feeds[symbol_root]

    def get_simulation_feed(self, symbol_root: str) -> SimulationFeed:
        """
        返回一个 SimulationFeed 实例，供获取逐笔快照数据流。
        """
        if symbol_root not in self._simulation_feeds:
            self._simulation_feeds[symbol_root] = create_simulation_feed(
                symbol_root=symbol_root,
                rollover_config=self._rollover_config,
                env_config=self._env_config
            )
        return self._simulation_feeds[symbol_root]
    
    def get_historical_vector(
            self,
            symbol_root: str,
            start_date: date,
            end_date: date,
            freq: str = '1d') -> pd.DataFrame:
        """
        针对某品种，获取指定日期范围内，特定频率的历史复权主力合约价格。

        Parameters
        ------
        symbol_root: str
            品种代码前缀，例如'RB'。
        start_date: datetime.date
            起始日期（交易日）
        end_date: datetime.date
            结束日期（交易日）
        freq: str, default "1d"
            K 线频率，格式同 Pandas Offset aliases 保持一致。
            例: 1min, 5min, 15min, 30min, 1h, 2h, 4h, 1d。
            默认1d, 即日频 。
        
        Returns
        ------
        pd.DataFrame
            复权后主力合约(连续)价格，每一行对应一个 Bar 。
        """
        r_feed = self.get_research_feed(symbol_root)
        return r_feed.get_historical_vector(
            start_date=start_date,
            end_date=end_date,
            freq=freq
        )
    
    def get_snapshot_stream(
            self,
            symbol_root: str,
            start_date: date,
            end_date: date
    )-> Generator[MarketSnapshot, None, None]: 
        """
        针对某品种，获取指定日期范围内的逐笔市场快照数据流。

        Parameters
        ------
        symbol_root: str
            品种代码前缀，例如'RB'。
        start_date: datetime.date
            起始日期（交易日）
        end_date: datetime.date
            结束日期（交易日）
        Yields
        ------
        MarketSnapshot
            包含主力合约的市场快照对象（若换月期，为新旧主力合约），严格遵循时间顺序下发。
        """
        s_feed = self.get_simulation_feed(symbol_root)
        return s_feed.get_snapshot_stream(
            start_date=start_date,
            end_date=end_date
        )
    