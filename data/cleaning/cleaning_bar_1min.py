from __future__ import annotations
import pandas as pd
from data.cleaning.cleaning_tick import (
    FilterBase)
from typing import TYPE_CHECKING
import numpy as np

if TYPE_CHECKING:
    pass

class DelayedNightOpenFixer(FilterBase):
    """
    由于结算系统故障， 2019-12-26 商品期货夜盘延迟到22:30开市。
    但1min原始数据中，数据商将集合竞价的结果放到了22:29的单价（O=H=L=C）K线中，
    并未像平日一样，放到夜盘第一笔K线的开盘价中。

    本滤芯将2019-12-26交易日的22:29的单价K线价格映射到22:30的开盘价，并删除单价K线。
    
    注意，1min数据的时间戳是日历交易时间戳，所以日历日为 2019-12-25。
    """
    def __init__(self):
        pass

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        
        assert isinstance(df.index, pd.DatetimeIndex)
        try:
            call_auction_price_o = df.at['2019-12-25 22:29', 'Open']
        except KeyError: # 说明根本没有集合竞价结果，那么直接跳过替换，返回输入dataframe
            return df
        call_auction_price_h = df.at['2019-12-25 22:29', 'High']
        call_auction_price_l = df.at['2019-12-25 22:29', 'Low']
        call_auction_price_c = df.at['2019-12-25 22:29', 'Close']

        # 验证仅有一条数据
        assert isinstance(call_auction_price_o, (int, float)) | pd.isna(call_auction_price_o), \
            f"Expected scalar or nan, got {type(call_auction_price_o)}"
        
        # 验证单价K线
        unique_p = pd.unique(np.array(
            [call_auction_price_o,call_auction_price_l,
             call_auction_price_h, call_auction_price_c]
            ))
        valid_p = unique_p[pd.notna(unique_p)]
        assert len(valid_p) == 1, '【DelayedNightOpenFixer】非单价K线！'

        # 替换 22:30开盘价，删除单价K线，并返回
        call_auction_p = valid_p[0]
        if 'open' in df:
            df.loc['2019-12-25 22:30', 'open'] = call_auction_p
        elif 'Open' in df:
            df.loc['2019-12-25 22:30', 'Open'] = call_auction_p
        else:
            raise ValueError(f'【DelayedNightOpenFixer】输入的 dataframe 未找到以 Open/open 命名的收盘价列。'
                             f'现在的 columns 为 {df.columns.tolist()}')
        return df.drop(['2019-12-25 22:29'], axis=0)
    



        
