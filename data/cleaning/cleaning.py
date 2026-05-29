from __future__ import annotations

from typing import TYPE_CHECKING
import pandas as pd
from abc import ABC, abstractmethod
import numpy as np

from utils.validation_utils import check_df_columns_exist

if TYPE_CHECKING:
    from data.metadata.daily_snapshot_manager import DailySnapshotManager
    from data.calendar.calendar import TradingCalendar

#滤芯标准接口
class FilterBase(ABC):
    """
    清洗过滤器基类(The Filter Socket)
    规范: 所有具体过滤器必须接受一个 Pandas DataFrame, 并返回一个清洗后的 DataFrame。
    """

    @abstractmethod
    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        pass

class DataEnricherPriceLimit(FilterBase):
    """
    数据增强滤芯：涨跌停价注入器
    负责为每笔 TICK 数据注入对应的涨跌停价。
    向 DailySnapshotManager 请求对应品种在特定交易日的涨跌停价信息，并将其合并到 TICK 数据中。
    (DailySnapShotManager 提供 get_price_limits 方法，根据输入的symbol_root, contract, trading_date, 返回 (涨停价，跌停价) )
    """
    def __init__(self, symbol_root, daily_snapshot_manager: DailySnapshotManager):
        self.symbol_root = symbol_root
        self.daily_snapshot_manager = daily_snapshot_manager
    
    def apply(self, df: pd.DataFrame) -> pd.DataFrame:

        clean_df = df.copy()

        if 'InstrumentID' in clean_df.columns: 
            contracts = clean_df['InstumentID'].str.upper().unique()
        else:
            raise ValueError('【DataEnricherPriceLimit】未能在数据中找到合约名称列 "InstrumentID"。请检查原始数据。')
        
        if 'TradingDay' in clean_df.columns:
            trading_dates = pd.unique(pd.DatetimeIndex(clean_df['TradingDay']).date)
        else:
            raise ValueError('【DataEnricherPriceLimit】未能在数据中找到交易日列 "TradingDay"。请检查原始数据。')
        
        if len(contracts) > 1: # 下游SimulationFeed使用时，是根据 contract 和 trading_date 来读取的，而原始数据也是根据这个形式来组织的，因此这里不会存在有多个合约，或多个交易日的情况，若有，则抛出错误。
            raise ValueError('【DataEnricherPriceLimit】该数据中有两个及以上的合约（应该只有一个），请检查错误。')
        if len(trading_dates) > 1:
            raise ValueError('【DataEnricherPriceLimit】该数据中有超过一个交易日（应该只有一个），请检查错误。')
        
        # 拿到合约和交易日，向日频快照管家询问涨停价和跌停价，并补充到数据中
        contract = contracts[0]
        trading_date = trading_dates[0]
        upper_limit_price, lower_limit_price = self.daily_snapshot_manager.get_price_limits(
            symbol_root=self.symbol_root,
            contract=contract, 
            trading_date=trading_date)
        clean_df['UpperLimitPrice'] = upper_limit_price
        clean_df['LowerLimitPrice'] = lower_limit_price

        return clean_df
    
class DataEnricherGenerateCalendarTimestamp(FilterBase):
    """
    数据增强滤芯：生成日历交易时间戳
    负责将原始数据中的交易时间戳（日期为交易日期，即夜盘的日期为下一个交易日）转换为日历时间戳，并存入新列 'calendar_timestamp' 中。
    """
    def __init__(self, calendar: TradingCalendar):
        self.calendar = calendar

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:

        clean_df = df.copy()

        if 'TradingDay' in clean_df.columns and 'UpdateTime' in clean_df.columns and 'UpdateMillisec' in clean_df.columns:
            # 假设原始数据中有 'TradingDay'（交易日，日期部分），'UpdateTime'（更新时间，时分秒部分）和 'UpdateMillisec'（更新时间的毫秒部分）这三列，合成一个完整的交易时间戳。
            clean_df['trading_date'] = pd.to_datetime(clean_df['TradingDay']).dt.date 
            clean_df['update_time'] = pd.to_datetime(pd.to_timedelta(clean_df['UpdateTime']) + pd.to_timedelta(clean_df['UpdateMillisec'], unit='ms')).dt.time
            clean_df['calendar_timestamp'] = self.calendar.get_calendar_timestamps_from_trading_date_and_time(
                trading_dates=clean_df['trading_date'],
                t_s=clean_df['update_time']
            )
        else:
            raise ValueError('【DataEnricherGenerateCalendarTimestamp】未能在数据中找到生成日历时间戳所需的列 "TradingDay", "UpdateTime", "UpdateMillisec"。请检查原始数据。')
        
        return clean_df.drop(columns=['trading_date', 'update_time'])

#异常值过滤器
class OutlierFilter(FilterBase):
    """
    异常值拦截器(The "Bouncer")
    拦截违背期货交易物理规则的“幻觉数据”
    1. 最新价(LastPrice)必须大于0: 除极个别负油价黑天鹅, 商品期货价格不能为负或者零。
    2. 除非涨跌停(Ask/Bid可能为0), 否则盘口不能倒挂, 即卖一必须大于买一; 如果出现, 是数据丢包或者乱序错位
    """

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:

        clean_df = df.copy()

        #记录清洗前的数据量
        original_length = len(clean_df)

        #规则1 
        if "LastPrice" in clean_df.columns:
            clean_df = clean_df[clean_df['LastPrice']>0]
        
        #规则2
        #首先筛选涨跌停的情况，即Ask/Bid可能为0
        valid_bid_asks = (clean_df['AskPrice1'] > 0) & (clean_df['BidPrice1'] > 0)
        #保留价格含0（涨跌停板正常现象），或者卖一>买一的正常行
        clean_df = clean_df[~valid_bid_asks | (clean_df['AskPrice1'] > clean_df['BidPrice1'])]

        dropped_rows = original_length - len(clean_df)
        if dropped_rows > 0:
            print(f'【OutlierFilter】清理了{dropped_rows}行脏数据。')
        
        return clean_df

class PriceLimitFilter(FilterBase):
    """
    涨跌停状态打标机
    识别当前 TICK 是否处于涨跌停状态， 并打上布尔标签， 供下游撮合引擎判断是否能成交。
    """

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:

        clean_df = df.copy()

        #检查必要的列是否存在（一般而言，涨跌停价由交易所提供在 TICK 中，
        #但由于我们这份原始购买的数据没有涨跌停价(UpperLimitPrice, LowerLimitPrice)，所以我们在代码中（清洗工序）
        #还需要额外的一部Data Enrichment。这里有很多细节需要注意，见word文档备注。
        #这里我们暂时假设已经enrich完毕，即存在涨跌停价这两个字段。

        required_cols = ['LastPrice', 'UpperLimitPrice', 'LowerLimitPrice']
        
        check_df_columns_exist(df=clean_df, required_columns=required_cols)
        
        # ---------------------------------------------------------
        # 核心逻辑：向量化打标
        # 涨停判定：最新价 >= 涨停板价 且 盘口卖一价为0（代表卖盘枯竭）
        # ---------------------------------------------------------

        #初始化标签列，默认为 Fasle
        clean_df['is_limit_up'] = False
        clean_df['is_limit_down'] = False

        #判定涨停：价格触及涨停，且卖一队列为空（卖一价为0，或某些数据中卖一量为0）
        limit_up_cond = (clean_df['LastPrice'] >= clean_df['UpperLimitPrice']) & \
                        ((clean_df['AskPrice1'] <= 0) | (clean_df.get('AskVolume1',0) <= 0))
        
        #判定跌停：价格触及跌停，且买一队列为空
        limit_down_cond = (clean_df['LastPrice'] <= clean_df['LowerLimitPrice']) & \
                          ((clean_df['BidPrice1'] <=0) | (clean_df.get('BidVolume1',0) <=0 ))
        
        #赋值
        clean_df.loc[limit_up_cond, 'is_limit_up'] = True
        clean_df.loc[limit_down_cond, 'is_limit_down'] = True

        limit_hits = limit_up_cond.sum() + limit_down_cond.sum()
        if limit_hits > 0:
            print(f'【PriceLimitFilter】识别到 {limit_hits} 行处于涨跌停状态并已打标。')
        
        return clean_df
    
class OutputSchemaEnforcer(FilterBase):
    """
    数据格式规范器：
    所有滤芯中的最后一个：将清洗完毕的数据转换为符合 ContractTick 定义的格式。
    """
    def apply(self, df: pd.DataFrame) -> pd.DataFrame:

        clean_df = df.copy()

        # 重命名列名
        # 首先定义一个重命名的mapping，key为原始列名，value为目标列名
        rename_map = {'InstrumentID': 'contract', 
                      'TradingDay': 'trading_date',
                      'calendar_timestamp': 'calendar_timestamp', 
                      'LastPrice': 'last',
                      'BidPrice1': 'bid', 'AskPrice1': 'ask', 
                      'BidVolume1': 'bid_volume', 'AskVolume1': 'ask_volume', 
                      'OpenInterest': 'open_interest',
                      'is_limit_up': 'is_limit_up', 'is_limit_down': 'is_limit_down'}
        # 确保所有的列必须存在
        check_df_columns_exist(df=clean_df, required_columns=list(rename_map.keys()))
        clean_df.rename(columns=rename_map, inplace=True)

        # 确保数据类型符合 ContractTick 的定义
        clean_df['contract'] = clean_df['contract'].astype(str)
        clean_df['trading_date'] = pd.to_datetime(clean_df['trading_date']).dt.date
        clean_df['calendar_timestamp'] = pd.to_datetime(clean_df['calendar_timestamp'])
        for col in ['last', 'bid', 'ask', 'open_interest']:
            clean_df[col] = clean_df[col].astype(float)
        for col in ['volume', 'bid_volume', 'ask_volume']:
            clean_df[col] = clean_df[col].astype(int)
        for col in ['is_limit_up', 'is_limit_down']:
            clean_df[col] = clean_df[col].astype(bool)
        
        return clean_df

#清洗流水线容器
class CleaningPipeline:
    """
    清洗流水线(The "Pipeline")
    管理并顺序执行一系列过滤器，将原始 Chunk 加工成标准格式。
    """
    
    def __init__(self):
        self._filters = []

    def add_filter(self, filter_obj: FilterBase):
        """在流水线中添加过滤芯"""
        self._filters.append(filter_obj)
        return self # 支持链式调用
    
    def process(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        原始数据流过（加入若干滤芯的）流水线，获得清洗后的数据。     
        
        """
        processed_df = df
        for filter_item in self._filters:
            processed_df = filter_item.apply(processed_df)
        return processed_df


# ==========================================
# 本地验证测试
# ==========================================

    
if __name__ == "__main__":
    print("🚀 启动清洗流水线全流程测试...")
    
    # 1. 模拟一个包含脏数据和涨停状态的混合 Chunk
    mixed_data = {
        'Timestamp': ['10:00:00', '10:00:01', '10:00:02'],
        'LastPrice': [3500, -5, 3600],            # 第二行是脏数据，第三行触及涨停
        'UpperLimitPrice': [3600, 3600, 3600],
        'LowerLimitPrice': [3400, 3400, 3400],
        'AskPrice1': [3501, 3502, 0],             # 第三行卖一价为0
        'BidPrice1': [3499, 3498, 3599]
    }
    raw_chunk = pd.DataFrame(mixed_data)
    
    # 2. 组装流水线
    pipeline = CleaningPipeline()
    #pipeline.add_filter(MockMetadataEnricher()) # 第一步：贴假标签
    pipeline.add_filter(OutlierFilter())     # 第一步：剔除负数和倒挂
    pipeline.add_filter(PriceLimitFilter())  # 第二步：涨跌停打标
    
    # 3. 一键清洗
    final_clean_chunk = pipeline.process(raw_chunk)
    
    print("\n🏁 最终产出的数据形态:")
    print(final_clean_chunk)
    
    # 验证打标是否成功
    if final_clean_chunk.iloc[-1]['is_limit_up']:
        print("\n✅ 成功！系统已准确识别并标记了涨停状态。")