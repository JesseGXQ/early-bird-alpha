"""期限结构与展期机制因子。

本模块包含：
    - ``CurveSlopeChange``：全曲线斜率变化；
    - ``CurveRollDown``：曲线 Roll-down 收益。

两个因子都只依赖期限结构长表，按品种独立计算。
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Mapping

import numpy as np
import pandas as pd

from factors.library.base import Factor
from factors.library.others.future_native._utils import (
    WarningMixin,
    empty_factor_series,
    prepare_term_structure_frame,
    require_positive_int,
    require_price_col,
)
from factors.models import DataRequirement, INDEX_NAMES

logger = logging.getLogger(__name__)


def _fit_slope(
        group: pd.DataFrame,
        price_col: str,
        min_contracts: int = 3,
) -> float:
    """对单日有效合约截面估计年化对数价格斜率。

    回归形式：``log P_i = a + b * (dte_i / 365)``，
    使用 OpenInterest 作为权重，返回斜率 ``b``。
    合约不足或期限不分散时返回 NaN。
    """
    valid = group[group['_valid']]
    if len(valid) < min_contracts:
        return np.nan
    if valid['_dte'].nunique() < 2:
        return np.nan

    x = valid['_dte'].to_numpy(dtype='float64') / 365.0
    y = np.log(valid[price_col].to_numpy(dtype='float64'))
    weights = valid['OpenInterest'].to_numpy(dtype='float64')
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        return np.nan
    if not np.isfinite(weights).all() or (weights <= 0).any():
        return np.nan

    design = np.column_stack([np.ones_like(x), x])
    sqrt_w = np.sqrt(weights)
    try:
        coefficients, *_ = np.linalg.lstsq(
            design * sqrt_w[:, None], y * sqrt_w, rcond=None
        )
    except np.linalg.LinAlgError:
        return np.nan
    return float(coefficients[1])


def _annualized_inverse_slope(
        group: pd.DataFrame,
        price_col: str,
) -> float:
    """返回 ``-b``，使 backwardation 越强因子值越大。"""
    beta = _fit_slope(group, price_col)
    if np.isnan(beta):
        return np.nan
    return -beta


class CurveSlopeChange(Factor, WarningMixin):
    """期限结构斜率变化因子。

    经济机制
    --------
    套保需求变化、库存稀缺和交割压力往往首先体现在期限结构的边际变化，
    而不是绝对 carry 水平。斜率水平的慢变量（如长期仓储成本、品种风险溢价）
    会被差分消掉，因此斜率变化更接近“近期边际供需冲击”。

    计算公式
    --------
    对每个品种、每个交易日 t，用全部有效合约做加权回归：

        log P_{i,t} = a_t + b_t * (dte_{i,t} / 365) + e_{i,t}

    回归权重为 OpenInterest，定义曲线斜率 ``S_t = -b_t``，则：

        F_t = S_t - S_{t-k}

    ``k`` 为 ``lookback``（交易日数）。backwardation 加深时 ``S`` 上升，
    因子为正；contango 加深时因子为负。

    预测方向
    --------
    值越大越看多。

    数据要求
    --------
    期限结构长表，包含价格、``last_trade_date``、``OpenInterest``、``Volume``。
    默认过滤 DTE 在 [3, 300] 天、成交量不低于 100 手的合约，并要求至少 3 个
    有效合约、至少 2 个不同 DTE。

    Parameters
    ----------
    lookback : int, default 5
        斜率差分窗口（按该品种实际有期限结构数据的交易日计）。
    price_col : {'Close', 'Settle'}, default 'Close'
        估计曲线使用的价格口径。

    Notes
    -----
    - 与 ``factors.library.basic.CurveSlope`` 使用相同的经济定义，但本因子
      自带稳健的数值处理：nullable dtype、空输入、退化截面均返回 NaN。
    - 输出索引为 MultiIndex[timestamp, product]；历史不足 k 期的时点为 NaN。
    """

    def __init__(
            self,
            lookback: int = 5,
            price_col: Literal['Close', 'Settle'] = 'Close',
    ):
        self.lookback = require_positive_int(lookback, 'lookback')
        self.price_col = require_price_col(price_col)
        self._reset_warnings()

    @property
    def name(self) -> str:
        return f'curve_slope_change_{self.lookback}_{self.price_col}'

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
                ),
                request_params={'price_col': self.price_col},
            ),
        )

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {
            'lookback': self.lookback,
            'price_col': self.price_col,
        }

    def compute(self, data: pd.DataFrame) -> pd.Series:
        """批量计算全曲线斜率变化。"""
        self._reset_warnings()
        if data is None or data.empty:
            return empty_factor_series(self.name)

        prepared = prepare_term_structure_frame(data, self.price_col)

        slope_records: dict[tuple[pd.Timestamp, str], float] = {}
        for (timestamp, product), group in prepared.groupby(
                level=['timestamp', 'product'], sort=True
        ):
            slope_records[(pd.Timestamp(timestamp), str(product))] = (
                _annualized_inverse_slope(group, self.price_col)
            )

        if not slope_records:
            return empty_factor_series(self.name)

        slope_index = pd.MultiIndex.from_tuples(
            slope_records.keys(), names=INDEX_NAMES
        )
        slopes = pd.Series(
            list(slope_records.values()),
            index=slope_index,
            name='curve_slope',
            dtype='float64',
        )

        pieces: list[pd.Series] = []
        for product, product_slopes in slopes.groupby(
                level='product', sort=False
        ):
            series = product_slopes.droplevel('product').sort_index()
            series = series[~series.index.duplicated(keep='last')]
            change = series - series.shift(self.lookback)

            all_nan = bool(change.notna().sum() == 0)
            if all_nan:
                self._warn(
                    f'product={product}: 有效斜率历史不足 {self.lookback + 1} '
                    f'个交易日，因子全部为 NaN。'
                )

            change.index = pd.MultiIndex.from_arrays(
                [change.index, [product] * len(change)],
                names=INDEX_NAMES,
            )
            pieces.append(change)

        result = pd.concat(pieces).sort_index()
        result.name = self.name
        result = result.astype('float64')
        self._log_warnings(logger, self.name)
        return result


class CurveRollDown(Factor, WarningMixin):
    r"""期限结构 Roll-down 因子。

    经济机制
    --------
    若当前期限曲线形状在短期内保持不变，随着合约剩余到期日缩短，合约价格
    会沿着曲线向近端移动。对 backwardation 曲线，这种移动本身产生正收益；
    对 contango 曲线则产生负收益。该因子度量的是“持有并等待合约到期日缩短”
    的机械收益，而不是曲线斜率水平本身。

    计算公式
    --------
    对主力合约 d，设其剩余到期日为 ``d_dom``，预测期限为 ``h`` 天：

        RD_t(h) = log \hat P_t(d_dom - h) - log P_t(d_dom)

    其中 ``\hat P_t`` 由当日有效合约的对数价格关于 DTE 做分段线性插值得到。
    若目标 DTE 超出当日有效合约的期限范围，则返回 NaN，不做外推。

    预测方向
    --------
    值越大越看多（正 roll-down）。

    数据要求
    --------
    期限结构长表，包含价格、``last_trade_date``、``OpenInterest``、``Volume``、
    ``is_dominant``。默认过滤 DTE 在 [3, 300] 天、成交量不低于 100 手的合约。

    Parameters
    ----------
    horizon_days : int, default 20
        假设合约剩余到期日缩短的日历天数。
    price_col : {'Close', 'Settle'}, default 'Close'
        价格口径。

    Notes
    -----
    - 主力合约缺失时退化为当日 OpenInterest 最大且有效的合约，并记录警告。
    - 目标 DTE 小于等于 0、或落在有效曲线范围之外时返回 NaN。
    - 输出为 ``h`` 天内的对数 roll-down 收益，未年化。
    """

    def __init__(
            self,
            horizon_days: int = 20,
            price_col: Literal['Close', 'Settle'] = 'Close',
    ):
        self.horizon_days = require_positive_int(
            horizon_days, 'horizon_days'
        )
        self.price_col = require_price_col(price_col)
        self._reset_warnings()

    @property
    def name(self) -> str:
        return f'curve_roll_down_{self.horizon_days}d_{self.price_col}'

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
                    'is_dominant',
                ),
                request_params={'price_col': self.price_col},
            ),
        )

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {
            'horizon_days': self.horizon_days,
            'price_col': self.price_col,
        }

    def compute(self, data: pd.DataFrame) -> pd.Series:
        """批量计算主力合约的 Roll-down 收益。"""
        self._reset_warnings()
        if data is None or data.empty:
            return empty_factor_series(self.name)

        prepared = prepare_term_structure_frame(
            data, self.price_col, require_dominant=True
        )

        records: dict[tuple[pd.Timestamp, str], float] = {}
        for (timestamp, product), group in prepared.groupby(
                level=['timestamp', 'product'], sort=True
        ):
            key = (pd.Timestamp(timestamp), str(product))
            records[key] = self._group_roll_down(group, key)

        if not records:
            return empty_factor_series(self.name)

        index = pd.MultiIndex.from_tuples(records.keys(), names=INDEX_NAMES)
        result = pd.Series(
            list(records.values()), index=index, name=self.name,
            dtype='float64',
        ).sort_index()
        self._log_warnings(logger, self.name)
        return result

    def _group_roll_down(
            self,
            group: pd.DataFrame,
            key: tuple[pd.Timestamp, str],
    ) -> float:
        """计算单个 (timestamp, product) 截面的 Roll-down。"""
        valid = group[group['_valid']]
        if len(valid) < 2:
            self._warn(
                f'timestamp={key[0]}, product={key[1]}: '
                f'有效合约数 {len(valid)} < 2，无法计算 Roll-down。'
            )
            return np.nan

        dominant = valid[valid['is_dominant']]
        if dominant.empty:
            dominant = valid.sort_values(
                'OpenInterest', ascending=False
            ).head(1)
            self._warn(
                f'timestamp={key[0]}, product={key[1]}: '
                f'无主力标记，退化为 OpenInterest 最大合约。'
            )
        else:
            dominant = dominant.sort_values(
                'OpenInterest', ascending=False
            ).head(1)

        dominant_row = dominant.iloc[0]
        target_dte = float(dominant_row['_dte']) - float(self.horizon_days)
        if target_dte <= 0:
            return np.nan

        curve = valid[['_dte', self.price_col]].copy()
        curve['_log_price'] = np.log(
            curve[self.price_col].to_numpy(dtype='float64')
        )
        curve = (
            curve.groupby('_dte', as_index=False)['_log_price']
            .mean()
            .sort_values('_dte')
        )
        if len(curve) < 2:
            return np.nan
        min_dte = float(curve['_dte'].iloc[0])
        max_dte = float(curve['_dte'].iloc[-1])
        if target_dte < min_dte or target_dte > max_dte:
            return np.nan

        interpolated_log_price = float(np.interp(
            target_dte,
            curve['_dte'].to_numpy(dtype='float64'),
            curve['_log_price'].to_numpy(dtype='float64'),
        ))
        return interpolated_log_price - float(
            np.log(float(dominant_row[self.price_col]))
        )
