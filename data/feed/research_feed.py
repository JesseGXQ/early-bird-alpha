from __future__ import annotations
import pandas as pd
import warnings
from typing import TYPE_CHECKING
from datetime import date
from utils.validation_utils import check_df_columns_exist

if TYPE_CHECKING:
    from data.stitching.stitching import ContinuousContractManager
    from data.metadata.product_meta_manager import ProductMetaManager
    from data.calendar.calendar import TradingCalendar
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
    calendar: TradingCalendar
        交易日历实例，提供交易日、交易小节、分钟偏移等查询功能。
    file_locator: FileLocator
        文件寻址器，提供根据合约名称和日期查询 1min 数据文件路径的功能。
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
            calendar: TradingCalendar, 
            file_locator: FileLocator, 
            bar_loader: BarLoader, 
            bar_aggregator: BarAggregator,
            adj_config: AdjustmentConfig):
        self.product: str = product.upper()

        self.contract_manager: ContinuousContractManager = contract_manager #包含 master_table 属性(换月总表)，有Dominant_Contract, Price_Gap 列
        self.meta_manager: ProductMetaManager = meta_manager
        self.calendar: TradingCalendar = calendar
        self.file_locator: FileLocator = file_locator
        self.bar_loader: BarLoader = bar_loader
        self.bar_aggregator: BarAggregator = bar_aggregator
        self.adj_config: AdjustmentConfig = adj_config

    def get_historical_vector(self, 
                              start_date: date, 
                              end_date: date,
                              freq: str = '1d') -> pd.DataFrame:
        """
        返回指定日期范围内，特定频率的历史复权主力合约价格。

        Parameters
        ------
        start_date : datetime.date or str
            起始交易日(Trading Date)，含义为该交易日完整 Session 的开始，
            即包含前一日历日夜盘。例如传入 2026-04-23 ，实际数据从 2026-04-22 21:00 开始。
        end_date : datetime.date or str
            结束交易日(Trading Date)，数据截止至该交易日日盘收盘 15:00。
        freq : str, default "1d"
            K 线频率，格式同 Pandas Offset aliases 保持一致。
            例: 1min, 5min, 15min, 30min, 1h, 2h, 4h, 1d。
            默认1d，即日频 。

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
                复权主连价格的OHLC，根据原始价格和复权因子做了调整。
            Raw_Close/ Raw_Open / Raw_High / Raw_Low: float
                主力合约的原始价格OHLC
            True_Return: float
                真实收益率，换月期已特殊处理。利用收盘价计算。
            is_roll: bool
                是否为换月时点
    
        """

        # 1. 找连续合约大管家索要指定日期范围的物理合约映射表
        # 这里 master_table (换月总表)的 index 应该是 Trading Date 概念，即“交易日期”(标签)。
        try:
            master_df_query = self.contract_manager.master_table.loc[start_date:end_date].copy()
        except KeyError:
            master_table_dates = self.contract_manager.master_table.index.sort_values()
            master_table_start_date = master_table_dates[0]
            master_table_end_date = master_table_dates[-1]
            warnings.warn(f'请求日期区间 {start_date} - {end_date} 超出了换月总表范围。目前支持 {master_table_start_date} - {master_table_end_date}。')

        if master_df_query.empty:
            return pd.DataFrame()
        
        # 2. 数据搬运：收集底层的 1min 数据
        to_concat_raw_datas = []

        for trading_date, row in master_df_query.iterrows():
            contract = row['Dominant_Contract']

            # 查找本地 1min 数据文件地址，并加载
            file_path = self.file_locator.get_1min_file_path(contract)
            df = self.bar_loader.load_1min_bar(file_path)

            if df.empty:
                continue
            else:
                df['Dominant_Contract'] = contract
                to_concat_raw_datas.append(df)

        if not to_concat_raw_datas:
            return pd.DataFrame()
        
        # 拼接
        raw_continuous = pd.concat(to_concat_raw_datas, axis=0)

        # 降维聚合，呼叫核心计算引擎
        # 注意，resampled_df （以及最后本函数在其之上返回的 DataFrame 的 index 都是 "calendar timestamp"，
        resampled_df = self.bar_aggregator.aggregate(
            raw_df=raw_continuous,
            freq=freq,
            add_trading_date_col = False #这个参数用于控制是否根据 calendar timestamp 生成交易日期标签，这里选否，下面手动做这一步
        )

        if resampled_df.empty:
            return resampled_df
        
        # 手动利用 TradingCalenar生成交易日期：对于Index中的每一个（日历）交易时间，生成相对应的交易日期标签
        resampled_df['trading_date'] = self.calendar.get_trading_dates(resampled_df.index)

        # 4. 计算复权价格和True_Return
        check_df_columns_exist(resampled_df, ['Open','High','Low','Close'])
        
        # 4.1 在换月表上，计算（日频的）复权因子
        if self.adj_config.direction == 'BACKWARD' and self.adj_config.method == 'ADDITIVE': # 前复权 + 等差法
            master_df_query.sort_index(inplace=True) # 首先确保换月表按交易日期顺序排列
            master_df_query['Adj_Factor'] = master_df_query['Price_Gap'].iloc[::-1].cumsum().iloc[::-1].shift(-1).fillna(0)
        else:
            raise ValueError(
                f'''
                【ResearchFeed】目前仅支持: 复权方向为前复权(前向调整法, 即 Backward Adjustment, 且复权计算方法为等差法)。 
                不支持复权方向：{self.adj_config.direction}。
                不支持复权计算方法：{self.adj_config.method}。
                '''
                )
        # 4.2 将复权因子按交易日期匹配到 resampled_df 上
        resampled_df = pd.merge(
            master_df_query['Adj_Factor'], 
            resampled_df, 
            left_index=True, 
            right_on=['trading_date'],
            how='inner'
            )

        # 4.3 计算复权价格
        for x in ['Open','High','Low','Close']:
            resampled_df.rename(columns={x: f'Raw_{x}'}, inplace=True) #将原始价格列重新命名，加上'Raw_'
            resampled_df[f'Adj_{x}'] = resampled_df[f'Raw_{x}'] + resampled_df['Adj_Factor'] #生成根据复权因子调整的bar(OHLC)

        # True_Return
        resampled_df['True_Return'] = resampled_df['Raw_Close'].pct_change()

        # 修正换月期的错误：
        # 换月日后的第一个 True_Return 应该用“新主力的换月后第一根聚合K线的 Close"/"新主力的换月前最后一根聚合K线的 Close"
        # 而上一行代码对于这个情况，计算的是"新主力的换月后第一根聚合K线的 Close"/"旧主力的换月前最后一根聚合K线的 Close"，这是错的
        rollover_mask = resampled_df['Dominant_Contract'] != resampled_df['Dominant_Contract'].shift(1)
        rollover_mask.iloc[0] = False #第一行特殊处理，设为非换月
        rollover_timestamps = resampled_df[rollover_mask].index

        resampled_df['is_roll'] = False

        for roll_ts in rollover_timestamps:
            #先做一个标记
            resampled_df.at[roll_ts, 'is_roll'] = True

            new_dominant_contract = resampled_df.at[roll_ts, 'Dominant_Contract']
            new_dominant_contract = str(new_dominant_contract) #确保是字符串，和 file_locator.get_1min_file_path() 的输入格式一致
            p_new_new_dominant = resampled_df.at[roll_ts, 'Raw_Close']

            #找到换月点前一个聚合K线的时间戳，根据 resampled_df 的格式，这里是日历日的时间戳
            prev_idx = resampled_df.index.get_loc(roll_ts) - 1 # resampled_df 需要是 unique 的(根据 Baraggregator.aggregate() 的逻辑应该不会出错)，否则这里会报错。
            pre_calendar_ts = resampled_df.index[prev_idx]

            #读取所需数据，并获取新合约在"前一个聚合K线的时间戳"的价格
            file_path = self.file_locator.get_1min_file_path(contract=new_dominant_contract)
            df_new_dominant_contract_prev = self.bar_loader.load_1min_bar(file_path)

            if df_new_dominant_contract_prev.empty:
                warnings.warn(f'寻找合约 {new_dominant_contract} 在换月前一根聚合K线：{pre_calendar_ts.strftime("%Y-%m-%d %H:%M:%S")} 。源数据为空，换月时的真实收益率(True_Return)数值错误！')
                continue
            else:
                # 寻找 pre_calendar_ts（若找不到，则找其neaerst before)
                sorted_index = df_new_dominant_contract_prev.index.sort_values()
                _idx = sorted_index.get_indexer([pre_calendar_ts], method='ffill')[0]
                #这里找到的 _idx 应该并非 -1（即未找到)，若确实是 -1，直接抛出错误
                if _idx == -1:
                    raise ValueError(f'寻找合约 {new_dominant_contract} 在换月前一根聚合K线：{pre_calendar_ts.strftime("%Y-%m-%d %H:%M:%S")} 。get_indexer 返回了 -1 (未找到)!')

                _pre_ts = df_new_dominant_contract_prev.index[_idx]
                check_df_columns_exist(df_new_dominant_contract_prev, ['Close'])
                # 计算换月时点的真实收益率
                p_old_new_dominant = df_new_dominant_contract_prev.at[_pre_ts, 'Close']
                true_return_roll = p_new_new_dominant / p_old_new_dominant -1 
                resampled_df.at[roll_ts, 'True_Return'] = true_return_roll
                # 顺手记录一下换月前一个时点的 calendar timestamp
                resampled_df.at[roll_ts, 'pre_bar_calendar_timestamp'] = _pre_ts
        
        return resampled_df

