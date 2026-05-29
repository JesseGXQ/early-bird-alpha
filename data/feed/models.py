"""
数据模型：单个合约的 TICK 数据和市场快照。

"""
from dataclasses import dataclass
import pandas as pd
from datetime import  date

@dataclass(frozen=True)
class ContractTick:
    """
    单个合约的单笔 TICK 数据。

    Parameters
    ------
    contract: str
        合约代码，例如 'RB2210'。
    calendar_timestamp: pd.Timestamp
        日历交易时间戳，包含日期和时间，例如 '2022-09-01 09:30:00'。
    trading_date: datetime.date
        交易日期。
    last: float
        最新价。   
    bid: float
        买一价。
    ask: float
        卖一价。
    volume: int
        成交量。
    ask_volume: int
        卖一量。
    bid_volume: int
        买一量。
    open_interest: float
        持仓量。
    is_limit_up: bool
        是否处于涨停状态。
    is_limit_down: bool
        是否处于跌停状态。
    """
    contract: str
    calendar_timestamp: pd.Timestamp
    trading_date: date
    last: float
    bid: float
    ask: float
    volume: int
    ask_volume: int
    bid_volume: int
    open_interest: float
    is_limit_up: bool
    is_limit_down: bool


@dataclass(frozen=True)
class MarketSnapshot:
    """
    市场快照。向回测引擎下发。

    Parameters
    ------
        calendar_timestamp: pd.Timestamp
            日历交易时间戳，例如 '2022-09-01 09:30:00'。
        trading_date: datetime.date
            交易日期。
        dominant: ContractTick or None
            主力合约的 TICK 数据。
        outgoing: ContractTick or None
            旧主力合约的 TICK 数据，换月期提供。非换月期为 None。
        is_roll: bool
            是否处于换月期。换月期为 True，非换月期为 False。

    """
    calendar_timestamp: pd.Timestamp
    trading_date: date
    dominant: ContractTick | None 
    outgoing: ContractTick | None 
    is_roll: bool 







    



