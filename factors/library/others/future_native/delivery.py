"""实物交割与库存机制因子。

本模块包含：
    - ``SeasonalReceiptScarcity``：季节调整后的仓单稀缺度；
    - ``DeliverableCoverage``：可交割覆盖率（挤仓压力）。

仓单是“已注册、可交割凭证”，不是社会总库存。这里的因子只解释交割月附近的
供给约束，不应等同于产业库存周期信号。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pandas as pd

from factors.library.base import Factor
from factors.library.others.future_native._utils import (
    WarningMixin,
    empty_factor_series,
    prepare_term_structure_frame,
    require_non_negative_int,
    require_positive_int,
    require_price_col,
    to_float,
)
from factors.models import DataRequirement, INDEX_NAMES

logger = logging.getLogger(__name__)

# 仓单单位已经与合约乘数单位一致；仅少数品种存在量纲差。
# AU 的合约乘数单位是克，而仓单日报单位是千克，需要把千克换算成克。
_RECEIPT_UNIT_SCALE: dict[tuple[str, str], float] = {
    ('AU', '千克'): 1000.0,
}

# 仓单缺失时允许向前填充的最大交易日数。
_RECEIPT_FFILL_LIMIT = 5


class SeasonalReceiptScarcity(Factor, WarningMixin):
    """季节调整后的仓单稀缺因子。

    经济机制
    --------
    商品库存和仓单具有强季节性（例如农产品收割季累库、工业品春节前后累库）。
    直接比较仓单的环比变化会把季节性误判为供需冲击。更合理的做法是把当前
    仓单水平与“历史同季节”的分布比较：当前仓单低于历史同周常态时，可交割
    供给偏紧，近月价格更容易获得支撑。

    计算公式
    --------
    令 ``x_t = log(1 + W_t)``，``W_t`` 为品种日度注册仓单总量。历史样本定义为：

        H_t = { x_s : s < t - 365 天，且 s 与 t 的 ISO 周相差不超过 week_window }

    要求 ``H_t`` 至少覆盖 ``min_years`` 个不同年份，且样本标准差为正，则：

        F_t = - (x_t - mean(H_t)) / std(H_t)

    预测方向
    --------
    仓单低于历史同周常态（负 z）时为正，偏多；高于常态时为负。

    数据要求
    --------
    仓单日聚合数据，列为 ``trading_date``、``product``、``receipt_quantity``。
    应当使用 ``aggregate=True`` 的品种-交易日级数据，避免仓库级明细重复计数。

    Parameters
    ----------
    week_window : int, default 1
        同季节匹配的 ISO 周容差。1 表示同周及前后各一周。
    min_years : int, default 3
        历史样本至少覆盖的不同年份数。小于 2 没有统计意义。

    Notes
    -----
    - 严格因果：历史样本只使用 ``t - 365`` 天之前的数据，且不包含当年数据。
    - 输出索引只包含仓单数据中真实存在的 (trading_date, product)。
    - 新品种或仓单历史不足 ``min_years + 1`` 年时，因子为 NaN。
    - 仓单为 0 是有效观测（``log1p(0)=0``），代表可交割供给极低。
    """

    def __init__(
            self,
            week_window: int = 1,
            min_years: int = 3,
    ):
        self.week_window = require_non_negative_int(
            week_window, 'week_window'
        )
        self.min_years = require_positive_int(
            min_years, 'min_years', minimum=2
        )
        self._reset_warnings()

    @property
    def name(self) -> str:
        return (
            f'seasonal_receipt_scarcity_w{self.week_window}'
            f'_y{self.min_years}'
        )

    @property
    def computation_scope(self) -> Literal['per_product']:
        return 'per_product'

    @property
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        return (
            DataRequirement(
                source='warehouse_receipt',
                columns=('trading_date', 'product', 'receipt_quantity'),
                request_params={'aggregate': True},
            ),
        )

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {
            'week_window': self.week_window,
            'min_years': self.min_years,
        }

    def compute(self, data: pd.DataFrame) -> pd.Series:
        """批量计算季节调整后的仓单稀缺度。"""
        self._reset_warnings()
        if data is None or data.empty:
            return empty_factor_series(self.name)

        required = ['trading_date', 'product', 'receipt_quantity']
        missing = [c for c in required if c not in data.columns]
        if missing:
            raise ValueError(f'warehouse_receipt 缺少必需列: {missing}。')

        frame = data.copy()
        frame['trading_date'] = pd.to_datetime(
            frame['trading_date'], errors='coerce'
        )
        frame['product'] = (
            frame['product'].astype('string').str.upper()
        )
        frame['receipt_quantity'] = to_float(frame['receipt_quantity'])
        frame = frame.dropna(subset=['trading_date', 'product'])
        frame = frame.sort_values(['product', 'trading_date'])
        duplicated = frame.duplicated(['product', 'trading_date'])
        if duplicated.any():
            raise ValueError(
                'warehouse_receipt 检测到重复的 (trading_date, product)：'
                '请使用 aggregate=True 的品种-交易日聚合数据。'
            )

        pieces: list[pd.Series] = []
        for product, group in frame.groupby('product', sort=False):
            group = group.dropna(subset=['receipt_quantity'])
            if group.empty:
                continue
            values = self._compute_product(group, str(product))
            pieces.append(values)

        if not pieces:
            return empty_factor_series(self.name)

        result = pd.concat(pieces).sort_index()
        result.name = self.name
        result = result.astype('float64')
        self._log_warnings(logger, self.name)
        return result

    def _compute_product(
            self,
            group: pd.DataFrame,
            product: str,
    ) -> pd.Series:
        """计算单个品种的季节调整仓单稀缺度。"""
        dates = pd.DatetimeIndex(group['trading_date'])
        quantity = group['receipt_quantity'].to_numpy(dtype='float64')
        if (quantity < 0).any():
            self._warn(
                f'product={product}: 存在负仓单量，负值按缺失处理。'
            )
            quantity = np.where(quantity >= 0, quantity, np.nan)

        x = np.log1p(quantity)
        weeks = dates.isocalendar()['week'].to_numpy(dtype='int64')
        years = dates.year.to_numpy(dtype='int64')
        values = np.full(len(dates), np.nan, dtype='float64')

        for i, date in enumerate(dates):
            if np.isnan(x[i]):
                continue
            cutoff = date - pd.Timedelta(days=365)
            prefix = int(dates.searchsorted(cutoff, side='left'))
            if prefix <= 0:
                continue

            week_distance = np.abs(weeks[:prefix] - weeks[i])
            week_distance = np.minimum(week_distance, 53 - week_distance)
            seasonal_mask = week_distance <= self.week_window
            if not seasonal_mask.any():
                continue

            sample = x[:prefix][seasonal_mask]
            sample_years = years[:prefix][seasonal_mask]
            finite = np.isfinite(sample)
            sample = sample[finite]
            sample_years = sample_years[finite]
            if sample.size < self.min_years:
                continue
            if np.unique(sample_years).size < self.min_years:
                continue

            std = float(np.std(sample, ddof=1))
            if not np.isfinite(std) or std <= 0:
                continue
            values[i] = - (x[i] - float(np.mean(sample))) / std

        index = pd.MultiIndex.from_arrays(
            [dates, [product] * len(dates)], names=INDEX_NAMES
        )
        result = pd.Series(values, index=index, name=self.name)
        if result.notna().sum() == 0:
            self._warn(
                f'product={product}: 仓单历史不足 {self.min_years + 1} 年，'
                f'或同季节样本方差为 0，因子全部为 NaN。'
            )
        return result


class DeliverableCoverage(Factor, WarningMixin):
    """可交割覆盖率（挤仓压力）因子。

    经济机制
    --------
    近月合约未平仓量代表潜在交割需求，注册仓单代表可交割供给。二者之比越高，
    空头在临近交割时越难组织实物交割，近月合约越容易维持 backwardation 或
    出现挤仓。该因子是“实物约束”信号，而不是普通库存周期信号。

    计算公式
    --------
    设 ``OI_near`` 为剩余到期日不小于 ``near_dte_min`` 的最近月合约持仓量，
    ``mult`` 为该合约乘数，``W_t`` 为仓单总量，``scale`` 为仓单单位到乘数单位的
    换算系数：

        F_t = log(1 + OI_near * mult) - log(1 + W_t * scale)   （物理单位仓单）

    对以“手/张”计量的仓单，仓单本身即合约当量，直接使用：

        F_t = log(1 + OI_near) - log(1 + W_t)

    使用 ``log1p`` 是为了压缩极值，并让 ``W_t=0`` 时仍得到有限值。

    预测方向
    --------
    值越大，近月可交割供给相对潜在交割需求越紧张，越偏多近月。

    数据要求
    --------
    需要两个数据源，``compute`` 接收 mapping：

        {
            'term_structure': MultiIndex[timestamp, product, contract] 长表,
            'warehouse_receipt': 品种-交易日聚合仓单数据,
        }

    ``term_structure`` 必须包含价格、``last_trade_date``、``OpenInterest``、
    ``Volume``、``multiplier``；``warehouse_receipt`` 必须包含
    ``trading_date``、``product``、``receipt_quantity``、``unit``。

    Parameters
    ----------
    near_dte_min : int, default 0
        最近月合约的最小剩余到期日。0 表示允许使用即将进入交割的合约；
        调大可以剔除临近到期、流动性骤降的合约。
    price_col : {'Close', 'Settle'}, default 'Close'
        用于判断合约价格有效性的价格口径。

    Notes
    -----
    - 仓单按品种-交易日重采样到期限结构日期，最多向前填充 5 个交易日。
    - 仓单单位缺失、乘数缺失、仓单量为负时返回 NaN。
    - AU 的乘数单位是克、仓单单位是千克，代码内做显式换算；其余品种不需要。
    - 输出索引为期限结构中的 (timestamp, product)。
    """

    def __init__(
            self,
            near_dte_min: int = 0,
            price_col: Literal['Close', 'Settle'] = 'Close',
    ):
        self.near_dte_min = require_non_negative_int(
            near_dte_min, 'near_dte_min'
        )
        self.price_col = require_price_col(price_col)
        self._reset_warnings()

    @property
    def name(self) -> str:
        return (
            f'deliverable_coverage_{self.price_col}'
            f'_dte{self.near_dte_min}'
        )

    @property
    def computation_scope(self) -> Literal['per_product']:
        return 'per_product'

    @property
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        return (
            DataRequirement(
                source='term_structure',
                columns=(
                    self.price_col,
                    'last_trade_date',
                    'OpenInterest',
                    'Volume',
                    'multiplier',
                ),
                request_params={'price_col': self.price_col},
            ),
            DataRequirement(
                source='warehouse_receipt',
                columns=(
                    'trading_date',
                    'product',
                    'receipt_quantity',
                    'unit',
                ),
                request_params={'aggregate': True},
            ),
        )

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {
            'near_dte_min': self.near_dte_min,
            'price_col': self.price_col,
        }

    def compute(self, data: Mapping[str, pd.DataFrame]) -> pd.Series:
        """批量计算可交割覆盖率。"""
        self._reset_warnings()
        if not isinstance(data, Mapping):
            raise ValueError(
                'DeliverableCoverage.compute 需要 mapping 输入，包含 '
                "'term_structure' 和 'warehouse_receipt' 两个数据源。"
            )

        required_sources = ('term_structure', 'warehouse_receipt')
        missing_sources = [s for s in required_sources if s not in data]
        if missing_sources:
            raise ValueError(f'缺少数据源: {missing_sources}。')

        term_raw = data['term_structure']
        if term_raw is None or term_raw.empty:
            return empty_factor_series(self.name)

        prepared = prepare_term_structure_frame(
            term_raw,
            self.price_col,
            min_dte=self.near_dte_min,
            min_volume=0.0,
        )
        if 'multiplier' not in prepared.columns:
            raise ValueError('term_structure 缺少必需列: multiplier。')
        prepared['multiplier'] = to_float(prepared['multiplier'])

        warehouse = self._prepare_warehouse(data['warehouse_receipt'])
        w_map = self._build_receipt_map(warehouse)

        dates_by_product = {
            str(product): pd.DatetimeIndex(sorted(
                group.index.get_level_values('timestamp').unique()
            ))
            for product, group in prepared.groupby(level='product')
        }
        aligned_receipts: dict[
            str, tuple[pd.Series, pd.Series]
        ] = {}
        for product, dates in dates_by_product.items():
            if product in w_map:
                quantity, unit = w_map[product]
                aligned_receipts[product] = (
                    quantity.reindex(dates).ffill(limit=_RECEIPT_FFILL_LIMIT),
                    unit.reindex(dates).ffill(limit=_RECEIPT_FFILL_LIMIT),
                )
            else:
                aligned_receipts[product] = (
                    pd.Series(np.nan, index=dates, dtype='float64'),
                    pd.Series(pd.NA, index=dates, dtype='object'),
                )

        records: dict[tuple[pd.Timestamp, str], float] = {}
        for (timestamp, product), group in prepared.groupby(
                level=['timestamp', 'product'], sort=True
        ):
            key = (pd.Timestamp(timestamp), str(product))
            quantity_series, unit_series = aligned_receipts[str(product)]
            records[key] = self._compute_group(
                group=group,
                product=str(product),
                timestamp=pd.Timestamp(timestamp),
                quantity_series=quantity_series,
                unit_series=unit_series,
            )

        if not records:
            return empty_factor_series(self.name)

        index = pd.MultiIndex.from_tuples(records.keys(), names=INDEX_NAMES)
        result = pd.Series(
            list(records.values()), index=index, name=self.name,
            dtype='float64',
        ).sort_index()
        self._log_warnings(logger, self.name)
        return result

    @staticmethod
    def _prepare_warehouse(data: pd.DataFrame | None) -> pd.DataFrame:
        """校验并整理仓单日聚合数据。"""
        columns = (
            'trading_date', 'product', 'receipt_quantity', 'unit'
        )
        if data is None or data.empty:
            return pd.DataFrame(columns=list(columns))
        missing = [c for c in columns if c not in data.columns]
        if missing:
            raise ValueError(f'warehouse_receipt 缺少必需列: {missing}。')

        frame = data.copy()
        frame['trading_date'] = pd.to_datetime(
            frame['trading_date'], errors='coerce'
        )
        frame['product'] = (
            frame['product'].astype('string').str.upper()
        )
        frame['receipt_quantity'] = to_float(frame['receipt_quantity'])
        frame['unit'] = frame['unit'].astype('string')
        frame = frame.dropna(subset=['trading_date', 'product'])
        frame = frame.sort_values(['product', 'trading_date'])
        duplicated = frame.duplicated(['product', 'trading_date'])
        if duplicated.any():
            raise ValueError(
                'warehouse_receipt 检测到重复的 (trading_date, product)：'
                '请使用 aggregate=True 的品种-交易日聚合数据。'
            )
        return frame

    @staticmethod
    def _build_receipt_map(
            frame: pd.DataFrame,
    ) -> dict[str, tuple[pd.Series, pd.Series]]:
        """构建 品种 -> (仓单量序列, 单位序列)。"""
        result: dict[str, tuple[pd.Series, pd.Series]] = {}
        if frame.empty:
            return result
        for product, group in frame.groupby('product', sort=False):
            indexed = group.set_index('trading_date').sort_index()
            result[str(product)] = (
                indexed['receipt_quantity'].astype('float64'),
                indexed['unit'],
            )
        return result

    def _compute_group(
            self,
            *,
            group: pd.DataFrame,
            product: str,
            timestamp: pd.Timestamp,
            quantity_series: pd.Series,
            unit_series: pd.Series,
    ) -> float:
        """计算单个 (timestamp, product) 截面的可交割覆盖率。"""
        valid = group[group['_valid']]
        if valid.empty:
            self._warn(
                f'timestamp={timestamp}, product={product}: '
                f'无有效合约，无法计算可交割覆盖率。'
            )
            return np.nan

        near = valid.sort_values(
            ['_dte', 'OpenInterest'], ascending=[True, False]
        ).iloc[0]
        near_oi = float(near['OpenInterest'])
        multiplier = float(near['multiplier'])
        receipt_quantity = quantity_series.get(timestamp, np.nan)
        unit = unit_series.get(timestamp, pd.NA)

        if (
            not np.isfinite(near_oi)
            or not np.isfinite(multiplier)
            or multiplier <= 0
            or pd.isna(receipt_quantity)
            or pd.isna(unit)
        ):
            return np.nan

        receipt_quantity = float(receipt_quantity)
        if receipt_quantity < 0:
            self._warn(
                f'timestamp={timestamp}, product={product}: '
                f'仓单量为负，返回 NaN。'
            )
            return np.nan

        unit_text = str(unit).strip()
        if not unit_text:
            self._warn(
                f'timestamp={timestamp}, product={product}: '
                f'仓单单位为空，返回 NaN。'
            )
            return np.nan
        if unit_text in ('手', '张'):
            demand = near_oi
            supply = receipt_quantity
        else:
            scale = _RECEIPT_UNIT_SCALE.get(
                (product, unit_text), 1.0
            )
            demand = near_oi * multiplier
            supply = receipt_quantity * scale

        return float(np.log1p(demand) - np.log1p(supply))
