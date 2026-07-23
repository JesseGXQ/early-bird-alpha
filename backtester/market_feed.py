from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Iterator, Literal

import pandas as pd

from backtester.models import BarMarketEvent

if TYPE_CHECKING:
    from data.facade import DataFacade


class BarMarketFeed:
    """将 DataFacade 的 DataFrame 结果适配为 BarMarketEvent 流。"""

    REQUIRED_COLUMNS = {
        "trading_date",
        "Dominant_Contract",
        "Adj_Open",
        "Adj_High",
        "Adj_Low",
        "Adj_Close",
        "Raw_Open",
        "Raw_High",
        "Raw_Low",
        "Raw_Close",
        "is_roll",
    }

    def __init__(self, facade: DataFacade):
        self.facade = facade

    def stream(
        self,
        product: str,
        start_date: date,
        end_date: date,
        freq: str = "1d",
        view: Literal["scheduled", "realized"] = "scheduled",
    ) -> Iterator[BarMarketEvent]:
        df = self.facade.get_historical_vector(
            product=product,
            start_date=start_date,
            end_date=end_date,
            freq=freq,
            view=view,
        )
        yield from self.from_dataframe(product=product, df=df)

    def from_dataframe(self, product: str, df: pd.DataFrame) -> Iterator[BarMarketEvent]:
        missing = self.REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(f"BarMarketFeed 缺少必要字段: {sorted(missing)}")

        normalized_product = product.upper()
        for timestamp, row in df.iterrows():
            yield BarMarketEvent(
                product=normalized_product,
                contract=str(row["Dominant_Contract"]),
                timestamp=pd.Timestamp(timestamp),
                trading_date=pd.Timestamp(row["trading_date"]).date(),
                adj_open=float(row["Adj_Open"]),
                adj_high=float(row["Adj_High"]),
                adj_low=float(row["Adj_Low"]),
                adj_close=float(row["Adj_Close"]),
                raw_open=float(row["Raw_Open"]),
                raw_high=float(row["Raw_High"]),
                raw_low=float(row["Raw_Low"]),
                raw_close=float(row["Raw_Close"]),
                is_roll=bool(row["is_roll"]),
            )
