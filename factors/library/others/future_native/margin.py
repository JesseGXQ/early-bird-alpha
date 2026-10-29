"""保证金与杠杆约束机制因子。

本模块包含：
    - ``MarginShockDeleveraging``：保证金冲击去杠杆。

交易所提高保证金率会直接抬高持仓成本，迫使高杠杆头寸减仓。若此前市场已经
形成单边拥挤行情，提保往往触发该方向的被动平仓，造成短期反转。
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
    cumulative_return,
    empty_factor_series,
    require_positive_int,
    stable_dominant_mask,
    to_float,
    validate_panel_index,
)
from factors.models import DataRequirement, INDEX_NAMES

logger = logging.getLogger(__name__)


class MarginShockDeleveraging(Factor, WarningMixin):
    """保证金冲击去杠杆因子。

    经济机制
    --------
    保证金率是交易所设定的杠杆约束。提高保证金率会增加持仓资金占用，迫使
    部分高杠杆账户减仓。若此前价格已经单边上涨（多头拥挤），提保更容易触发
    多头被动平仓，价格短期偏空；若此前单边下跌（空头拥挤），提保则可能触发
    空头回补，价格短期偏多。因此因子方向应逆于此前的拥挤方向。

    计算公式
    --------
    设 ``M_t`` 为主力合约的多空保证金率均值，``r_k`` 为最近 k 期复合收益率：

        F_t = - sign(r_k) * max(M_t - M_{t-k}, 0)

    仅当保证金率上升时因子非零；保证金不变或下降时取 0。

    预测方向
    --------
    涨势后提保为负（偏空），跌势后提保为正（偏多）。

    数据要求
    --------
    需要两个数据源，``compute`` 接收 mapping：

        {
            'main_panel': MultiIndex[timestamp, product] 主面板,
            'instrument_daily_status': 合约日频状态表,
        }

    ``main_panel`` 需要 ``True_Return``、``Dominant_Contract``；
    ``instrument_daily_status`` 需要 ``trading_date``、``product``、
    ``instrument``、``long_margin_ratio``、``short_margin_ratio``。

    Parameters
    ----------
    lookback : int, default 5
        保证金变化和前期动量的共同回看期数（交易日）。

    Notes
    -----
    - 使用主力合约的交易所保证金率，并对多空保证金率取平均；不使用期货公司
      加收后的保证金率。
    - 窗口内主力合约发生变化时返回 NaN，避免换月带来的保证金机械跳变。
    - 保证金率或收益率缺失时返回 NaN；保证金率未上升时返回 0。
    - 交易所保证金率通常在 2007 年之后才有较完整的历史，早期样本可能缺失。
    """

    def __init__(self, lookback: int = 5):
        self.lookback = require_positive_int(lookback, 'lookback')
        self._reset_warnings()

    @property
    def name(self) -> str:
        return f'margin_shock_{self.lookback}'

    @property
    def computation_scope(self) -> Literal['per_product']:
        return 'per_product'

    @property
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        return (
            DataRequirement(
                source='main_panel',
                columns=('True_Return', 'Dominant_Contract'),
            ),
            DataRequirement(
                source='instrument_daily_status',
                columns=(
                    'trading_date',
                    'product',
                    'instrument',
                    'long_margin_ratio',
                    'short_margin_ratio',
                ),
            ),
        )

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {'lookback': self.lookback}

    def compute(self, data: Mapping[str, pd.DataFrame]) -> pd.Series:
        """批量计算保证金冲击去杠杆。"""
        self._reset_warnings()
        if not isinstance(data, Mapping):
            raise ValueError(
                'MarginShockDeleveraging.compute 需要 mapping 输入，包含 '
                "'main_panel' 和 'instrument_daily_status' 两个数据源。"
            )

        required_sources = ('main_panel', 'instrument_daily_status')
        missing_sources = [s for s in required_sources if s not in data]
        if missing_sources:
            raise ValueError(f'缺少数据源: {missing_sources}。')

        panel = data['main_panel']
        if panel is None or panel.empty:
            return empty_factor_series(self.name)
        validate_panel_index(panel, ('timestamp', 'product'), 'main_panel')
        required_columns = ['True_Return', 'Dominant_Contract']
        missing_columns = [
            column for column in required_columns
            if column not in panel.columns
        ]
        if missing_columns:
            raise ValueError(
                f'main_panel 缺少必需列: {missing_columns}。'
            )

        status = self._prepare_status(data['instrument_daily_status'])

        pieces: list[pd.Series] = []
        for product, group in panel.groupby(level='product', sort=False):
            product_series = group.droplevel('product').sort_index()
            values = self._compute_product(
                series=product_series,
                status=status,
                product=str(product),
            )
            pieces.append(values)

        if not pieces:
            return empty_factor_series(self.name)

        result = pd.concat(pieces).sort_index()
        result.name = self.name
        result = result.astype('float64')
        self._log_warnings(logger, self.name)
        return result

    @staticmethod
    def _prepare_status(data: pd.DataFrame | None) -> pd.DataFrame:
        """校验并整理合约日频状态表。"""
        columns = (
            'trading_date',
            'product',
            'instrument',
            'long_margin_ratio',
            'short_margin_ratio',
        )
        if data is None or data.empty:
            return pd.DataFrame(columns=list(columns) + ['margin_ratio'])
        missing = [c for c in columns if c not in data.columns]
        if missing:
            raise ValueError(
                f'instrument_daily_status 缺少必需列: {missing}。'
            )

        frame = data.copy()
        frame['trading_date'] = pd.to_datetime(
            frame['trading_date'], errors='coerce'
        )
        frame['product'] = (
            frame['product'].astype('string').str.upper()
        )
        frame['instrument'] = (
            frame['instrument'].astype('string').str.upper()
        )
        frame['margin_ratio'] = (
            to_float(frame['long_margin_ratio'])
            + to_float(frame['short_margin_ratio'])
        ) / 2.0
        frame = frame.dropna(
            subset=['trading_date', 'product', 'instrument', 'margin_ratio']
        )
        frame = frame.drop_duplicates(
            ['trading_date', 'product', 'instrument'], keep='last'
        )
        return frame

    def _compute_product(
            self,
            *,
            series: pd.DataFrame,
            status: pd.DataFrame,
            product: str,
    ) -> pd.Series:
        """计算单个品种的保证金冲击因子。"""
        left = series[['True_Return', 'Dominant_Contract']].reset_index()
        left['instrument'] = (
            left['Dominant_Contract'].astype('string').str.upper()
        )
        product_status = status[status['product'] == product]
        merged = left.merge(
            product_status,
            left_on=['timestamp', 'instrument'],
            right_on=['trading_date', 'instrument'],
            how='left',
        )
        merged = merged.set_index('timestamp').sort_index()

        margin = merged['margin_ratio'].astype('float64')
        returns = to_float(merged['True_Return'])
        momentum = cumulative_return(returns, self.lookback)
        margin_change = margin - margin.shift(self.lookback)
        stable = stable_dominant_mask(
            merged['Dominant_Contract'], self.lookback
        )

        values = (
            -np.sign(momentum) * margin_change.clip(lower=0.0)
        ).where(stable & momentum.notna() & margin_change.notna())
        values = values.astype('float64')
        if values.notna().sum() == 0:
            self._warn(
                f'product={product}: 无有效保证金或收益率样本，'
                f'因子全部为 NaN。'
            )

        values.index = pd.MultiIndex.from_arrays(
            [values.index, [product] * len(values)],
            names=INDEX_NAMES,
        )
        return values
