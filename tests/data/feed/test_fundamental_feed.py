from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from data.config import EnvironmentConfig
from data.facade import DataFacade
from data.feed.fundamental_feed import (
    WAREHOUSE_RECEIPT_AGGREGATED_COLUMNS,
    FundamentalFeed,
)
from data.storage.warehouse_receipt_loader import WAREHOUSE_RECEIPT_COLUMNS


def make_frame() -> pd.DataFrame:
    rows = []
    for trading_date, warehouse, quantity in [
        ("2026-01-06", "乙仓", 20),
        ("2026-01-05", "甲仓", 10),
        ("2026-01-07", "丙仓", 30),
    ]:
        rows.append(
            {
                "trading_date": pd.Timestamp(trading_date),
                "product": "ZN",
                "product_name": "沪锌",
                "exchange": "SHFE",
                "source_symbol": "ZN",
                "source_product_name": "锌",
                "source_exchange": "SHFE",
                "warehouse": warehouse,
                "warehouse_id": pd.NA,
                "previous_receipt_quantity": quantity,
                "receipt_quantity": quantity,
                "receipt_change_quantity": 0,
                "unit": "吨",
                "area": pd.NA,
                "year": pd.NA,
                "grade": pd.NA,
                "brand": pd.NA,
                "place": pd.NA,
                "premium_discount": pd.NA,
                "is_converted_receipt": pd.NA,
            }
        )
    return pd.DataFrame(rows).loc[:, WAREHOUSE_RECEIPT_COLUMNS]


class FakeLoader:
    def load(self, product: str) -> pd.DataFrame:
        assert product == "ZN"
        return make_frame()


class FrameLoader:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame

    def load(self, product: str) -> pd.DataFrame:
        return self.frame.copy()


def make_multi_unit_frame() -> pd.DataFrame:
    frame = make_frame().iloc[[0, 1]].copy()
    frame.loc[frame.index[0], "trading_date"] = pd.Timestamp("2026-01-05")
    frame.loc[frame.index[0], "unit"] = "吨"
    frame.loc[frame.index[0], "previous_receipt_quantity"] = 2
    frame.loc[frame.index[0], "receipt_quantity"] = 3
    frame.loc[frame.index[0], "receipt_change_quantity"] = 1
    frame.loc[frame.index[1], "unit"] = "千克"
    frame.loc[frame.index[1], "previous_receipt_quantity"] = 1000
    frame.loc[frame.index[1], "receipt_quantity"] = 2000
    frame.loc[frame.index[1], "receipt_change_quantity"] = 1000
    return frame.reset_index(drop=True)


def test_feed_filters_closed_date_interval_and_sorts():
    feed = FundamentalFeed("zn", FakeLoader())

    result = feed.get_warehouse_receipt_data(
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 6),
    )

    assert result["trading_date"].tolist() == [
        pd.Timestamp("2026-01-05"),
        pd.Timestamp("2026-01-06"),
    ]


def test_feed_rejects_reversed_interval():
    feed = FundamentalFeed("ZN", FakeLoader())

    with pytest.raises(ValueError, match="不能晚于"):
        feed.get_warehouse_receipt_data(date(2026, 1, 7), date(2026, 1, 6))


def test_feed_rejects_non_boolean_aggregate():
    feed = FundamentalFeed("ZN", FakeLoader())

    with pytest.raises(ValueError, match="aggregate 必须是 bool"):
        feed.get_warehouse_receipt_data(aggregate="False")


def test_feed_aggregates_single_unit_by_product_and_trading_date():
    frame = make_frame().iloc[[0, 1]].copy()
    frame["trading_date"] = pd.Timestamp("2026-01-05")
    feed = FundamentalFeed("ZN", FrameLoader(frame))

    result = feed.get_warehouse_receipt_data(aggregate=True)

    assert result.columns.tolist() == WAREHOUSE_RECEIPT_AGGREGATED_COLUMNS
    assert len(result) == 1
    assert result.loc[0, "receipt_quantity"] == 30
    assert result.loc[0, "unit"] == "吨"


def test_feed_rejects_multi_unit_aggregation_without_conversion():
    feed = FundamentalFeed("ZN", FrameLoader(make_multi_unit_frame()))

    with pytest.raises(ValueError, match="应用转换规则后仍为"):
        feed.get_warehouse_receipt_data(aggregate=True)


def test_feed_normalizes_units_across_query_period_before_aggregation():
    frame = make_multi_unit_frame()
    frame.loc[1, "trading_date"] = pd.Timestamp("2026-01-06")
    feed = FundamentalFeed("ZN", FrameLoader(frame))

    result = feed.get_warehouse_receipt_data(
        aggregate=True,
        agg_unit_conversions={("zn", "吨", "千克"): 1000.0},
    )

    assert result["trading_date"].tolist() == [
        pd.Timestamp("2026-01-05"),
        pd.Timestamp("2026-01-06"),
    ]
    assert result["receipt_quantity"].tolist() == [3000.0, 2000.0]
    assert result["unit"].tolist() == ["千克", "千克"]


@pytest.mark.parametrize("coefficient", [0, -1, float("inf"), float("nan"), True])
def test_feed_rejects_invalid_conversion_coefficient(coefficient):
    feed = FundamentalFeed("ZN", FrameLoader(make_multi_unit_frame()))

    with pytest.raises(ValueError, match="有限正数"):
        feed.get_warehouse_receipt_data(
            aggregate=True,
            agg_unit_conversions={("ZN", "吨", "千克"): coefficient},
        )


def test_feed_rejects_ambiguous_conversion_target():
    feed = FundamentalFeed("ZN", FrameLoader(make_multi_unit_frame()))

    with pytest.raises(ValueError, match="多个目标单位"):
        feed.get_warehouse_receipt_data(
            aggregate=True,
            agg_unit_conversions={
                ("ZN", "吨", "千克"): 1000.0,
                ("ZN", "吨", "克"): 1_000_000.0,
            },
        )


def test_feed_returns_aggregated_schema_for_empty_interval():
    feed = FundamentalFeed("ZN", FakeLoader())

    result = feed.get_warehouse_receipt_data(
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 2),
        aggregate=True,
    )

    assert result.empty
    assert result.columns.tolist() == WAREHOUSE_RECEIPT_AGGREGATED_COLUMNS


def test_facade_exposes_warehouse_receipt_data(tmp_path):
    make_frame().to_parquet(tmp_path / "SHFE_ZN.parquet", index=False)
    facade = DataFacade(
        env_config=EnvironmentConfig(warehouse_receipt_dir=str(tmp_path))
    )

    result = facade.get_warehouse_receipt_data(
        "zn",
        start_date=date(2026, 1, 6),
        end_date=date(2026, 1, 6),
        aggregate=True,
    )

    assert len(result) == 1
    assert result.loc[0, "receipt_quantity"] == 20


def test_facade_exposes_aggregated_multiple_product_interface(tmp_path):
    frame = make_frame().iloc[[0, 1]].copy()
    frame["trading_date"] = pd.Timestamp("2026-01-05")
    frame.to_parquet(tmp_path / "SHFE_ZN.parquet", index=False)
    facade = DataFacade(
        env_config=EnvironmentConfig(warehouse_receipt_dir=str(tmp_path))
    )

    result = facade.get_warehouse_receipt_data_multiple_products(
        ["zn"],
        aggregate=True,
    )

    assert result.columns.tolist() == WAREHOUSE_RECEIPT_AGGREGATED_COLUMNS
    assert len(result) == 1
    assert result.loc[0, "receipt_quantity"] == 30
