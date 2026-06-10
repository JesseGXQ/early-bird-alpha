"""
期货信息数据库 Tier1 
品种规则配置管家
"""
from typing import Literal, Any
import yaml
from dataclasses import dataclass
from pathlib import Path
from datetime import date

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
    position_type: bool
        持仓类型。False 为单向， True 为双向持仓。
    position_date_type: bool
        持仓日期类型。False 为不区分今仓和昨仓， True 为区分。
    max_margin_side_algorithm: bool
        是否使用大额单边保证金算法。
    combination_type: int。
        组合类型。
    
    """
    product: str
    exchange: str
    position_type: bool
    position_date_type: bool
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

    """
    # TODO [业务规则完善] 
    # 国内有些期货品种曾经实行过“仅对主力合约拉高平今手续费”，因此这里需要加一个类似 special_rules 字段来管理这种特殊情况
    # 例如：{'main_contracts': {'close_today': 30.0}} 表示如果是主力合约，平今手续费覆盖为 30
    # special_rules: dict | None = None
    effective_date: date 
    commission_type: Literal['flat', 'ratio']
    open: float
    close_yesterday: float
    close_today: float

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
    remain_old_rule_instruments: list[str]
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
class Session:
    """
    (某一时期开始生效的)交易时段规则（日盘、夜盘、以及相应的交易小节，日盘分为早盘和午盘）。
    
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
   
class ProductMetaManager:
    #Note: 对象的职责边界与实例化粒度（Object Responsibility & Instantiation Granularity）问题。 
    #该类在概念上是一个“集中管理型仓库”——一个仓库服务于所有期货品种。所以这个类实例化时应该是无状态的(stateless)，
    #在调用其方法时（例如获取某品种的交易成本规则），输入品种代码。
    """
    管理期货品种的静态属性、交易成本规则、最小变动价位规则、合约乘数规则、下单量限制规则和交易时段规则。

    Parameters
    ------
    product_meta_dir: str
        规则数据库tier1——品种元数据的目录路径。

    Methods
    ------
    get_static_meta(product)
        返回品种的静态属性。
    get_transaction_cost_rule(product, trading_date)
        返回特定交易日的交易成本规则。
    get_price_tick_rule(product, trading_date)
        返回特定交易日的最小变动价位规则。
    get_multiplier_rule(product, trading_date)
        返回特定交易日的合约乘数规则。
    get_order_volume_limits_rule(product, trading_date)
        返回特定交易日的下单量限制规则。
    get_session_rule(product, trading_date)
        返回特定交易日的交易时段规则。
    
    """
    def __init__(self, product_meta_dir: str):
        self._meta_dir: Path = Path(product_meta_dir)
        self._cache: dict[str, dict[str, Any]] = {} # key is product, value is the loaded result of yaml file 
    
    def _load(self, product: str) -> dict[str, Any]:
        """懒加载并缓存品种的 YAML 数据。"""
        if product not in self._cache:
            path = self._meta_dir / f'{product}.yaml'
            with open(path, encoding='utf-8') as fh:
                self._cache[product] = yaml.safe_load(fh)
        return self._cache[product]
    
    def _get_effective_rule(self, history: list[dict[str, Any]], trading_date: date) -> dict[str, Any]:
        """从历史记录里获取在特定日期 (trading_date) 的交易规则

        Logic: for each element in history, compare the effective_date with trading_date, 
        and select the last one that has effective_date smaller than trading_date

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
    
    def get_static_meta(self, product: str) -> StaticMeta:
        """
        返回品种的静态属性。

        Parameters
        ------
        product: str
            品种代码。
        
        Returns
        ------
        StaticMeta
            品种静态属性数据类。
        """
        raw = self._load(product)
        return StaticMeta(
            product=raw['product'],
            exchange=raw['exchange'],
            position_type=bool(raw['position_type']),
            position_date_type=bool(raw['position_date_type']),
            max_margin_side_algorithm=bool(raw['max_margin_side_algorithm']),
            combination_type=raw['combination_type'],
        )

    def get_transaction_cost(self, product: str, trading_date: date) -> TransactionCostRule:
        """
        返回特定交易日的交易成本规则。

        Parameters
        ------
        product: str
            品种代码，例如'RB'。
        trading_date: datetime.date
            交易日期。

        Returns
        ------
        TransactionCostRule
            交易成本规则。
        """
        raw = self._load(product)
        if 'transaction_cost_history' not in raw:
            path = self._meta_dir / f'{product}.yaml'
            raise ValueError(f'{product} 的 YAML 文件未包含交易成本规则，请检查： {path} ')
        rule = self._get_effective_rule(history=raw['transaction_cost_history'],
                                        trading_date=trading_date)
        return TransactionCostRule(
            effective_date=date.fromisoformat(rule['effective_date']),
            commission_type=rule['commission_type'],
            open=float(rule['open']),
            close_today=float(rule['close_today']),
            close_yesterday=float(rule['close_yesterday']),
        )

    def get_price_tick(self, product: str, trading_date: date) -> PriceTickRule:
        """
        返回特定交易日的最小变动价位规则。

        Parameters
        ------
        product: str
            品种代码，例如'RB'。
        trading_date: datetime.date
            交易日期。
        
        Returns
        ------
        PriceTickRule
            最小变动价位规则。
        """
        raw = self._load(product)
        if 'price_tick_history' not in raw:
            path = self._meta_dir / f'{product}.yaml'
            raise ValueError(f'{product} 的 YAML 文件未包含最小变动价位规则，请检查： {path} ')
        rule = self._get_effective_rule(history=raw['price_tick_history'],
                                        trading_date=trading_date)
        return PriceTickRule(
            effective_date=date.fromisoformat(rule['effective_date']),
            price_tick=float(rule['price_tick']),
            remain_old_rule_instruments=rule.get('remain_old_rule_instruments', [])
        )
    
    def get_multiplier(self, product: str, trading_date: date) -> MultiplierRule:
        """
        返回特定交易日的合约乘数规则。

        Parameters
        ------
        product: str
            品种代码，例如'RB'。
        trading_date: datetime.date
            交易日期。
        
        Returns
        ------
        MultiplierRule
            合约乘数规则。
        """
        raw = self._load(product)
        if 'multiplier_history' not in raw:
            path = self._meta_dir / f'{product}.yaml'
            raise ValueError(f'{product} 的 YAML 文件未包含合约乘数规则，请检查： {path} ')
        rule = self._get_effective_rule(history=raw['multiplier_history'],
                                        trading_date=trading_date)
        return MultiplierRule(
            effective_date=date.fromisoformat(rule['effective_date']),
            multiplier=int(rule['multiplier'])
        )

    def get_order_volume_limits(self, product: str, trading_date: date) -> OrderVolumeLimitsRule:
        """
        返回特定交易日的下单量限制规则。

        Parameters
        ------
        product: str
            品种代码，例如'RB'。
        trading_date: datetime.date
            交易日期。
        
        Returns
        ------
        OrderVolumeLimitsRule
            下单量限制规则。
        """
        raw = self._load(product)
        if 'order_volume_limits_history' not in raw:
            path = self._meta_dir / f'{product}.yaml'
            raise ValueError(f'{product} 的 YAML 文件未包含下单量限制规则，请检查： {path} ')
        rule = self._get_effective_rule(history=raw['order_volume_limits_history'],
                                        trading_date=trading_date)
        return OrderVolumeLimitsRule(
            effective_date=date.fromisoformat(rule['effective_date']),
            max_market_order_volume=int(rule['max_market_order_volume']),
            min_market_order_volume=int(rule['min_market_order_volume']),
            max_limit_order_volume=int(rule['max_limit_order_volume']),
            min_limit_order_volume=int(rule['min_limit_order_volume'])
        )
    
    def get_sessions(self, product: str, trading_date: date) -> Session:
        """
        返回特定交易日的交易时段规则。

        Parameters
        ------
        product: str
            品种代码，例如'RB'。
        trading_date: datetime.date
            交易日期。
        
        Returns
        ------
        Session
            交易时段规则。
        """
        raw = self._load(product)
        if 'sessions_history' not in raw:
            path = self._meta_dir / f'{product}.yaml'
            raise ValueError(f'{product} 的 YAML 文件未包含交易时段规则，请检查： {path} ')
        rule = self._get_effective_rule(history=raw['sessions_history'],
                                        trading_date=trading_date)
        return Session(
            effective_date=date.fromisoformat(rule['effective_date']),
            day_sessions=rule['day_sessions'],
            night_sessions=rule.get('night_sessions', [])
        )




        
                







    





