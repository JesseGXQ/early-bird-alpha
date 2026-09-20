from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from data.config import RolloverConfig
from data.stitching.models import RollState
from data.stitching.stitching import ContinuousContractManager, RollGapCalculator, RolloverDetector
from scripts.build_product_master_table import build_product_master_table


class StubRegistryManager:
    """为换月单测提供合约最后交易日。"""

    def __init__(self, last_trade_dates: dict[str, date]):
        self.last_trade_dates = last_trade_dates

    def get_instrument_registry(self, contract: str) -> SimpleNamespace:
        return SimpleNamespace(last_trade_date=self.last_trade_dates[contract])


class StubCalendar:
    """为换月单测提供连续工作日历。"""

    def __init__(self):
        self.exchange_trading_dates = pd.bdate_range('2026-01-01', '2026-02-27')

    def next_trading_date(self, trading_date: date) -> date:
        dates = self.exchange_trading_dates
        position = dates.searchsorted(pd.Timestamp(trading_date), side='right')
        if position >= len(dates):
            raise ValueError('交易日历已到末尾。')
        return dates[position].date()


def _detector(last_trade_dates, *, buffer_days=3, transition_days=1):
    return RolloverDetector(
        registry_manager=StubRegistryManager(last_trade_dates),
        calendar=StubCalendar(),
        buffer_days=buffer_days,
        ratio_threshold=1.1,
        transition_days=transition_days,
    )


@pytest.mark.parametrize(
    ('transition_days', 'last_trade_date'),
    [(1, '2026-01-06'), (2, '2026-01-07')],
)
def test_hard_rule_starts_transition_before_expiry(transition_days, last_trade_date):
    """硬规则提前触发，旧合约在最后交易日及此前的过渡日仍可交易。"""
    dates = pd.bdate_range('2026-01-01', '2026-01-12')
    oi = pd.DataFrame({'OLD': 100.0, 'NEXT': 60.0}, index=dates)
    detector = _detector(
        {'OLD': date.fromisoformat(last_trade_date), 'NEXT': date(2026, 2, 27)},
        transition_days=transition_days,
    )
    calendar = detector.generate_rollover_calendar(oi)

    assert calendar.at[pd.Timestamp('2026-01-05'), 'Dominant_Contract'] == 'OLD'
    assert calendar.at[pd.Timestamp('2026-01-06'), 'Dominant_Contract'] == 'NEXT'
    manager = ContinuousContractManager.__new__(ContinuousContractManager)
    manager.master_table = calendar
    manager.dates_list = calendar.index.tolist()
    manager.transition_days = transition_days
    first_roll = manager.get_active_physical_contracts(date(2026, 1, 6))
    assert first_roll.contracts == ('OLD', 'NEXT')
    assert first_roll.roll_state is RollState.ROLL
    last_roll = manager.get_active_physical_contracts(date.fromisoformat(last_trade_date))
    assert last_roll.contracts == ('OLD', 'NEXT')
    assert last_roll.roll_state is RollState.ROLL
    after_expiry = detector.calendar.next_trading_date(date.fromisoformat(last_trade_date))
    assert manager.get_active_physical_contracts(after_expiry).contracts == ('NEXT',)


def test_soft_rule_counts_only_threshold_crossing_days():
    """候选首日未跨阈值不得计入连续三日，确认后次日才生效。"""
    dates = pd.bdate_range('2026-01-02', '2026-01-09')
    oi = pd.DataFrame(
        {'OLD': [100.0] * len(dates), 'NEXT': [80, 109, 111, 110, 120, 120]},
        index=dates,
    )
    calendar = _detector({
        'OLD': date(2026, 2, 27), 'NEXT': date(2026, 2, 27),
    }).generate_rollover_calendar(oi)

    assert calendar.at[pd.Timestamp('2026-01-08'), 'Dominant_Contract'] == 'OLD'
    assert calendar.at[pd.Timestamp('2026-01-09'), 'Dominant_Contract'] == 'NEXT'


def test_soft_rule_with_one_confirmation_day():
    """缓冲天数为一时，达标当天确认，下一交易日生效。"""
    dates = pd.bdate_range('2026-01-02', '2026-01-07')
    oi = pd.DataFrame({'OLD': [100.0] * 4, 'NEXT': [50, 110, 120, 120]}, index=dates)
    calendar = _detector({
        'OLD': date(2026, 2, 27), 'NEXT': date(2026, 2, 27),
    }, buffer_days=1).generate_rollover_calendar(oi)

    assert calendar.at[pd.Timestamp('2026-01-05'), 'Dominant_Contract'] == 'OLD'
    assert calendar.at[pd.Timestamp('2026-01-06'), 'Dominant_Contract'] == 'NEXT'


def test_hard_rule_skips_ineligible_higher_oi_contract():
    """硬规则在合格合约中取最大持仓，跳过即将到期的更大持仓。"""
    dates = pd.bdate_range('2026-01-02', '2026-01-08')
    oi = pd.DataFrame(
        {'OLD': [100.0] * 5, 'EXPIRED': [0, 200, 200, 0, 0], 'NEXT': [60.0] * 5},
        index=dates,
    )
    calendar = _detector({
        'OLD': date(2026, 1, 6),
        'EXPIRED': date(2026, 1, 6),
        'NEXT': date(2026, 2, 27),
    }).generate_rollover_calendar(oi)

    assert calendar.at[pd.Timestamp('2026-01-06'), 'Dominant_Contract'] == 'NEXT'
    assert calendar.at[pd.Timestamp('2026-01-05'), 'Max_OI_Contract_Today'] == 'EXPIRED'


def test_hard_rule_without_successor_leaves_next_day_unassigned():
    """最后交易日前没有后继时，应明确标记失败而非延用即将到期的主力。"""
    dates = pd.bdate_range('2026-01-02', '2026-01-07')
    oi = pd.DataFrame({'OLD': [100.0] * 4}, index=dates)
    calendar = _detector({'OLD': date(2026, 1, 6)}).generate_rollover_calendar(oi)

    assert calendar.at[pd.Timestamp('2026-01-05'), 'Fail_Reason'] == (
        'hard_rule_but_no_other_contract'
    )
    assert pd.isna(calendar.at[pd.Timestamp('2026-01-06'), 'Dominant_Contract'])


def test_no_current_dominant_skips_ineligible_higher_oi_contract():
    """重新选择主力时，已不满足过渡期的最大持仓合约不能挡住有效合约。"""
    dates = pd.bdate_range('2026-01-02', '2026-01-07')
    oi = pd.DataFrame(
        {'EXPIRING': [200.0] * len(dates), 'NEXT': [100.0] * len(dates)},
        index=dates,
    )
    calendar = _detector({
        'EXPIRING': date(2026, 1, 5),
        'NEXT': date(2026, 2, 27),
    }).generate_rollover_calendar(oi)

    assert pd.isna(calendar.at[pd.Timestamp('2026-01-02'), 'Dominant_Contract'])
    assert calendar.at[pd.Timestamp('2026-01-05'), 'Dominant_Contract'] == 'NEXT'
    assert calendar.at[pd.Timestamp('2026-01-02'), 'Max_OI_Contract_Today'] == 'EXPIRING'


def test_rollover_calendar_sorts_dates_before_processing():
    """无序的输入日期应先按时间排序，再逐日决定下一交易日的主力。"""
    dates = pd.bdate_range('2026-01-02', '2026-01-06')
    oi = pd.DataFrame({'OLD': [100.0] * len(dates)}, index=dates[::-1])
    calendar = _detector({'OLD': date(2026, 2, 27)}).generate_rollover_calendar(oi)

    assert calendar.index.equals(dates)
    assert calendar.at[pd.Timestamp('2026-01-05'), 'Dominant_Contract'] == 'OLD'


def test_soft_rule_skips_ineligible_higher_oi_contract():
    """失效合约不能阻挡持仓量较低但有效的软规则候选人。"""
    dates = pd.bdate_range('2026-01-02', '2026-01-08')
    oi = pd.DataFrame(
        {'OLD': [100.0] * 5, 'EXPIRED': [0, 200, 200, 0, 0],
         'NEXT': [60.0, 120.0, 120.0, 120.0, 120.0]},
        index=dates,
    )
    calendar = _detector({
        'OLD': date(2026, 2, 27),
        'EXPIRED': date(2026, 1, 6),
        'NEXT': date(2026, 2, 27),
    }, buffer_days=2).generate_rollover_calendar(oi)

    assert calendar.at[pd.Timestamp('2026-01-07'), 'Dominant_Contract'] == 'NEXT'


def test_failed_decision_does_not_become_next_day_state():
    """当前主力 OI 缺失后，下一日主力为空；再恢复时重新初始化。"""
    dates = pd.bdate_range('2026-01-02', '2026-01-08')
    oi = pd.DataFrame(
        {'OLD': [100.0, float('nan'), 120.0, 120.0, 120.0],
         'NEXT': [50.0, 90.0, 80.0, 80.0, 80.0]},
        index=dates,
    )
    calendar = _detector({
        'OLD': date(2026, 2, 27), 'NEXT': date(2026, 2, 27),
    }).generate_rollover_calendar(oi)

    assert pd.isna(calendar.at[pd.Timestamp('2026-01-06'), 'Dominant_Contract'])
    assert calendar.at[pd.Timestamp('2026-01-05'), 'Fail_Reason'] == (
        'current_dom_not_present_thus_inpossible_to_comparable'
    )
    assert calendar.at[pd.Timestamp('2026-01-07'), 'Dominant_Contract'] == 'OLD'


def test_last_snapshot_does_not_create_future_master_row():
    """最后一日 OI 不生成缺乏行情覆盖的次日主力。"""
    dates = pd.bdate_range('2026-01-02', '2026-01-06')
    oi = pd.DataFrame({'OLD': [100.0] * 3}, index=dates)
    calendar = _detector({'OLD': date(2026, 2, 27)}).generate_rollover_calendar(oi)

    assert calendar.index.equals(dates)
    assert pd.isna(calendar.at[dates[0], 'Dominant_Contract'])
    assert calendar.at[dates[-1], 'Dominant_Contract'] == 'OLD'


def test_roll_gap_keeps_unknown_gap_missing():
    """主力缺失附近的价差未知，普通无换月日的价差为零。"""
    dates = pd.bdate_range('2026-01-02', '2026-01-07')
    calendar = pd.DataFrame(
        {'Dominant_Contract': [None, 'OLD', 'OLD', 'NEXT']}, index=dates,
    )
    prices = pd.DataFrame(
        {'OLD': [100.0] * 4, 'NEXT': [110.0] * 4}, index=dates,
    )
    master = RollGapCalculator().build_master_table(calendar, prices)

    assert pd.isna(master.at[dates[0], 'Price_Gap'])
    assert master.at[dates[1], 'Price_Gap'] == 0.0
    assert master.at[dates[2], 'Price_Gap'] == 10.0
    assert master.at[dates[2], 'Price_Gap_Basis'] == 'close'
    assert pd.isna(master.at[dates[3], 'Price_Gap'])


def test_roll_gap_uses_paired_settlement_fallback():
    """任一收盘价缺失时，新旧主力必须成对回退至结算价。"""
    dates = pd.bdate_range('2026-01-05', periods=2)
    calendar = pd.DataFrame(
        {'Dominant_Contract': ['OLD', 'NEXT']}, index=dates,
    )
    closes = pd.DataFrame(
        {'OLD': [None, None], 'NEXT': [120.0, 121.0]}, index=dates,
    )
    settlements = pd.DataFrame(
        {'OLD': [100.0, 101.0], 'NEXT': [115.0, 116.0]}, index=dates,
    )

    master = RollGapCalculator().build_master_table(
        calendar, closes, settle_matrix=settlements,
    )

    assert master.at[dates[0], 'Price_Gap'] == 15.0
    assert master.at[dates[0], 'Price_Gap_Basis'] == 'settle_fallback'


def test_roll_gap_rejects_incomplete_close_and_settlement_pairs():
    """已知换月边界无法取得任一完整价格对时必须严格失败。"""
    dates = pd.bdate_range('2026-01-05', periods=2)
    calendar = pd.DataFrame(
        {'Dominant_Contract': ['OLD', 'NEXT']}, index=dates,
    )
    closes = pd.DataFrame(
        {'OLD': [None, None], 'NEXT': [120.0, 121.0]}, index=dates,
    )
    settlements = pd.DataFrame(
        {'OLD': [100.0, 101.0], 'NEXT': [None, 116.0]}, index=dates,
    )

    with pytest.raises(ValueError, match='无法取得完整的结算价对'):
        RollGapCalculator().build_master_table(
            calendar, closes, settle_matrix=settlements,
        )


def test_builder_passes_calendar_and_transition_days(tmp_path):
    """构建入口传递统一日历、过渡天数以及两种日频价格。"""
    class StubLoader:
        requested_columns = None

        def load(self, product, columns):
            self.requested_columns = columns
            rows = []
            for day in pd.bdate_range('2026-01-02', '2026-01-06'):
                rows.extend([
                    {'date': day, 'instrument': 'OLD', 'oi': 100.0,
                     'close': 100.0, 'settle': 101.0},
                    {'date': day, 'instrument': 'NEXT', 'oi': 50.0,
                     'close': 110.0, 'settle': 115.0},
                ])
            return pd.DataFrame(rows)

    config = RolloverConfig(transition_days=1)
    loader = StubLoader()
    build_product_master_table(
        product='TEST', rollover_config=config,
        master_table_dir=str(tmp_path), daily_snapshot_loader=loader,
        registry_manager=StubRegistryManager({
            'OLD': date(2026, 1, 6), 'NEXT': date(2026, 2, 27),
        }),
        calendar=StubCalendar(),
    )
    master = pd.read_parquet(tmp_path / f'TEST_{config.signature}.parquet')
    assert loader.requested_columns == [
        'date', 'instrument', 'oi', 'close', 'settle'
    ]
    assert master.at[pd.Timestamp('2026-01-05'), 'Price_Gap'] == 10.0
    assert master.at[pd.Timestamp('2026-01-05'), 'Price_Gap_Basis'] == 'close'
    assert master.at[pd.Timestamp('2026-01-06'), 'Dominant_Contract'] == 'NEXT'
