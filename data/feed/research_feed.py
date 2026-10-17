from __future__ import annotations
import pandas as pd
from typing import TYPE_CHECKING, Literal
from datetime import date

from tqdm import tqdm

from data.stitching.models import RollState
from utils.validation_utils import check_df_columns_exist
from data.feed.utils import filter_date_range

if TYPE_CHECKING:
    from data.cleaning.cleaning_tick import CleaningPipeline
    from data.stitching.stitching import ContinuousContractManager
    from data.rules.product_meta_manager import ProductMetaManager
    from data.storage.daily_snapshot_manager import DailySnapshotManager
    from data.calendar.product_calendar import ProductCalendar
    from data.feed.aggregator import BarAggregator
    from data.storage.file_locator import FileLocator
    from data.storage.bar_loader import BarLoader
    from data.config import AdjustmentConfig

RFEED_1d_COLUMNS = [
    'Dominant_Contract', 'Price_Gap', 'Adj_Factor', 'roll_state', 'is_roll',
    'is_contract_switch', 'continuity_segment_id', 'Raw_Open', 'Raw_High',
    'Raw_Low', 'Raw_Close', 'Volume', 'OpenInterest', 'trading_date',
    'Adj_Open', 'Adj_High', 'Adj_Low', 'Adj_Close', 'True_Return'
]

class ResearchFeed:
    """
    投研价格向量提取器(The "Vector Extractor")

    将主力合约映射表和物理合约价格匹配，并根据复权因子调整，生成复权价格。
    同时，提供“真实收益率”(True Return)的全量历史时间序列。

    Parameters
    ------
    product: str
        品种代码，例如'RB'。
    contract_manager: ContinuousContractManager
        连续合约大管家，包含 master_table 属性(换月总表)，有 Dominant_Contract, Price_Gap 列。
    meta_manager: ProductMetaManager
        品种元数据管家，提供品种的交易时间、交易小节划分等元信息查询功能。
    daily_snapshot_manager: DailySnapshotManager
        日频行情快照管家，用于提取日频主力行情及切换日的新主力前收盘价。
    calendar: ProductCalendar
        交易日历实例，提供交易日、交易小节、分钟偏移等查询功能。
    file_locator: FileLocator
        文件寻址器，提供根据合约代码和日期查询 1min 数据文件路径的功能。
    bar_loader: BarLoader
        K 线数据加载器，提供读取 1min 原始数据并返回 DataFrame 的功能。
    cleaning_pipeline: CleaningPipeline
        分钟行情清洗管道。
    bar_aggregator: BarAggregator
        K 线降频采样聚合器，提供将 1min 数据聚合成其他频率数据的功能。
    adj_config: AdjustmentConfig
        复权配置参数，包含复权方向(direction)和复权计算方法(method)。

    Methods
    ------
    get_historical_vector(start_date, end_date, freq, view)
        返回指定日期范围内，特定频率的历史复权主力合约的 K 线数据。

    """

    def __init__(
            self, 
            product: str,
            contract_manager: ContinuousContractManager, 
            meta_manager: ProductMetaManager, 
            daily_snapshot_manager: DailySnapshotManager, 
            calendar: ProductCalendar, 
            file_locator: FileLocator, 
            bar_loader: BarLoader, 
            cleaning_pipeline: CleaningPipeline,
            bar_aggregator: BarAggregator,
            adj_config: AdjustmentConfig):

        self.product: str = product.upper()
        self.contract_manager: ContinuousContractManager = contract_manager #包含 master_table 属性(换月总表)，有Dominant_Contract, Price_Gap 列
        self.meta_manager: ProductMetaManager = meta_manager
        self.daily_snapshot_manager: DailySnapshotManager = daily_snapshot_manager
        self.calendar: ProductCalendar = calendar
        self.file_locator: FileLocator = file_locator
        self.bar_loader: BarLoader = bar_loader
        self.cleaning_pipeline: CleaningPipeline = cleaning_pipeline
        self.bar_aggregator: BarAggregator = bar_aggregator
        self.adj_config: AdjustmentConfig = adj_config
        self._research_master_table: pd.DataFrame | None = None

    def get_historical_vector(
            self, 
            start_date: date | None, 
            end_date: date | None,
            freq: str = '1d',
            view: Literal['scheduled', 'realized'] = 'scheduled',
    ) -> pd.DataFrame:
        """
        返回指定日期范围内，指定频率的历史复权主连合约价格。

        Parameters
        ------
        start_date : datetime.date or None
            起始交易日，含义为该交易日完整 Session 的开始，
            即包含前一日历日夜盘。例如传入 2026-04-23 ，实际数据从 2026-04-22 21:00 开始。
            当为 None 时，意为最早可用日期。
        end_date : datetime.date or None
            结束交易日，数据截止至该交易日日盘收盘 15:00。
            当为 None 时，意为最晚可用日期。
        freq : str, default "1d"
            K 线频率，格式同 Pandas Offset aliases 保持一致。
            例: 1min, 5min, 15min, 30min, 1h, 2h, 4h, 1d。
            默认1d ，即日频 。
        view: Literal['scheduled, 'realized'], 默认 'scheduled'。
            计划或实际视图。默认为计划视图。

        Returns
        -------
        pd.DataFrame
            复权后主力合约(连续)价格，每一行对应一个 Bar 。
            若请求日期范围完全不在可用范围内，返回结构化的空 DataFrame；
            若请求日期和可用范围有交集，取两者交集。

        **Index**
        ~~~~~
            - 1d : DatetimeIndex(name='trading_date')
            - other: DatetimeIndex(name='Datetime')
        Columns
        ~~~~~~~
            Dominant_Contract: str
                主力合约（名称）
            Price_Gap: float
                换月时新主力和旧主力的价格差，优先使用成对收盘价，必要时
                成对回退为结算价。
            Adj_Factor: float
                复权因子。
            roll_state: {'roll', 'not_roll', 'unknown'}
                换月过渡窗口的三态判定。
            is_roll: bool
                是否处于换月过渡窗口；主力语义无法确定时为缺失值。
            is_contract_switch: bool
                当日是否为主力合约实际生效切换日；无法判断时为缺失值。
            continuity_segment_id: int
                连续主力分段编号。主力缺失会切断分段，正常换月不会切断分段。
            Raw_Close/ Raw_Open / Raw_High / Raw_Low: float
                主力合约的原始价格OHLC
            Volume: int
                成交量。
            OpenInterest: int
                持仓量。
            trading_date: datetime.date
                交易日期标签
            Adj_Close / Adj_Open / Adj_High / Adj_Low: float
                复权主连价格的OHLC ，根据原始价格和复权因子做了调整。
            True_Return: float
                真实收益率，换月期已特殊处理。
            ------
            以下为 freq != '1d' 时的列
            is_contract_switch_first: bool
                是否换月第一天。
            pre_bar_calendar_timestamp: pd.Timestamp
                前一根K线的日历交易时间戳。
    
        """
        earliest = self.contract_manager.dates_list[0].date()
        latest   = self.contract_manager.dates_list[-1].date()
        start_date, end_date = filter_date_range(
            start_date, end_date, earliest,latest)
        if start_date is None or end_date is None:
            return pd.DataFrame(
                [], columns= RFEED_1d_COLUMNS,
                index=pd.DatetimeIndex([],name='trading_date')
            )

        # 1. 找连续合约大管家索要指定日期范围的物理合约映射表
        master_df_query = self._query_master_table(start_date=start_date, end_date=end_date)
        # 2. Dominant Contracts的原始行情数据收集
        contract_dates_mapping = self._get_contract_dates_mapping(master_df_query=master_df_query)
        # 3. 完全分离日频和非日频的处理路径
        if freq == '1d':
            raw_continuous = self._collect_raw_daily_data(contract_dates_mapping=contract_dates_mapping)
            # 对齐到 master_table 的日期索引
            raw_continuous = raw_continuous.reindex(master_df_query.index)
            # 复权
            re_df = self._adjust_price(raw_continuous,
                                       master_df=master_df_query,
                                       daily_freq=True)
            # 计算 True Return
            re_df = self._calculate_true_return_daily(re_df)
            return re_df[RFEED_1d_COLUMNS]
        else:
            with tqdm(total=5, desc=f'{self.product} 主连复权', unit='step', ncols=80) as pbar:
                pbar.set_postfix_str('读取原始1min数据')
                raw_continuous = self._collect_raw_1min_data(contract_dates_mapping=contract_dates_mapping)
                pbar.update(1)

                if raw_continuous.empty:
                    # 查询区间全部为主力空缺时没有可承载日期语义的分钟 Bar；
                    # 返回列结构完整的空结果，而不是让清洗/聚合阶段误报数据错误。
                    re_df = self._adjust_price(
                        raw_continuous, master_df=master_df_query,
                        daily_freq=False,
                    )
                    re_df = self._calculate_true_return_intraday(
                        re_df, freq=freq, view=view,
                    )
                    pbar.update(4)
                    return re_df

                pbar.set_postfix_str('数据清洗')
                cleaned_continuous = self.cleaning_pipeline.process(raw_continuous)
                pbar.update(1)

                pbar.set_postfix_str('聚合运算')
                resampled_df = self.bar_aggregator.aggregate(
                    raw_df=cleaned_continuous,
                    freq=freq,
                    view=view
                )
                pbar.update(1)

                pbar.set_postfix_str('复权操作')
                re_df = self._adjust_price(resampled_df,
                                           master_df=master_df_query,
                                           daily_freq=False)
                pbar.update(1)

                pbar.set_postfix_str('计算 True Return')
                re_df = self._calculate_true_return_intraday(
                    re_df, freq=freq, view=view)
                pbar.update(1)
            return re_df

    def _query_master_table(self, start_date: date, end_date: date) -> pd.DataFrame:
        """返回指定日期范围、已补充投研语义字段的物理合约映射表。

        Notes
        -----
        复权因子、换月三态、实际切换日与连续主力分段均在完整 master table 上
        计算，随后才按查询区间切片，避免查询边界改变历史结果。
        """
        if self._research_master_table is None:
            self._research_master_table = self._build_research_master_table()
        return self._research_master_table.loc[start_date:end_date].copy()

    def _build_research_master_table(self) -> pd.DataFrame:
        """在完整换月总表上构造投研所需的日期级语义字段。

        Notes
        -----
        目前支持前复权和后复权两种复权方向上的等差复权。

        ``Price_Gap`` 优先采用成对收盘价，必要时成对
        回退为结算价；它位于旧主力的最后一个交易日。

        前复权时，反向累加后得到复权因子。
        后复权时，先对``Price_Gap``取相反数，然后向后移动一个交易日，然后正向累加，
        得到复权因子。
        
        最新日以及主力缺失造成的 ``Price_Gap`` 空值按 0 累加，从而跨断档沿用
        同一复权因子。

        ``roll_state`` 忠实保留 ``ROLL / NOT_ROLL / UNKNOWN`` 三态；``is_roll``
        是其兼容性布尔表达，其中 UNKNOWN 映射为 ``pd.NA``。实际合约切换由
        ``is_contract_switch`` 单独表示。``continuity_segment_id`` 只在主力由
        缺失恢复为有效合约时递增，普通换月不会开启新分段。
        """
        # 检查换月总表：必需列与索引类型
        master = self.contract_manager.master_table.copy().sort_index()
        check_df_columns_exist(master, ['Dominant_Contract', 'Price_Gap'])
        if not isinstance(master.index, pd.DatetimeIndex):
            raise TypeError('【ResearchFeed】换月总表索引必须是 DatetimeIndex。')

        # 检查是否存在主力链连续但价差缺失
        dominant = master['Dominant_Contract']
        next_dominant = dominant.shift(-1)
        unexplained_gap_missing = (
            master['Price_Gap'].isna()
            & dominant.notna()
            & next_dominant.notna()
        )
        if unexplained_gap_missing.any():
            bad_dates = master.index[unexplained_gap_missing].strftime(
                '%Y-%m-%d'
            ).tolist()
            raise ValueError(
                '【ResearchFeed】发现主力链连续但 Price_Gap 缺失的交易日：'
                f'{bad_dates}。仅允许将主力缺失或序列末端造成的价差空值按 0 累计。'
            )
        # 根据复权配置，生成复权因子
        if self.adj_config.direction == 'BACKWARD':
            master['Adj_Factor'] = (
                master['Price_Gap'].fillna(0.0).iloc[::-1].cumsum().iloc[::-1]
            )  
        else: # 'FORWARD'
            # RollGapCalculator.build_master_table 是把 p_new - p_old 记在
            # 旧合约的最后一个交易日。而后复权所需要的是旧合约最后一个
            # 交易日（T-1日）的 p_old - p_new 用于调整 T 日及以后价格。
            # 所以要先取相反数，然后向后平移一个交易日，再延时间正序做累加求和计算
            # 复权因子。
            master['Price_Gap_Basis'] = master['Price_Gap_Basis'].shift(1)
            master['Adj_Factor'] = (
                (- master['Price_Gap']).shift(1).fillna(0).cumsum() 
            )

        # 生成换月状态标记
        roll_states = pd.Series(
            (
                self.contract_manager
                .get_active_physical_contracts(ts.date())
                .roll_state.value
                for ts in master.index
            ), # type: ignore
            index=master.index,
            dtype='string',
            name='roll_state',
        )
        master['roll_state'] = roll_states
        master['is_roll'] = roll_states.map({
            RollState.ROLL.value: True,
            RollState.NOT_ROLL.value: False,
            RollState.UNKNOWN.value: pd.NA,
        }).astype('boolean')

        previous_dominant = dominant.shift(1)
        comparable = dominant.notna() & previous_dominant.notna()
        is_contract_switch = pd.Series(pd.NA, index=master.index, dtype='boolean') # type: ignore
        is_contract_switch.loc[comparable] = (
            dominant.loc[comparable] != previous_dominant.loc[comparable]
        )
        master['is_contract_switch'] = is_contract_switch

        dominant_exists = dominant.notna()
        segment_starts = dominant_exists & ~dominant_exists.shift(
            1, fill_value=False
        )
        master['continuity_segment_id'] = (
            segment_starts.cumsum().where(dominant_exists).astype('Int64')
        )
        return master

    def _get_contract_dates_mapping(self, master_df_query: pd.DataFrame) -> dict[str, list[date]]:
        """获取每个 dominant contract 和其对应交易日的字典。"""
        contract_dates_mapping: dict[str, list[date]] = {}
        contracts = master_df_query['Dominant_Contract'].dropna().unique()
        for c in contracts:
            ts_dates = master_df_query.loc[master_df_query['Dominant_Contract'] == c].index.tolist() # 注意，这里日期并不一定都是连续的（可能有两段连续，出现在主力合约切换到别的，然后又切换回来的情况）
            contract_dates_mapping[c] = [ts.date() for ts in ts_dates]
        return contract_dates_mapping
    
    def _collect_raw_daily_data(self, contract_dates_mapping: dict[str, list[date]]) -> pd.DataFrame:
        """
        收集，筛选并拼接底层的日频原始数据。索引是交易日，类型为 pd.DatetimeIndex。
        返回的 dataframe 索引为 Datetime。包含 trading_date 列，每个元素为 datetime.date。

        Notes
        ------
        注意，这里的 contract_dates_mapping 可能包含日频快照中不存在的交易日。
        例如 'RB': 2025-05-26。原因在于，在构造换月总表（build_product_master_table.py)中，
        将来源于日频快照的 oi_matrix 和交易日历做了一次对齐，会暴露出原本日频快照缺失
        的一些日期。所以在从日频快照中取数据时，要先做一次取交集，剔除不存在于日频快照
        索引(contract, trading_date)中的元素。
        """
        product_df = self.daily_snapshot_manager.get_product_df(product=self.product, normalize_col_names=True)
        
        product_df = product_df.set_index(['Contract', 'Datetime'])
        # 将 contract_dates 转换为 list of tuples，利用 loc 查询索引
        idx_tuples = [(c, dt) for c, dates in contract_dates_mapping.items() for dt in pd.to_datetime(dates)] # type: ignore
        if idx_tuples:
            raw_continuous = product_df.loc[product_df.index.isin(idx_tuples)] #索引取交集后再提取
        else:
            raw_continuous = product_df.iloc[0:0]
        
        # 对数据格式做必要修正
        raw_continuous = raw_continuous.reset_index().set_index(['Datetime']) # 将交易日作为索引
        assert isinstance(raw_continuous.index, pd.DatetimeIndex)
        raw_continuous['trading_date'] = raw_continuous.index.date # 添加一列交易日
        raw_continuous.rename(columns={'Contract': 'Dominant_Contract'}, inplace=True) 

        if raw_continuous.empty and idx_tuples:
            raise ValueError(
                f'【ResearchFeed】品种 {self.product} 日频原始数据为空。'
                '根据业务逻辑该查询范围内不应缺失数据，'
                '请检查数据源是否完整。'
            )
        return raw_continuous
    
    def _collect_raw_1min_data(self, contract_dates_mapping: dict[str, list[date]]) -> pd.DataFrame:
        """
        收集，筛选并拼接底层的日频或分钟级原始数据。返回的 dataframe 的索引是日历交易时间戳，类型为 pd.DatetimeIndex

        返回的 dataframe 包含 trading_date 列，每个元素为 datetime.date。
        """
        to_concat_datas: list[pd.DataFrame] = []
        for contract, dates in contract_dates_mapping.items():
            # 查找本地 1min 数据文件地址，并加载
            file_path = self.file_locator.get_1min_file_path(contract)
            df = self.bar_loader.load_1min_bar(file_path)
            # 选取相应日期范围(这里 bar_loader 读进来的原始数据是日历交易时间，不是交易日，要先调用交易日历，做一个日历交易时间->交易日的转换)
            calendar_timestamps = pd.to_datetime(df.index)
            df['trading_date'] = self.calendar.get_trading_dates(calendar_timestamps)
            df = df[df['trading_date'].isin(dates)] 
            
            df['Dominant_Contract'] = contract  # 1min 原始数据没有合约代码列，这里加上
            to_concat_datas.append(df)

        if not to_concat_datas:
            return pd.DataFrame(
                columns=['Open', 'High', 'Low', 'Close', 'trading_date',
                         'Dominant_Contract'],
                index=pd.DatetimeIndex([], name='Datetime'),
            )

        return pd.concat(to_concat_datas, axis=0)
    
    def _adjust_price(self, df: pd.DataFrame,
                      master_df: pd.DataFrame,
                      daily_freq: bool) -> pd.DataFrame:
        """
        将完整换月总表预先算好的复权因子匹配到行情，并生成复权 OHLC。

        Notes
        -----
        本方法不在查询切片上重新计算 ``Adj_Factor``。因子来自完整 master
        table；同一个加法因子同时施加到 OHLC，以保持每根 K 线内部的
        绝对价差结构。``True_Return`` 始终另行使用未复权的 ``Raw_Close`` 计算。
        """
        check_df_columns_exist(df, ['Open','High','Low','Close'])

        metadata_columns = [
            'Dominant_Contract', 'Price_Gap', 'Adj_Factor', 'roll_state',
            'is_roll', 'is_contract_switch', 'continuity_segment_id',
        ]
        check_df_columns_exist(master_df, metadata_columns)
        # 将复权因子按交易日期匹配到 df 上
        assert isinstance(master_df.index, pd.DatetimeIndex)
        assert isinstance(df.index, pd.DatetimeIndex)
        if daily_freq:
            raw_df = df.drop(
                columns=metadata_columns + ['trading_date'], errors='ignore'
            )
            df = master_df[metadata_columns].join(raw_df, how='left')
            df['trading_date'] = df.index.date # pyright: ignore[reportAttributeAccessIssue]
        else:
            metadata = master_df[metadata_columns].copy()
            metadata['trading_date'] = metadata.index.date # pyright: ignore[reportAttributeAccessIssue]
            metadata.reset_index(drop=True, inplace=True)
            raw_df = df.drop(
                columns=['roll_state', 'is_roll', 'is_contract_switch',
                         'continuity_segment_id', 'Price_Gap', 'Adj_Factor'],
                errors='ignore',
            ).copy()
            raw_df['_index'] = raw_df.index
            df = pd.merge(
                raw_df, metadata.drop(columns=['Dominant_Contract']),
                on='trading_date', how='left', validate='many_to_one',
            )
            df.set_index('_index', drop=True, inplace=True)
            df.index.name = 'Datetime'
        # 计算复权价格
        for x in ['Open','High','Low','Close']:
            df.rename(columns={x: f'Raw_{x}'}, inplace=True) # 将原始价格列重新命名，加上'Raw_'
            df[f'Adj_{x}'] = df[f'Raw_{x}'] + df['Adj_Factor'] # 生成根据复权因子调整的bar(OHLC)
        return df
    
    def _calculate_true_return_daily(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        使用未复权收盘价计算日频真实收益率。

        Notes
        -----
        同一连续主力分段、同一物理合约内使用相邻 ``Raw_Close`` 计算收益率。
        仅在 ``is_contract_switch=True`` 的实际主力生效切换日，改用“新主力
        当日收盘价 / 新主力前一交易日收盘价 - 1”。换月过渡窗口内其余日期
        不修正。主力断档恢复首日属于新分段，因缺少可比的连续前值，
        ``True_Return`` 明确保留为 NaN，不跨断档回填。
        """
        df = df.sort_index()

        raw_return = df['Raw_Close'].pct_change(fill_method=None) # pyright: ignore[reportArgumentType]
        same_segment = (
            df['continuity_segment_id'].notna()
            & df['continuity_segment_id'].eq(df['continuity_segment_id'].shift(1))
        )
        same_contract = (
            df['Dominant_Contract'].notna()
            & df['Dominant_Contract'].eq(df['Dominant_Contract'].shift(1))
        )
        df['True_Return'] = raw_return.where(same_segment & same_contract)

        switch_mask = df['is_contract_switch'].eq(True).fillna(False)
        switch_dates = df.index[switch_mask & df['Raw_Close'].notna()]
        for dt in switch_dates:
            assert isinstance(dt, pd.Timestamp)
            # 获取主力合约在当日的价格
            dom = df.at[dt, 'Dominant_Contract']
            p_new_dom = df.at[dt, 'Raw_Close']
            # 找到前一个交易日
            pre_date = pd.Timestamp(
                self.calendar._get_prev_trading_date(trading_date=dt.date())
            )
            # prev_idx = df.index.get_loc(dt) - 1
            # pre_date = df.index[prev_idx]
            assert isinstance(pre_date, pd.Timestamp)
            # 向日频快照管家询问该合约在前一交易日的价格
            p_old_dom = self.daily_snapshot_manager.get_contract_snapshot_on_date(
                contract=dom, trading_date=pre_date).close
            true_return = p_new_dom / p_old_dom - 1
            df.at[dt, 'True_Return'] = true_return
        
        return df

    def _calculate_true_return_intraday(
            self, df: pd.DataFrame,
            freq: str, view: Literal['scheduled', 'realized']
        ) -> pd.DataFrame:
        """
        使用未复权收盘价计算分钟级真实收益率。

        Notes
        -----
        收益率仅在相邻 Bar 同属一个 ``continuity_segment_id`` 且物理合约相同
        时直接计算，因此不会跨越主力缺失区间。仅在实际合约切换日
        ``is_contract_switch=True`` 的第一根 Bar，改用新主力上一交易日最后一个
        标准 Bar 的收盘价作为分母。断档恢复首日不是可比较的换月收益，首根
        Bar 的 ``True_Return`` 保持 NaN；其后 Bar 正常计算。
        """
        df = df.sort_index()
        raw_return = df['Raw_Close'].pct_change(fill_method=None) # pyright: ignore[reportArgumentType]
        same_segment = (
            df['continuity_segment_id'].notna()
            & df['continuity_segment_id'].eq(df['continuity_segment_id'].shift(1))
        )
        same_contract = (
            df['Dominant_Contract'].notna()
            & df['Dominant_Contract'].eq(df['Dominant_Contract'].shift(1))
        )
        df['True_Return'] = raw_return.where(same_segment & same_contract)

        first_bar_of_day = df.groupby('trading_date').cumcount() == 0
        switch_mask = df['is_contract_switch'].eq(True).fillna(False)
        df['is_contract_switch_first'] = switch_mask & first_bar_of_day
        switch_first_timestamps = df.index[df['is_contract_switch_first']]
        # 对于每个实际切换日的第一根 K 线，读取新主力前一交易日的可比价格。
        for ts in switch_first_timestamps:
            assert isinstance(ts, pd.Timestamp)
            new_dom_contract = df.at[ts, 'Dominant_Contract']
            if pd.isna(new_dom_contract):
                continue
            # 找前一个交易日该合约所对应的频率为freq的标准时间戳序列
            trading_date = df.at[ts, 'trading_date']
            pre_date = self.calendar._get_prev_trading_date(trading_date)
            standard_timestamps_pre_date = self.calendar.get_standard_timestamps(
                instrument=new_dom_contract,
                start_date=pre_date,
                end_date=pre_date,
                freq=freq,
                view=view
            )
            pre_calendar_ts = standard_timestamps_pre_date[-1] # 前一天标准时间戳的最后1根K线
            assert isinstance(pre_calendar_ts, pd.Timestamp)

            # 读取所需数据，并获取新合约在"前一个聚合K线的时间戳"的价格
            file_path = self.file_locator.get_1min_file_path(contract=new_dom_contract)
            df_new_dominant_contract_prev = self.bar_loader.load_1min_bar(file_path)

            if df_new_dominant_contract_prev.empty:
                raise ValueError(f'寻找合约 {new_dom_contract} 在换月前一根聚合K线：'
                                f'{pre_calendar_ts.strftime("%Y-%m-%d %H:%M:%S")} 。'
                                '源数据为空，换月时的真实收益率(True_Return)数值错误！')
            
            # 寻找 pre_calendar_ts（若找不到，则找其neaerst before)
            sorted_index = df_new_dominant_contract_prev.index.sort_values()
            _idx = sorted_index.get_indexer([pre_calendar_ts], method='ffill')[0]
            # 这里找到的 _idx 应该并非 -1（即未找到)，若确实是 -1，直接抛出错误
            if _idx == -1:
                raise ValueError(f'寻找合约 {new_dom_contract} 在换月前一根聚合K线：'
                                    f'{pre_calendar_ts.strftime("%Y-%m-%d %H:%M:%S")} 。'
                                    'get_indexer 返回了 -1 (未找到)!')

            _pre_ts = sorted_index[_idx]
            # 计算换月时点的真实收益率
            p_old_new_dominant = df_new_dominant_contract_prev.at[_pre_ts, 'Close']
            p_new_new_dominant = df.at[ts, 'Raw_Close']
            true_return = p_new_new_dominant / p_old_new_dominant - 1
            df.at[ts, 'True_Return'] = true_return
            # 顺手记录一下换月前一个时点的 calendar timestamp
            df.at[ts, 'pre_bar_calendar_timestamp'] = _pre_ts
        return df
