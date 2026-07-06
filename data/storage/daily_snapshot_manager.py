"""
日频快照数据管理器，负责加载和管理日频快照数据。

"""
from __future__ import annotations
from typing import TYPE_CHECKING
from datetime import date
import pandas as pd

if TYPE_CHECKING:
    from data.storage.daily_snapshot_loader import DailySnapshotLoader


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

    def __init__(self, daily_snapshot_loader: DailySnapshotLoader):
        self.daily_snapshot_loader = daily_snapshot_loader 
        self._cache: dict[str, pd.DataFrame] = {}

    def _load_price_limits(self, product: str) -> pd.DataFrame:
        """懒加载并缓存特定品种的日频快照数据，
        仅包含涨跌停板。
        """
        if product not in self._cache:
            df = self.daily_snapshot_loader.load(
                product,
                ['TradingDate','Contract','UpperLimitPrice','LowerLimitPrice']
                )
            
            # 设置索引，加速查询
            df['TradingDate'] = pd.to_datetime(df['TradingDate'])
            df.set_index(['TradingDate','Contract'], inplace=True)
            
            # 放入缓存中
            self._cache[product] = df
        return self._cache[product]

    def get_price_limits(self, 
                         product: str,
                         contract: str, 
                         trading_date: date) -> tuple[float, float]:
        """
        获取某品种的特定合约在特定交易日的涨停价和跌停价。

        Parameters
        ------  
        product: str
            品种代码，例如'RB'。
        contract: str
            合约代码。
        trading_date: datetime.date
            交易日期。
        
        Returns
        ------
        tuple[float, float]
            (涨停价, 跌停价)
        """
        df = self._load_price_limits(product)
        ts = pd.Timestamp(trading_date)
        try:
            
            upper_limit_price, lower_limit_price =  df.loc[(ts, contract), ['UpperLimitPrice', 'LowerLimitPrice']]
            return float(upper_limit_price), float(lower_limit_price) # pyright: ignore[reportArgumentType]
        except KeyError:
            raise ValueError(f"无法在日频快照中找到 {product} 的 {contract} 合约在 {trading_date} 的涨跌停板价格！")


        

        



