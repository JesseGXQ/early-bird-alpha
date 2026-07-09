import pytest
import pandas as pd
from unittest.mock import MagicMock

# 导入被测试的清洗组件
from data.cleaning.cleaning import (
    OutlierFilter,
    PriceLimitFilter,
    InstrumentCodeTranslator,
    CleaningPipeline,
    DataEnricherPriceLimit
)

# ==========================================
# 1. 测试 OutlierFilter (异常值过滤器)
# ==========================================

def test_outlier_filter_negative_last_price():
    """测试 LastPrice 小于等于 0 的数据是否被成功剔除。"""
    filter_obj = OutlierFilter()
    
    # 准备测试数据：第 2 行 LastPrice 为 -5，第 3 行 LastPrice 为 0
    test_data = pd.DataFrame({
        'LastPrice': [3500.0, -5.0, 0.0, 3600.0],
        'AskPrice1': [3501.0, 3502.0, 3503.0, 3601.0],
        'BidPrice1': [3499.0, 3498.0, 3497.0, 3599.0]
    })
    
    result_df = filter_obj.apply(test_data)
    
    # 期望：只剩下第 1 行和第 4 行，共 2 行数据
    assert len(result_df) == 2
    assert result_df['LastPrice'].tolist() == [3500.0, 3600.0]


def test_outlier_filter_ask_bid_crossing():
    """测试买一卖一倒挂（AskPrice1 <= BidPrice1）是否被成功剔除。"""
    filter_obj = OutlierFilter()
    
    # 准备测试数据：
    # 行 0: 正常 (Ask=3501, Bid=3499)
    # 行 1: 倒挂 (Ask=3498, Bid=3500) -> 应该剔除
    # 行 2: 涨停状态，卖一价为 0，买一价为 3600 (Ask=0, Bid=3600) -> 不应剔除
    test_data = pd.DataFrame({
        'LastPrice': [3500.0, 3500.0, 3600.0],
        'AskPrice1': [3501.0, 3498.0, 0.0],
        'BidPrice1': [3499.0, 3500.0, 3600.0]
    })
    
    result_df = filter_obj.apply(test_data)
    
    # 期望：保留行 0 和行 2，剔除行 1
    assert len(result_df) == 2
    assert result_df['AskPrice1'].tolist() == [3501.0, 0.0]


# ==========================================
# 2. 测试 PriceLimitFilter (涨跌停打标器)
# ==========================================

def test_price_limit_filter_limit_up():
    """测试当价格达到涨停，且卖一队列为空时，是否打上 is_limit_up 标签。"""
    filter_obj = PriceLimitFilter()
    
    # 准备测试数据：
    # 行 0: 正常触及涨停，且卖一价为 0 -> 涨停
    # 行 1: 触及涨停，但卖一价不为 0 -> 未涨停
    test_data = pd.DataFrame({
        'LastPrice': [3600.0, 3600.0],
        'UpperLimitPrice': [3600.0, 3600.0],
        'LowerLimitPrice': [3400.0, 3400.0],
        'AskPrice1': [0.0, 3601.0],
        'BidPrice1': [3599.0, 3599.0]
    })
    
    result_df = filter_obj.apply(test_data)
    
    assert result_df['is_limit_up'].tolist() == [True, False]
    assert result_df['is_limit_down'].tolist() == [False, False]


def test_price_limit_filter_limit_down():
    """测试当价格达到跌停，且买一队列为空时，是否打上 is_limit_down 标签。"""
    filter_obj = PriceLimitFilter()
    
    # 准备测试数据：
    # 行 0: 正常触及跌停，且买一价为 0 -> 跌停
    # 行 1: 触及跌停，但买一价不为 0 -> 未跌停
    test_data = pd.DataFrame({
        'LastPrice': [3400.0, 3400.0],
        'UpperLimitPrice': [3600.0, 3600.0],
        'LowerLimitPrice': [3400.0, 3400.0],
        'AskPrice1': [3401.0, 3401.0],
        'BidPrice1': [0.0, 3399.0]
    })
    
    result_df = filter_obj.apply(test_data)
    
    assert result_df['is_limit_up'].tolist() == [False, False]
    assert result_df['is_limit_down'].tolist() == [True, False]


# ==========================================
# 3. 使用 Mock 测试 InstrumentCodeTranslator (依赖外部管理器的滤芯)
# ==========================================

def test_instrument_code_translator():
    """
    测试合约代码翻译器。
    由于它依赖 InstrumentRegistryManager 的 translate_code 方法，
    我们使用 unittest.mock.MagicMock 来模拟这个管理器，从而实现单纯的单元测试。
    """
    # 1. 模拟 InstrumentRegistryManager 实例
    mock_registry = MagicMock()
    # 2. 规定 mock_registry.translate_code 的返回值
    # 假设把 'SR309' (郑商所老合约格式) 翻译为内部格式 'SR2309'
    mock_registry.translate_code.return_value = 'SR2309'
    
    # 3. 构造被测类，并将 mock 注入
    translator = InstrumentCodeTranslator(registry_manager=mock_registry)
    
    # 4. 准备测试数据
    test_data = pd.DataFrame({
        'InstrumentID': ['SR309'],
        'TradingDay': ['2023-09-01']
    })
    
    result_df = translator.apply(test_data)
    
    # 5. 验证是否调用了 mock 方法，以及转换后的值是否符合预期
    mock_registry.translate_code.assert_called_once_with('exchange_to_internal', 'SR309', pd.to_datetime('2023-09-01').date())
    assert result_df['InstrumentID'].tolist() == ['SR2309']

# ======================================================
# 4. 测试 DataEnricherPriceLimit
# ======================================================
def test_data_enricher_price_limit():
    import datetime

    test_df = pd.DataFrame({
        'InstrumentID': ['RB2610','RB2610'],
        'TradingDay': [20260109, 20260109],
    })

    mock_daily_snapshot_manager = MagicMock()
    mock_daily_snapshot_manager.get_price_limits.return_value = (3600, 3100)

    enricher = DataEnricherPriceLimit('RB', daily_snapshot_manager=mock_daily_snapshot_manager)
    result_df = enricher.apply(test_df)

    mock_daily_snapshot_manager.get_price_limits.assert_called_once_with(
        product='RB',
        contract='RB2610',
        trading_date=datetime.date(2026, 1, 9)
    )
    assert result_df['UpperLimitPrice'].tolist() == [3600, 3600]
    assert result_df['LowerLimitPrice'].tolist() == [3100, 3100]

# =======================================
# 测试 DataEnricherGenerateCalendarTimestamp
# =======================================
def test_data_enricher_generate_calendar_timestamp():
    import numpy as np

    real_df = pd.read_csv('d:/量化策略/CTA策略/RAW_DATA/TICK/2026/20260109/rb2610.csv')
    first_row = real_df.iloc[0,:]

    test_df = pd.DataFrame({
        'InstrumentID': first_row['Instrument'],
        'TradingDay': first_row['TradingDay'],
        'UpdateTime': first_row['UpdateTime'],
        'UpdateMillisec': first_row['UpdateMillisec']
    })

    mock_calendar = MagicMock()
    mock_calendar.get_calendar_timestamps_from_traing_date_and_time.return_value = np.array([])