from __future__ import annotations
import pandas as pd
import warnings
from typing import TYPE_CHECKING, Literal
from datetime import date

from utils.validation_utils import check_df_columns_exist

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
    calendar: ProductCalendar
        交易日历实例，提供交易日、交易小节、分钟偏移等查询功能。
    file_locator: FileLocator
        文件寻址器，提供根据合约代码和日期查询 1min 数据文件路径的功能。
    bar_loader: BarLoader
        K 线数据加载器，提供读取 1min 原始数据并返回 DataFrame 的功能。
    bar_aggregator: BarAggregator
        K 线降频采样聚合器，提供将 1min 数据聚合成其他频率数据的功能。
    adj_config: AdjustmentConfig
        复权配置参数，包含复权方向(direction)和复权计算方法(method)。

    Methods
    ------
    get_historical_vector(start_date, end_date, freq)
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

    def get_historical_vector(self, 
                              start_date: date, 
                              end_date: date,
                              freq: str = '1d',
                              view: Literal['scheduled', 'realized'] = 'scheduled') -> pd.DataFrame:
        """
        返回指定日期范围内，特定频率的历史复权主力合约价格。

        Parameters
        ------
        start_date : datetime.date or str
            起始交易日，含义为该交易日完整 Session 的开始，
            即包含前一日历日夜盘。例如传入 2026-04-23 ，实际数据从 2026-04-22 21:00 开始。
        end_date : datetime.date or str
            结束交易日，数据截止至该交易日日盘收盘 15:00。
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

        Index
        ~~~~~
            calendar_time: pd.DatetimeIndex 
                （日历）交易时间戳。
        Columns
        ~~~~~~~
            trading_date: datetime.date
                交易日期标签
            Dominant_Contract: str
                主力合约（名称）
            Price_Gap: float
                换月时新主力和旧主力的价格差
            Adj_Close / Adj_Open / Adj_High / Adj_Low: float
                复权主连价格的OHLC ，根据原始价格和复权因子做了调整。
            Raw_Close/ Raw_Open / Raw_High / Raw_Low: float
                主力合约的原始价格OHLC
            True_Return: float
                真实收益率，换月期已特殊处理。利用收盘价计算。
            is_roll: bool
                是否为换月时点
    
        """
        # 1. 找连续合约大管家索要指定日期范围的物理合约映射表
        master_df_query = self._query_master_table(start_date=start_date, end_date=end_date)
        # 2. Dominant Contracts的原始行情数据收集
        contract_dates_mapping = self._get_contract_dates_mapping(master_df_query=master_df_query)
        # 3. 完全分离日频和非日频的处理路径
        if freq == '1d':
            raw_continuous = self._collect_raw_daily_data(contract_dates_mapping=contract_dates_mapping)
            # 复权
            re_df = self._adjust_price(raw_continuous, master_df=master_df_query)
            # 计算 True Return
            re_df = self._calculate_true_return_daily(re_df)
            return re_df
        else:
            print('读取原始1min数据...')
            raw_continuous = self._collect_raw_1min_data(contract_dates_mapping=contract_dates_mapping)
            # 清洗
            print('数据清洗...')
            cleaned_continuous = self.cleaning_pipeline.process(raw_continuous)
            # 聚合
            print('聚合运算...')
            resampled_df = self.bar_aggregator.aggregate(
                raw_df=cleaned_continuous,
                freq=freq,
                view=view
            )
            # 复权
            print('复权操作...')
            re_df = self._adjust_price(resampled_df, master_df=master_df_query)
            # 计算 True Return
            print('计算 True Return 序列...')
            re_df = self._calculate_true_return_intraday(re_df)
            return re_df

    def _query_master_table(self, start_date: date, end_date: date) -> pd.DataFrame:
        """返回指定日期范围的物理合约映射表"""
        master_table_dates = self.contract_manager.master_table.index
        if pd.Timestamp(start_date) < master_table_dates[0] or pd.Timestamp(end_date) > master_table_dates[-1]:
            raise ValueError(f'请求日期区间 {start_date} - {end_date} 超出了换月总表范围。'
                             f'品种 {self.product} 支持 {master_table_dates[0].strftime('%Y-%m-%d')} - {master_table_dates[-1].strftime('%Y-%m-%d')}。')
        master_df_query = self.contract_manager.master_table.loc[start_date:end_date].copy()
        return master_df_query

    def _get_contract_dates_mapping(self, master_df_query: pd.DataFrame) -> dict[str, list[date]]:
        """获取每个 dominant contract 和其对应交易日的字典。"""
        contract_dates_mapping: dict[str, list[date]] = {}
        contracts = master_df_query['Dominant_Contract'].unique()
        for c in contracts:
            ts_dates = master_df_query.loc[master_df_query['Dominant_Contract'] == c].index.tolist() # 注意，这里日期并不一定都是连续的（可能有两段连续，出现在主力合约切换到别的，然后又切换回来的情况）
            contract_dates_mapping[c] = [ts.date() for ts in ts_dates]
        return contract_dates_mapping
    
    def _collect_raw_daily_data(self, contract_dates_mapping: dict[str, list[date]]) -> pd.DataFrame:
        """
        收集，筛选并拼接底层的日频原始数据。索引是交易日，类型为 pd.DatetimeIndex。

        返回的 dataframe 包含 trading_date 列，每个元素为 datetime.date。
        """
        product_df = self.daily_snapshot_manager.get_product_df(product=self.product, normalize_col_names=True)
        
        product_df = product_df.set_index(['Contract', 'Datetime'])
        # 将 contract_dates 转换为 list of tuples，利用 loc 查询索引
        idx_tuples = [(c, dt) for c, dates in contract_dates_mapping.items() for dt in pd.to_datetime(dates)]
        raw_continuous = product_df.loc[idx_tuples]
        
        # 对数据格式做必要修正
        raw_continuous = raw_continuous.reset_index().set_index(['Datetime']) # 将交易日作为索引
        assert isinstance(raw_continuous.index, pd.DatetimeIndex)
        raw_continuous['trading_date'] = raw_continuous.index.date # 添加一列交易日
        raw_continuous.rename(columns={'Contract': 'Dominant_Contract'}, inplace=True) 
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

        raw_continuous = pd.concat(to_concat_datas, axis=0)
        return raw_continuous
    
    def _adjust_price(self, df: pd.DataFrame, master_df: pd.DataFrame) -> pd.DataFrame:
        """
        计算复权价格。
        
        检查输入的 dataframe 包含 OHLC, 加入复权后的 OHLC 价格。
        """
        check_df_columns_exist(df, ['Open','High','Low','Close'])

        # 在换月表上，计算（日频的）复权因子
        if self.adj_config.direction == 'BACKWARD' and self.adj_config.method == 'ADDITIVE': # 前复权 + 等差法
            master_df.sort_index(inplace=True) # 首先确保换月表按交易日期顺序排列
            master_df['Adj_Factor'] = master_df['Price_Gap'].iloc[::-1].cumsum().iloc[::-1].shift(-1).fillna(0)
        else:
            raise ValueError(
                f'''
                【ResearchFeed】目前仅支持: 复权方向为前复权(前向调整法, 即 Backward Adjustment, 且复权计算方法为等差法)。 
                不支持复权方向：{self.adj_config.direction}。
                不支持复权计算方法：{self.adj_config.method}。
                '''
                )
        # 将复权因子按交易日期匹配到 df 上
        assert isinstance(master_df.index, pd.DatetimeIndex), '否则下面一行尝试获取mater_df.index的date属性时会出错。'
        #print(f'【_adjust_price】将 master_df merge 进来前 df 的索引为', df.index)
        df['_index'] = df.index # 为防止丢失 index，先取下来
        df = pd.merge(master_df['Adj_Factor'], df, left_on=master_df.index.date, right_on=['trading_date'], how='inner')
        df = df.set_index('_index') # 再把index设置回去
        #print(f'【_adjust_price】将 master_df merge 进来后 df 的索引为', df.index)
        # 计算复权价格
        for x in ['Open','High','Low','Close']:
            df.rename(columns={x: f'Raw_{x}'}, inplace=True) # 将原始价格列重新命名，加上'Raw_'
            df[f'Adj_{x}'] = df[f'Raw_{x}'] + df['Adj_Factor'] # 生成根据复权因子调整的bar(OHLC)

        return df
    
    def _calculate_true_return_daily(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        对于日频数据，计算换月期的 True Return。
        
        换月日后的第一个 True_Return 应该用“新主力的换月后第一根K线的价格"/"新主力的换月前最后一根K线的价格"
        """
        df = df.sort_index()

        # 先计算一列 True_Return，但取值只对非换月日成立
        df['True_Return'] = df['Raw_Close'].pct_change()

        rollover_mask = df['Dominant_Contract'] != df['Dominant_Contract'].shift(1)
        rollover_mask.iloc[0] = False #第一行特殊处理，设为非换月
        rollover_dates = df[rollover_mask].index

        df['is_roll'] = False

        for roll_date in rollover_dates:
            # 先做一个标记
            df.at[roll_date, 'is_roll'] = True
            # 取新主力合约的换月后第一根K线价格
            new_dominant_contract = df.at[roll_date, 'Dominant_Contract']
            p_new_new_dominant = df.at[roll_date, 'Raw_Close']
            # 找到换月日的前一天
            prev_idx = df.index.get_loc(roll_date) - 1 
            pre_date = df.index[prev_idx]
            try:
                assert isinstance(pre_date, pd.Timestamp)
            except AssertionError:
                raise ValueError(f'''
                                 roll_date is {roll_date}, prev_idx is {prev_idx}.
                                 Variable pre_date is {pre_date}, and is of type {type(pre_date)}, not pd.Timestamp!
                                 ''')
            # 向日频快照管家询问新主力合约在换月日前一天的价格
            p_old_new_dominant = self.daily_snapshot_manager.get_contract_snapshot_on_date(
                contract=new_dominant_contract, trading_date=pre_date).close
            # 计算换月日正确的 True Return
            true_return_roll = p_new_new_dominant / p_old_new_dominant - 1
            df.at[pre_date, 'True_Return'] = true_return_roll
        
        return df
    
    def _calculate_true_return_intraday(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        对于非日频数据，计算换月期的 True Return。

        换月日后的第一个 True Return 应该用“新主力的换月后第一根聚合K线的价格"/"新主力的换月前最后一根聚合K线的价格"。
        """
        df = df.sort_index()

        # 先计算一列 True_Return，但取值只对非换月日成立
        df['True_Return'] = df['Raw_Close'].pct_change()

        rollover_mask = df['Dominant_Contract'] != df['Dominant_Contract'].shift(1)
        rollover_mask.iloc[0] = False # 第一行特殊处理，设为非换月
        rollover_timestamps = df[rollover_mask].index

        df['is_roll'] = False
            
        for roll_ts in rollover_timestamps:
            # 先做一个标记
            df.at[roll_ts, 'is_roll'] = True
            # 记录新主力合约的换月后第一根聚合K线价格
            new_dominant_contract = df.at[roll_ts, 'Dominant_Contract']
            p_new_new_dominant = df.at[roll_ts, 'Raw_Close']

            # 找到换月点前一个聚合K线的时间戳
            prev_idx = df.index.get_loc(roll_ts) - 1 
            pre_calendar_ts = df.index[prev_idx]

            # 读取所需数据，并获取新合约在"前一个聚合K线的时间戳"的价格
            file_path = self.file_locator.get_1min_file_path(contract=new_dominant_contract)
            df_new_dominant_contract_prev = self.bar_loader.load_1min_bar(file_path)

            if df_new_dominant_contract_prev.empty:
                raise ValueError(f'寻找合约 {new_dominant_contract} 在换月前一根聚合K线：'
                              f'{pre_calendar_ts.strftime("%Y-%m-%d %H:%M:%S")} 。'
                              '源数据为空，换月时的真实收益率(True_Return)数值错误！')
            
            # 寻找 pre_calendar_ts（若找不到，则找其neaerst before)
            sorted_index = df_new_dominant_contract_prev.index.sort_values()
            _idx = sorted_index.get_indexer([pre_calendar_ts], method='ffill')[0]
            # 这里找到的 _idx 应该并非 -1（即未找到)，若确实是 -1，直接抛出错误
            if _idx == -1:
                raise ValueError(f'寻找合约 {new_dominant_contract} 在换月前一根聚合K线：'
                                 f'{pre_calendar_ts.strftime("%Y-%m-%d %H:%M:%S")} 。'
                                 'get_indexer 返回了 -1 (未找到)!')

            _pre_ts = df_new_dominant_contract_prev.index[_idx]
            # 计算换月时点的真实收益率
            p_old_new_dominant = df_new_dominant_contract_prev.at[_pre_ts, 'Close']
            true_return_roll = p_new_new_dominant / p_old_new_dominant -1 
            df.at[roll_ts, 'True_Return'] = true_return_roll
            # 顺手记录一下换月前一个时点的 calendar timestamp
            df.at[roll_ts, 'pre_bar_calendar_timestamp'] = _pre_ts
        
        return df   
