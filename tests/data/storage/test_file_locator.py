import datetime

import pytest

from data.storage.file_locator import FileLocator
from data.calendar.product_calendar import ProductCalendar

product = 'IF' 

# 构建一个品种级交易日历
@pytest.fixture
def calendar(meta_manager, rule_service, exchange_calendar_manager):
    calendar = ProductCalendar(
        product=product, 
        meta_manager=meta_manager,
        exchange_calendar_manager=exchange_calendar_manager,
        trading_rule_service=rule_service
    )
    return calendar

def test_file_locator_1min(
        meta_manager, registry_manager, daily_status_manager, holiday_manager,
        exchange_calendar_manager, rule_service, calendar, data_dir_1min
):
    file_locator = FileLocator(
        data_dir=data_dir_1min,
        meta_manager = meta_manager,
        exchange_calendar_manager=exchange_calendar_manager,
        registry_manager = registry_manager,
    )

    contract = 'IF2109'
    path = file_locator.get_1min_file_path(contract)
    print(f'合约 {contract} 在本地的 1min 文件路径为：{path}')

    contract2 = 'IF2808' 
    # 下面这行应该在 registry_manager.get_instrument_registry 就会报错 KeyError
    with pytest.raises(KeyError):
        path = file_locator.get_1min_file_path(contract2)

def test_file_locator_tick(meta_manager, registry_manager, daily_status_manager, holiday_manager,
        exchange_calendar_manager, rule_service, calendar, tick_data_dir
):
    file_locator = FileLocator(
        data_dir=tick_data_dir,
        meta_manager=meta_manager,
        exchange_calendar_manager=exchange_calendar_manager,
        registry_manager=registry_manager,
    )
    contract = 'TF2606'
    trading_date = datetime.date(2026, 1, 9)
    path = file_locator.get_tick_file_path(contract, trading_date=trading_date)

    assert path != ''
    print(f'合约 {contract} 在交易日 {trading_date} 的 本地 tick 文件路径为 {path}')

