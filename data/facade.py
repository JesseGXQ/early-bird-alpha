from __future__ import annotations
from typing import TYPE_CHECKING, Literal

from data.config import (
    AdjustmentConfig, EnvironmentConfig, 
    RolloverConfig, SamplingConfig
)
from data.factory import create_research_feed, create_simulation_feed, create_term_structure_feed
from data.query_service import RuleQueryService

if TYPE_CHECKING:
    from data.feed.research_feed import ResearchFeed 
    from data.feed.simulation_feed import SimulationFeed
    from data.feed.term_structure_feed import TermStructureFeed
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
    get_research_feed(product)
        获取 ResearchFeed 实例。
    get_simulation_feed(product)
        获取 SimulationFeed 实例。
    get_historical_vector(product, start_date, end_date, freq, view)
        获取复权主连历史价格 DataFrame 。
    get_snapshot_stream(product, start_date, end_date)
        获取逐笔市场快照数据流。
    get_term_structure_data(product, start_date, end_date)
        获取指定品种的期限结构长表 DataFrame 。
    
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

        self.rule_query = RuleQueryService(env_config=self._env_config)

        self._research_feeds: dict[str, ResearchFeed] = {} # 内部懒加载的 ResearchFeed 实例缓存
        self._simulation_feeds: dict[str, SimulationFeed] = {} # 内部懒加载的 SimulationFeed 实例缓存
        self._term_structure_feeds: dict[str, TermStructureFeed] = {} # 内部懒加载的 TermStructureFeed 实例缓存

    def get_research_feed(self, product: str) -> ResearchFeed: 
        """
        返回一个 ResearchFeed 实例，供获取历史复权价格向量。
        """
        if product not in self._research_feeds:
            self._research_feeds[product] = create_research_feed(
                product=product,
                rollover_config=self._rollover_config,
                adj_config=self._adj_config,
                sample_config=self._sample_config,
                env_config=self._env_config
            )
        return self._research_feeds[product]

    def get_simulation_feed(self, product: str) -> SimulationFeed:
        """
        返回一个 SimulationFeed 实例，供获取逐笔快照数据流。
        """
        if product not in self._simulation_feeds:
            self._simulation_feeds[product] = create_simulation_feed(
                product=product,
                rollover_config=self._rollover_config,
                env_config=self._env_config
            )
        return self._simulation_feeds[product]
    
    def get_historical_vector(
            self,
            product: str,
            start_date: date,
            end_date: date,
            freq: str = '1d',
            view: Literal['scheduled', 'realized']='scheduled') -> pd.DataFrame:
        """
        针对某品种，获取指定日期范围内，特定频率的历史复权主力合约价格。

        Parameters
        ------
        product: str
            品种代码，例如'RB'。
        start_date: datetime.date
            起始日期（交易日）
        end_date: datetime.date
            结束日期（交易日）
        freq: str, default "1d"
            K 线频率，格式同 Pandas Offset aliases 保持一致。
            例: 1min, 5min, 15min, 30min, 1h, 2h, 4h, 1d。
            默认1d, 即日频。
        view: Literal['scheduled', 'realized'], default 'scheduled'
            计划或实际视图，默认计划视图。
        
        Returns
        ------
        pd.DataFrame
            复权后主力合约(连续)价格的 pandas DataFrame ，每一行对应一根 K 线 。
        """
        r_feed = self.get_research_feed(product)
        return r_feed.get_historical_vector(
            start_date=start_date,
            end_date=end_date,
            freq=freq,
            view=view
        )
    
    def get_snapshot_stream(
            self,
            product: str,
            start_date: date,
            end_date: date
    )-> Generator[MarketSnapshot, None, None]: 
        """
        针对某品种，获取指定日期范围内的逐笔市场快照数据流。

        Parameters
        ------
        product: str
            品种代码，例如'RB'。
        start_date: datetime.date
            起始日期（交易日）
        end_date: datetime.date
            结束日期（交易日）
        
        Yields
        ------
        MarketSnapshot
            包含主力合约的市场快照对象（若换月期，为新旧主力合约），严格遵循时间顺序下发。
        """
        s_feed = self.get_simulation_feed(product)
        return s_feed.get_snapshot_stream(
            start_date=start_date,
            end_date=end_date
        )
    
    def get_term_structure_data(
            self,
            product: str,
            start_date: date,
            end_date: date,
    ) -> pd.DataFrame:
        """
        针对某品种，获取指定日期范围内的期限结构长表数据。

        返回全部上市合约的每日结算价、成交量、持仓量，
        并标注主力合约标识和合约最后交易日。

        Parameters
        ------
        product: str
            品种代码，例如 'RB'。
        start_date: datetime.date
            起始交易日。
        end_date: datetime.date
            结束交易日。

        Returns
        -------
        pd.DataFrame
            期限结构长表，MultiIndex[trading_date, contract]。
            列: product, dominant, Settle, last_trade_date, OpenInterest, Volume 。
        """
        if product not in self._term_structure_feeds:
            self._term_structure_feeds[product] = create_term_structure_feed(
                product=product,
                rollover_config=self._rollover_config,
                env_config=self._env_config,
            )
        ts_feed = self._term_structure_feeds[product]
        return ts_feed.get_term_structure_data(
            start_date=start_date,
            end_date=end_date,
        )

# ==================================
# inline testing
# ==================================
if __name__ == '__main__':

    facade = DataFacade()

    # ------------------------ query rule -----------------------------
    product = 'AP'
    print(f'品种 {product} 的上市日期为 {facade.rule_query.get_list_date_product(product)}。')


    # -------------------------ResearchFeed------------------------
    # rb
    # freq=1d
    from datetime import date
    product = 'rb'
    freq='1d'
    start_date = date(2009, 7, 20)
    end_date = date(2025, 10, 18)
    df1 = facade.get_historical_vector(product,start_date, end_date, freq='1d')
    print(f'product = {product}, from {start_date} to {end_date}, freq={freq}, 复权主连历史价格dataframe: \n', df1.head())
    print(f'数据集的列为: {df1.columns.tolist()}')
    # freq='5min'
    # freq='5min'
    # df2 = facade.get_historical_vector(product, start_date, end_date, freq='5min')
    # print(f'product = {product}, from {start_date} to {end_date}, freq={freq}, 复权主连历史价格dataframe: \n', df2.head())


    # -------------------------SimulationFeed------------------------
    # product = 'TF'
    # start_date = date(2026, 1, 5)
    # end_date = date(2026, 1, 9)
    # stream = facade.get_snapshot_stream(product, start_date, end_date)
    # for i, snapshot in enumerate(stream):
    #     if i<=10:
    #         print(snapshot)
    #     else:
    #         break
    
    #print('\n\n Test of DataFacade: ALL DONE :) !')


    # -------------------------TermStructureFeed------------------------
    from datetime import date as dt_date
    product_ts = 'AP'
    start_ts = dt_date(2021, 8, 1)
    end_ts = dt_date(2021, 8, 10)
    df_ts = facade.get_term_structure_data(product_ts, start_ts, end_ts)
    print(f'\n=== TermStructureFeed: {product_ts}, {start_ts} ~ {end_ts} ===')
    print(df_ts.to_string())
    print(f'\n索引名称: {df_ts.index.names}')
    print(f'列: {df_ts.columns.tolist()}')
    print(f'行数: {len(df_ts)}')




    

    