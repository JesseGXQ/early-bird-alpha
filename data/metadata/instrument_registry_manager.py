"""
期货信息数据库 Tier2
合约注册数据管家

"""
import pandas as pd
from dataclasses import dataclass
from datetime import date
from pathlib import Path

@dataclass(frozen=True)
class InstrumentRegistry:
    """
    合约级注册属性
    """
    instrument: str
    name: str
    product: str
    exchange: str
    list_date: date
    expire_date: date
    delivery_year: int
    delivery_month: int
    start_deliv_date: date
    end_deliv_date: date
    exchange_inst: str
    multiplier: int


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
        self._inst_cache: dict[str, InstrumentRegistry] = {} # 已实例化的合约对象缓存，保证每个合约对象智慧被实例化一次

    def _load_all(self) -> None:
        """ 一次性读取全市场所有合约 parquet 数据 """
        if self._registry_df is not None:
            return
        
        dfs: list[pd.DataFrame] = []

        file_paths = self.instrument_registry_dir.glob('*.parquet') # 这里的 parquet 文件应该是按照交易所来分的
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
        self._registry_df.set_index('instrument', inplace=True)

    def get_instrument(self, contract: str) -> InstrumentRegistry:
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
            instrument=contract,
            name=str(row['name']), #收紧类型，下同
            product=str(row['product']),
            exchange=str(row['exchange']),
            list_date=pd.to_datetime(row['list_date']).date(),
            expire_date=pd.to_datetime(row['expire_date']).date(),
            delivery_year=int(row['delivery_year']),
            delivery_month=int(row['delivery_month']),
            start_deliv_date=pd.to_datetime(row['start_deliv_date']).date(),
            end_deliv_date=pd.to_datetime(row['end_deliv_date']).date(),
            exchange_inst=str(row['exchange_inst']),
            multiplier=int(row['multiplier'])
            )

        self._inst_cache[contract] = inst_registry
        return inst_registry    
        
        
        
        




        

        

    


    
    


