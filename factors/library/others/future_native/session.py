"""交易时段信息机制因子。

本模块包含：
    - ``OvernightDayReturn``：隔夜/日盘收益分解。

夜盘主要承接海外市场信息，日盘主要承接国内现货、政策和产业信息。二者的
信息含量和参与者结构不同，因此把日收益拆成夜盘和日盘两部分，可以构造
“夜盘信息延续/日盘信息反转”等研究假设。
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

_COMPONENTS = ('night', 'day', 'diff')


class OvernightDayReturn(Factor, WarningMixin):
    """隔夜/日盘收益分解因子。

    经济机制
    --------
    国内商品期货的夜盘（前一自然日 21:00 至次日凌晨）主要交易海外宏观、
    外盘商品和汇率信息；日盘（09:00–15:00）则更多反映国内现货、库存、政策
    和产业信息。把交易日收益拆成夜盘和日盘，可以区分信息来自“外部冲击”还是
    “国内基本面”，并检验哪一段信息更容易延续。

    计算公式
    --------
    对交易日 t：

        r_night,t = 夜盘各分钟 True_Return 的复合收益
        r_day,t   = 日盘各分钟 True_Return 的复合收益

    因子按 ``component`` 取：

        F_t = r_night,t                    （component='night'）
        F_t = r_day,t                      （component='day'）
        F_t = r_night,t - r_day,t          （component='diff'）

    预测方向
    --------
    默认 ``component='night'``：夜盘信息方向通常更容易延续，夜盘上涨偏多、
    夜盘下跌偏空。实际方向应按品种和研究样本验证；``diff`` 用于研究夜盘与
    日盘信息的相对强弱。

    数据要求
    --------
    日频化的交易时段收益表，MultiIndex[timestamp, product]，至少包含
    ``night_return``、``day_return`` 两列。该数据源由后续桥接层从 1min
    行情派生；因子本身不直接读取分钟数据。

    Parameters
    ----------
    component : {'night', 'day', 'diff'}, default 'night'
        输出哪一段收益或两段之差。

    Notes
    -----
    - ``r_night`` 包含前一交易日 15:00 收盘到夜盘收盘之间的跳空收益，因为
      它由夜盘第一根 Bar 的 ``True_Return``（相对前一日最后一根 Bar）复合而来。
    - ``r_day`` 是日盘连续交易收益，通常从夜盘最后一根 Bar 收盘到当日 15:00。
    - 无夜盘品种（如股指期货）的 ``night_return`` 为 NaN；此时 ``night`` 和
      ``diff`` 均为 NaN，``day`` 仍可计算，但其收益包含隔夜跳空。
    - 任一子时段内存在缺失 Bar 时，该时段收益为 NaN，不做部分复合。
    """

    def __init__(
            self,
            component: Literal['night', 'day', 'diff'] = 'night',
    ):
        if component not in _COMPONENTS:
            raise ValueError(
                f"component 仅支持 {_COMPONENTS}，收到 {component!r}。"
            )
        self.component = component
        self._reset_warnings()

    @property
    def name(self) -> str:
        return f'overnight_day_return_{self.component}'

    @property
    def computation_scope(self) -> Literal['per_product']:
        return 'per_product'

    @property
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        return (
            DataRequirement(
                source='session_return',
                columns=('night_return', 'day_return'),
            ),
        )

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {'component': self.component}

    def compute(self, data: pd.DataFrame) -> pd.Series:
        """批量计算隔夜/日盘收益分解。"""
        self._reset_warnings()
        if data is None or data.empty:
            return empty_factor_series(self.name)

        validate_panel_index(data, ('timestamp', 'product'), 'session_return')
        required = ('night_return', 'day_return')
        missing = [column for column in required if column not in data.columns]
        if missing:
            raise ValueError(f'session_return 缺少必需列: {missing}。')

        night = to_float(data['night_return'])
        day = to_float(data['day_return'])
        if self.component == 'night':
            values = night
        elif self.component == 'day':
            values = day
        else:
            values = night - day

        values = values.astype('float64')
        values.name = self.name
        if values.notna().sum() == 0:
            self._warn(
                f'component={self.component}: 所有样本均为 NaN，'
                f'请检查品种是否有夜盘或时段收益数据是否完整。'
            )
        self._log_warnings(logger, self.name)
        return values
