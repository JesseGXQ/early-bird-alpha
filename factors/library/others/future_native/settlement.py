"""结算机制因子。

本模块包含：
    - ``CloseSettleDeviation``：收盘—结算偏离。

结算价是逐日盯市、保证金计算和次日涨跌停基准的核心价格。尾盘收盘价相对
结算中枢的偏离，往往包含短期的流动性冲击或情绪性交易，存在回归倾向。
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
    to_float,
    validate_panel_index,
)
from factors.models import DataRequirement

logger = logging.getLogger(__name__)


class CloseSettleDeviation(Factor, WarningMixin):
    """收盘—结算偏离因子。

    经济机制
    --------
    结算价由交易所按全天成交加权等方式计算，是保证金和次日涨跌停的基准；
    收盘价则是尾盘连续竞价的最后成交。收盘显著高于结算价，说明尾盘买盘
    偏急、价格暂时偏离全天中枢，次日容易回落；收盘显著低于结算价则相反。
    该偏离也可能反映尾盘流动性不足造成的临时价格冲击。

    计算公式
    --------
    使用未复权的主力合约收盘价 ``C_t`` 与结算价 ``S_t``：

        F_t = - log(C_t / S_t)

    预测方向
    --------
    收盘低于结算为正（偏多），收盘高于结算为负（偏空）。

    数据要求
    --------
    主面板的 ``Raw_Close`` 与 ``Settle``。不使用复权价，因为复权只改变价格
    水平，不改变同日收盘/结算的相对关系；但使用原始列可以避免复权因子口径
    差异。

    Parameters
    ----------
    无。

    Notes
    -----
    - 收盘价或结算价非正、缺失时返回 NaN。
    - 该因子是日频、逐品种的横截面/时序通用信号，不涉及换月调整。
    """

    def __init__(self):
        self._reset_warnings()

    @property
    def name(self) -> str:
        return 'close_settle_deviation'

    @property
    def computation_scope(self) -> Literal['per_product']:
        return 'per_product'

    @property
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        return (
            DataRequirement(
                source='main_panel',
                columns=('Raw_Close', 'Settle'),
            ),
        )

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {}

    def compute(self, data: pd.DataFrame) -> pd.Series:
        """批量计算收盘—结算偏离。"""
        self._reset_warnings()
        if data is None or data.empty:
            return empty_factor_series(self.name)

        validate_panel_index(data, ('timestamp', 'product'), 'main_panel')
        missing = [
            column for column in ('Raw_Close', 'Settle')
            if column not in data.columns
        ]
        if missing:
            raise ValueError(f'main_panel 缺少必需列: {missing}。')

        close = to_float(data['Raw_Close'])
        settle = to_float(data['Settle'])
        valid = (
            close.notna() & (close > 0)
            & settle.notna() & (settle > 0)
        )

        values = (-np.log(close / settle)).where(valid)
        values = values.astype('float64')
        values.name = self.name
        if values.notna().sum() == 0:
            self._warn('所有样本的收盘价或结算价均无效，因子全部为 NaN。')
        self._log_warnings(logger, self.name)
        return values
