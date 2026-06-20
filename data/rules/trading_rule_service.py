"""
期货交易规则统一门面
"""
from __future__ import annotations
from datetime import date
from typing import TYPE_CHECKING, Literal
import copy

from data.rules.models import TradingSessions
from data.rules.session_rule_resolver import SessionRuleResolver

if TYPE_CHECKING:
    from data.rules.product_meta_manager import ProductMetaManager
    from data.rules.instrument_registry_manager import InstrumentRegistryManager
    from data.rules.exchange_holiday_manager import ExchangeHolidayManager
    from data.rules.instrument_daily_status_manager import InstrumentDailyStatusManager


class TradingRuleService:
    """
    面向下游的期货交易规则统一门面服务，组合 TIER1/2/3 数据库，输出各种交易规则。

    Parameters
    ----------
    meta_manager : ProductMetaManager
        品种规则配置管家（Tier 1 品种元数据）。
    registry_manager : InstrumentRegistryManager
        合约注册数据管家（Tier 2 合约注册）。
    holiday_manager : ExchangeHolidayManager
        交易所节假日管家。
    daily_status_manager: InstrumentDailyStatusManger or None, default None
        合约日频动态状态属性管家。默认为 None。
    
    Methods
    ------
    get_trading_sessions(contract, trading_date, view)
        获取合约在指定交易日的真实交易时段（计划或实际）。
    """

    def __init__(
        self,
        meta_manager: ProductMetaManager,
        registry_manager: InstrumentRegistryManager,
        holiday_manager: ExchangeHolidayManager,
        daily_status_manager: InstrumentDailyStatusManager | None = None # 可选持有
    ):
        self.meta_manager: ProductMetaManager = meta_manager
        self.registry_manager: InstrumentRegistryManager = registry_manager
        self.holiday_manager: ExchangeHolidayManager = holiday_manager
        self.daily_status_manager: InstrumentDailyStatusManager | None = daily_status_manager

        self._trading_sessions_cache: dict[tuple[str, date, str], TradingSessions] = {} # key: (contract, trading_date, view)
        self._session_rule_resolver: SessionRuleResolver | None = None 

    def _get_session_rule_resolver(self) -> SessionRuleResolver: # lazy construction
        if self._session_rule_resolver is None:
            self._session_rule_resolver = SessionRuleResolver(
                registry_manager=self.registry_manager,
                meta_manager=self.meta_manager,
                holiday_manager=self.holiday_manager,
            )
        return self._session_rule_resolver
    
    def get_trading_sessions(
        self,
        contract: str,
        trading_date: date,
        view: Literal['scheduled', 'realized']='scheduled'
    ) -> TradingSessions:
        """
        获取合约在指定交易日的真实交易时段。

        通过查询合约注册信息、品种基础交易时段及对应的特殊时段规则（如合约生命周期规则、
        特定日期/节假日时段调整、实际例外事件等），构造并返回该合约在指定交易日的交易时段。
        结果按 (contract, trading_date, view) 进行缓存，避免重复计算。

        Parameters
        ----------
        contract : str
            合约代码。
        trading_date : date
            需查询的交易日。
        view : {'scheduled', 'realized'}
            - 'scheduled': 计划交易时段视图。包含基础交易时段以及合约生命周期和特定日期/节假日的计划变更。
            - 'realized': 实际交易时段视图。在计划交易时段的基础上，额外扣除实际发生的异常/暂停交易区间。

        Returns
        -------
        TradingSessions
            合约在指定交易日的真实交易时段。
        """
        # 验证传入的 view 是否合法
        if view not in ['scheduled', 'realized']:
            raise ValueError(f"参数 view 目前只能取 'scheduled'/'realized'，传入的为 {view}。")

        # 内部做一个大写转换
        contract = copy.copy(contract)
        contract = contract.upper()

        # 若已缓存，直接返回
        cache_key = (contract, trading_date, view)
        if cache_key in self._trading_sessions_cache:
            return self._trading_sessions_cache[cache_key]

        # 否则需要构造
        # 1. 获取该 contract 所对应的 product 的 BaseSessions
        product = self.registry_manager.get_instrument_registry(contract).product
        base_sessions = self.meta_manager.get_base_sessions(product, trading_date)
        
        # 2. 获取该 product 的 SessionOverrideRules
        session_override_rules = self.meta_manager.get_session_override_rules(product)

        # 3. 调用 SessionRuleResolver.resolve_rules(...) 得到 TradingSessions，并缓存
        rule_resolver = self._get_session_rule_resolver()
        trading_sessions = rule_resolver.resolve_rules(
            contract=contract,
            trading_date=trading_date,
            base_sessions=base_sessions, 
            session_override_rules=session_override_rules, 
            view=view
            )
        self._trading_sessions_cache[(contract, trading_date, view)] = trading_sessions
        return trading_sessions

        


    
    