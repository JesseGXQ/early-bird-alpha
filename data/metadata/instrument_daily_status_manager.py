"""
期货信息数据库 Tier3
日频动态状态属性管家

"""

from data.metadata.product_meta_manager import ProductMetaManager
import pandas as pd
from dataclasses  import dataclass
from datetime import date
from pathlib import Path

@dataclass(frozen=True)
class InstrumentDailyStatus:
    """合约的日频动态状态属性"""
    trading_date: date
    instrument: str
    inst_life_phase: int
    is_trading: bool
    long_margin_ratio: float
    short_margin_ratio: float

class InstrumentDailyStatusManager:
    """
    合约的日频动态状态属性管家

    Parameters
    ------
    instrument_daily_status_dir: str
        合约日频动态状态数据的目录路径。
    meta_manager: ProductMetaManager
        品种元数据管家。
    
    Methods
    ------
    get_status_all_inst_of_product_on_date(product, trading_date)
        获取特定交易日，某品种下所有合约的日频动态状态属性。
    get_status_inst_on_date(contract, trading_date)
        获取特定交易日，某合约的日频动态状态属性。
    
    """
    def __init__(self, 
    instrument_daily_status_dir: str, 
    meta_manager: ProductMetaManager):
        self.instrument_daily_status_dir: Path = Path(instrument_daily_status_dir)
        self.meta_manager: ProductMetaManager = meta_manager

        # 内存缓存
        self._product_df_cache: dict[str, pd.DataFrame] = {} # 品种粒度的全量数据缓存 (product -> df)
        self._product_date_df_cache: dict[tuple[str, date], pd.DataFrame] = {} # 品种（下所有合约）-交易日级别的内部缓存
        self._inst_date_cache: dict[tuple[str, date], InstrumentDailyStatus] = {} # 合约-交易日层面的内部缓存

    def _load_product_date(self, product: str, trading_date: date) -> None:
        """
        读取（并缓存）整个品种的日频动态数据，缓存（品种-交易日）级别的日频动态状态数据。
        """
        product_upper = product.upper()
        
        # 1. 若该品种的全量数据尚未读入内存，则加载并标准化
        if product_upper not in self._product_df_cache:
            exchange = self.meta_manager.get_static_meta(product_upper).exchange
            path = self.instrument_daily_status_dir / f'{exchange}_{product_upper}.parquet'

            if not path.exists():
                raise FileNotFoundError(f"未能在 {self.instrument_daily_status_dir} 下找到品种 {product_upper} 的数据文件 {path.name}")

            df = pd.read_parquet(path)
            df['trading_date'] = pd.to_datetime(df['trading_date']).dt.date
            df['instrument'] = df['instrument'].str.upper()
            
            self._product_df_cache[product_upper] = df

        # 2. 从全量缓存中进行内存级别的日期切片过滤
        df_all = self._product_df_cache[product_upper]
        df_filtered = df_all[df_all['trading_date'] == trading_date]
        self._product_date_df_cache[(product_upper, trading_date)] = df_filtered

    def get_status_all_inst_of_product_on_date(
        self, 
        product: str, 
        trading_date: date
    ) -> dict[str, InstrumentDailyStatus]:
        """
        获取特定交易日，某品种下所有合约的日频动态状态属性。
        
        Parameters
        ------
        product: str
            品种代码。
        trading_date: date
            交易日期。
            
        Returns
        ------
        dict[str, InstrumentDailyStatus]
            特定交易日，某品种下所有合约的日频动态状态属性。
        """
        product_upper = product.upper()
        cache_key = (product_upper, trading_date)

        if cache_key not in self._product_date_df_cache:
            self._load_product_date(product_upper, trading_date)

        df_date = self._product_date_df_cache[cache_key]

        res: dict[str, InstrumentDailyStatus] = {}
        for _, row in df_date.iterrows():
            contract = str(row['instrument'])
            status = InstrumentDailyStatus(
                trading_date=trading_date,
                instrument=contract,
                inst_life_phase=int(row['inst_life_phase']),
                is_trading=bool(row['is_trading']),
                long_margin_ratio=float(row['long_margin_ratio']),
                short_margin_ratio=float(row['short_margin_ratio'])
            )
            self._inst_date_cache[(contract, trading_date)] = status
            res[contract] = status

        return res

    def get_status_inst_on_date(self, contract: str, trading_date: date) -> InstrumentDailyStatus:
        """
        获取特定交易日，某合约的日频动态状态属性。

        Parameters
        ------
        contract: str
            合约代码。
        trading_date: date
            交易日期。
            
        Returns
        ------
        InstrumentDailyStatus
            某合约的日频动态状态属性。
        """
        contract_upper = contract.upper()
        cache_key = (contract_upper, trading_date)

        if cache_key in self._inst_date_cache:
            return self._inst_date_cache[cache_key]

        # 解析合约代码中的品种代码 (例如 'rb2610' -> 'RB')
        import re
        match = re.match(r'^[a-zA-Z]+', contract_upper)
        if not match:
            raise ValueError(f"不合法的合约代码: {contract}")
        product = match.group()

        # 触发批量查询，将该品种当天的所有合约载入缓存
        self.get_status_all_inst_of_product_on_date(product, trading_date)

        if cache_key not in self._inst_date_cache:
            raise KeyError(f"未能在日频状态表中查到合约 {contract_upper} 在 {trading_date} 的状态")

        return self._inst_date_cache[cache_key]
    