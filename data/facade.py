from __future__ import annotations
from typing import TYPE_CHECKING, Literal, Mapping

import pandas as pd

from data.config import (
    AdjustmentConfig, EnvironmentConfig, 
    RolloverConfig, SamplingConfig
)
from data.factory import (
    create_fundamental_feed,
    create_research_feed,
    create_simulation_feed,
    create_term_structure_feed,
)
from data.query_service import RuleQueryService

if TYPE_CHECKING:
    from data.feed.research_feed import ResearchFeed 
    from data.feed.simulation_feed import SimulationFeed
    from data.feed.term_structure_feed import TermStructureFeed
    from data.feed.fundamental_feed import FundamentalFeed
    from data.feed.models import MarketEvent, MarketSnapshot
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
    get_fundamental_feed(product)
        获取 FundamentalFeed 实例。
    get_historical_vector(product, start_date=None, end_date=None, freq, view)
        获取复权主连历史价格 DataFrame 。
    get_historical_vector_multiple_products(products, start_date=None, end_date=None, freq, view)
        多品种批量获取复权主连历史价格面板 DataFrame 。
    get_snapshot_stream(product, start_date, end_date)
        获取仅含逐笔市场快照的数据流，遇无主力日时报错。
    get_market_event_stream(product, start_date, end_date)
        获取含逐笔快照和无主力控制事件的混合流。
    get_term_structure_data(product, start_date=None, end_date=None)
        获取指定品种的期限结构长表 DataFrame 。
    get_term_structure_data_multiple_products(products, start_date=None, end_date=None)
        多品种批量获取期限结构长表面板 DataFrame 。
    get_warehouse_receipt_data(product, start_date=None, end_date=None, aggregate=False, agg_unit_conversions=None)
        获取指定品种的仓单日报明细或品种日聚合数据。
    get_warehouse_receipt_data_multiple_products(products, start_date=None, end_date=None, aggregate=False, agg_unit_conversions=None)
        多品种批量获取仓单日报明细或品种日聚合数据。

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
        self._fundamental_feeds: dict[str, FundamentalFeed] = {} # 内部懒加载的 FundamentalFeed 实例缓存

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

    def get_fundamental_feed(self, product: str) -> FundamentalFeed:
        """返回指定品种的基本面数据馈送器。"""
        product = product.upper()
        if product not in self._fundamental_feeds:
            self._fundamental_feeds[product] = create_fundamental_feed(
                product=product,
                env_config=self._env_config,
            )
        return self._fundamental_feeds[product]

    def get_warehouse_receipt_data(
        self,
        product: str,
        start_date: date | None = None,
        end_date: date | None = None,
        aggregate: bool = False,
        agg_unit_conversions: Mapping[tuple[str, str, str], float] | None = None,
    ) -> pd.DataFrame:
        """
        获取指定品种、指定日期闭区间内的仓单日报数据。

        aggregate=False 时返回仓库级明细；aggregate=True 时先按
        agg_unit_conversions 将完整查询区间的单位统一，再按“品种-交易日”
        加总。转换键为 (品种, 原单位, 目标单位)，系数直接乘于数量。
        """
        feed = self.get_fundamental_feed(product)
        return feed.get_warehouse_receipt_data(
            start_date=start_date,
            end_date=end_date,
            aggregate=aggregate,
            agg_unit_conversions=agg_unit_conversions,
        )

    def get_warehouse_receipt_data_multiple_products(
        self,
        products: list[str],
        start_date: date | None = None,
        end_date: date | None = None,
        aggregate: bool = False,
        agg_unit_conversions: Mapping[tuple[str, str, str], float] | None = None,
    ) -> pd.DataFrame:
        """串行获取多个品种的仓单日报明细或品种日聚合数据。"""
        if not products:
            raise ValueError("products 列表不能为空。")

        results: dict[str, pd.DataFrame] = {}
        for product in products:
            try:
                results[product.upper()] = self.get_warehouse_receipt_data(
                    product=product,
                    start_date=start_date,
                    end_date=end_date,
                    aggregate=aggregate,
                    agg_unit_conversions=agg_unit_conversions,
                )
            except Exception as exc:
                raise RuntimeError(f"品种 {product} 获取仓单数据失败。") from exc

        frames = [results[product] for product in sorted(results)]
        result = pd.concat(frames, ignore_index=True)
        sort_columns = ["trading_date", "product"]
        if not aggregate:
            sort_columns.append("warehouse")
        result.sort_values(
            sort_columns,
            na_position="last",
            kind="stable",
            inplace=True,
        )
        result.reset_index(drop=True, inplace=True)
        return result
    
    def get_historical_vector(
            self,
            product: str,
            start_date: date | None = None,
            end_date: date | None = None,
            freq: str = '1d',
            view: Literal['scheduled', 'realized']='scheduled') -> pd.DataFrame:
        """
        针对某品种，获取指定日期范围内，特定频率的复权主力合约历史价格。

        Parameters
        ------
        product: str
            品种代码，例如'RB'。
        start_date: datetime.date or None, optional
            起始日期（交易日），默认None表示最早可用日期。
        end_date: datetime.date or None, optional
            结束日期（交易日），默认None表示最晚可用日期。
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
        针对某品种，获取指定日期范围内的仅快照数据流。

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

        Notes
        -----
        保持仅输出 ``MarketSnapshot`` 的旧接口契约。遇无主力控制事件时，
        底层 Feed 会抛出 ``NoDominantContractError``，生成器随之终止。
        需要接收控制事件并继续后续交易日时，使用 ``get_market_event_stream``。
        """
        s_feed = self.get_simulation_feed(product)
        return s_feed.get_snapshot_stream(
            start_date=start_date,
            end_date=end_date
        )

    def get_market_event_stream(
            self,
            product: str,
            start_date: date,
            end_date: date,
    ) -> Generator[MarketEvent, None, None]:
        """获取逐笔快照与无主力控制事件组成的混合流。

        Parameters
        ----------
        product : str
            品种代码。
        start_date : datetime.date
            起始交易日。
        end_date : datetime.date
            结束交易日。

        Yields
        ------
        MarketSnapshot | NoDominantEvent
            有主力时输出市场快照；无主力时输出逐日控制事件。

        Notes
        -----
        换月表当日主力为空时，Feed 发出一条 ``NoDominantEvent``，不生成
        该日市场快照，然后继续后续交易日。下游收到事件后自行决定持仓与
        回测状态的处理方式；本接口不静默丢弃异常日期。
        """
        s_feed = self.get_simulation_feed(product)
        return s_feed.get_market_event_stream(start_date=start_date, end_date=end_date)
    
    def get_term_structure_data(
            self,
            product: str,
            start_date: date | None = None,
            end_date: date | None = None,
            price_col: Literal['Close', 'Settle'] = 'Close',
    ) -> pd.DataFrame:
        """
        针对某品种，获取指定日期范围内的期限结构长表数据。

        返回全部上市合约的每日价格、成交量、持仓量，
        并标注主力合约标识和合约最后交易日。

        Parameters
        ------
        product: str
            品种代码，例如 'RB'。
        start_date: datetime.date or None, default None
            起始交易日，默认None表示最早可用日期。
        end_date: datetime.date or None, default None
            结束交易日，默认None表示最晚可用日期。
        price_col: {'Close', 'Settle'}, default 'Close'
            价格变量口径。为 'Close' 时使用收盘价，为 'Settle' 时使用结算价；
            输出的价格列以所选口径命名。

        Returns
        -------
        pd.DataFrame
            期限结构长表，MultiIndex[trading_date, contract]。
            列: product, is_dominant, Close/Settle, last_trade_date,
            OpenInterest, Volume, multiplier。其中价格列列名与 price_col 一致。
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
            price_col=price_col,
        )

    def get_term_structure_data_multiple_products(
            self,
            products: list[str],
            start_date: date | None = None,
            end_date: date | None = None,
            price_col: Literal['Close', 'Settle'] = 'Close',
    ) -> pd.DataFrame:
        """
        多品种批量获取期限结构长表数据。

        Parameters
        ------
        products : list[str]
            品种代码列表，例如 ['RB', 'TA']。
        start_date : datetime.date or None, default None
            起始交易日。默认为 None，各品种最早可用交易日。
        end_date : datetime.date or None, default None
            结束交易日。默认为 None，各品种最新可用交易日。
        price_col : {'Close', 'Settle'}, default 'Close'
            价格变量口径。为 'Close' 时使用收盘价，为 'Settle' 时使用结算价；
            输出的价格列以所选口径命名。

        Returns
        ------
        pd.DataFrame
            MultiIndex[trading_date, contract] 的长表面板，
            列: product, is_dominant, Close/Settle, last_trade_date,
            OpenInterest, Volume, multiplier。其中价格列列名与 price_col 一致。
        """
        if not products:
            raise ValueError('products 列表不能为空。')

        dfs: list[pd.DataFrame] = []
        for product in products:
            try:
                df_product = self.get_term_structure_data(
                    product=product,
                    start_date=start_date,
                    end_date=end_date,
                    price_col=price_col,
                )
            except Exception as e:
                raise RuntimeError(f'品种 {product} 获取数据失败。') from e
            dfs.append(df_product)

        df = pd.concat(dfs, axis=0)
        df.sort_index(inplace=True)

        return df

    def get_historical_vector_multiple_products(
            self,
            products: list[str],
            start_date: date | None = None,
            end_date: date | None = None,
            freq: str = '1d',
            view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> pd.DataFrame:
        """
        多品种批量获取复权主力合约历史价格数据。

        内部复用 `get_historical_vector`，按 `products` 顺序串行取数，
        最后拼接为统一的长表面板。

        Parameters
        ------
        products : list[str]
            品种代码列表，例如 ['RB', 'TA', 'MA']。
        start_date : datetime.date, optional
            起始交易日，默认 None 为各品种最早可用日期。
        end_date : datetime.date, optional
            结束交易日，默认 None 为各品种最晚可用日期。
        freq : str, default '1d'
            K 线频率，同 Pandas Offset aliases。
        view : Literal['scheduled', 'realized'], default 'scheduled'
            计划或实际视图。

        Returns
        ------
        pd.DataFrame
            MultiIndex[calendar_time, product] 的长表面板，
            列与 `get_historical_vector` 输出一致。
        """
        if not products:
            raise ValueError('products 列表不能为空。')

        dfs: list[pd.DataFrame] = []
        for product in products:
            try:
                df_product = self.get_historical_vector(
                    product=product,
                    start_date=start_date,
                    end_date=end_date,
                    freq=freq,
                    view=view,
                )
            except Exception as e:
                raise RuntimeError(f'品种 {product} 获取数据失败。') from e
            df_product['product'] = product
            dfs.append(df_product)

        df = pd.concat([df for df in dfs if not df.empty], axis=0)
        df.set_index('product', append=True, inplace=True)
        df.sort_index(inplace=True)

        return df

# ==================================
# inline testing
# ==================================
if __name__ == '__main__':
    from datetime import date
    facade = DataFacade()

    # ------------------------ query rule -----------------------------
    # product = 'JR'
    # print(f'品种 {product} 的上市日期为 {facade.rule_query.get_list_date_product(product)}。')

    # # 测试 rule_query.classification
    # print('品种分类 DataFrame 为: \n ', facade.rule_query.classification.get_classification())
    # print('品种按 sector 分组 的 dummy variable DataFrame: \n ',facade.rule_query.classification.get_dummy_matrix(level='sector'))

    # -------------------------ResearchFeed（错误排查）------------------------
    # from datetime import date
    # product = 'AD'
    # freq='1d'
    # start_date = None
    # end_date = None
    # df1 = facade.get_historical_vector(product,start_date, end_date, freq='1d')
    # print(f'product = {product}, from {start_date} to {end_date}, freq={freq}, 复权主连历史价格dataframe: \n', df1.head())
    # print(f'数据集的列为: {df1.columns.tolist()}')
    

    # -------------------------ResearchFeed------------------------
    # from datetime import date
    products = ['JR','PD']
    freq='1d'
    start_date = None
    end_date = None
    df1 = facade.get_historical_vector_multiple_products(products,start_date, end_date, freq='1d')
    print(f'product = {products}, from {start_date} to {end_date}, freq={freq}, 复权主连历史价格dataframe: \n', df1.head())
    print('columns of returned DataFrame: \n', df1.columns)
    print(f'nlevels of index: {df1.index.nlevels}')
    
    # ------
    # freq='5min'
    # df2 = facade.get_historical_vector_multiple_products(products, start_date, end_date, freq='5min')
    # print(f'product = {products}, from {start_date} to {end_date}, freq={freq}, 复权主连历史价格dataframe: \n', df2.head())
    # print('columns of returned DataFrame: \n', df2.columns)


    # print('Intersection of columns: \n', df1.columns.intersection(df2.columns))

    # start_date 和 end_date 逻辑测试
    # freq='1d'
    # products = ['PM']
    # start_date = date(2008, 1, 25)
    # end_date = date(2026,10,25)
    # df3 = facade.get_historical_vector_multiple_products(products,start_date, end_date, freq='1d')
    # print(f'product = {products}, from {start_date} to {end_date}, freq={freq}, 复权主连历史价格dataframe: \n', df3.head())

    # start_date = date(1978, 1, 25)
    # end_date = date(1975,10,25)
    # df4 = facade.get_historical_vector_multiple_products(products,start_date, end_date, freq='1d')
    # print(f'product = {products}, from {start_date} to {end_date}, freq={freq}, 复权主连历史价格dataframe: \n', df4.head())



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
    # from datetime import date as dt_date
    # product_ts = 'AP'
    # start_ts = dt_date(2020, 8, 1)
    # end_ts = dt_date(2021, 8, 10)
    # df_ts = facade.get_term_structure_data(product_ts, start_ts, end_ts)
    # print(f'\n=== TermStructureFeed: {product_ts}, {start_ts} ~ {end_ts} ===')
    # print(df_ts.to_string())
    # print(f'\n索引名称: {df_ts.index.names}')
    # print(f'列: {df_ts.columns.tolist()}')
    # print(f'行数: {len(df_ts)}')

    # ----------------- get_historical_vector_multiple_products ------------------
    # from datetime import date as dt_date

    # print('\n' + '=' * 60)
    # print('多品种批量复权主连测试')
    # print('=' * 60)

    # test_products = ['AP', 'RB', 'FG', 'CF']
    # test_start = None
    # test_end = dt_date(2021, 12, 31)

    # df_multi = facade.get_historical_vector_multiple_products(
    #     products=test_products,
    #     start_date=test_start,
    #     end_date=test_end,
    #     freq='1d',
    # )

    # print(f'\n多品种面板: {len(df_multi)} 行')
    # print(f'索引层级: {df_multi.index.names}')
    # print(f'各品种样本数:')
    # for p in test_products:
    #     count = len(df_multi.xs(p, level='product')) if p in df_multi.index.get_level_values('product') else 0
    #     print(f'  {p}: {count}')
    # print(f'\n前 10 行:')
    # print(df_multi.head(10).to_string(line_width=80))
    # print(f'\n列名: {df_multi.columns.tolist()}')
    # print("MultiIndex level 0 (trading_date), first element type is:", type(df_multi.index.get_level_values(0)[0]))

    # # ----------------- get_term_structure_data_multiple_products ------------------
    # from datetime import date as dt_date
    # print('\n' + '=' * 60)
    # print('多品种批量期限结构测试')
    # print('=' * 60)

    # ts_products = ['AP', 'RB']
    # ts_start = dt_date(2021, 7, 25)
    # ts_end = dt_date(2024, 8, 10)

    # df_ts_multi = facade.get_term_structure_data_multiple_products(
    #     products=ts_products,
    #     start_date=ts_start,
    #     end_date=ts_end,
    # )

    # print(f'\n多品种期限结构面板: {len(df_ts_multi)} 行')
    # print(f'索引层级: {df_ts_multi.index.names}')
    # print(f'列名: {df_ts_multi.columns.tolist()}')
    # print(f'各品种样本数:')
    # for p in ts_products:
    #     mask = df_ts_multi['product'] == p
    #     print(f'  {p}: {mask.sum()}')

    # idx_df = df_ts_multi.index.to_frame()
    # idx_df.insert(loc=1, column='product', value=df_ts_multi['product'])
    # idx_df.rename(columns={'trading_date':'timestamp'}, inplace=True)
    # df_ts_multi.index = pd.MultiIndex.from_frame(idx_df)

    # print(f'\n DataFrame 头部:')
    # print(df_ts_multi.head(50).to_string())
    # print("MultiIndex level 0 (trading_date), first element type is:", type(df_ts_multi.index.get_level_values(0)[0]))

    
