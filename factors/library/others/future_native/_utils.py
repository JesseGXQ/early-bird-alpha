"""期货原生机制因子的内部公共工具。

本模块只服务于 ``factors.library.others.future_native`` 下的因子实现：
    - 参数校验；
    - 数值列归一化；
    - 期限结构数据的统一预处理；
    - 主力合约稳定性掩码；
    - 有上限的警告收集。

这里不包含任何因子经济逻辑，也不负责取数。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd

from factors.models import INDEX_NAMES

# 警告明细最多保留的条数；超出的只计数，避免逐日逐品种刷屏。
_MAX_WARNINGS = 100

# 期限结构拟合的默认合约过滤口径。
_TERM_MIN_DTE = 3
_TERM_MAX_DTE = 300
_TERM_MIN_VOLUME = 100.0


class WarningMixin:
    """为因子提供有上限的警告收集能力。

    Attributes
    ----------
    warnings : list[str]
        保留的警告明细，最多 ``_MAX_WARNINGS`` 条。
    _warning_count : int
        本次计算累计触发的警告总数。
    """

    warnings: list[str]
    _warning_count: int

    def _reset_warnings(self) -> None:
        """每次批量计算前清空上一轮警告。"""
        self.warnings = []
        self._warning_count = 0

    def _warn(self, message: str) -> None:
        """记录一条警告；明细超过上限后只累计数量。"""
        self._warning_count += 1
        if len(self.warnings) < _MAX_WARNINGS:
            self.warnings.append(message)

    def _log_warnings(self, logger: logging.Logger, factor_name: str) -> None:
        """汇总输出警告，避免每条明细都打日志。"""
        if self._warning_count == 0:
            return
        logger.warning(
            '【%s】本次计算共记录 %d 条警告，明细见该因子的 warnings 属性。',
            factor_name,
            self._warning_count,
        )


def require_positive_int(value: Any, parameter: str, minimum: int = 1) -> int:
    """校验正整数参数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(
            f'{parameter} 必须是不小于 {minimum} 的整数，收到 {value!r}。'
        )
    return value


def require_non_negative_int(value: Any, parameter: str) -> int:
    """校验非负整数参数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f'{parameter} 必须是非负整数，收到 {value!r}。'
        )
    return value


def require_price_col(value: Any) -> str:
    """校验期限结构价格列口径。"""
    if value not in ('Close', 'Settle'):
        raise ValueError(
            f"price_col 仅支持 'Close' 或 'Settle'，收到 {value!r}。"
        )
    return str(value)


def to_float(series: pd.Series) -> pd.Series:
    """把可能带 nullable/object dtype 的列安全转换为 float64。"""
    return pd.to_numeric(series, errors='coerce').astype('float64')


def empty_factor_series(name: str) -> pd.Series:
    """返回符合因子输出契约的空 Series。"""
    index = pd.MultiIndex.from_tuples((), names=INDEX_NAMES)
    return pd.Series(
        np.array([], dtype='float64'), index=index, name=name, dtype='float64'
    )


def validate_panel_index(
        data: pd.DataFrame,
        expected_names: Iterable[str],
        source: str,
) -> None:
    """校验输入面板的索引层级名称。"""
    expected = list(expected_names)
    actual = list(data.index.names)
    if actual != expected:
        raise ValueError(
            f'{source} 的索引层级应为 {expected}，实际为 {actual}。'
        )


def require_columns(
        data: pd.DataFrame,
        columns: Iterable[str],
        source: str,
) -> None:
    """校验输入数据包含所需列。"""
    required = list(columns)
    missing = [column for column in required if column not in data.columns]
    if missing:
        raise ValueError(f'{source} 缺少必需列: {missing}。')


def prepare_term_structure_frame(
        data: pd.DataFrame,
        price_col: str,
        *,
        min_dte: int = _TERM_MIN_DTE,
        max_dte: int = _TERM_MAX_DTE,
        min_volume: float = _TERM_MIN_VOLUME,
        require_dominant: bool = False,
) -> pd.DataFrame:
    """为期限结构因子统一整理合约截面。

    Parameters
    ----------
    data : pd.DataFrame
        MultiIndex[timestamp, product, contract] 的期限结构长表。
    price_col : {'Close', 'Settle'}
        价格列口径。
    min_dte, max_dte : int
        参与计算的剩余到期日区间（日历日）。
    min_volume : float
        最低成交量；为 0 表示不设流动性门槛。
    require_dominant : bool
        是否要求输入包含 ``is_dominant`` 列。

    Returns
    -------
    pd.DataFrame
        输入数据的副本，额外包含：
        - ``_dte``：剩余到期日；
        - ``_valid``：是否可参与当前因子的合约筛选；
        - ``is_dominant``（可选）：规范为 bool，缺失按 False 处理。

    Notes
    -----
    本函数只做列类型归一化和合约有效性标记，不在原对象上原地修改。
    """
    required = [price_col, 'last_trade_date', 'OpenInterest', 'Volume']
    if require_dominant:
        required.append('is_dominant')
    validate_panel_index(
        data, ('timestamp', 'product', 'contract'), 'term_structure'
    )
    require_columns(data, required, 'term_structure')

    df = data.copy()
    df[price_col] = to_float(df[price_col])
    df['OpenInterest'] = to_float(df['OpenInterest'])
    df['Volume'] = to_float(df['Volume'])

    timestamps = pd.to_datetime(
        df.index.get_level_values('timestamp'), errors='coerce'
    )
    last_trade_dates = pd.to_datetime(df['last_trade_date'], errors='coerce')
    dte = (last_trade_dates - timestamps).dt.days
    df['_dte'] = dte.astype('float64')

    df['_valid'] = (
        df[price_col].notna()
        & (df[price_col] > 0)
        & df['OpenInterest'].notna()
        & (df['OpenInterest'] > 0)
        & df['Volume'].notna()
        & (df['Volume'] >= float(min_volume))
        & df['_dte'].notna()
        & (df['_dte'] >= float(min_dte))
        & (df['_dte'] <= float(max_dte))
    )

    if require_dominant:
        df['is_dominant'] = (
            df['is_dominant'].fillna(False).astype(bool)
        )
    return df


def stable_dominant_mask(
        dominant: pd.Series,
        lookback: int,
) -> pd.Series:
    """返回“最近 lookback 个步长内主力合约始终未变”的掩码。

    Parameters
    ----------
    dominant : pd.Series
        按时间排序的主力合约序列。
    lookback : int
        回看步数；``lookback=5`` 表示要求 t-5 到 t 共 6 个时点主力一致。

    Returns
    -------
    pd.Series
        与输入同索引的布尔序列。首个时点及换月后的 ``lookback`` 个时点为 False。

    Notes
    -----
    主力合约切换会造成持仓量、保证金率等指标的机械跳变，因此相关因子在
    窗口跨越换月时应返回缺失值，而不是把机械跳变解释为资金流或政策冲击。
    """
    same_step = (
        dominant.eq(dominant.shift(1)).fillna(False).astype(bool)
    )
    return same_step.rolling(
        window=lookback, min_periods=lookback
    ).min().eq(1.0)


def cumulative_return(returns: pd.Series, lookback: int) -> pd.Series:
    """计算最近 lookback 期的复合收益率。

    输入收益率应为小数形式（例如 0.01 表示 1%）。窗口内任一缺失值都会
    使结果为 NaN，从而避免跨数据断档拼接收益。
    """
    values = to_float(returns)
    return (
        (1.0 + values)
        .rolling(window=lookback, min_periods=lookback)
        .apply(np.prod, raw=True)
        - 1.0
    )
