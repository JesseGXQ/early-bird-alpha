import pandas as pd
import numpy as np
import yaml
from dataclasses import dataclass
from pathlib import Path
from datetime import date
import copy

@dataclass(frozen=True)
class StaticMeta:
    """
    期货品种的静态属性。

    Parameters
    ------
    symbol_root: str
        品种代码前缀。例如'RB'。
    exchange: str
        交易所代码。
    contract_multiplier: float
        合约乘数。
    tick_size: float
        最小变动价位。

    """
    symbol_root: str
    exchange: str
    contract_multiplier: float
    tick_size: float

@dataclass(frozen=True)
class TransactionCostRule:
    """
    (某一时期开始生效的)交易成本规则。

    Parameters
    ------
    effective_date: datetime.date 
        规则生效日期。
    commission_rate: float or None
        按成交金额收取的手续费率。
    commission_per_lot: float or None
        按手数收取的手续费，单位为元。
    intraday_offset_discount_rate: float
        平今仓折扣率。

    """
    effective_date: date 
    commission_rate: float | None
    commission_per_lot: float | None
    intraday_offset_discount_rate: float

@dataclass(frozen=True)
class MarginRule:
    """
    (某一时期开始生效的)保证金规则。

    Parameters
    ------
    effective_date: datetime.date
        规则生效日期。
    exchange_margin_rate: float
        交易所保证金率。
    
    broker_margin_rate: float
        期货公司保证金率
    

    """
    effective_date: date
    exchange_margin_rate: float
    broker_margin_rate: float #通常储存公司交易主通道（例如中信建投、永安期货）的实际费率。

@dataclass(frozen=True)
class Session:
    """
    (某一时期开始生效的)交易时段规则（日盘、夜盘、以及相应的交易小节，即日盘分为早盘和午盘）。
    
    Parameters
    ------
    effective_date: datetime.date
        规则生效日期。
    day_sessions: list of list of str
        日盘的交易小节。例：[['09:00', '10:15'], ['10:30', '11:30'], ['13:30', '15:00']] 或 [['09:00', '11:15'],['13:00'- '15:15']]
    night_sessions: list of list of str
        夜盘的交易小节。若无夜盘则为空。例：[['21:00', '23:00']] 或 []
    """
    effective_date: date
    day_sessions: list[list[str]] # like [['09:00', '10:15'],['10:30', '11:30'],['13:30', '15:00']] or [['09:00', '11:15'],['13:00'- '15:15']]
    night_sessions: list[list[str]] # like [['21:00', '23:00']] or []
   
class InstrumentMetaManager:
    #Note: 对象的职责边界与实例化粒度（Object Responsibility & Instantiation Granularity）问题。 
    #该类在概念上是一个“集中管理型仓库”——一个仓库服务于所有期货品种。所以这个类实例化时应该是无状态的(stateless)，
    #在调用其方法时（例如获取某品种的交易成本规则），输入品种代码。
    """
    管理期货品种的静态属性、交易成本、保证金和交易时段。

    Parameters
    ------
    meta_dir: str
        存储各期货品种相关属性文件的地址。

    Methods
    ------
    get_static_meta(symbol_root)
        返回品种的静态属性。
    get_static_meta(symbol_root, trading_date)
        返回特定交易日的交易成本规则。
    get_margin_rule(symbol_root, trading_date)
        返回特定交易日的保证金规则。
    
    """
    def __init__(self, meta_dir: str = './data/metadata/instrument_meta'):
        self._meta_dir = Path(meta_dir)
        self._cache: dict[str, dict] = {} # key is symbol_root, value is the loaded result of yaml file 
    
    def _load(self, symbol_root: str) -> dict:
        """懒加载并缓存品种的 YAML 数据。"""
        if symbol_root not in self._cache:
            path = self._meta_dir / f'{symbol_root}.yaml'
            with open(path, encoding='utf-8') as fh:
                self._cache[symbol_root] = yaml.safe_load(fh)
        return self._cache[symbol_root]
    
    def _get_effective_rule(self, history: list[dict], trading_date: date) -> dict:
        """从历史记录里获取在特定日期 (trading_date) 的交易规则

        Logic: for each element in history, compare the effective_date with trading_date, and select the last one that has effective_date smaller than trading_date

        history looks like: 
        'margin_history': [{'effective_date': '2015-01-01',
                     'exchange_margin_rate': 0.08,
                     'broker_margin_rate': 0.1},
                    {'effective_date': '2022-11-01',
                     'exchange_margin_rate': 0.12,
                     'broker_margin_rate': 0.15}]
        
        """
        applicable = []

        # 将历史记录按生效日期升序排列
        sorted_history = sorted(history, key=lambda x: date.fromisoformat(x['effective_date']))
        for rule in sorted_history:
            _dt = date.fromisoformat(rule['effective_date'])
            if trading_date >= _dt: # 输入的交易日期在特定规则生效日期以后
                applicable.append(rule)

        # 若交易规则历史列表按日期排序，则最后一个满足“输入交易日期在特定规则生效日期以后”的规则就是所需结果
        # 首先做一层防御判断(输入的 trading_date 早于最早生效的规则日期，导致 applicable 为空，直接抛出错误)
        if not applicable:
            raise ValueError(f'找不到 {trading_date} 及之前生效的规则。历史最早记录为 {sorted_history[0]['effective_date']}。')
        return applicable[-1] 
    
    def get_static_meta(self, symbol_root: str) -> StaticMeta:
        """
        返回品种的静态属性。

        Parameters
        ------
        symbol_root: str
            品种代码前缀，例如'RB'。
        
        Returns
        ------
        StaticMeta
            品种静态属性数据类。
        """
        raw = self._load(symbol_root)
        return StaticMeta(
            symbol_root=raw['symbol'],
            exchange=raw['exchange'],
            contract_multiplier=raw['contract_multiplier'],
            tick_size=raw['tick_size']
        )

    def get_transaction_cost(self, symbol_root: str, trading_date: date) -> TransactionCostRule:
        """
        返回特定交易日的交易成本规则。

        Parameters
        ------
        symbol_root: str
            品种代码前缀，例如'RB'。
        trading_date: datetime.date or str
            交易日期。

        Returns
        ------
        TransactionCostRule
            交易成本规则。
        """
        raw = self._load(symbol_root)
        if 'transaction_cost_history' not in raw:
            path = self._meta_dir / f'{symbol_root}.yaml'
            raise ValueError(f'{symbol_root} 的 YAML 文件未包含交易成本规则，请检查： {path} ')
        rule = self._get_effective_rule(history=raw['transaction_cost_history'],
                                        trading_date=trading_date)
        return TransactionCostRule(
            effective_date=date.fromisoformat(rule['effective_date']),
            commission_rate=rule.get('commission_rate'),
            commission_per_lot=rule.get('commission_per_lot'),
            intraday_offset_discount_rate=rule['intraday_offset_discount_rate']
        )
    
    def get_margin_rule(self, symbol_root: str, trading_date: date) -> MarginRule:
        """
        返回特定交易日的保证金规则。

        Parameters
        ------
        symbol_root: str
            品种代码前缀，例如'RB'。
        trading_date: datetime.date or str
            交易日期。
        
        Returns
        ------
        MarginRule
            保证金规则。
        """
        raw = self._load(symbol_root)
        if 'margin_history' not in raw:
            path = self._meta_dir / f'{symbol_root}.yaml'
            raise ValueError(f'{symbol_root} 的 YAML 文件未包含保证金规则，请检查： {path} ')
        rule = self._get_effective_rule(history=raw['margin_history'],
                                        trading_date=trading_date)
        return MarginRule(
            effective_date=date.fromisoformat(rule['effective_date']),
            exchange_margin_rate=rule['exchange_margin_rate'],
            broker_margin_rate=rule.get('broker_margin_rate', rule['exchange_margin_rate']+0.03)
        )
    
    def get_sessions(self, symbol_root: str, trading_date: date) -> Session:
        """
        返回特定交易日的交易时段规则。

        Parameters
        ------
        symbol_root: str
            品种代码前缀，例如'RB'。
        trading_date: datetime.date or str
            交易日期。
        
        Returns
        ------
        Session
            交易时段规则。
        """
        raw = self._load(symbol_root)
        if 'sessions_history' not in raw:
            path = self._meta_dir / f'{symbol_root}.yaml'
            raise ValueError(f'{symbol_root} 的 YAML 文件未包含交易时段规则，请检查： {path} ')
        rule = self._get_effective_rule(history=raw['sessions_history'],
                                        trading_date=trading_date)
        return Session(
            effective_date=date.fromisoformat(rule['effective_date']),
            day_sessions=rule['day_sessions'],
            night_sessions=rule.get('night_sessions', [])
        )



        
                







    





