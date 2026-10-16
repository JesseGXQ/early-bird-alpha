# type: ignore
"""《101 Formulaic Alphas》附录 A 的严格 pandas 实现。

输入是 ``dict[str, pandas.DataFrame]``；每个 DataFrame 均为“日期 × 标的”的宽表。
必需字段为 open、high、low、close、volume、vwap。returns 可省略（届时由 close
计算），dollar_volume/amount 可省略（届时按 vwap * volume 计算）。cap、sector、
industry、subindustry 只在相应公式用到时才要求提供。

论文规定所有非整数时间窗口向下取整。本模块不把无穷值或缺失值填成 0；数学上
未定义的结果统一保留为 NaN。
"""

from __future__ import annotations

from math import floor
from typing import Mapping

import numpy as np
import pandas as pd


Frame = pd.DataFrame


def _window(value: float | int) -> int:
    """按论文规则把窗口向下取整。"""
    result = floor(float(value))
    if result < 1:
        raise ValueError(f"窗口必须至少为 1，收到 {value!r}")
    return result


def _finite(value: Frame) -> Frame:
    return value.replace([np.inf, -np.inf], np.nan)


def returns(close: Frame) -> Frame:
    return close.pct_change(fill_method=None)


def ts_sum(value: Frame, window: float | int) -> Frame:
    return value.rolling(_window(window), min_periods=_window(window)).sum()


def sma(value: Frame, window: float | int) -> Frame:
    return value.rolling(_window(window), min_periods=_window(window)).mean()


def stddev(value: Frame, window: float | int) -> Frame:
    return value.rolling(_window(window), min_periods=_window(window)).std()


def correlation(x: Frame, y: Frame, window: float | int) -> Frame:
    w = _window(window)
    return _finite(x.rolling(w, min_periods=w).corr(y))


def covariance(x: Frame, y: Frame, window: float | int) -> Frame:
    w = _window(window)
    return _finite(x.rolling(w, min_periods=w).cov(y))


def rank(value: Frame) -> Frame:
    """逐日横截面百分位排名；并列值取平均名次。"""
    return value.rank(axis=1, method="average", pct=True)


def scale(value: Frame, a: float = 1.0) -> Frame:
    """逐日横截面缩放，使每行 ``sum(abs(x)) == a``。"""
    denominator = value.abs().sum(axis=1, min_count=1).replace(0.0, np.nan)
    return value.mul(a).div(denominator, axis=0)


def delay(value: Frame, period: float | int = 1) -> Frame:
    return value.shift(_window(period))


def delta(value: Frame, period: float | int = 1) -> Frame:
    return value - delay(value, period)


def ts_rank(value: Frame, window: float | int) -> Frame:
    """当前值在滚动窗口内的百分位排名；并列值取平均名次。"""
    w = _window(window)
    return value.rolling(w, min_periods=w).apply(
        lambda x: pd.Series(x).rank(method="average", pct=True).iloc[-1], raw=False
    )


def product(value: Frame, window: float | int) -> Frame:
    w = _window(window)
    return value.rolling(w, min_periods=w).apply(np.prod, raw=True)


def ts_min(value: Frame, window: float | int) -> Frame:
    w = _window(window)
    return value.rolling(w, min_periods=w).min()


def ts_max(value: Frame, window: float | int) -> Frame:
    w = _window(window)
    return value.rolling(w, min_periods=w).max()


def ts_argmax(value: Frame, window: float | int) -> Frame:
    w = _window(window)
    return value.rolling(w, min_periods=w).apply(lambda x: np.argmax(x) + 1, raw=True)


def ts_argmin(value: Frame, window: float | int) -> Frame:
    w = _window(window)
    return value.rolling(w, min_periods=w).apply(lambda x: np.argmin(x) + 1, raw=True)


def decay_linear(value: Frame, window: float | int) -> Frame:
    w = _window(window)
    weights = np.arange(1.0, w + 1.0)
    weights /= weights.sum()
    return value.rolling(w, min_periods=w).apply(lambda x: np.dot(x, weights), raw=True)


def signed_power(value: Frame, exponent: float | Frame) -> Frame:
    return np.sign(value) * np.power(np.abs(value), exponent)


def safe_log(value: Frame) -> Frame:
    return _finite(np.log(value.where(value > 0)))


def safe_divide(numerator: Frame, denominator: Frame | float) -> Frame:
    if isinstance(denominator, pd.DataFrame):
        denominator = denominator.where(denominator != 0)
    elif denominator == 0:
        raise ZeroDivisionError("分母不能为 0")
    return _finite(numerator / denominator)


def _comparison(left: Frame, right: Frame | float, op: str) -> Frame:
    valid = left.notna()
    if isinstance(right, pd.DataFrame):
        valid &= right.notna()
    result = left.lt(right) if op == "lt" else left.le(right)
    return result.astype(float).where(valid)


def elementwise_min(x: Frame, y: Frame) -> Frame:
    return x.where(x.le(y), y).where(x.notna() & y.notna())


def elementwise_max(x: Frame, y: Frame) -> Frame:
    return x.where(x.ge(y), y).where(x.notna() & y.notna())


def indneutralize(value: Frame, groups: pd.Series | Frame) -> Frame:
    """逐日、逐组去均值。groups 可为静态 Series 或日期 × 标的宽表。"""
    if isinstance(groups, pd.Series):
        labels = groups.reindex(value.columns)
        return value.T.groupby(labels).transform(lambda x: x - x.mean()).T
    groups = groups.reindex(index=value.index, columns=value.columns)
    result = Frame(np.nan, index=value.index, columns=value.columns, dtype=float)
    for date in value.index:
        labels = groups.loc[date]
        result.loc[date] = value.loc[date].groupby(labels).transform(
            lambda x: x - x.mean()
        )
    return result


class Alphas101Strict:
    """论文 101 个公式的无填充值实现。"""

    _REQUIRED = ("open", "high", "low", "close", "volume", "vwap")

    def __init__(self, data: Mapping[str, Frame | pd.Series]):
        missing = [name for name in self._REQUIRED if name not in data]
        if missing:
            raise KeyError(f"缺少必需字段：{', '.join(missing)}")
        base = data["close"]
        if not isinstance(base, pd.DataFrame):
            raise TypeError("行情字段必须是日期 × 标的的 pandas.DataFrame")
        self._data = data
        for name in self._REQUIRED:
            value = data[name]
            if not isinstance(value, pd.DataFrame):
                raise TypeError(f"{name} 必须是 pandas.DataFrame")
            if not value.index.equals(base.index) or not value.columns.equals(base.columns):
                raise ValueError(f"{name} 的索引或列与 close 不一致")
            setattr(self, name, value.astype(float))
        supplied_returns = data.get("returns")
        if supplied_returns is not None and not isinstance(supplied_returns, pd.DataFrame):
            raise TypeError("returns 必须是 pandas.DataFrame")
        if isinstance(supplied_returns, pd.DataFrame):
            if not supplied_returns.index.equals(base.index) or not supplied_returns.columns.equals(base.columns):
                raise ValueError("returns 的索引或列与 close 不一致")
            self.returns = supplied_returns.astype(float)
        else:
            self.returns = returns(self.close)
        dollar_volume = data.get("dollar_volume", data.get("amount"))
        if dollar_volume is not None and not isinstance(dollar_volume, pd.DataFrame):
            raise TypeError("dollar_volume/amount 必须是 pandas.DataFrame")
        if isinstance(dollar_volume, pd.DataFrame):
            if not dollar_volume.index.equals(base.index) or not dollar_volume.columns.equals(base.columns):
                raise ValueError("dollar_volume/amount 的索引或列与 close 不一致")
            self.dollar_volume = dollar_volume.astype(float)
        else:
            self.dollar_volume = self.vwap * self.volume

    def _adv(self, window: float | int) -> Frame:
        # 附录 A.2 明确定义 adv 为 average daily dollar volume。
        return sma(self.dollar_volume, window)

    def _field(self, name: str) -> Frame:
        value = self._data.get(name)
        if not isinstance(value, pd.DataFrame):
            raise KeyError(f"该因子需要日期 × 标的字段 {name!r}")
        return value.astype(float).reindex(index=self.close.index, columns=self.close.columns)

    def _group(self, name: str) -> pd.Series | Frame:
        value = self._data.get(name)
        if not isinstance(value, (pd.Series, pd.DataFrame)):
            raise KeyError(f"该因子需要行业分类字段 {name!r}")
        return value

    def compute(self, number: int) -> Frame:
        if not 1 <= number <= 101:
            raise ValueError("因子编号必须在 1..101")
        return _finite(getattr(self, f"alpha{number:03d}")())

    def alpha001(self):
        inner = self.close.where(self.returns >= 0, stddev(self.returns, 20))
        return rank(ts_argmax(signed_power(inner, 2.0), 5)) - 0.5

    def alpha002(self):
        return -correlation(rank(delta(safe_log(self.volume), 2)), rank(safe_divide(self.close - self.open, self.open)), 6)

    def alpha003(self): return -correlation(rank(self.open), rank(self.volume), 10)
    def alpha004(self): return -ts_rank(rank(self.low), 9)
    def alpha005(self): return rank(self.open - ts_sum(self.vwap, 10) / 10) * -rank(self.close - self.vwap).abs()
    def alpha006(self): return -correlation(self.open, self.volume, 10)

    def alpha007(self):
        change = delta(self.close, 7)
        signal = -ts_rank(change.abs(), 60) * np.sign(change)
        condition = self._adv(20) < self.volume
        return signal.where(condition, -1.0).where(self._adv(20).notna() & self.volume.notna())

    def alpha008(self):
        inner = ts_sum(self.open, 5) * ts_sum(self.returns, 5)
        return -rank(inner - delay(inner, 10))

    def alpha009(self):
        change = delta(self.close, 1)
        condition = (ts_min(change, 5) > 0) | (ts_max(change, 5) < 0)
        return change.where(condition, -change).where(ts_min(change, 5).notna() & ts_max(change, 5).notna())

    def alpha010(self):
        change = delta(self.close, 1)
        condition = (ts_min(change, 4) > 0) | (ts_max(change, 4) < 0)
        return rank(change.where(condition, -change).where(ts_min(change, 4).notna() & ts_max(change, 4).notna()))

    def alpha011(self): return (rank(ts_max(self.vwap - self.close, 3)) + rank(ts_min(self.vwap - self.close, 3))) * rank(delta(self.volume, 3))
    def alpha012(self): return np.sign(delta(self.volume, 1)) * -delta(self.close, 1)
    def alpha013(self): return -rank(covariance(rank(self.close), rank(self.volume), 5))
    def alpha014(self): return -rank(delta(self.returns, 3)) * correlation(self.open, self.volume, 10)
    def alpha015(self): return -ts_sum(rank(correlation(rank(self.high), rank(self.volume), 3)), 3)
    def alpha016(self): return -rank(covariance(rank(self.high), rank(self.volume), 5))
    def alpha017(self): return -rank(ts_rank(self.close, 10)) * rank(delta(delta(self.close, 1), 1)) * rank(ts_rank(safe_divide(self.volume, self._adv(20)), 5))
    def alpha018(self): return -rank(stddev((self.close - self.open).abs(), 5) + self.close - self.open + correlation(self.close, self.open, 10))
    def alpha019(self): return -np.sign(self.close - delay(self.close, 7) + delta(self.close, 7)) * (1 + rank(1 + ts_sum(self.returns, 250)))
    def alpha020(self): return -rank(self.open - delay(self.high, 1)) * rank(self.open - delay(self.close, 1)) * rank(self.open - delay(self.low, 1))

    def alpha021(self):
        mean8, mean2 = ts_sum(self.close, 8) / 8, ts_sum(self.close, 2) / 2
        result = Frame(-1.0, index=self.close.index, columns=self.close.columns)
        result = result.where(~(mean2 < mean8 - stddev(self.close, 8)), 1.0)
        result = result.where(~(mean8 + stddev(self.close, 8) < mean2), -1.0)
        valid = mean8.notna() & mean2.notna() & stddev(self.close, 8).notna() & self._adv(20).notna() & self.volume.notna()
        middle = ~(mean8 + stddev(self.close, 8) < mean2) & ~(mean2 < mean8 - stddev(self.close, 8))
        result = result.where(~middle, (safe_divide(self.volume, self._adv(20)) >= 1).astype(float).replace(0, -1))
        return result.where(valid)

    def alpha022(self): return -delta(correlation(self.high, self.volume, 5), 5) * rank(stddev(self.close, 20))
    def alpha023(self): return (-delta(self.high, 2)).where(ts_sum(self.high, 20) / 20 < self.high, 0.0).where((ts_sum(self.high, 20) / 20).notna() & self.high.notna())

    def alpha024(self):
        ratio = safe_divide(delta(ts_sum(self.close, 100) / 100, 100), delay(self.close, 100))
        return (-(self.close - ts_min(self.close, 100))).where(ratio <= 0.05, -delta(self.close, 3)).where(ratio.notna())

    def alpha025(self): return rank(-self.returns * self._adv(20) * self.vwap * (self.high - self.close))
    def alpha026(self): return -ts_max(correlation(ts_rank(self.volume, 5), ts_rank(self.high, 5), 5), 3)
    def alpha027(self):
        value = rank(ts_sum(correlation(rank(self.volume), rank(self.vwap), 6), 2) / 2.0)
        return Frame(np.where(value > 0.5, -1.0, 1.0), index=value.index, columns=value.columns).where(value.notna())
    def alpha028(self): return scale(correlation(self._adv(20), self.low, 5) + (self.high + self.low) / 2 - self.close)

    def alpha029(self):
        value = rank(rank(-rank(delta(self.close - 1, 5))))
        value = rank(rank(scale(safe_log(ts_sum(ts_min(value, 2), 1)))))
        return ts_min(product(value, 1), 5) + ts_rank(delay(-self.returns, 6), 5)

    def alpha030(self):
        signs = np.sign(self.close - delay(self.close, 1)) + np.sign(delay(self.close, 1) - delay(self.close, 2)) + np.sign(delay(self.close, 2) - delay(self.close, 3))
        return safe_divide((1 - rank(signs)) * ts_sum(self.volume, 5), ts_sum(self.volume, 20))

    def alpha031(self): return rank(rank(rank(decay_linear(-rank(rank(delta(self.close, 10))), 10)))) + rank(-delta(self.close, 3)) + np.sign(scale(correlation(self._adv(20), self.low, 12)))
    def alpha032(self): return scale(ts_sum(self.close, 7) / 7 - self.close) + 20 * scale(correlation(self.vwap, delay(self.close, 5), 230))
    def alpha033(self): return rank(-(1 - safe_divide(self.open, self.close)))
    def alpha034(self): return rank(2 - rank(safe_divide(stddev(self.returns, 2), stddev(self.returns, 5))) - rank(delta(self.close, 1)))
    def alpha035(self): return ts_rank(self.volume, 32) * (1 - ts_rank(self.close + self.high - self.low, 16)) * (1 - ts_rank(self.returns, 32))
    def alpha036(self): return 2.21 * rank(correlation(self.close - self.open, delay(self.volume, 1), 15)) + 0.7 * rank(self.open - self.close) + 0.73 * rank(ts_rank(delay(-self.returns, 6), 5)) + rank(correlation(self.vwap, self._adv(20), 6).abs()) + 0.6 * rank((ts_sum(self.close, 200) / 200 - self.open) * (self.close - self.open))
    def alpha037(self): return rank(correlation(delay(self.open - self.close, 1), self.close, 200)) + rank(self.open - self.close)
    def alpha038(self): return -rank(ts_rank(self.close, 10)) * rank(safe_divide(self.close, self.open))
    def alpha039(self): return -rank(delta(self.close, 7) * (1 - rank(decay_linear(safe_divide(self.volume, self._adv(20)), 9)))) * (1 + rank(ts_sum(self.returns, 250)))
    def alpha040(self): return -rank(stddev(self.high, 10)) * correlation(self.high, self.volume, 10)
    def alpha041(self): return np.sqrt(self.high * self.low) - self.vwap
    def alpha042(self): return safe_divide(rank(self.vwap - self.close), rank(self.vwap + self.close))
    def alpha043(self): return ts_rank(safe_divide(self.volume, self._adv(20)), 20) * ts_rank(-delta(self.close, 7), 8)
    def alpha044(self): return -correlation(self.high, rank(self.volume), 5)
    def alpha045(self): return -rank(ts_sum(delay(self.close, 5), 20) / 20) * correlation(self.close, self.volume, 2) * rank(correlation(ts_sum(self.close, 5), ts_sum(self.close, 20), 2))

    def alpha046(self):
        slope = (delay(self.close, 20) - delay(self.close, 10)) / 10 - (delay(self.close, 10) - self.close) / 10
        result = -delta(self.close, 1)
        result = result.where(~(slope < 0), 1.0).where(~(slope > 0.25), -1.0)
        return result.where(slope.notna())

    def alpha047(self): return safe_divide(rank(safe_divide(self.close * 0 + 1, self.close)) * self.volume, self._adv(20)) * safe_divide(self.high * rank(self.high - self.close), ts_sum(self.high, 5) / 5) - rank(self.vwap - delay(self.vwap, 5))
    def alpha048(self):
        numerator = safe_divide(correlation(delta(self.close, 1), delta(delay(self.close, 1), 1), 250) * delta(self.close, 1), self.close)
        numerator = indneutralize(numerator, self._group("subindustry"))
        denominator = ts_sum(safe_divide(delta(self.close, 1), delay(self.close, 1)) ** 2, 250)
        return safe_divide(numerator, denominator)

    def _alpha049_051(self, threshold):
        slope = (delay(self.close, 20) - delay(self.close, 10)) / 10 - (delay(self.close, 10) - self.close) / 10
        return (-delta(self.close, 1)).where(~(slope < threshold), 1.0).where(slope.notna())

    def alpha049(self): return self._alpha049_051(-0.1)
    def alpha050(self): return -ts_max(rank(correlation(rank(self.volume), rank(self.vwap), 5)), 5)
    def alpha051(self): return self._alpha049_051(-0.05)
    def alpha052(self): return (-ts_min(self.low, 5) + delay(ts_min(self.low, 5), 5)) * rank((ts_sum(self.returns, 240) - ts_sum(self.returns, 20)) / 220) * ts_rank(self.volume, 5)
    def alpha053(self): return -delta(safe_divide((self.close - self.low) - (self.high - self.close), self.close - self.low), 9)
    def alpha054(self): return safe_divide(-(self.low - self.close) * self.open ** 5, (self.low - self.high) * self.close ** 5)
    def alpha055(self): return -correlation(rank(safe_divide(self.close - ts_min(self.low, 12), ts_max(self.high, 12) - ts_min(self.low, 12))), rank(self.volume), 6)
    def alpha056(self): return -rank(safe_divide(ts_sum(self.returns, 10), ts_sum(ts_sum(self.returns, 2), 3))) * rank(self.returns * self._field("cap"))
    def alpha057(self): return -safe_divide(self.close - self.vwap, decay_linear(rank(ts_argmax(self.close, 30)), 2))
    def alpha058(self): return -ts_rank(decay_linear(correlation(indneutralize(self.vwap, self._group("sector")), self.volume, 3.92795), 7.89291), 5.50322)
    def alpha059(self): return -ts_rank(decay_linear(correlation(indneutralize(self.vwap * 0.728317 + self.vwap * (1 - 0.728317), self._group("industry")), self.volume, 4.25197), 16.2289), 8.19648)
    def alpha060(self): return -(2 * scale(rank(safe_divide(((self.close - self.low) - (self.high - self.close)) * self.volume, self.high - self.low))) - scale(rank(ts_argmax(self.close, 10))))
    def alpha061(self): return _comparison(rank(self.vwap - ts_min(self.vwap, 16.1219)), rank(correlation(self.vwap, self._adv(180), 17.9282)), "lt")

    def alpha062(self):
        left = rank(correlation(self.vwap, ts_sum(self._adv(20), 22.4101), 9.91009))
        inner = _comparison(rank(self.open) + rank(self.open), rank((self.high + self.low) / 2) + rank(self.high), "lt")
        return -_comparison(left, rank(inner), "lt")

    def alpha063(self):
        left = rank(decay_linear(delta(indneutralize(self.close, self._group("industry")), 2.25164), 8.22237))
        mixed = self.vwap * 0.318108 + self.open * (1 - 0.318108)
        right = rank(decay_linear(correlation(mixed, ts_sum(self._adv(180), 37.2467), 13.557), 12.2883))
        return -(left - right)

    def alpha064(self):
        left = rank(correlation(ts_sum(self.open * 0.178404 + self.low * (1 - 0.178404), 12.7054), ts_sum(self._adv(120), 12.7054), 16.6208))
        right = rank(delta((self.high + self.low) / 2 * 0.178404 + self.vwap * (1 - 0.178404), 3.69741))
        return -_comparison(left, right, "lt")

    def alpha065(self):
        left = rank(correlation(self.open * 0.00817205 + self.vwap * (1 - 0.00817205), ts_sum(self._adv(60), 8.6911), 6.40374))
        return -_comparison(left, rank(self.open - ts_min(self.open, 13.635)), "lt")

    def alpha066(self):
        first = rank(decay_linear(delta(self.vwap, 3.51013), 7.23052))
        second = ts_rank(decay_linear(safe_divide(self.low * 0.96633 + self.low * (1 - 0.96633) - self.vwap, self.open - (self.high + self.low) / 2), 11.4157), 6.72611)
        return -(first + second)

    def alpha067(self):
        base = rank(self.high - ts_min(self.high, 2.14593))
        exponent = rank(correlation(indneutralize(self.vwap, self._group("sector")), indneutralize(self._adv(20), self._group("subindustry")), 6.02936))
        return -signed_power(base, exponent)

    def alpha068(self):
        left = ts_rank(correlation(rank(self.high), rank(self._adv(15)), 8.91644), 13.9333)
        right = rank(delta(self.close * 0.518371 + self.low * (1 - 0.518371), 1.06157))
        return -_comparison(left, right, "lt")

    def alpha069(self):
        base = rank(ts_max(delta(indneutralize(self.vwap, self._group("industry")), 2.72412), 4.79344))
        exponent = ts_rank(correlation(self.close * 0.490655 + self.vwap * (1 - 0.490655), self._adv(20), 4.92416), 9.0615)
        return -signed_power(base, exponent)

    def alpha070(self):
        base = rank(delta(self.vwap, 1.29456))
        exponent = ts_rank(correlation(indneutralize(self.close, self._group("industry")), self._adv(50), 17.8256), 17.9171)
        return -signed_power(base, exponent)

    def alpha071(self):
        first = ts_rank(decay_linear(correlation(ts_rank(self.close, 3.43976), ts_rank(self._adv(180), 12.0647), 18.0175), 4.20501), 15.6948)
        second = ts_rank(decay_linear(rank((self.low + self.open) - (self.vwap + self.vwap)) ** 2, 16.4662), 4.4388)
        return elementwise_max(first, second)

    def alpha072(self):
        first = rank(decay_linear(correlation((self.high + self.low) / 2, self._adv(40), 8.93345), 10.1519))
        second = rank(decay_linear(correlation(ts_rank(self.vwap, 3.72469), ts_rank(self.volume, 18.5188), 6.86671), 2.95011))
        return safe_divide(first, second)

    def alpha073(self):
        first = rank(decay_linear(delta(self.vwap, 4.72775), 2.91864))
        mixed = self.open * 0.147155 + self.low * (1 - 0.147155)
        second = ts_rank(decay_linear(-safe_divide(delta(mixed, 2.03608), mixed), 3.33829), 16.7411)
        return -elementwise_max(first, second)

    def alpha074(self):
        left = rank(correlation(self.close, ts_sum(self._adv(30), 37.4843), 15.1365))
        right = rank(correlation(rank(self.high * 0.0261661 + self.vwap * (1 - 0.0261661)), rank(self.volume), 11.4791))
        return -_comparison(left, right, "lt")

    def alpha075(self): return _comparison(rank(correlation(self.vwap, self.volume, 4.24304)), rank(correlation(rank(self.low), rank(self._adv(50)), 12.4413)), "lt")

    def alpha076(self):
        first = rank(decay_linear(delta(self.vwap, 1.24383), 11.8259))
        second = ts_rank(decay_linear(ts_rank(correlation(indneutralize(self.low, self._group("sector")), self._adv(81), 8.14941), 19.569), 17.1543), 19.383)
        return -elementwise_max(first, second)

    def alpha077(self):
        first = rank(decay_linear((self.high + self.low) / 2 - self.vwap, 20.0451))
        second = rank(decay_linear(correlation((self.high + self.low) / 2, self._adv(40), 3.1614), 5.64125))
        return elementwise_min(first, second)

    def alpha078(self):
        base = rank(correlation(ts_sum(self.low * 0.352233 + self.vwap * (1 - 0.352233), 19.7428), ts_sum(self._adv(40), 19.7428), 6.83313))
        exponent = rank(correlation(rank(self.vwap), rank(self.volume), 5.77492))
        return signed_power(base, exponent)

    def alpha079(self):
        left = rank(delta(indneutralize(self.close * 0.60733 + self.open * (1 - 0.60733), self._group("sector")), 1.23438))
        right = rank(correlation(ts_rank(self.vwap, 3.60973), ts_rank(self._adv(150), 9.18637), 14.6644))
        return _comparison(left, right, "lt")

    def alpha080(self):
        base = rank(np.sign(delta(indneutralize(self.open * 0.868128 + self.high * (1 - 0.868128), self._group("industry")), 4.04545)))
        exponent = ts_rank(correlation(self.high, self._adv(10), 5.11456), 5.53756)
        return -signed_power(base, exponent)

    def alpha081(self):
        inner = rank(correlation(self.vwap, ts_sum(self._adv(10), 49.6054), 8.47743))
        left = rank(safe_log(product(rank(signed_power(inner, 4)), 14.9655)))
        right = rank(correlation(rank(self.vwap), rank(self.volume), 5.07914))
        return -_comparison(left, right, "lt")

    def alpha082(self):
        first = rank(decay_linear(delta(self.open, 1.46063), 14.8717))
        second = ts_rank(decay_linear(correlation(indneutralize(self.volume, self._group("sector")), self.open * 0.634196 + self.open * (1 - 0.634196), 17.4842), 6.92131), 13.4283)
        return -elementwise_min(first, second)

    def alpha083(self):
        ratio = safe_divide(self.high - self.low, ts_sum(self.close, 5) / 5)
        return safe_divide(rank(delay(ratio, 2)) * rank(rank(self.volume)), safe_divide(ratio, self.vwap - self.close))

    def alpha084(self): return signed_power(ts_rank(self.vwap - ts_max(self.vwap, 15.3217), 20.7127), delta(self.close, 4.96796))

    def alpha085(self):
        base = rank(correlation(self.high * 0.876703 + self.close * (1 - 0.876703), self._adv(30), 9.61331))
        exponent = rank(correlation(ts_rank((self.high + self.low) / 2, 3.70596), ts_rank(self.volume, 10.1595), 7.11408))
        return signed_power(base, exponent)

    def alpha086(self):
        left = ts_rank(correlation(self.close, ts_sum(self._adv(20), 14.7444), 6.00049), 20.4195)
        right = rank(self.close - self.vwap)
        return -_comparison(left, right, "lt")

    def alpha087(self):
        first = rank(decay_linear(delta(self.close * 0.369701 + self.vwap * (1 - 0.369701), 1.91233), 2.65461))
        second = ts_rank(decay_linear(correlation(indneutralize(self._adv(81), self._group("industry")), self.close, 13.4132).abs(), 4.89768), 14.4535)
        return -elementwise_max(first, second)

    def alpha088(self):
        first = rank(decay_linear(rank(self.open) + rank(self.low) - rank(self.high) - rank(self.close), 8.06882))
        second = ts_rank(decay_linear(correlation(ts_rank(self.close, 8.44728), ts_rank(self._adv(60), 20.6966), 8.01266), 6.65053), 2.61957)
        return elementwise_min(first, second)

    def alpha089(self):
        first = ts_rank(decay_linear(correlation(self.low * 0.967285 + self.low * (1 - 0.967285), self._adv(10), 6.94279), 5.51607), 3.79744)
        second = ts_rank(decay_linear(delta(indneutralize(self.vwap, self._group("industry")), 3.48158), 10.1466), 15.3012)
        return first - second

    def alpha090(self):
        base = rank(self.close - ts_max(self.close, 4.66719))
        exponent = ts_rank(correlation(indneutralize(self._adv(40), self._group("subindustry")), self.low, 5.38375), 3.21856)
        return -signed_power(base, exponent)

    def alpha091(self):
        first = ts_rank(decay_linear(decay_linear(correlation(indneutralize(self.close, self._group("industry")), self.volume, 9.74928), 16.398), 3.83219), 4.8667)
        second = rank(decay_linear(correlation(self.vwap, self._adv(30), 4.01303), 2.6809))
        return -(first - second)

    def alpha092(self):
        condition = _comparison((self.high + self.low) / 2 + self.close, self.low + self.open, "lt")
        first = ts_rank(decay_linear(condition, 14.7221), 18.8683)
        second = ts_rank(decay_linear(correlation(rank(self.low), rank(self._adv(30)), 7.58555), 6.94024), 6.80584)
        return elementwise_min(first, second)

    def alpha093(self):
        numerator = ts_rank(decay_linear(correlation(indneutralize(self.vwap, self._group("industry")), self._adv(81), 17.4193), 19.848), 7.54455)
        denominator = rank(decay_linear(delta(self.close * 0.524434 + self.vwap * (1 - 0.524434), 2.77377), 16.2664))
        return safe_divide(numerator, denominator)

    def alpha094(self):
        base = rank(self.vwap - ts_min(self.vwap, 11.5783))
        exponent = ts_rank(correlation(ts_rank(self.vwap, 19.6462), ts_rank(self._adv(60), 4.02992), 18.0926), 2.70756)
        return -signed_power(base, exponent)

    def alpha095(self):
        left = rank(self.open - ts_min(self.open, 12.4105))
        inner = rank(correlation(ts_sum((self.high + self.low) / 2, 19.1351), ts_sum(self._adv(40), 19.1351), 12.8742)) ** 5
        return _comparison(left, ts_rank(inner, 11.7584), "lt")

    def alpha096(self):
        first = ts_rank(decay_linear(correlation(rank(self.vwap), rank(self.volume), 3.83878), 4.16783), 8.38151)
        second = ts_rank(decay_linear(ts_argmax(correlation(ts_rank(self.close, 7.45404), ts_rank(self._adv(60), 4.13242), 3.65459), 12.6556), 14.0365), 13.4143)
        return -elementwise_max(first, second)

    def alpha097(self):
        mixed = self.low * 0.721001 + self.vwap * (1 - 0.721001)
        first = rank(decay_linear(delta(indneutralize(mixed, self._group("industry")), 3.3705), 20.4523))
        second = ts_rank(decay_linear(ts_rank(correlation(ts_rank(self.low, 7.87871), ts_rank(self._adv(60), 17.255), 4.97547), 18.5925), 15.7152), 6.71659)
        return -(first - second)

    def alpha098(self):
        first = rank(decay_linear(correlation(self.vwap, ts_sum(self._adv(5), 26.4719), 4.58418), 7.18088))
        second = rank(decay_linear(ts_rank(ts_argmin(correlation(rank(self.open), rank(self._adv(15)), 20.8187), 8.62571), 6.95668), 8.07206))
        return first - second

    def alpha099(self):
        left = rank(correlation(ts_sum((self.high + self.low) / 2, 19.8975), ts_sum(self._adv(60), 19.8975), 8.8136))
        right = rank(correlation(self.low, self.volume, 6.28259))
        return -_comparison(left, right, "lt")

    def alpha100(self):
        first = rank(safe_divide(((self.close - self.low) - (self.high - self.close)) * self.volume, self.high - self.low))
        first = indneutralize(indneutralize(first, self._group("subindustry")), self._group("subindustry"))
        second = correlation(self.close, rank(self._adv(20)), 5) - rank(ts_argmin(self.close, 30))
        second = indneutralize(second, self._group("subindustry"))
        return -(1.5 * scale(first) - scale(second)) * safe_divide(self.volume, self._adv(20))

    def alpha101(self): return safe_divide(self.close - self.open, self.high - self.low + 0.001)


__all__ = [
    "Alphas101Strict", "correlation", "covariance", "decay_linear", "delay",
    "delta", "indneutralize", "product", "rank", "returns", "safe_divide",
    "scale", "signed_power", "sma", "stddev", "ts_argmax", "ts_argmin",
    "ts_max", "ts_min", "ts_rank", "ts_sum",
]
