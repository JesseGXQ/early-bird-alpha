"""
期货交易规则-数据类
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal, Any


@dataclass(frozen=True)
class StaticMeta:
    """
    期货品种的静态属性。

    Parameters
    ------
    product: str
        品种代码。例如'RB'。
    exchange: str
        交易所代码。
    position_date_type: Literal['distinguish_today_yesterday', 'undistinguished']
        持仓日期类型——是否区分今仓和昨仓。
    max_margin_side_algorithm: bool
        是否使用大额单边保证金算法。
    combination_type: int。
        组合类型。
    
    """
    product: str
    exchange: str
    position_date_type: Literal['distinguish_today_yesterday', 'undistinguished']
    # TODO [业务逻辑完善]
    # 以下两个字段目前过粗，真实的业务逻辑更复杂，日后有需要要完善。
    max_margin_side_algorithm: bool
    combination_type: int

@dataclass(frozen=True)
class TransactionCostRule:
    """
    (某一时期开始生效的)交易成本规则。

    Parameters
    ------
    effective_date: datetime.date 
        规则生效日期。
    commission_type: str, 'flat' or 'ratio'
        手续费类型：按手数收取，还是按金额收取。
    open: float
        开仓手续费。
    close_yesterday: float
        平昨手续费。
    close_today: float
        平今手续费。
    scope: list of str
        合约代码列表，用于表达规则仅对某些特定合约生效。
    condition: Any
        用于cover其他更复杂情况的条件表达, 目前为空。

    """
    effective_date: date 
    commission_type: Literal['flat', 'ratio']
    open: float
    close_yesterday: float
    close_today: float
    scope: list[str]  # 合约代码列表，用于表达规则仅对某些特定合约生效。
    # TODO [业务逻辑完善] 目前过粗，日后有需要再完善。
    condition: Any #用于cover其他更复杂情况，目前暂缺

@dataclass(frozen=True)
class PriceTickRule:
    """
    （某一时期开始生效的）最小变动价位规则。

    Parameters
    ------
    effective_date: date
        规则生效日期。
    price_tick: float
        最小变动价位。
    remain_old_rule_instruments: list of str
        规则变动有两种方式：新老划断和一刀切。若一刀切，该字段应为空列表。
        若新老划断，则该字段记录保持旧规则的合约名称列表。
    """
    effective_date: date
    price_tick: float
    remain_old_rule_instruments: list[str]

@dataclass(frozen=True)
class MultiplierRule:
    """
    （某一时期开始生效的）合约乘数规则。

    Parameters
    ------
    effective_date: datetime.date
        规则生效日期。
    multiplier: int
        合约乘数。
    """
    effective_date: date
    multiplier: int

@dataclass(frozen=True)
class OrderVolumeLimitsRule:
    """
    （某一时期开始生效的）最大最小下单量限制。

    Parameters
    ------
    effective_date: datetime.date
        规则生效日期。
    max_market_order_volume: int
        市价单最大下单量。
    min_market_order_volume: int
        市价单最小下单量。
    max_limit_order_volume: int
        限价单最大下单量。  
    min_limit_order_volume: int
        限价单最小下单量。
    
    """
    effective_date: date
    max_market_order_volume: int
    min_market_order_volume: int
    max_limit_order_volume: int
    min_limit_order_volume: int


@dataclass(frozen=True)
class BaseSessions:
    """
    (某一时期开始生效的)基础交易时段规则。包含早盘、午盘和夜盘。
    
    Parameters
    ------
    effective_date: datetime.date
        规则生效日期。
    morning_sessions: list of list of str
        早盘的交易小节。例：[['09:00', '10:15'], ['10:30', '11:30']] 或 [['09:00', '11:15']]。均为左闭右开语义，下同。
    afternoon_sessions: list of list of str
        午盘的交易小节。例： [['13:30', '15:00']]
    night_sessions: list of list of str
        夜盘的交易小节。若无夜盘则为空。例：[['21:00', '23:00']] 或 []
    """
    effective_date: date
    morning_sessions: list[list[str]]
    afternoon_sessions: list[list[str]]
    night_sessions: list[list[str]]

@dataclass(frozen=True)
class SessionOverrideRules:
    """
    交易时段规则特殊安排， 包含合约生命周期类rules、特定日期overrides、实际发生的例外事件。
    
    """
    recurring_instrument_lifecycle_rules: list[dict[str, Any]]
    scheduled_dated_overrides: list[dict[str, Any]]
    realized_session_events: list[dict[str, Any]]

@dataclass(frozen=True)
class TradingSessions:
    """
    计划或已实现的真实交易时段。
    
    计划：基础交易时段 + 合约生命周期类rules + 特定日期overrides。
    实际发生：计划 -  实际发生的例外事件(导致的暂停或非连续竞价区间)。
    
    Parameters
    ------
    trading_date: date
        交易日。
    contract: str
        合约代码。
    morning_sessions: list of list of str
        早盘的交易小节。
    afternoon_sessions: list of list of str
        午盘的交易小节。
    night_sessions: list of list of str
        夜盘的交易小节。
    view: str, 'scheduled' or 'realized'
        - scheduled: 计划的交易时段，未包含未发生的例外事件。
        - realized: 实际发生的交易时段，包含所有例外事件。
    
    applied_override_ids: list of str
        应用的override id列表。
    """
    trading_date: date
    contract: str
    morning_sessions: list[list[str]]
    afternoon_sessions: list[list[str]]
    night_sessions: list[list[str]]
    view: Literal['scheduled', 'realized']
    applied_override_ids: list[str]


@dataclass(frozen=True)
class InstrumentRegistry:
    """
    合约级注册属性
    """
    instrument: str # 合约代码
    name: str # 合约名称
    product: str # 品种代码
    product_class: int # 品种类型，是期权、期货还是组合、套利合约
    exchange: str # 交易所
    list_date: date # 上市日期
    last_trade_date: date # 最后交易日
    expire_date: date # 合约到期日
    delivery_year: int # 交割年份
    delivery_month: int # 交割月份
    start_deliv_date: date # 开始交割日
    end_deliv_date: date # 最后交割日
    exchange_inst: str # 交易所报单代码
    multiplier: int # 合约乘数

@dataclass(frozen=True)
class InstrumentDailyStatus:
    """合约的日频动态状态属性"""
    trading_date: date # 交易日
    instrument: str # 合约代码
    is_tradable: bool # 是否可交易
    inst_life_phase: Literal['not_started', 'started', 'paused', 'expired'] # 合约生命周期阶段
    """
    Note: 
    `is_tradable`和`inst_life_phase`的组合关系：
    - 未上市： `inst_life_phase=not_started`, `is_tradable=False`
    - 正常交易：`started`, `True`
    - 暂停交易: `paused`, `False`
    - 已退市：`expired`, `False`
    """
    long_margin_ratio: float # 交易所多头保证金率
    short_margin_ratio: float # 交易所空头保证金率