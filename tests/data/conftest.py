import pytest
from data.rules.product_meta_manager import ProductMetaManager
from data.rules.instrument_registry_manager import InstrumentRegistryManager
from data.rules.instrument_daily_status_manager import InstrumentDailyStatusManager
from data.rules.exchange_holiday_manager import ExchangeHolidayManager
from data.calendar.exchange_calendar_manager import ExchangeCalendarManager
from data.rules.trading_rule_service import TradingRuleService
from data.storage.daily_snapshot_loader import DailySnapshotLoader
from data.storage.daily_snapshot_manager import DailySnapshotManager, ContractDailySnapshot

# ----------------------------------------------------------------
# 基础目录配置 Fixtures
# ----------------------------------------------------------------

@pytest.fixture(scope="session")
def product_meta_dir():
    return 'db/rules/product_meta'

@pytest.fixture(scope="session")
def instrument_registry_dir():
    return 'db/rules/instrument_registry'

@pytest.fixture(scope="session")
def instrument_daily_status_dir():
    return r'db\rules\instrument_daily_status'

@pytest.fixture(scope="session")
def exchange_holiday_dir():
    return r'db\rules\exchange_holiday'

@pytest.fixture(scope="session")
def calendar_dir():
    return r'db\calendar\trading_calendars'

@pytest.fixture(scope='session')
def daily_snapshot_dir():
    return r'db\market\daily_snapshots\bar_1d\bar_1d_tushare\cleaned'

# ----------------------------------------------------------------
# 各业务数据管理器 Fixtures (设置为 session 级别，避免重复读取硬盘数据)
# ----------------------------------------------------------------

@pytest.fixture(scope="session")
def meta_manager(product_meta_dir):
    return ProductMetaManager(product_meta_dir)

@pytest.fixture(scope="session")
def registry_manager(instrument_registry_dir):
    return InstrumentRegistryManager(instrument_registry_dir)

@pytest.fixture(scope="session")
def daily_status_manager(instrument_daily_status_dir, meta_manager, registry_manager):
    return InstrumentDailyStatusManager(
        instrument_daily_status_dir,
        meta_manager=meta_manager, 
        registry_manager=registry_manager
    )

@pytest.fixture(scope="session")
def holiday_manager(exchange_holiday_dir):
    return ExchangeHolidayManager(exchange_holiday_dir=exchange_holiday_dir)

@pytest.fixture(scope="session")
def exchange_calendar_manager(calendar_dir):
    return ExchangeCalendarManager(calendar_dir)

@pytest.fixture(scope='session')
def daily_snapshot_loader(daily_snapshot_dir, meta_manager):
    return DailySnapshotLoader(daily_snapshot_dir=daily_snapshot_dir, 
                               meta_manager=meta_manager)

@pytest.fixture(scope='session')
def daily_snapshot_manager(daily_snapshot_loader, registry_manager):
    return DailySnapshotManager(daily_snapshot_loader=daily_snapshot_loader,
                                registry_manager=registry_manager)
# ----------------------------------------------------------------
# 统一交易规则服务门面 Fixture
# ----------------------------------------------------------------

@pytest.fixture(scope="session")
def rule_service(meta_manager, registry_manager, holiday_manager, exchange_calendar_manager, daily_status_manager):
    return TradingRuleService(
        meta_manager=meta_manager,
        registry_manager=registry_manager,
        holiday_manager=holiday_manager,
        exchange_calendar_manager=exchange_calendar_manager,
        daily_status_manager=daily_status_manager,
    )
