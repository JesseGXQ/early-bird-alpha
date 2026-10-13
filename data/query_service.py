from __future__ import annotations
from typing import Literal, TYPE_CHECKING

from data.factory import _create_trading_rule_components
from data.config import EnvironmentConfig
from datetime import date

if TYPE_CHECKING:
    from data.rules.models import TradingSessions
    import pandas as pd


class RuleQueryService:
    """面向业务的查询接口，用于查询期货交易规则类数据"""

    def __init__(self, env_config: EnvironmentConfig):
        (self._meta_manager, 
        self._registry_manager,
        self._holiday_manager, 
        self._exchange_calendar_manager,
        self._daily_status_manager,
        self._trading_rule_service,
        self._classification_manager
        )=_create_trading_rule_components(env_config=env_config)

        self.classification = self._classification_manager # 挂上品种分类管家，暴露给下游

    def get_multiplier(self, contract: str) -> int:
        """获取合约乘数。"""
        # 直接委托给 registry_manager
        return self._registry_manager.get_instrument_registry(contract).multiplier

    def get_list_date_product(self, product: str) -> date:
        """获取品种上市日期"""
        return self._meta_manager.get_static_meta(product).list_date

    def get_delist_date_product(self, product: str) -> date | None:
        """获取品种退市日期。

        与 `get_list_date_product` 对称，读取 TIER1 静态元数据的 `delist_date`。

        Parameters
        ----------
        product: str
            品种代码，例如'RB'。

        Returns
        -------
        date | None
            品种退市日期；``None`` 表示该品种仍在市。

        Notes
        -----
        口径见 `StaticMeta.delist_date`：取值为**该品种最后一个合约的最后交易日**
        （`InstrumentRegistry.last_trade_date`），而非交易所公告的挂牌终止日，
        也不是最后一个合约的最后交割日（`end_deliv_date`）。
        只有因**品种更换代码**（如郑商所 WS 换 WH、ME 换 MA）而退市的品种才有该值。
        """
        return self._meta_manager.get_static_meta(product).delist_date

    def get_product_universe(self, return_type: Literal['list', 'dict'] = 'list'):
        """获取全市场期货品种。"""
        return self._meta_manager.get_product_universe(return_type=return_type)

    # def get_product_classification(self) -> pd.DataFrame:
    #     """
    #     获取全市场品种分类元数据。

    #     Returns
    #     -------
    #     pd.DataFrame
    #         列: product, exchange, product_name, asset_class, sector, subsector, supply_chain
    #     """
    #     return self._classification_manager.get_classification()
