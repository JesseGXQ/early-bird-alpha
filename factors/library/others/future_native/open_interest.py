"""持仓与资金流机制因子。

本模块包含：
    - ``SignedOpenInterestChange``：有符号增仓；
    - ``OpenInterestExhaustion``：减仓衰竭。

两个因子共享同一套“收益率 + 持仓量变化 + 主力稳定性”计算框架，区别只在
如何把四个象限（涨价/跌价 × 增仓/减仓）映射为因子符号。
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Mapping

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


class _OpenInterestFactor(Factor, WarningMixin):
    """有符号持仓变化因子的私有公共实现。

    子类通过 ``_mode`` 选择输出象限：
        - ``'signed'``：涨价增仓为正、跌价增仓为负；
        - ``'exhaustion'``：跌价减仓为正、涨价减仓为负。
    """

    _mode: Literal['signed', 'exhaustion']

    def __init__(self, lookback: int):
        self.lookback = require_positive_int(lookback, 'lookback')
        self._reset_warnings()

    @property
    def computation_scope(self) -> Literal['per_product']:
        return 'per_product'

    @property
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        return (
            DataRequirement(
                source='main_panel',
                columns=(
                    'True_Return',
                    'OpenInterest',
                    'Dominant_Contract',
                ),
            ),
        )

    def compute(self, data: pd.DataFrame) -> pd.Series:
        """批量计算有符号持仓变化。
        参数 `data` 所需列： True_Return, OpenInterest, Dominant_Contract。
        """
        self._reset_warnings()
        if data is None or data.empty:
            return empty_factor_series(self.name)

        validate_panel_index(data, ('timestamp', 'product'), 'main_panel')
        required = ['True_Return', 'OpenInterest', 'Dominant_Contract']
        missing = [c for c in required if c not in data.columns]
        if missing:
            raise ValueError(f'main_panel 缺少必需列: {missing}。')

        frame = data.copy()
        frame['True_Return'] = to_float(frame['True_Return'])
        frame['OpenInterest'] = to_float(frame['OpenInterest'])
        frame['Dominant_Contract'] = (
            frame['Dominant_Contract'].astype('string')
        )

        pieces: list[pd.Series] = []
        for product, group in frame.groupby(level='product', sort=False):
            series = group.droplevel('product').sort_index()
            values = self._compute_product(series, str(product))
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
            series: pd.DataFrame,
            product: str,
    ) -> pd.Series:
        """计算单个品种的因子序列。"""
        returns = series['True_Return']
        open_interest = series['OpenInterest']
        dominant = series['Dominant_Contract']

        momentum = cumulative_return(returns, self.lookback)
        oi_base = open_interest.shift(self.lookback)
        oi_change = open_interest - oi_base
        relative_change = oi_change / oi_base.where(oi_base > 0)
        stable = stable_dominant_mask(dominant, self.lookback)

        if self._mode == 'signed':
            raw = np.sign(momentum) * relative_change.clip(lower=0.0)
        else:
            raw = -np.sign(momentum) * (-relative_change).clip(lower=0.0)

        values = raw.where(
            stable & momentum.notna() & relative_change.notna()
        )
        values = values.astype('float64')
        if values.notna().sum() == 0:
            self._warn(
                f'product={product}: 无有效样本（历史不足、换月窗口或持仓量缺失），'
                f'因子全部为 NaN。'
            )

        values.index = pd.MultiIndex.from_arrays(
            [values.index, [product] * len(values)],
            names=INDEX_NAMES,
        )
        return values


class SignedOpenInterestChange(_OpenInterestFactor):
    """有符号增仓因子。

    经济机制
    --------
    期货持仓量同时包含多头和空头，单看持仓变化无法判断方向。把持仓变化与
    同期价格方向结合，可以近似识别“新资金进入的方向”：

        - 价格上涨且持仓增加：新多资金主动进入，趋势偏延续；
        - 价格下跌且持仓增加：新空资金主动进入，下跌偏延续；
        - 价格上涨且持仓减少：更可能是空头回补，信号质量较弱（本因子不取）；
        - 价格下跌且持仓减少：更可能是多头被动退出（由减仓衰竭因子刻画）。

    计算公式
    --------
    设 ``r_k`` 为最近 k 期复合收益率，``OI`` 为主力合约持仓量：

        F_t = sign(r_k) * max( (OI_t - OI_{t-k}) / OI_{t-k}, 0 )

    预测方向
    --------
    涨价增仓为正、跌价增仓为负，偏趋势延续。

    Parameters
    ----------
    lookback : int, default 5
        收益率和持仓变化的回看期数（交易日）。

    Notes
    -----
    - 主力合约在窗口内发生变化时返回 NaN，避免持仓量因换月机械跳变。
    - 窗口内任一收益率缺失（数据断档）时 ``r_k`` 为 NaN，因子随之为 NaN。
    - ``ΔOI/OI`` 使用简单百分比变化；OI 基数必须为正。
    """

    _mode: Literal['signed'] = 'signed'

    def __init__(self, lookback: int = 5):
        super().__init__(lookback=lookback)

    @property
    def name(self) -> str:
        return f'signed_oi_change_{self.lookback}'

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {'lookback': self.lookback}


class OpenInterestExhaustion(_OpenInterestFactor):
    """减仓衰竭因子。

    经济机制
    --------
    持仓量下降意味着已有头寸在离场，而不是新资金进入。结合价格方向：

        - 价格下跌且持仓减少：多头止损/被动退出，抛压可能衰竭，偏反转向上；
        - 价格上涨且持仓减少：空头回补，上涨缺乏新多承接，偏反转向下；
        - 价格下跌且持仓增加、价格上涨且持仓增加：由有符号增仓因子刻画。

    计算公式
    --------
    设 ``r_k`` 为最近 k 期复合收益率，``OI`` 为主力合约持仓量：

        F_t = -sign(r_k) * max( -(OI_t - OI_{t-k}) / OI_{t-k}, 0 )

    预测方向
    --------
    跌价减仓为正、涨价减仓为负，偏短期反转。

    Parameters
    ----------
    lookback : int, default 5
        收益率和持仓变化的回看期数（交易日）。

    Notes
    -----
    与 ``SignedOpenInterestChange`` 相同，窗口内主力切换或数据断档时返回 NaN。
    """

    _mode: Literal['exhaustion'] = 'exhaustion'

    def __init__(self, lookback: int = 5):
        super().__init__(lookback=lookback)

    @property
    def name(self) -> str:
        return f'oi_exhaustion_{self.lookback}'

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {'lookback': self.lookback}
