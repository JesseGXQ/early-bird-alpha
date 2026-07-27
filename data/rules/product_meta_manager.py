"""
期货信息数据库 Tier1 
品种规则配置管家
"""
from typing import Any, Literal
import yaml
from pathlib import Path
from datetime import date
from data.rules.models import (
    StaticMeta, PriceTickRule,  
    BaseSessions, SessionOverrideRules
)
from functools import lru_cache

class ProductMetaManager:
    #Note: 对象的职责边界与实例化粒度（Object Responsibility & Instantiation Granularity）问题。 
    #该类在概念上是一个“集中管理型仓库”——一个仓库服务于所有期货品种。所以这个类实例化时应该是无状态的(stateless)，
    #在调用其方法时（例如获取某品种的交易成本规则），输入交易所和品种代码。
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
    get_price_tick(product, trading_date)
        返回特定交易日的最小变动价位规则。
    get_base_sessions(product, trading_date)
        返回特定交易日的基础交易时段规则。
    get_session_override_rules(product)
        返回交易时段规则特殊安排。
    
    """
    def __init__(self, product_meta_dir: str):
        self._meta_dir: Path = Path(product_meta_dir)
        self._cache: dict[str, dict[str, Any]] = {}
    
    def _load(self, product: str) -> dict[str, Any]:
        """
        懒加载并缓存品种的 YAML 数据。
        文件名称：{PRODUCT}.yaml
        """
        product_upper = product.upper()
        if product_upper not in self._cache:
            path = self._meta_dir / f'{product_upper}.yaml'
            if not path.exists():
                raise FileNotFoundError(f'未找到 {product} 的品种规则配置文件: {path}')
            with open(path, encoding='utf-8') as fh:
                self._cache[product_upper] = yaml.safe_load(fh)
        return self._cache[product_upper]
    
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
            raise ValueError(f"找不到 {trading_date} 及之前生效的规则。历史最早记录为 {sorted_history[0]['effective_date']}。")
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
            product=str(raw['product']).upper(),
            exchange=str(raw['exchange']).upper(),
            name=str(raw['name']),
            position_date_type=raw['position_date_type'],
            max_margin_side_algorithm=bool(raw['max_margin_side_algorithm']),
            combination_type=int(raw['combination_type']),
            list_date=date.fromisoformat(str(raw['list_date'])),
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
            raise ValueError(f'{product} 的 YAML 文件未包含最小变动价位规则。')
        rule = self._get_effective_rule(history=raw['price_tick_history'],
                                        trading_date=trading_date)
        return PriceTickRule(
            effective_date=date.fromisoformat(rule['effective_date']),
            price_tick=float(rule['price_tick']),
        )
    
    def get_base_sessions(self, product: str, trading_date: date) -> BaseSessions:
        """
        返回特定交易日的基础交易时段规则。

        Parameters
        ------
        product: str
            品种代码，例如'RB'。
        trading_date: datetime.date
            交易日期。
        
        Returns
        ------
        BaseSessions
            基础交易时段规则。
        """
        raw = self._load(product)
        if 'sessions_history' not in raw:
            raise ValueError(f'{product} 的 YAML 文件未包含交易时段规则。')
        rule = self._get_effective_rule(history=raw['sessions_history'],
                                        trading_date=trading_date)
        return BaseSessions(
            effective_date=date.fromisoformat(rule['effective_date']),
            morning_sessions=rule['morning_sessions'],
            afternoon_sessions=rule['afternoon_sessions'],
            night_sessions=rule.get('night_sessions', [])
        )

    def _load_market_session_override_rules(self) -> dict[str, Any]:
        """读取市场级 session override rules yaml 文件"""
        path = self._meta_dir / 'market_session_override_rules' / 'market_session_override_rules.yaml'
        if not path.exists():
            raise FileNotFoundError(f'未找到市场级 session override rules 文件： {path}')
        with open(path, encoding='utf-8') as fh:
            rule: dict[str, Any] = yaml.safe_load(fh)
        return rule
    
    @lru_cache(maxsize=1)
    def get_session_override_rules(self) -> SessionOverrideRules:
        """
        返回交易时段规则特殊安排。

        Returns
        ------
        SessionOverrideRules
            交易时段规则特殊安排。
        """
        rule = self._load_market_session_override_rules()
        return SessionOverrideRules(
            schema_version=rule['schema_version'],
            recurring_instrument_lifecycle_rules=rule.get('recurring_instrument_lifecycle_rules',[]),
            scheduled_dated_overrides=rule.get('scheduled_dated_overrides',[]),
            realized_session_events=rule.get('realized_session_events',[])
        )
    
    def get_product_universe(self, return_type: Literal['dict', 'list'] = 'list') -> list[str] | dict[str, dict[str, str]]:
        """
        获取全市场期货品种列表。

        Parameters
        ----------
        return_type : {'list', 'dict'}, default='list'
            返回格式：'list' 返回品种代码列表，'dict' 返回 {品种: {exchange, name}}。

        Returns
        -------
        list | dict[str, dict[str, str]]
            'list' 返回 list[str]；'dict' 返回 {品种: {'exchange': 交易所, 'name': 名称}}。

        Raises
        ------
        ValueError
            参数不是 'list' 或 'dict' 时抛出。
        """
        if return_type == 'list':
            return [path.stem for path in self._meta_dir.glob('*.yaml')]
        elif return_type == 'dict':
            products: dict[str, dict[str, str]] = {}
            paths = self._meta_dir.glob('*.yaml')
            for path in paths:
                product = path.stem
                sm = self.get_static_meta(product)
                exchange, name = sm.exchange, sm.name
                products[product] = {'exchange': exchange, 'name': name}
            return products
        else:
            raise ValueError(f"return_type 参数仅接受 'dict', 'list'，现在为 {return_type} 。")
        

# ================================
# simple test
# ================================
if __name__ == '__main__':
    from datetime import date
    product_meta_dir = 'db/rules/product_meta'
    meta_manager = ProductMetaManager(product_meta_dir)
    product = 'y'
    
    static_meta = meta_manager.get_static_meta(product)
    print(f'product = {product} ，静态属性: {static_meta}')

    trading_date = date(2026, 3, 25)
    price_tick_rule = meta_manager.get_price_tick(product, trading_date)
    print(f'最小价位变动规则, {price_tick_rule}')

    base_session = meta_manager.get_base_sessions(product, trading_date)
    session_overrie_rules = meta_manager.get_session_override_rules()
    print(f'交易时段基础规则, {base_session}')
    print(f'交易时段特殊规则, {session_overrie_rules}')








        
                







    





