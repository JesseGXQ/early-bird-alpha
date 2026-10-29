"""T1 引擎字段暴露测试（合成数据，不依赖真实数据库）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd

from data.feed.research_feed import RFEED_1d_COLUMNS, ResearchFeed
from data.feed.term_structure_feed import TermStructureFeed


def test_research_feed_daily_exposes_price_limits() -> None:
    """日频复权路径应把快照涨跌停价暴露为 Upper_Limit / Lower_Limit。"""
    timestamp = pd.Timestamp('2025-01-06')
    index = pd.DatetimeIndex([timestamp], name='Datetime')
    raw = pd.DataFrame(
        {
            'Open': [100.0],
            'High': [102.0],
            'Low': [99.0],
            'Close': [101.0],
            'Settle': [100.5],
            'upper_limit': [110.0],
            'lower_limit': [90.0],
            'Volume': [1000.0],
            'OpenInterest': [500.0],
        },
        index=index,
    )
    master = pd.DataFrame(
        {
            'Dominant_Contract': ['RB2505'],
            'Price_Gap': [0.0],
            'Adj_Factor': [5.0],
            'roll_state': ['not_roll'],
            'is_roll': [False],
            'is_contract_switch': [False],
            'continuity_segment_id': [1],
        },
        index=index,
    )
    feed = ResearchFeed.__new__(ResearchFeed)

    result = feed._adjust_price(raw, master, daily_freq=True)

    assert result.at[timestamp, 'Upper_Limit'] == 110.0
    assert result.at[timestamp, 'Lower_Limit'] == 90.0
    assert result.at[timestamp, 'Adj_Close'] == 106.0
    # True_Return 在 _calculate_true_return_daily 中另行计算
    assert (
        set(RFEED_1d_COLUMNS) - {'True_Return'}
    ).issubset(set(result.columns))


def test_term_structure_feed_exposes_contract_multiplier() -> None:
    """期限结构输出应包含来自合约注册表的 multiplier。"""
    timestamp = pd.Timestamp('2025-01-06')
    product_df = pd.DataFrame(
        {
            'Contract': ['RB2505', 'RB2510'],
            'Datetime': [timestamp, timestamp],
            'Close': [3000.0, 3100.0],
            'Settle': [3001.0, 3101.0],
            'Volume': [100.0, 200.0],
            'OpenInterest': [1000.0, 500.0],
        }
    )

    class _FakeDailySnapshotManager:
        def get_product_df(
                self, product: str, normalize_col_names: bool = True
        ) -> pd.DataFrame:
            return product_df.copy()

    class _FakeRegistryManager:
        def get_instrument_registry(self, contract: str):
            last_trade_date = (
                date(2025, 5, 15)
                if contract == 'RB2505'
                else date(2025, 10, 15)
            )
            return SimpleNamespace(
                last_trade_date=last_trade_date,
                multiplier=10,
            )

    class _FakeContractManager:
        master_table = pd.DataFrame(
            {'Dominant_Contract': ['RB2505']},
            index=pd.DatetimeIndex([timestamp]),
        )

    feed = TermStructureFeed(
        product='RB',
        daily_snapshot_manager=_FakeDailySnapshotManager(),  # type: ignore[arg-type]
        registry_manager=_FakeRegistryManager(),  # type: ignore[arg-type]
        contract_manager=_FakeContractManager(),  # type: ignore[arg-type]
    )

    result = feed.get_term_structure_data(
        start_date=date(2025, 1, 6),
        end_date=date(2025, 1, 6),
    )

    assert 'multiplier' in result.columns
    assert (result['multiplier'] == 10).all()
