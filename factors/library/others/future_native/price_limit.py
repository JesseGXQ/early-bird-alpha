"""价格限制机制因子。

本模块包含：
    - ``LimitPressure``：涨跌停压力。

涨跌停板是交易所制度性价格约束。价格越接近涨停，未实现买盘越可能延续到
下一交易日；越接近跌停则相反。该因子可以视为“相对当日涨跌停区间的位置”，
而不是普通收益率。
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Mapping

import pandas as pd

from factors.library.base import Factor
from factors.library.others.future_native._utils import (
    WarningMixin,
    empty_factor_series,
    to_float,
    validate_panel_index,
)
from factors.models import DataRequirement

logger = logging.getLogger(__name__)


class LimitPressure(Factor, WarningMixin):
    """涨跌停压力因子。

    经济机制
    --------
    当价格接近涨停时，买盘可能因无法成交而累积，未实现需求延续到下一交易日；
    接近跌停时，卖盘累积，价格压力向下。即使没有真正封板，收盘价在当日涨跌停
    区间中的位置也反映了尾盘的方向性压力。

    计算公式
    --------
    设 ``C_t`` 为收盘价，``U_t``、``L_t`` 为当日涨停价和跌停价：

        F_t = 2 * (C_t - L_t) / (U_t - L_t) - 1

    因子取值在 [-1, 1] 内：
        - +1：收于涨停；
        - 0：收于区间中点；
        - -1：收于跌停。

    预测方向
    --------
    越接近涨停越偏多，越接近跌停越偏空。

    数据要求
    --------
    主面板的 ``Raw_Close``、``Upper_Limit``、``Lower_Limit``。

    Parameters
    ----------
    无。

    Notes
    -----
    - 涨跌停价缺失、或上限不大于下限时返回 NaN。
    - 若收盘价因数据噪声略微越界，结果截断到 [-1, 1]。
    - 该因子是“区间位置”而非“是否封板”的哑变量；封板强度需要 Tick 盘口数据，
      不在本 V1 实现范围内。
    """

    def __init__(self):
        self._reset_warnings()

    @property
    def name(self) -> str:
        return 'limit_pressure'

    @property
    def computation_scope(self) -> Literal['per_product']:
        return 'per_product'

    @property
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        return (
            DataRequirement(
                source='main_panel',
                columns=('Raw_Close', 'Upper_Limit', 'Lower_Limit'),
            ),
        )

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {}

    def compute(self, data: pd.DataFrame) -> pd.Series:
        """批量计算涨跌停压力。"""
        self._reset_warnings()
        if data is None or data.empty:
            return empty_factor_series(self.name)

        validate_panel_index(data, ('timestamp', 'product'), 'main_panel')
        required = ('Raw_Close', 'Upper_Limit', 'Lower_Limit')
        missing = [column for column in required if column not in data.columns]
        if missing:
            raise ValueError(f'main_panel 缺少必需列: {missing}。')

        close = to_float(data['Raw_Close'])
        upper = to_float(data['Upper_Limit'])
        lower = to_float(data['Lower_Limit'])
        width = upper - lower
        valid = (
            close.notna()
            & upper.notna()
            & lower.notna()
            & (width > 0)
        )

        values = (2.0 * (close - lower) / width - 1.0).where(valid)
        values = values.clip(lower=-1.0, upper=1.0)
        values = values.astype('float64')
        values.name = self.name
        if values.notna().sum() == 0:
            self._warn('所有样本的涨跌停价均无效，因子全部为 NaN。')
        self._log_warnings(logger, self.name)
        return values
