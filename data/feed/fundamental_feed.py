"""
基本面数据馈送器。

当前提供仓单日报明细和品种日聚合数据；后续库存、现货价格等基本面数据
可继续在此领域入口扩展。
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date
from numbers import Real
from typing import TYPE_CHECKING

import pandas as pd

from data.storage.warehouse_receipt_loader import WAREHOUSE_RECEIPT_COLUMNS

if TYPE_CHECKING:
    from data.storage.warehouse_receipt_loader import WarehouseReceiptLoader


WAREHOUSE_RECEIPT_QUANTITY_COLUMNS = [
    "previous_receipt_quantity",
    "receipt_quantity",
    "receipt_change_quantity",
]

WAREHOUSE_RECEIPT_AGGREGATED_COLUMNS = [
    "trading_date",
    "product",
    "product_name",
    "exchange",
    *WAREHOUSE_RECEIPT_QUANTITY_COLUMNS,
    "unit",
]

# 仓单源端在明细里夹带的分地区小计行。这些行的 warehouse 是“合计”或“总计”，
# 其数值等于同一 area 下仓库明细之和，并非独立的仓库记录；若与明细一起加总会
# 重复计数（AG 有 1040 个交易日的日度加总因此恰好翻倍）。全库扫描确认小计标签
# 只有这两种，其余形近标签（小计/总/总量/汇总/总数/sum/total）均不存在。
WAREHOUSE_RECEIPT_SUBTOTAL_WAREHOUSES = ("合计", "总计")

UnitConversionKey = tuple[str, str, str]


class FundamentalFeed:
    """提供单个期货品种的基本面数据。"""

    def __init__(
        self,
        product: str,
        warehouse_receipt_loader: WarehouseReceiptLoader,
    ) -> None:
        self.product = product.upper()
        self.warehouse_receipt_loader = warehouse_receipt_loader

    def get_warehouse_receipt_data(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        aggregate: bool = False,
        agg_unit_conversions: Mapping[UnitConversionKey, float] | None = None,
    ) -> pd.DataFrame:
        """
        返回指定日期闭区间内的仓单日报数据。可选返回“品种-日期-仓库”
        级别明细，或加总到“品种-日期”级别。需要加总时，必须提供
        ``agg_unit_conversion``参数。

        Parameters
        ----------
        start_date : datetime.date or None, default None
            起始交易日；None 表示不限制。
        end_date : datetime.date or None, default None
            结束交易日；None 表示不限制。
        aggregate : bool, default False
            False 返回仓库级明细；True 将查询区间内全部记录先统一单位，
            再按“品种-交易日”加总。加总会自动剔除 warehouse 为“合计”或
            “总计”的分地区小计行，避免明细被重复计入；明细接口不受影响。
        agg_unit_conversions : Mapping[tuple[str, str, str], float] or None
            聚合时采用的单位转换规则。键依次为
            ``(品种代码, 原单位, 目标单位)``，值为原数量乘以的转换系数。
            例如 ``{('SC', '吨', '桶'): 7.33}``。规则只执行一步直接
            转换；查询区间存在多个单位时，所有记录必须最终收敛到唯一单位。
            单位本来唯一时无需提供规则。aggregate=False 时本参数不生效。

        Returns
        -------
        pandas.DataFrame
            aggregate=False 时返回列固定为 ``WAREHOUSE_RECEIPT_COLUMNS``；
            aggregate=True 时返回列固定为
            ``WAREHOUSE_RECEIPT_AGGREGATED_COLUMNS``，且每个交易日最多一行。

        Raises
        ------
        ValueError
            日期区间反转、转换规则非法，或聚合后仍存在多个单位时抛出。

        Notes
        -----
        单位统一针对完整查询区间执行，而不是仅处理同一天内共存的单位。
        这可以避免 AU 等品种在不同日期切换单位后形成量纲不一致的时间序列。

        仓单源端在明细中夹带了 warehouse 为“合计”“总计”的行，其数值等于同一
        area 下仓库明细之和，是分地区小计而非独立仓库记录。aggregate=True 时
        这些行会被剔除，否则日度加总会重复计数（AG 曾因此恰好翻倍）。它们只凭
        warehouse 标签识别，不依赖 area 等可能缺失的字段。aggregate=False 时
        原样返回，便于调用方自行核对小计。
        """
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError(f"start_date {start_date} 不能晚于 end_date {end_date}。")
        if not isinstance(aggregate, bool):
            raise ValueError("aggregate 必须是 bool。")

        df = self.warehouse_receipt_loader.load(self.product)
        result = self._filter_date_range(df, start_date, end_date)
        if aggregate:
            return self._aggregate_warehouse_receipts(
                result,
                agg_unit_conversions=agg_unit_conversions,
            )

        result.sort_values(
            ["trading_date", "warehouse", "warehouse_id", "brand", "grade"],
            na_position="last",
            kind="stable",
            inplace=True,
        )
        result.reset_index(drop=True, inplace=True)
        return result

    @staticmethod
    def _filter_date_range(
        df: pd.DataFrame,
        start_date: date | None,
        end_date: date | None,
    ) -> pd.DataFrame:
        """按闭区间筛选仓单明细，并保持标准明细 schema。"""
        if df.empty:
            return df.loc[:, WAREHOUSE_RECEIPT_COLUMNS].reset_index(drop=True)

        trading_dates = pd.to_datetime(df["trading_date"])
        mask = pd.Series(True, index=df.index)
        if start_date is not None:
            mask &= trading_dates >= pd.Timestamp(start_date)
        if end_date is not None:
            mask &= trading_dates <= pd.Timestamp(end_date)
        return df.loc[mask, WAREHOUSE_RECEIPT_COLUMNS].copy()

    def _aggregate_warehouse_receipts(
        self,
        df: pd.DataFrame,
        agg_unit_conversions: Mapping[UnitConversionKey, float] | None,
    ) -> pd.DataFrame:
        """统一查询区间内的计量单位，再按品种和交易日加总。

        加总前先剔除源端夹带的分地区小计行（warehouse 为“合计”或“总计”），
        它们不是独立仓库记录，与明细一同加总会重复计数。
        """
        if df.empty:
            return (
                df.reindex(columns=WAREHOUSE_RECEIPT_AGGREGATED_COLUMNS)
                .reset_index(drop=True)
            )

        df = self._exclude_subtotal_rows(df)
        if df.empty:
            raise ValueError(
                f"品种 {self.product} 在查询区间内只有“合计”“总计”小计行，"
                "剔除后没有可加总的仓库明细。"
            )

        self._validate_aggregate_identity(df)
        units = self._validate_units(df)
        conversions = self._normalize_unit_conversions(agg_unit_conversions)
        product_conversions = conversions.get(self.product, {})

        normalized = df.copy()
        for source_unit in units:
            conversion = product_conversions.get(source_unit)
            if conversion is None:
                continue
            target_unit, coefficient = conversion
            mask = normalized["unit"] == source_unit
            normalized.loc[mask, WAREHOUSE_RECEIPT_QUANTITY_COLUMNS] = (
                normalized.loc[mask, WAREHOUSE_RECEIPT_QUANTITY_COLUMNS]
                * coefficient
            )
            normalized.loc[mask, "unit"] = target_unit

        normalized_units = sorted(normalized["unit"].unique().tolist())
        if len(normalized_units) != 1:
            raise ValueError(
                f"品种 {self.product} 在查询区间内存在多个计量单位 "
                f"{sorted(units)}，应用转换规则后仍为 {normalized_units}，"
                "无法按‘品种-交易日’安全加总。"
            )

        metadata = {
            column: normalized[column].dropna().iloc[0]
            for column in ("product", "product_name", "exchange")
        }
        result = (
            normalized.groupby("trading_date", sort=True)[
                WAREHOUSE_RECEIPT_QUANTITY_COLUMNS
            ]
            .sum(min_count=1)
            .reset_index()
        )
        result.insert(1, "product", metadata["product"])
        result.insert(2, "product_name", metadata["product_name"])
        result.insert(3, "exchange", metadata["exchange"])
        result["unit"] = normalized_units[0]
        result = result.loc[:, WAREHOUSE_RECEIPT_AGGREGATED_COLUMNS]
        result.sort_values("trading_date", kind="stable", inplace=True)
        result.reset_index(drop=True, inplace=True)
        return result

    @staticmethod
    def _exclude_subtotal_rows(df: pd.DataFrame) -> pd.DataFrame:
        """剔除仓单源端夹带的分地区小计行。

        “合计”行的数值等于同一 ``area`` 下仓库明细之和，是分地区小计而非全国
        总计，也不是独立仓库记录。加总时若保留它们，明细与小计会被重复计入。
        仅剔除 warehouse 命中固定小计标签的行，不依赖 ``area`` 等可能缺失的字段。
        """
        warehouses = df["warehouse"].astype("string")
        is_subtotal = warehouses.isin(WAREHOUSE_RECEIPT_SUBTOTAL_WAREHOUSES)
        if not is_subtotal.any():
            return df
        return df.loc[~is_subtotal].copy()

    def _validate_aggregate_identity(self, df: pd.DataFrame) -> None:
        """确保聚合结果中的品种身份字段具有唯一且非空的取值。"""
        for column in ("product", "product_name", "exchange"):
            values = df[column].dropna().unique().tolist()
            if len(values) != 1:
                raise ValueError(
                    f"品种 {self.product} 的聚合身份字段 {column} "
                    f"必须有且仅有一个非空值，实际为 {values}。"
                )
        actual_product = str(df["product"].dropna().iloc[0]).upper()
        if actual_product != self.product:
            raise ValueError(
                f"加载数据的品种代码 {actual_product} 与请求品种 "
                f"{self.product} 不一致。"
            )

    def _validate_units(self, df: pd.DataFrame) -> set[str]:
        """校验待聚合数据中的单位均为非空字符串。"""
        if df["unit"].isna().any():
            raise ValueError(f"品种 {self.product} 的待聚合仓单数据存在空单位。")
        invalid = [
            unit
            for unit in df["unit"].unique().tolist()
            if not isinstance(unit, str) or not unit.strip()
        ]
        if invalid:
            raise ValueError(
                f"品种 {self.product} 的待聚合仓单数据存在非法单位：{invalid}。"
            )
        return set(df["unit"].unique().tolist())

    @staticmethod
    def _normalize_unit_conversions(
        agg_unit_conversions: Mapping[UnitConversionKey, float] | None,
    ) -> dict[str, dict[str, tuple[str, float]]]:
        """校验并规范化一步式单位转换规则。"""
        if agg_unit_conversions is None:
            return {}
        if not isinstance(agg_unit_conversions, Mapping):
            raise ValueError(
                "agg_unit_conversions 必须是映射或 None。"
                "其中映射的 key 是 (品种代码，原单位，目标单位) 三元字符串组。" \
                "value 需要是 float。"
            )

        normalized: dict[str, dict[str, tuple[str, float]]] = {}
        normalized_keys: dict[UnitConversionKey, float] = {}
        for key, coefficient in agg_unit_conversions.items():
            if (
                not isinstance(key, tuple)
                or len(key) != 3
                or not all(isinstance(part, str) and part.strip() for part in key)
            ):
                raise ValueError(
                    "单位转换规则的键必须是 "
                    "(品种代码, 原单位, 目标单位) 三元字符串组。"
                )
            if (
                isinstance(coefficient, bool)
                or not isinstance(coefficient, Real)
                or not math.isfinite(float(coefficient))
                or coefficient <= 0
            ):
                raise ValueError(f"单位转换系数必须是有限正数：{key} -> {coefficient}。")

            product, source_unit, target_unit = (
                key[0].strip().upper(),
                key[1].strip(),
                key[2].strip(),
            )
            if source_unit == target_unit:
                raise ValueError(f"单位转换的原单位与目标单位不能相同：{key}。")

            normalized_key = (product, source_unit, target_unit)
            existing_coefficient = normalized_keys.get(normalized_key)
            if (
                existing_coefficient is not None
                and existing_coefficient != float(coefficient)
            ):
                raise ValueError(f"单位转换规则重复且系数冲突：{normalized_key}。")
            normalized_keys[normalized_key] = float(coefficient)

            product_rules = normalized.setdefault(product, {})
            existing = product_rules.get(source_unit)
            conversion = (target_unit, float(coefficient))
            if existing is not None and existing != conversion:
                raise ValueError(
                    f"品种 {product} 的原单位 {source_unit} 配置了多个"
                    f"目标单位或系数：{existing} 与 {conversion}。"
                )
            product_rules[source_unit] = conversion

        for product, product_rules in normalized.items():
            source_units = set(product_rules)
            chained_units = {
                target_unit
                for target_unit, _coefficient in product_rules.values()
                if target_unit in source_units
            }
            if chained_units:
                raise ValueError(
                    f"品种 {product} 的单位转换规则形成链式或循环转换："
                    f"{sorted(chained_units)}。当前仅支持一步直接转换。"
                )
        return normalized
