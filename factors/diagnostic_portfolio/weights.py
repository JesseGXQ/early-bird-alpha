from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Any, Literal

import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
if TYPE_CHECKING:
    from factors.diagnostic_portfolio.models import DiagnosticPortfolioSpec

from factors.diagnostic_portfolio.risk import DiagnosticPortfolioRiskManager, ExAntePortfolioVolatility

@dataclass(frozen=True)
class WeightMeta:
    cov_mat_estimation_meta: pd.Series | None

@dataclass(frozen=True)
class CurrentWeight:
    weights: pd.Series
    warnings: tuple[str,...]
    metadata: WeightMeta


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
    - 基类只提供通用的品种池应用、输入索引校验与状态记录；具体权重
      算法由子类在 :meth:`update` 中实现。
    - ``products`` 在构造时排序后保存为 ``_products``，作为所有权重
      Series 的稳定索引顺序。

    """

    def __init__(self, spec: DiagnosticPortfolioSpec,
                 products: list[str]):
        """构造权重状态机；实例化后即处于就绪状态。

        Parameters
        ----------
        spec : DiagnosticPortfolioSpec
            本次组合实验的完整不可变配置，子类据此确定权重算法与约束。
        products : list of str
            参与组合的全部品种代码；内部排序后保存，作为权重 Series
            的固定索引。

        Notes
        -----
        构造时即完成内部状态初始化：权重历史、再平衡历史与元数据历史
        为空，上一期权重为全品种空仓。无需再调用额外的初始化方法。

        """
        self._spec = spec
        self._products = sorted(products)
        self._weight_history: list[pd.Series] = []
        self._last_weights: pd.Series = self._flat_positions()


    def _flat_positions(self) -> pd.Series:
        """返回全品种空仓。"""
        return pd.Series(0.0, index=self._products)

    def _validate_input_index(self, signal: pd.Series, 
                              eligible: pd.Series[bool]) -> None:
        """校验输入索引的合法性。

        1. 信号与品种池的索引必须完全一致（标签与顺序相同）。
        2. 品种池（及信号）必须恰好覆盖状态机构造时的全部品种，
           不得缺失或出现多余品种，避免 `.where` 对齐时静默置零。
        """
        if not signal.index.equals(eligible.index):
            raise ValueError('输入信号和品种池的索引不完全相同。')
        expected = set(self._products)
        if not eligible.index.is_unique or set(eligible.index) != expected:
            missing = sorted(expected - set(eligible.index))
            extra = sorted(set(eligible.index) - expected)
            raise ValueError(
                '品种池(及信号)的索引必须与状态机的品种集合完全一致，'
                f'缺少 {missing}，多余 {extra}。'
            )

    def _apply_universe(self, eligible: pd.Series[bool]) -> pd.Series:
        """应用品种池：在上一期权重Series中，将本期已不在品种池内的设为 0 。"""
        return self._last_weights.where(eligible, 0.0)

    @abstractmethod
    def update(self, rebalance: bool, 
               eligible: pd.Series[bool], signal: pd.Series,
               ret: pd.Series | None) -> CurrentWeight:
        """按品种池、信号与约束，更新当期目标权重并返回。

        Parameters
        ----------
        rebalance : bool
            是否为计划调仓时点。
        eligible : pd.Series[bool]
            品种池准入条件，Index[product]。
        signal : pd.Series
            各品种的当期信号值，Index[product]。
        ret: pd.Series
            各品种的当期收益率。MultiIndex[timestamp, product]

        Returns
        -------
        CurrentWeight
            当期有效权重类。
            - weights: pd.Series
            - warnings: tuple of str
            - cov_mat_estimation_metadata: pd.Series or None。

        Notes
        -----
        具体权重更新逻辑由子类实现；实现方还需负责把结果写入状态历史
        并更新上一期权重。

        """
        ...

    def _enforce_max_gross(self, weights: pd.Series) -> pd.Series:
        """对权重施加 max_gross 限制；max_gross 为 None 表示不封顶。"""
        g_max = self._spec.constraints.max_gross
        if g_max is None:
            return weights
        g_w = weights.abs().sum()
        if g_w > g_max: # 若超出，则做缩放
            return weights * (g_max / g_w)
        return weights



    @property
    def weight_change(self) -> pd.Series:
        """计算本期权重和上一期权重的变化。"""
        if len(self._weight_history) == 0:  # 尚未运行 update()
            raise ValueError('必须先至少运行一次 update() 以更新权重。')

        if len(self._weight_history) == 1: # 只运行过一次 update() 得到了第一期权重
            return self._last_weights  
        return self._last_weights - self._weight_history[-2]   
    
    def _select_eligible_weights(self, weights: pd.Series, 
                         eligible: pd.Series[bool]) -> pd.Series:
        """选取在品种池内的品种权重。"""
        assert weights.notna().all()
        mask = eligible.loc[weights.index]
        return weights[mask]


class MarketRankLSWeightStateMachine(WeightStateMachine):
    """``market_rank_ls``（全市场排名多空）收获范式的权重状态机。

    计划调仓日对品种池内信号有效的品种做升序平均排名，去中心化后
    归一化，得到净敞口为零、gross 为一的截面多空权重；非调仓日仅在
    上一期权重上执行品种池强制退出（退出品种归零，其余冻结）。

    """

    def __init__(self, spec: DiagnosticPortfolioSpec, products: list[str]):
        """构造 ``market_rank_ls`` 权重状态机。

        Parameters
        ----------
        spec : DiagnosticPortfolioSpec
            组合实验配置，其 ``harvest_profile.name`` 必须为
            ``'market_rank_ls'``。
        products : list of str
            参与组合的全部品种代码。

        Raises
        ------
        ValueError
            当 ``spec.harvest_profile.name`` 不是 ``'market_rank_ls'``
            时抛出。

        """
        if spec.harvest_profile.name != 'market_rank_ls':
            raise ValueError(f'收获范式必须为 "market_rank_ls",'
                             f'现在为 {spec.harvest_profile.name}。')
        super().__init__(spec=spec, products=products)

    def update(self, rebalance: bool, 
               eligible: pd.Series, signal: pd.Series, 
               ret: pd.Series | None = None
    ) -> CurrentWeight:
        """按品种池、信号与单品种上限，更新当期目标权重并返回。

        Parameters
        ----------
        rebalance : bool
            是否为计划调仓时点。``True`` 时按信号重算截面权重；
            ``False`` 时仅在上一期权重上执行品种池强制退出。
        eligible : pd.Series
            品种池准入条件，布尔 Series，Index[product]，必须与状态机
            品种集合完全一致。
        signal : pd.Series
            各品种的当期信号值，Index[product]，必须与 ``eligible``
            完全对齐。允许包含 NaN（按缺失信号处理）。
        ret: pd.Series or None, optional
            仅为满足基类契约，该状态机对收益率序列无需求。

        Returns
        -------
        pd.Series
            当期有效权重，Index 为状态机品种集合 ``self._products``，
            数据类型为 float。

        Raises
        ------
        ValueError
            当 ``signal`` 与 ``eligible`` 索引不一致，或二者未恰好覆盖
            状态机品种集合时抛出。

        Notes
        -----
        权重更新遵循以下顺序。

        1. 输入校验：校验输入索引恰好覆盖全部品种。
        2. 非调仓日（``rebalance=False``）：令上一期权重在已退出品种上
           归零，其余品种权重冻结，不重算、不重新分配。
        3. 调仓日（``rebalance=True``）：

           - 取品种池内信号并剔除缺失值；当有效品种数少于
             ``min_cross_section_size``，或有效信号全部相等时，当期
             全组合空仓（目标权重全为 0）。
           - 否则先由 :meth:`_gen_base_weights` 生成基础权重，再经
             :meth:`_enforce_constraints` 应用单品种绝对权重上限，最后
             ``reindex`` 回全部品种（未参与品种补 0）。

        4. 无论是否成功形成组合，当期权重都会追加进 ``_weight_history``
        ，并更新 ``_last_weights``，作为下一期冻结/退出的起点。

        返回值为历史中权重的副本，避免调用方原地修改污染内部状态。

        """
        self._validate_input_index(signal, eligible)

        if not rebalance:
            final_weights = self._apply_universe(eligible) 
        else:
            signal_eligible = signal[eligible]
            signal_valid = signal_eligible.dropna()  # 在品种池内且非缺失信号

            if len(signal_valid) < self._spec.min_cross_section_size: # type: ignore 
                # 有效品种不足：该日无法形成组合，全组合空仓
                final_weights = self._flat_positions()
            elif signal_valid.nunique() == 1: #所有信号相等，直接全组合空仓
                final_weights = self._flat_positions()
            else:
                proposed_weights = self._gen_base_weights(signal_valid)
                final_weights = self._enforce_max_product_weight(proposed_weights)
                final_weights = final_weights.reindex(self._products, fill_value=0) # 将缺失的品种权重回补为 0 
        
        # 记录权重机状态
        self._weight_history.append(final_weights)
        self._last_weights = final_weights
        
        return CurrentWeight(
            weights=final_weights.copy(),
            warnings=(),
            metadata=WeightMeta(cov_mat_estimation_meta=None)
        )

    def _gen_base_weights(
            self, 
            signal: pd.Series
        ) -> pd.Series:
        """根据信号，生成（market_rank_ls）收获范式下的基础权重。"""
        ranks = signal.rank(method='average')
        qvalues = ranks - ranks.mean()
        weights = qvalues / qvalues.abs().sum() # gross = 1, sum of weights = 0
        return weights

    def _enforce_max_product_weight(self, weights: pd.Series) -> pd.Series:
        """对基础权重应用单品种绝对权重上限。

        未设置 ``max_product_weight`` 时原样返回；否则把权重拆成正、负
        两侧，按两侧共同可达的 exposure 缩放，再分别做
        cap-and-redistribute，最后合并回原索引。正负两侧目标 exposure
        相同，因此始终保持净敞口为零。

        Parameters
        ----------
        weights : pd.Series
            基础权重，Index[product]。

        Returns
        -------
        pd.Series
            约束后的权重，Index 与 ``weights`` 一致。

        """
        if self._spec.constraints.max_product_weight is None:
            return weights
        positive_part = weights[weights > 0]
        negative_part = weights[weights < 0]
        s = self._spec.constraints.max_product_weight
        target_exposure = min(0.5, len(positive_part)*s, len(negative_part)*s) # 例：正数侧只有两个品种，s=0.2，则此时正数侧能达到的最大 exposure 是0.4。而按照“市场中性”的要求，负数侧的 exposure 目标也应该是0.4。

        if target_exposure < 0.5: # 目标 exposure 达不到 0.5。由于原本正负两侧的 exposure 都是0.5，先对两侧都做目标 exposure 分配（或者说对原权重做等比例缩放，缩放到总 exposure 等于目标 exposure）
            positive_part = positive_part / positive_part.sum() * target_exposure
            negative_part = negative_part / negative_part.abs().sum() * target_exposure
        
        redistributed_positive_part = self._cap_and_redistribute(positive_part)
        redistributed_negative_part = - self._cap_and_redistribute(- negative_part)
        re_weights = pd.concat(
                [redistributed_positive_part, redistributed_negative_part],
                axis=0
            ).reindex(weights.index, fill_value=0) # 将被丢掉的0权重品种补回
        
        return re_weights

    def _cap_and_redistribute(self, weights: pd.Series) -> pd.Series:
        """把单侧（全为正）权重压到单品种上限内，并同侧再分配超额。

        Parameters
        ----------
        weights : pd.Series
            单侧权重，必须全为正。

        Returns
        -------
        pd.Series
            所有值均不超过 ``max_product_weight`` 的单侧权重，总和与
            输入相同。

        Raises
        ------
        ValueError
            当所有品种都已达到上限、没有可再分配空间时抛出。

        """
        assert (weights > 0).all()
        weights = weights.copy()
        s = self._spec.constraints.max_product_weight
        assert s is not None
        while True: 
            if (weights <= s).all() or np.isclose(weights, s).all():
                return weights
            if (weights >= s).all() and (weights > s).any():
                raise ValueError(
                    '无剩余权重分配空间，无法执行 cap-and-redistribute 算法。'
                )
            # 找出 weights 中最大的权重(超出s)
            prod_max_w = weights.idxmax()
            extra: float = weights.at[prod_max_w] - s # 先取出超出上限的部分
            weights.at[prod_max_w] = s # 再将该品种设为上限s
            # 将多余部分按原绝对权重分配到未触及上限s的品种
            unbinding = weights[weights < s].index
            pct_to_distribute: pd.Series = \
                weights.loc[unbinding] / weights.loc[unbinding].sum()
            to_distribute = pct_to_distribute * extra
            weights.loc[unbinding] += to_distribute     
    

        
class TSInverseVolatilityWeightMachine(WeightStateMachine):

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
            risk_config=self._spec.risk
        )

    def update(self, rebalance: bool, eligible: pd.Series, signal: pd.Series, 
               ret: pd.Series | None) -> CurrentWeight: # ret 必须是 MultiIndex[timestamp, product]
        assert isinstance(ret, pd.Series)
        assert isinstance(ret.index, pd.MultiIndex)
        self._risk_manager.append(ret=ret) # 将收益率series转交给 risk manager

        self._validate_input_index(signal, eligible)

        cov_mat_estimation_meta = None
        warnings: list[str] = []

        if not rebalance:
            final_weights = self._apply_universe(eligible) 
        else:
            signal_eligible = signal[eligible]
            signal_valid = signal_eligible.dropna()  # 在品种池内且非缺失信号
            proposed_weights = self._gen_base_weight(signal=signal_valid)
            if self._is_target_vol:
                # 检验是否是无效品种（回看窗口期收益率计数不达标）
                # 若是，则权重设为0，然后重新做归一化
                invalid_products = self._risk_manager.get_invalid_products() 
                remove = pd.Index(invalid_products)\
                    .intersection(proposed_weights.index)
                if not remove.empty:
                    proposed_weights.loc[remove] = 0.0
                    proposed_weights = self._scale_to_gross_one(proposed_weights)
                # 选出非零权重品种，做目标波动率缩放
                nonzero = proposed_weights[proposed_weights!=0]
                if not nonzero.empty:
                    proposed_weights, exante_vol_res = \
                        self._scale_to_target_vol(nonzero)
                    cov_mat_estimation_meta = \
                        exante_vol_res.cov_mat_estimation_meta 
                    warnings.extend(exante_vol_res.warnings)
                proposed_weights = self._enforce_max_gross(proposed_weights)
            final_weights = self._enforce_max_product_weight(proposed_weights)
            final_weights = final_weights.reindex(self._products, fill_value=0) # 将缺失的品种权重回补为 0 

        # 记录权重机状态
        self._weight_history.append(final_weights)
        self._last_weights = final_weights

        return CurrentWeight(
            weights=final_weights.copy(),
            warnings=tuple(warnings),
            metadata=WeightMeta(
                cov_mat_estimation_meta=cov_mat_estimation_meta
            )
        )
    def _enforce_max_product_weight(self, weights: pd.Series) -> pd.Series:
        """施加单品种权重上限限制。仅做 clip， 不做剩余权重再分配。"""
        if self._spec.constraints.max_product_weight is None:
            return weights
        s = self._spec.constraints.max_product_weight
        assert s > 0
        return weights.clip(lower=-s,upper=s)


    def _scale_to_gross_one(self, weights: pd.Series) -> pd.Series:
        """gross = 1 的权重归一化。"""
        if weights.abs().sum() == 0 : # 分母为0
            return self._flat_positions().reindex(weights.index)
        return weights / weights.abs().sum()

    def _scale_to_target_vol(self, weights: pd.Series) -> \
        tuple[pd.Series, ExAntePortfolioVolatility]:
        """使用目标波动率，对组合权重做缩放（对权重做调整，使得在考虑品种间
        相关性的情况下，组合的事前波动率等于目标波动率）。

        返回 (缩放后权重Series，ExantePortfolioVolatility实例)。
        """
        assert weights.notna().all()
        vol_result = self._risk_manager\
            .ex_ante_portfolio_volatility_annualized(
                weights=weights, 
                annualization_factor=self._spec.annualization_factor
            )
        vol, meta = vol_result.value, vol_result.cov_mat_estimation_meta
        assert vol >= 0
        self._cov_est_metadata.append(meta)

        if vol == 0: # 估计出的组合事前波动率为0，这属于估计失败，直接空仓
            return self._flat_positions().reindex(weights.index), vol_result
        
        lambda_star: float = self._spec.risk.target_volatility / vol  # type: ignore
        lambda_clipped = min(
            self._spec.constraints.max_volatility_scaler, # pyright: ignore[reportArgumentType]
            lambda_star
        )
        return lambda_clipped * weights, vol_result

    def _gen_base_weight(self, signal: pd.Series) -> pd.Series: # signal 为在品种池内且无缺失
        """在 profile 为 ts_inv_vol_gross_one 下，根据信号，生成基础权重：
        头尾部对称 clip -> scaled by ex ante vol -> scaled to gross = 1
        """
        assert self._spec.clip_bound is not None
        signal_clipped = signal.clip(
            lower=-self._spec.clip_bound, upper=self._spec.clip_bound
        )
        vol: pd.Series = self._risk_manager.ex_ante_volatility()
        u_weights = signal_clipped / vol.reindex(signal_clipped.index)
        u_weights.fillna(0, inplace=True) # 将由于事前波动率缺失导致的缺失用0填充
        return self._scale_to_gross_one(weights=u_weights)
