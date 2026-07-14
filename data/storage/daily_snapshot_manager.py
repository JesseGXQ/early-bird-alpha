"""
日频快照数据管理器，负责加载和管理日频快照数据。

"""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
from datetime import date
import pandas as pd

from data.storage import daily_snapshot_loader


if TYPE_CHECKING:
    from data.rules.instrument_registry_manager import InstrumentRegistryManager    
    from data.storage.daily_snapshot_loader import DailySnapshotLoader

@dataclass(frozen=True)
class ContractDailySnapshot:
    """合约的日频快照状态。"""
    contract: str
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    settle: float 
    volume: float 
    open_interest: int
    upper_limit: float
    lower_limit: float


class DailySnapshotManager:
    """
    日频快照数据管家
    
    Parameters
    ------
    daily_snapshot_loader: DailySnapshotLoader
        日频快照数据加载器实例。
    
    Methods
    ------
    get_price_limits(product, contract, trading_date)
        获取某品种的特定合约在特定交易日的涨停价和跌停价。
    """

    def __init__(self, daily_snapshot_loader: DailySnapshotLoader, 
                 registry_manager: InstrumentRegistryManager):
        self.daily_snapshot_loader: DailySnapshotLoader = daily_snapshot_loader 
        self.registry_manager: InstrumentRegistryManager = registry_manager
        self._price_limit_cache: dict[str, pd.DataFrame] = {}
        self._cache: dict[str, pd.DataFrame] = {}
    
    def _load_product(self, product: str) -> pd.DataFrame:
        """懒加载并缓存某品种的日频快照数据集。以 (date, instrument) 作为索引。"""
        if product not in self._cache:
            df = self.daily_snapshot_loader.load(product=product)
            
            df['date'] = pd.to_datetime(df['date'])
            # 设置索引，加速查询
            df.set_index(['date','instrument'], inplace=True)
            # 放入缓存中
            self._cache[product] = df
        return self._cache[product]

    def get_product_df(self, product: str, normalize_col_names: bool = True) -> pd.DataFrame:
        """获取品种的日频快照数据集；可选是否标准化列名。"""
        product_df = self._load_product(product).reset_index()
        if normalize_col_names:
            names_mapping = {
                'instrument': 'Contract',
                'date': 'Datetime',
                'open': 'Open',
                'high': 'High',
                'low': 'Low',
                'close': 'Close',
                'settle': 'Settle',
                'vol': 'Volume',
                'oi': 'OpenInterest'
            }
            return product_df.rename(columns=names_mapping)
        return product_df.copy()

    def get_contract_snapshot_on_date(self, contract: str, trading_date: date) -> ContractDailySnapshot:
        """获取合约在特定交易日的日频快照状态。"""
        product = self.registry_manager.get_instrument_registry(contract).product
        product_df = self._load_product(product)
        try:
            row = product_df.loc[(pd.Timestamp(trading_date), contract), :]
        except KeyError:
            raise KeyError(f'无法找到在日频快照数据中找到合约{contract}在{trading_date}的数据。')
        try: 
            assert isinstance(row, pd.Series)
        except AssertionError:
            raise ValueError(f'当以({trading_date}, {contract})筛选日频快照数据时， 应该至多找到1条数据 ，现在找到{len(row)}条。')

        return ContractDailySnapshot(
            contract=contract,
            trading_date=trading_date,
            open=float(row['open']),
            high=float(row['high']),
            low=float(row['low']),
            close=float(row['close']),
            settle=float(row['settle']),
            volume=float(row['vol']),
            open_interest=int(row['oi']),
            upper_limit=float(row['upper_limit']),
            lower_limit=float(row['lower_limit'])
        )

    def _load_price_limits(self, product: str,) -> pd.DataFrame:
        """获取特定品种的日频快照数据，仅包含涨跌停板。"""
        return self._load_product(product)[['upper_limit','lower_limit']]

    def get_price_limits(self, 
                         contract: str, 
                         trading_date: date) -> tuple[float, float]:
        """
        获取某品种的特定合约在特定交易日的涨停价和跌停价。

        Parameters
        ------  
        contract: str
            合约代码。
        trading_date: datetime.date
            交易日期。
        
        Returns
        ------
        tuple[float, float]
            (涨停价, 跌停价)
        """
        product = self.registry_manager.get_instrument_registry(contract).product
        df = self._load_price_limits(product)
        ts = pd.Timestamp(trading_date)
        try:
            upper_limit_price, lower_limit_price =  df.loc[(ts, contract), ['upper_limit', 'lower_limit']]
            return float(upper_limit_price), float(lower_limit_price) # pyright: ignore[reportArgumentType]
        except KeyError:
            raise ValueError(f"无法在日频快照中找到 {product} 的 {contract} 合约在 {trading_date} 的涨跌停板价格！")


# ========================================
# inline testing
# ========================================
if __name__ == '__main__':
    # 选择 contract='AP1805', trading_date='2018-01-19'
    from data.storage.daily_snapshot_loader import DailySnapshotLoader
    from data.rules.instrument_registry_manager import InstrumentRegistryManager
    from data.rules.product_meta_manager import ProductMetaManager

    meta_dir = r'db\rules\product_meta'
    meta_manager = ProductMetaManager(meta_dir)

    daily_snapshot_dir = r'db\market\daily_snapshots\bar_1d\bar_1d_tushare\cleaned'
    instrument_registry_dir = r'db\rules\instrument_registry'

    loader = DailySnapshotLoader(daily_snapshot_dir, meta_manager)
    registry = InstrumentRegistryManager(instrument_registry_dir)

    daily_snapshot_manager = DailySnapshotManager(daily_snapshot_loader=loader, registry_manager=registry)

    upper, lower = daily_snapshot_manager.get_price_limits(contract='AP1805', trading_date=date(2018, 1, 19))
    print(f"AP1805 2018-01-19 涨停价: {upper}, 跌停价: {lower}")

        

        



