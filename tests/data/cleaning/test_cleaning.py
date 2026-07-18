import pytest
import pandas as pd
from unittest.mock import MagicMock

# 导入被测试的清洗组件
from data.cleaning.cleaning_tick import (
    OutlierFilter,
    PriceLimitFilter,
    InstrumentCodeTranslator,
    CleaningPipeline,
    DataEnricherPriceLimit,
    DataEnricherGenerateCalendarTimestamp,
    OutputSchemaEnforcer
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
# 3. 测试 InstrumentCodeTranslator
# ==========================================

def test_instrument_code_translator(registry_manager):
    """
    测试合约代码翻译器。
    """
    translator = InstrumentCodeTranslator(registry_manager=registry_manager)

    test_df = pd.DataFrame({
        'InstrumentID': ['AP805'],
        'TradingDay': [20180119]
    })

    result_df = translator.apply(test_df)
    assert result_df['InstrumentID'].tolist()[0] == 'AP1805'

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
        contract='RB2610',
        trading_date=datetime.date(2026, 1, 9)
    )
    assert result_df['UpperLimitPrice'].tolist() == [3600, 3600]
    assert result_df['LowerLimitPrice'].tolist() == [3100, 3100]

# =======================================
# 测试 DataEnricherGenerateCalendarTimestamp
# =======================================
def test_data_enricher_generate_calendar_timestamp(exchange_calendar_manager,
                                                   meta_manager, rule_service):
    import numpy as np
    from data.calendar.product_calendar import ProductCalendar

    real_df = pd.read_csv('d:/量化策略/CTA策略/RAW_DATA/TICK/2026/20260109/rb2610.csv')
    row = real_df.iloc[3,:]

    test_df = pd.DataFrame({
        'InstrumentID': [row['InstrumentID']],
        'TradingDay': [row['TradingDay']],
        'UpdateTime': [row['UpdateTime']],
        'UpdateMillisec': [row['UpdateMillisec']]
    })

    rb_calendar = ProductCalendar(product='rb', meta_manager=meta_manager,
                                  exchange_calendar_manager=exchange_calendar_manager,
                                  trading_rule_service=rule_service)
    
    enricher = DataEnricherGenerateCalendarTimestamp(rb_calendar)
    result_df = enricher.apply(test_df)
    assert result_df['calendar_timestamp'].tolist()[0] == pd.Timestamp('2026-01-08 21:00:00.500000')

# ============================================
# 测试清洗流水线（全滤芯集成测试）
# ============================================
def test_pipeline(exchange_calendar_manager, meta_manager, rule_service, registry_manager):
    """全流程测试：读取真实 TICK 原始数据，流过所有滤芯，验证最终产出。"""
    from data.calendar.product_calendar import ProductCalendar
    from data.storage.daily_snapshot_loader import DailySnapshotLoader
    from data.storage.daily_snapshot_manager import DailySnapshotManager

    # ----------------------------------------------------------
    # 0. 准备：读取真实 TICK 原始数据（取前 200 行，涵盖异常行和正常数据）
    # ----------------------------------------------------------
    raw_df = pd.read_csv(
        'd:/量化策略/CTA策略/RAW_DATA/TICK/2026/20260109/rb2610.csv',
        nrows=200
    )
    print(f"\n📥 原始数据行数: {len(raw_df)}")

    # 记录前 5 行的 UpdateTime 用于观察
    print("原始前5行时间:", raw_df['UpdateTime'].head(5).tolist())

    # ----------------------------------------------------------
    # 1. 构建 ProductCalendar（RB 品种）
    # ----------------------------------------------------------
    rb_calendar = ProductCalendar(
        product='rb',
        meta_manager=meta_manager,
        exchange_calendar_manager=exchange_calendar_manager,
        trading_rule_service=rule_service
    )

    # ----------------------------------------------------------
    # 2. 构建真实的 DailySnapshotManager（涨跌停价查询）
    # ----------------------------------------------------------
    daily_snapshot_dir = r'db\market\daily_snapshots\bar_1d\bar_1d_tushare\cleaned'
    loader = DailySnapshotLoader(daily_snapshot_dir, meta_manager)
    daily_snapshot_manager = DailySnapshotManager(
        daily_snapshot_loader=loader,
        registry_manager=registry_manager
    )

    # ----------------------------------------------------------
    # 3. 组装完整清洗流水线
    # ----------------------------------------------------------
    pipeline = CleaningPipeline()
    pipeline.add_filter(InstrumentCodeTranslator(registry_manager=registry_manager))
    pipeline.add_filter(DataEnricherPriceLimit(
        product='RB',
        daily_snapshot_manager=daily_snapshot_manager
    ))
    pipeline.add_filter(DataEnricherGenerateCalendarTimestamp(
        calendar=rb_calendar,
        drop_invalid_time_rows=True
    ))
    pipeline.add_filter(OutlierFilter())
    pipeline.add_filter(PriceLimitFilter())
    pipeline.add_filter(OutputSchemaEnforcer())

    # ----------------------------------------------------------
    # 4. 执行清洗
    # ----------------------------------------------------------
    clean_df = pipeline.process(raw_df)

    # ----------------------------------------------------------
    # 5. 验证
    # ----------------------------------------------------------
    print(f"\n✅ 清洗后数据行数: {len(clean_df)}")
    print(f"   剔除行数: {len(raw_df) - len(clean_df)}")
    print(f"   预期剔除: 18:47 结算快照 2 行 + 20:59 竞价撮合 1 行 = 3 行")

    # 5a. 不应再有 18:47 或 20:59 的数据（它们不在 scheduled 交易时段内）
    clean_times = clean_df['calendar_timestamp'].dt.time
    anomalous = clean_times[clean_times.astype(str).str.startswith(('18:47', '20:59'))]
    assert len(anomalous) == 0, f'仍有非法交易时段数据残留: {anomalous.tolist()}'

    # 5b. 输出列名应匹配 OutputSchemaEnforcer 的 rename_map
    expected_cols = [
        'contract', 'trading_date', 'calendar_timestamp',
        'last', 'bid', 'ask', 'bid_volume', 'ask_volume',
        'open_interest', 'is_limit_up', 'is_limit_down'
    ]
    for col in expected_cols:
        assert col in clean_df.columns, f'缺少期望列: {col}'

    # 5c. 数据类型校验
    assert clean_df['last'].dtype == 'float64'
    assert clean_df['calendar_timestamp'].dtype == 'datetime64[ns]'
    assert clean_df['is_limit_up'].dtype == 'bool'

    # 5d. 不应有 last <= 0 的数据
    assert (clean_df['last'] > 0).all(), '存在 LastPrice <= 0 的数据'

    # 5e. 日历时间戳应单调递增
    assert clean_df['calendar_timestamp'].is_monotonic_increasing, \
        'calendar_timestamp 应单调递增'

    print("\n🏁 全流程清洗测试通过！")
    print(clean_df.head(10).to_string())


