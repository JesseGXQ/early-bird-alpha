"""
期货信息数据库 Tier2
合约注册数据管家

"""
import pandas as pd
from datetime import date
from pathlib import Path
from data.rules.models import InstrumentRegistry
from typing import Literal
from data.config import ALL_EXCHANGES

def _to_date(val) -> date | None:
    if pd.isna(val):
        return None
    return pd.to_datetime(val).date()

class InstrumentRegistryManager:

    """
    合约注册表管家。

    Parameters
    ------
    instrument_registry_dir: str
        合约注册表数据的目录路径。
    
    Methods
    ------
    get_instrument(contract)
        获取合约的注册信息。
    """

    def __init__(self, instrument_registry_dir: str):
        self.instrument_registry_dir: Path = Path(instrument_registry_dir)
        
        self._registry_df: pd.DataFrame | None = None # 全市场所有合约的注册表汇总
        self._inst_cache: dict[str, InstrumentRegistry] = {} # key is instrument。已实例化的合约对象缓存，保证每个合约对象智慧被实例化一次

    def _load_all(self) -> None:
        """ 一次性读取全市场所有合约 parquet 数据 """
        if self._registry_df is not None:
            return
        
        dfs: list[pd.DataFrame] = []

        file_paths = [
            self.instrument_registry_dir / f'{exchange}.parquet' 
            for exchange in ALL_EXCHANGES
        ] # 这里的 parquet 文件应该按照交易所来分的
        for path in file_paths:
            df = pd.read_parquet(path)
            dfs.append(df)
        
        # 拼接
        if not dfs:
            raise FileNotFoundError(
                f"在 {self.instrument_registry_dir} 下未找到任何合约注册数据文件(*.parquet)"
            )
        
        self._registry_df = pd.concat(dfs, axis=0)
        self._registry_df['instrument'] = self._registry_df['instrument'].str.upper()
        self._registry_df.set_index('instrument', inplace=True, drop=False)

        #这里我没有选择将(exchange, instrument)作为索引，因此要检查一下合约代码的唯一性
        if self._registry_df.index.duplicated().any():
            raise ValueError(f"合约代码存在重复，后续查询会有问题。请检查数据源，确认是否合约代码重复问题。")

    def get_instrument_registry(self, contract: str) -> InstrumentRegistry:
        """
        获取合约的注册信息

        Parameters
        ------
        contract: str
            合约代码。
        
        Returns
        ------
        InstrumentRegistry
            合约级注册属性。
        """
        # 若已缓存该合约，直接返回
        contract = contract.upper()   
        if contract in self._inst_cache:
            return self._inst_cache[contract]

        # 若该合约未被缓存，检查是否未加载数据
        if self._registry_df is None:
            self._load_all()

        assert self._registry_df is not None

        # 在 DataFrame 中查找合约
        try:
            row = self._registry_df.loc[contract]
        except KeyError:
            raise KeyError(f"未能在合约注册表中找到合约: {contract}")

        # 创建 InstrumentRegistry 对象并缓存
        inst_registry = InstrumentRegistry(
            instrument=contract, #收紧类型，下同
            product=str(row['product']).upper(),
            exchange=str(row['exchange']).upper(),
            list_date=pd.to_datetime(row['list_date']).date(),
            last_trade_date=pd.to_datetime(row['last_trade_date']).date(),
            delivery_year=int(row['delivery_year']),
            delivery_month=int(row['delivery_month']),
            end_deliv_date=_to_date(row['end_deliv_date']),
            exchange_inst=row['exchange_inst'],
            multiplier=int(row['multiplier'])
            )

        self._inst_cache[contract] = inst_registry
        return inst_registry    
    
    def translate_code(
        self, 
        direction: Literal['internal_to_exchange', 'exchange_to_internal'], 
        code: str,
        trading_date: date | None = None) -> str:
        """
        翻译合约代码，从 internal format 到 exchange format， 或从 exchange format 到 internal format。

        其中： internal format，是系统内部的合约代码，将郑商所的合约后数字由3位补全为4位。
        例如 CF001 (delivery_year=2010) -> CF1001；CF001 (delivery_year=2020) -> CF2001。
        因而内部的合约代码是全市场unique的。

        而 exchange format 则维持郑商所合约后数字为3位不变，例如CF001。
        """
        # 确认数据已加载
        if self._registry_df is None:
            self._load_all()
        
        assert self._registry_df is not None

        if direction == 'internal_to_exchange': # 可以直接返回，此时 code 是 unique
            return self.get_instrument_registry(contract=code).exchange_inst
        
        elif direction == 'exchange_to_internal':
            if trading_date is None:
                raise ValueError(f'当需要翻译的方向是 exchange to internal 时，必须传入 trading_date 。')
            # TODO [performance issue] 在大批量映射时每次都需要构造日期比较的 mask，很慢。
            mask = (
                (self._registry_df['exchange_inst'] == code) &
                (trading_date >= pd.to_datetime(self._registry_df['list_date']).dt.date) &
                (trading_date <= pd.to_datetime(self._registry_df['last_trade_date']).dt.date)
            )
            instruments = self._registry_df.loc[mask, 'instrument'].to_list()

            assert len(instruments) <= 1 # 当限定 exchange_inst==code，且传入的 trading_date 在 list_date 和 last_trade_date 之间时，必须最多找到一个合约

            if not instruments: # 若未找到，报错
                raise ValueError(f'在交易规则数据库- TIER2 合约注册信息表中，无法找到需要翻译代码的合约 {code}')
            
            return instruments[0]


            



        
        
        




        

        

    


    
    


