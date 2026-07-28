from __future__ import annotations
from typing import Literal, TYPE_CHECKING

from data.factory import _create_trading_rule_components
from data.config import EnvironmentConfig
from datetime import date

if TYPE_CHECKING:
    from data.rules.models import TradingSessions


class RuleQueryService:
    """面向业务的查询接口，用于查询期货交易规则类数据"""

    def __init__(self, env_config: EnvironmentConfig = EnvironmentConfig()):
        (self._meta_manager, 
        self._registry_manager,
        self._holiday_manager, 
        self._exchange_calendar_manager,
        self._daily_status_manager,
        self._trading_rule_service
        )=_create_trading_rule_components(env_config=env_config)

    def get_multiplier(self, contract: str) -> int:
        """获取合约乘数。"""
        # 直接委托给 registry_manager
        return self._registry_manager.get_instrument_registry(contract).multiplier

    def get_list_date_product(self, product: str) -> date:
        """获取品种上市日期"""
        return self._meta_manager.get_static_meta(product).list_date
