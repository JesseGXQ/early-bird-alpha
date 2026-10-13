"""
``factors/diagnostic_portfolio/weights.py`` —— 组合权重状态机。

设计约定（性能）
----------------
权重机在实验主循环中逐期被调用（默认约 7600 期），每次输入/输出都是覆盖
全部品种的权重向量。由于 ``products`` 集合在构造时即固定，**内部一律使用
``np.ndarray``** 并以位置隐含对齐：``w[i]`` 永远对应 ``self._products[i]``。

因此运行期不存在任何 pandas 索引对齐、``reindex``、``Index.equals`` 或
``Series`` 构造。pandas 只出现在三个边界上：

1. 输入适配：``update()`` 开头把 ``pd.Series`` 转成 ndarray（约 3 微秒），
   便于直接以 Series 调用状态机（测试与探索脚本）。
2. ``CurrentWeight.weights`` 是 ``np.ndarray``；调用方按需自行构造 Series。
3. ``WeightMeta.cov_mat_estimation_meta`` 是审计用元数据，由 risk 层产出。

不变式
------
``self._w`` 是当前权重，``(n_products,)`` 的独立数组。状态机**从不原地修改**
``self._w``——每次 :meth:`update` 都产生新数组再整体替换——因此把 ``self._w``
交给调用方之前需要显式复制（见各 ``update`` 实现），以维持
“返回副本、避免调用方回写污染内部状态”的契约。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from factors.diagnostic_portfolio.models import DiagnosticPortfolioSpec

from factors.diagnostic_portfolio.risk import (
    DiagnosticPortfolioRiskManager,
    ExAntePortfolioVolatility,
)


@dataclass(frozen=True)
class WeightMeta:
    cov_mat_estimation_meta: pd.Series | None


@dataclass(frozen=True)
class CurrentWeight:
    weights: np.ndarray  # 覆盖全部品种的 (n_products,) 权重，位置与 products 对齐
    warnings: tuple[str, ...]
    metadata: WeightMeta


def _average_rank(values: np.ndarray) -> np.ndarray:
    """升序平均秩（1 起），与 ``Series.rank(method='average')`` 等价。

    平均秩 ``= (lo + hi) / 2``，其中 ``lo``、``hi`` 是该值在升序序列中的
    1 起首末位置。等价地 ``= start + (1 + count) / 2``，这里用后一写法，
    因为 ``count`` 为整数而 ``(count - 1) / 2`` 必为 ``.0`` 或 ``.5``，
    两者都是精确可表示的浮点数，故与 pandas 的算式逐位相同。

    Parameters
    ----------
    values : np.ndarray
        一维浮点数组，**不得含 NaN**（调用方需先剔除）。

    Returns
    -------
    np.ndarray
        与输入等长的平均秩。

    """
    _, inverse = np.unique(values, return_inverse=True)
    counts = np.bincount(inverse)
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    return (starts + 1 + (counts - 1) / 2.0)[inverse]


class WeightStateMachine(ABC):
    """权重状态机抽象基类。

    在给定的组合收获范式与权重约束下，把「品种池 + 单列信号」逐步
    转换为逐决策时点的目标权重。状态机是有状态的：每次 :meth:`update`
    都会把当期有效权重写入内部历史，并作为下一期冻结或再平衡的起点。

    子类必须实现 :meth:`update`，且应在构造时声明自己只接受某一种
    ``harvest_profile``。状态机实例化后即就绪，生命周期为：
    构造（即就绪）→ 反复 :meth:`update`。

    Notes
    -----
    - 基类只提供通用的品种池应用与状态记录；具体权重算法由子类在
      :meth:`update` 中实现。
    - ``products`` 在构造时排序后保存为 ``_products``，作为所有权重向量的
      固定位置顺序；``_products_index`` 仅用于边界处的 pandas 构造。

    """

    def __init__(self, spec: DiagnosticPortfolioSpec,
                 products: list[str]):
        """构造权重状态机；实例化后即处于就绪状态。

        Parameters
        ----------
        spec : DiagnosticPortfolioSpec
            本次组合实验的完整不可变配置，子类据此确定权重算法与约束。
        products : list of str
            参与组合的全部品种代码；内部排序后保存，作为权重向量的固定
            位置顺序。

        """
        self._spec = spec
        self._products = sorted(products)
        self._n: int = len(self._products)
        self._w: np.ndarray = np.zeros(self._n, dtype=float)
        # ------------------------------------------------------------------
    # 通用算子
    # ------------------------------------------------------------------
    def _flat_positions(self) -> np.ndarray:
        """返回全品种空仓。"""
        return np.zeros(self._n, dtype=float)

    # ------------------------------------------------------------------
    # 通用算子
    # ------------------------------------------------------------------
    def _apply_universe(self, eligible: np.ndarray) -> np.ndarray:
        """应用品种池：把本期已不在池内的上一期权重置 0。"""
        return np.where(eligible, self._w, 0.0)

    def _enforce_max_gross(self, weights: np.ndarray) -> np.ndarray:
        """对权重施加 max_gross 限制；max_gross 为 None 表示不封顶。"""
        g_max = self._spec.constraint.max_gross
        if g_max is None:
            return weights
        g_w = float(np.abs(weights).sum())
        if g_w > g_max:
            return weights * (g_max / g_w)
        return weights

    def _scale_to_gross_one(self, weights: np.ndarray) -> np.ndarray:
        """gross = 1 的权重归一化。"""
        gross = float(np.abs(weights).sum())
        if gross == 0.0:
            return np.zeros(self._n, dtype=float)
        return weights / gross

    def _commit(self, weights: np.ndarray) -> None:
        """把当期权重写入内部状态（整体替换，不原地修改旧数组）。"""
        self._w = weights

    @abstractmethod
    def update(self, rebalance: bool,
               eligible: np.ndarray, signal: np.ndarray,
               ret: np.ndarray | None = None,
               *, timestamp: pd.Timestamp | None = None) -> CurrentWeight:
        """按品种池、信号与约束，更新当期目标权重并返回。

        Parameters
        ----------
        rebalance : bool
            是否为计划调仓时点。
        eligible : np.ndarray[bool]
            品种池准入条件，形状 ``(n_products,)``，位置与 ``self._products``
            对齐。
        signal : np.ndarray
            各品种的当期信号值，形状 ``(n_products,)``，位置与
            ``self._products`` 对齐；允许 NaN。
        ret : np.ndarray, optional
            各品种的当期收益率，形状 ``(n_products,)``，位置与
            ``self._products`` 对齐；允许 NaN。
        timestamp : pd.Timestamp, optional
            当期时间戳。``ret`` 本身不携带时间戳，需要收益率历史的实现
            必须传入；不关心的实现可以忽略。

        Returns
        -------
        CurrentWeight
            ``weights`` 为 ``(n_products,)`` 的 ndarray 副本。

        Notes
        -----
        三个输入的长度必须都是 ``n_products``，且与 ``self._products`` 同序。
        该前提由实验主循环保证：它把 ``build_panel_arrays`` 产出的
        ``(T, P)`` 矩阵按行切片后传入，本方法不再重复校验。

        """
        ...


class MarketRankLSWeightStateMachine(WeightStateMachine):
    """``market_rank_ls``（全市场排名多空）收获范式的权重状态机。

    计划调仓日对品种池内信号有效的品种做升序平均排名，去中心化后
    归一化，得到净敞口为零、gross 为一的截面多空权重；非调仓日仅在
    上一期权重上执行品种池强制退出（退出品种归零，其余冻结）。

    """

    def __init__(self, spec: DiagnosticPortfolioSpec, products: list[str]):
        """构造 ``market_rank_ls`` 权重状态机。

        Raises
        ------
        ValueError
            当 ``spec.harvest_profile.name`` 不是 ``'market_rank_ls'`` 时抛出。

        """
        if spec.harvest_profile.name != 'market_rank_ls':
            raise ValueError(f'收获范式必须为 "market_rank_ls",'
                             f'现在为 {spec.harvest_profile.name}。')
        super().__init__(spec=spec, products=products)

    def update(self, rebalance: bool,
               eligible: np.ndarray, signal: np.ndarray,
               ret: np.ndarray | None = None,
               *, timestamp: pd.Timestamp | None = None) -> CurrentWeight:
        """按品种池、信号与单品种上限，更新当期目标权重并返回。

        Notes
        -----
        权重更新遵循以下顺序。

        1. 非调仓日：令上一期权重在已退出品种上归零，其余冻结，不重算。
        2. 调仓日：取品种池内且信号非缺失的品种；当有效品种数少于
           ``min_cross_section_size``、或有效信号全部相等时全组合空仓；
           否则先由 :meth:`_gen_base_weights` 生成基础权重，再经
           :meth:`_enforce_max_product_weight` 应用单品种绝对权重上限。
        3. 当期权重写入内部状态，并作为下一期冻结/退出的起点。

        """
        if not rebalance:
            final_weights = self._apply_universe(eligible)
        else:
            participating = eligible & ~np.isnan(signal)
            min_cs = self._spec.min_cross_section_size or 0
            if int(participating.sum()) < min_cs:
                # 有效品种不足：该日无法形成组合，全组合空仓
                final_weights = self._flat_positions()
            else:
                valid_signal = signal[participating]
                if np.unique(valid_signal).size == 1:
                    # 所有信号相等，直接全组合空仓
                    final_weights = self._flat_positions()
                else:
                    base = np.zeros(self._n, dtype=float)
                    base[participating] = self._gen_base_weights(valid_signal)
                    final_weights = self._enforce_max_product_weight(base)

        self._commit(final_weights)
        return CurrentWeight(
            weights=final_weights.copy(),
            warnings=(),
            metadata=WeightMeta(cov_mat_estimation_meta=None),
        )

    def _gen_base_weights(self, signal: np.ndarray) -> np.ndarray:
        """根据信号生成基础权重（仅传入参与计算的子集）。

        平均排名后去中心化再归一化，使 sum(weights) = 0、gross = 1。
        """
        ranks = _average_rank(signal)
        qvalues = ranks - ranks.mean()
        return qvalues / np.abs(qvalues).sum()

    def _enforce_max_product_weight(self, weights: np.ndarray) -> np.ndarray:
        """对基础权重应用单品种绝对权重上限，并同侧再分配超额。

        未设置 ``max_product_weight`` 时原样返回；否则把权重拆成正、负
        两侧，按两侧共同可达的 exposure 缩放，再分别做
        cap-and-redistribute。正负两侧目标 exposure 相同，因此始终保持
        净敞口为零。
        """
        s = self._spec.constraint.max_product_weight
        if s is None:
            return weights

        positive = weights > 0
        negative = weights < 0
        n_pos = int(positive.sum())
        n_neg = int(negative.sum())
        target_exposure = min(0.5, n_pos * s, n_neg * s) # 例：正数侧只有两个品种，s=0.2，则此时正数侧能达到的最大 exposure 是0.4。而按照“市场中性”的要求，负数侧的 exposure 目标也应该是0.4。

        out = np.zeros_like(weights)
        if target_exposure < 0.5:
            # 目标 exposure 达不到 0.5：由于原本正负两侧的 exposure 都是0.5，正负两侧各自缩放到该 exposure。
            # _cap_and_redistribute 要求输入全为正，故负侧先取负再算、结果再取负。
            pos = self._cap_and_redistribute(
                self._rescale_side(weights, positive, target_exposure))
            neg = -self._cap_and_redistribute(
                -self._rescale_side(weights, negative, target_exposure))
        else:
            # 布尔掩码索引本身已返回新数组，无需再 copy（见下方两条注释）。
            pos = self._cap_and_redistribute(weights[positive])
            neg = -self._cap_and_redistribute(-weights[negative])

        out[positive] = pos
        out[negative] = neg
        return out

    @staticmethod
    def _rescale_side(weights: np.ndarray, side: np.ndarray,
                      target_exposure: float) -> np.ndarray:
        """把 ``weights`` 在 ``side`` 一侧的部分缩放到 ``target_exposure``。"""
        values = weights[side]   # 掩码索引已返回新数组
        if values.size == 0:
            return values
        denom = float(np.abs(values).sum())
        return values / denom * target_exposure

    def _cap_and_redistribute(self, weights: np.ndarray) -> np.ndarray:
        """把单侧权重压到单品种上限内，并同侧再分配超额。

        本方法会**原地修改**入参的副本，因此调用方无需额外 copy。

        Parameters
        ----------
        weights : np.ndarray
            单侧权重（已剔除 0 与另一侧），必须全为正。

        Returns
        -------
        np.ndarray
            所有值均不超过 ``max_product_weight`` 的同侧权重，总和与输入
            相同。

        Raises
        ------
        ValueError
            当所有品种都已达到上限、没有可再分配空间时抛出。

        """
        w = np.array(weights, dtype=float, copy=True)   # 唯一一次拷贝
        s = self._spec.constraint.max_product_weight
        assert s is not None
        while True:
            if (w <= s).all() or np.isclose(w, s).all():
                return w
            if (w >= s).all() and (w > s).any():
                raise ValueError(
                    '无剩余权重分配空间，无法执行 cap-and-redistribute 算法。'
                )
            i = int(np.argmax(w))  # 找出 weights 中最大的权重(超出s)；与 idxmax 一致，取首次最大值
            extra = w[i] - s       # 先取出超出上限的部分
            w[i] = s               # 将该品种设为上限s
            below = w < s
            # 将多余部分按原绝对权重分配到未触及上限s的品种
            w[below] += (w[below] / w[below].sum()) * extra


class TSInverseVolatilityWeightMachine(WeightStateMachine):
    """``ts_inv_vol_gross_one`` / ``ts_inv_vol_target_vol`` 权重状态机。"""

    def __init__(self, spec: DiagnosticPortfolioSpec,
                 products: list[str]):
        if spec.harvest_profile.name not in (
            'ts_inv_vol_gross_one', 'ts_inv_vol_target_vol'):
            raise ValueError('收获范式必须为 "ts_inv_vol_gross_one" 或 "ts_inv_vol_target_vol"'
                             f'现在为 {spec.harvest_profile.name}。')
        self._is_target_vol = (
            spec.harvest_profile.name == 'ts_inv_vol_target_vol'
        )
        if self._is_target_vol:
            self._cov_est_metadata: list[pd.Series] = []

        super().__init__(spec=spec, products=products)

        assert self._spec.risk is not None
        self._risk_manager = DiagnosticPortfolioRiskManager(
            risk_config=self._spec.risk, n_products=self._n
        )

    def update(self, rebalance: bool,
               eligible: np.ndarray, signal: np.ndarray,
               ret: np.ndarray | None = None,
               *, timestamp: pd.Timestamp | None = None) -> CurrentWeight:
        """按 clip、逆波动率缩放与（可选的）目标波动率缩放更新权重。"""
        if ret is None:
            raise ValueError(
                'ts_inv_vol 收获范式需要当期收益率，但 ret 为 None。')
        if timestamp is None:
            raise ValueError(
                'ts_inv_vol 收获范式需要当期时间戳来归档收益率，'
                '但 timestamp 为 None。')
        self._risk_manager.append(ret=ret, timestamp=timestamp)

        cov_mat_estimation_meta = None
        warnings: list[str] = []

        if not rebalance:
            final_weights = self._apply_universe(eligible)
        else:
            participating = eligible & ~np.isnan(signal)
            # 非参与品种的信号置 NaN，使其在本期基础权重中为 0。
            masked_signal = np.where(participating, signal, np.nan)
            proposed_weights = self._gen_base_weight(masked_signal)
            if self._is_target_vol:
                # 剔除回看窗口收益率计数不达标的品种，再重新归一化
                invalid = self._risk_manager.invalid_mask()
                if invalid.any():
                    proposed_weights[invalid] = 0.0
                    proposed_weights = self._scale_to_gross_one(
                        proposed_weights)
                active = proposed_weights != 0
                if active.any():
                    proposed_weights, exante_vol_res = \
                        self._scale_to_target_vol(proposed_weights, active)
                    cov_mat_estimation_meta = \
                        exante_vol_res.cov_mat_estimation_meta
                    warnings.extend(exante_vol_res.warnings)
                proposed_weights = self._enforce_max_gross(proposed_weights)
            final_weights = self._enforce_max_product_weight(proposed_weights)

        self._commit(final_weights)
        return CurrentWeight(
            weights=final_weights.copy(),
            warnings=tuple(warnings),
            metadata=WeightMeta(
                cov_mat_estimation_meta=cov_mat_estimation_meta
            ),
        )

    def _enforce_max_product_weight(self, weights: np.ndarray) -> np.ndarray:
        """施加单品种权重上限限制。仅做 clip，不做剩余权重再分配。"""
        s = self._spec.constraint.max_product_weight
        if s is None:
            return weights
        assert s > 0
        return np.clip(weights, -s, s)

    def _scale_to_target_vol(self, weights: np.ndarray,
                             active: np.ndarray) \
        -> tuple[np.ndarray, ExAntePortfolioVolatility]:
        """使用目标波动率对权重做缩放。

        协方差在 ``active`` 选出的品种子集上估计——这与旧实现把
        ``nonzero.index`` 传给 ``ret_wide[products]`` 是同一口径。

        Returns
        -------
        tuple[np.ndarray, ExAntePortfolioVolatility]
            缩放后的全品种权重，以及波动率估计结果。

        """
        vol_result = self._risk_manager\
            .ex_ante_portfolio_volatility_annualized(
                weights=weights[active],
                annualization_factor=self._spec.annualization_factor,
                valid_mask=active,
            )
        vol, meta = vol_result.value, vol_result.cov_mat_estimation_meta
        assert vol >= 0
        self._cov_est_metadata.append(meta)

        if vol == 0:  # 估计出的组合事前波动率为0，属于估计失败，直接空仓
            return self._flat_positions(), vol_result

        lambda_star: float = self._spec.risk.target_volatility / vol  # type: ignore
        lambda_clipped = min(
            self._spec.constraint.max_volatility_scaler,  # type: ignore[arg-type]
            lambda_star,
        )
        return lambda_clipped * weights, vol_result

    def _gen_base_weight(self, signal: np.ndarray) -> np.ndarray:
        """生成基础权重：头尾部对称 clip（可选） -> 按事前波动率倒数缩放
        -> gross = 1。

        Parameters
        ----------
        signal : np.ndarray
            信号，**未参与计算的品种为 NaN**（会被 clip 保留为 NaN，除法后
            填充为 0）。

        Notes
        -----
        ``spec.clip_bound`` 为 None 时只跳过 clip 这一步；逆波动率缩放与
        gross = 1 归一化与 clip 无关，始终执行，因此返回的权重量纲
        始终是归一化后的比例而非信号原值。

        """
        clip_bound = self._spec.clip_bound
        # clip_bound 为 None 表示不截断信号。注意这里只能跳过 clip 一步，
        # 不能提前返回——下面的逆波动率缩放与 gross=1 归一化必须照常执行。
        signal_clipped = signal if clip_bound is None \
            else np.clip(signal, -clip_bound, clip_bound)
        vol: np.ndarray = self._risk_manager.ex_ante_volatility()
        with np.errstate(invalid='ignore', divide='ignore'):
            u_weights = signal_clipped / vol
        # 事前波动率缺失（NaN）的品种按 0 处理。这里只填 NaN：收益率与事前
        # 波动率均为有限值，inf 不应出现，应由上游数据保证。
        u_weights = np.where(np.isnan(u_weights), 0.0, u_weights)
        return self._scale_to_gross_one(u_weights)