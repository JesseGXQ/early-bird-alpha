"""主力合约解析的三态与窗口边界测试。"""

from datetime import date

import pandas as pd
import pytest

from data.stitching.models import MultipleRolloverError, RollState
from data.stitching.stitching import ContinuousContractManager


def _manager(dominants: list[str | None], transition_days: int = 1):
    dates = pd.bdate_range('2026-01-02', periods=len(dominants))
    manager = ContinuousContractManager.__new__(ContinuousContractManager)
    manager.product = 'TEST'
    manager.master_table = pd.DataFrame({'Dominant_Contract': dominants}, index=dates)
    manager.dates_list = dates.tolist()
    manager.transition_days = transition_days
    return manager, dates


def test_no_dominant_and_recovery_keep_known_contract():
    """空主力与历史断档分开表达；恢复首日仍保留已知主力。"""
    manager, dates = _manager([None, 'A', None, 'B', 'B'])

    first = manager.get_active_physical_contracts(dates[0].date())
    assert first.dominant is None
    assert first.contracts == ()
    assert first.roll_state is RollState.UNKNOWN

    resumed = manager.get_active_physical_contracts(dates[1].date())
    assert resumed.contracts == ('A',)
    assert resumed.outgoing is None
    assert resumed.roll_state is RollState.UNKNOWN

    assert manager.get_active_physical_contracts(dates[2].date()).dominant is None
    resumed_again = manager.get_active_physical_contracts(dates[3].date())
    assert resumed_again.contracts == ('B',)
    assert resumed_again.roll_state is RollState.UNKNOWN
    stable = manager.get_active_physical_contracts(dates[4].date())
    assert stable.contracts == ('B',)
    assert stable.roll_state is RollState.NOT_ROLL


def test_one_change_remains_roll_for_entire_transition_window():
    """过渡窗口只发生一次换月时，旧主力一直是变化前的合约。"""
    manager, dates = _manager(['A', 'A', 'B', 'B', 'B'], transition_days=2)

    for trading_date in dates[2:4]:
        selected = manager.get_active_physical_contracts(trading_date.date())
        assert selected.contracts == ('A', 'B')
        assert selected.roll_state is RollState.ROLL
    assert manager.get_active_physical_contracts(dates[4].date()).roll_state is RollState.NOT_ROLL


def test_multiple_changes_and_missing_table_date_are_errors():
    """多次切换不能压缩为单个旧主力；缺少整行也不是空主力。"""
    manager, dates = _manager(['A', 'B', 'C'], transition_days=2)

    with pytest.raises(MultipleRolloverError, match='多次换月'):
        manager.get_active_physical_contracts(dates[2].date())
    with pytest.raises(ValueError, match='找不到所查询的日期'):
        manager.get_active_physical_contracts(date(2026, 1, 1))
