import pandas as pd
from abc import ABC, abstractmethod
import numpy as np

#滤芯标准接口
class FilterBase(ABC):
    """
    清洗过滤器基类(The Filter Socket)
    规范: 所有具体过滤器必须接受一个 Pandas DataFrame, 并返回一个清洗后的 DataFrame。
    """

    @abstractmethod
    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        pass

# ========== 特殊滤芯：替身增广器 ==========
class MockMetadataEnricher(FilterBase):
    """
    基础信息增广滤芯 (Mock 替身版)
    职责：在未连接真实交易日历库前，强行给 Tick 数据打上绝对宽容的涨跌停价，保证流水线闭环。
    """
    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        
        clean_df = df.copy()
        
        # 强行注入极宽的涨跌停边界（保证正常 Tick 绝对不会误触）
        if 'UpperLimitPrice' not in clean_df.columns:
            clean_df['UpperLimitPrice'] = 999999.0
        if 'LowerLimitPrice' not in clean_df.columns:
            clean_df['LowerLimitPrice'] = 0.0
            
        # 在研发初期，为了解耦底层清洗逻辑与外部日历库的依赖，引入了 Mock 注入机制。
        # 实盘上线前，只需将此滤芯替换为真实调用 Redis 的 RealMetadataEnricher 即可。”
        return clean_df


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
        if not all(col in clean_df.columns for col in required_cols):
            #如果数据里没有涨跌停价，直接跳过该滤芯，不报崩溃
            return clean_df
        
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
            print(f'【PriceLimitFilter】识别到 {limit_hits}行处于涨跌停状态并已打标。')
        
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
        数据流过流水线
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