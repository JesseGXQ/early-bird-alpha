"""Carry 类因子。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Mapping

import numpy as np
import pandas as pd

from factors.library.base import Factor
from factors.models import (DataRequirement, FactorState, 
                            IncrementalFactorResult, INDEX_NAMES)


type _RY_COMBO = Literal[
    'near_next',        # 近月 + 次近月
    'near_dominant',    # 近月 + 主力
    'near_farthest',    # 近月 + 最远月
    'dominant_secondary',  # 主力 + 次主力
]


class RollYieldFactor(Factor):
    """展期收益率因子。"""

    def __init__(self, combination: _RY_COMBO = 'near_dominant',
                 near_dte_min: int = 0, far_dte_max: int = 365):
        self.combination = combination
        self.near_dte_min = near_dte_min
        self.far_dte_max = far_dte_max
        self.warnings: list[str] = []

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {
            'combination':  self.combination,
            'near_dte_min': self.near_dte_min,
            'far_dte_max':  self.far_dte_max
        }
    
    @property
    def name(self) -> str:
        return f'roll_yield_{self.combination}'

    @property
    def computation_scope(self) -> Literal['per_product']:
        return 'per_product'

    @property
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        return    (
            DataRequirement(
                source='term_structure',
                columns=('Settle', 'last_trade_date',
                         'is_dominant', 'OpenInterest', 'Volume'),
            ),
        )

    @property
    def warmup_periods(self) -> None:
        """暖机窗口，对展期因子不适用。"""
        return None

    def compute(self, data: pd.DataFrame) -> pd.Series:
        """
        批量计算接口。

        Parameters
        ---
        data: pd.DataFrame
            从 ResearchDataset.auxiliary['term_structure'] 获取的期限结构数据，
            MultiIndex[timestamp, product, contract]

        Returns
        ---
        pd.Series
            因子值Series。MultiIndex[timestamp, product]
        """
        term_df = data.copy()

        term_df['days_to_last_trade'] = (
            (pd.to_datetime(term_df['last_trade_date']) 
            - term_df.index.get_level_values('timestamp'))
            .dt.days
        )

        rst_dict: dict[tuple[pd.Timestamp, str], float] = {}
        for (ts, prod), grouped_df in term_df.groupby(level=INDEX_NAMES):

            df = grouped_df.reset_index(level=INDEX_NAMES, drop=True) # 去掉 timestamp 和 product 这两个 idx level

            df = self._filter_contracts_dte(df) 
            # 若过滤后发现是空DataFrame或只有一行了，令因子值为np.nan，并记录警告
            if len(df) <= 1:
                self.warnings.append(
                    f'timestamp={ts}, product={prod}:'
                    f'满足 days_to_last_trade 大于{self.near_dte_min} 且小于{self.far_dte_max}'
                    f'的合约数量为 {len(df)}，无法计算展期因子。')
                rst_dict[(ts,prod)] = np.nan
                continue
            
            near, far, msg = self._select_contract_pair(df) 
            if near is None or far is None: # 若出错
                self.warnings.append(
                    f'timestamp={ts}, product={prod}:'
                    f'根据 combination ={self.combination} 选择合约时出错。'
                    f'错误原因为 {msg}'
                )
                continue

            roll_yield = self._compute_roll_yield(df, near, far)
            rst_dict[(ts,prod)] = roll_yield

        idx = pd.MultiIndex.from_tuples(rst_dict.keys(), names=INDEX_NAMES)
        return pd.Series(rst_dict.values(), index=idx, name=self.name)

    def _compute_roll_yield(self, df: pd.DataFrame, near: str, far: str) -> float:
        """根据近月合约和远月合约计算年化展期收益率。"""
        near_price = df.at[near, 'Settle']
        far_price  = df.at[far,  'Settle']
        near_dte   = df.at[near, 'days_to_last_trade']
        far_dte    = df.at[far,  'days_to_last_trade']
        assert near_dte != far_dte
        return (near_price / far_price -1) / (far_dte - near_dte) * 365.0

    def _filter_contracts_dte(self, df: pd.DataFrame) -> pd.DataFrame:
        """根据 near_dte_min, far_dte_max 过滤合约。"""
        mask = ((df['days_to_last_trade'] >= self.near_dte_min) 
                & (df['days_to_last_trade'] <= self.far_dte_max))
        return df[mask]

    def _select_contract_pair(
            self, df: pd.DataFrame) -> tuple[str | None , str | None, str]:
        """
        根据 combination ，选择并返回 (近月合约，远月合约)。
        当出错时，返回 `(None, None, error_msg)`。
        出错条件：近月=主力；找不到次主力（到期日晚于主力，持仓量小于主力）。
        """
        def assert_has_dominant(df):
            assert df['is_dominant'].any(), \
                  '主力合约缺失，无法选出 combination 为 near_dominant 时的远月合约。'

        assert len(df) > 1 # 该方法必须要求至少两个合约

        df = df.copy() 
        df['lifespan_rank'] = df['days_to_last_trade'].rank()

        if self.combination == 'near_next':
            near = df.index[df['lifespan_rank'] == 1][0]
            far  = df.index[df['lifespan_rank'] == 2][0]

        elif self.combination == 'near_dominant':
            assert_has_dominant(df)
            near = df.index[df['lifespan_rank'] == 1][0]
            far  = df.index[df['is_dominant']][0]
            if near == far: # 近月合约就是主力，此时近月=远月
                return None, None, 'error: 近月合约就是主力'
            
        elif self.combination == 'near_farthest':
            near = df.index[df['lifespan_rank'] == 1][0]
            far = df.index[df['lifespan_rank'] == df['lifespan_rank'].max()][0]

        elif self.combination == 'dominant_secondary': 
            # 选出到期日晚于主力，持仓量小于等于主力的合约中，持仓量最大的那张
            assert_has_dominant(df)
            near = df.index[df['is_dominant']][0]
            lifespan_rank_dom = df.loc[df['is_dominant'], 'lifespan_rank'].squeeze()
            oi_dom = df.loc[df['is_dominant'], 'OpenInterest'].squeeze()
            assert np.isscalar(lifespan_rank_dom)
            assert np.isscalar(oi_dom)
            mask = (  (df['lifespan_rank'] > lifespan_rank_dom)
                    & (df['OpenInterest']  <= oi_dom)
            )
            df_mask = df[mask]
            if df_mask.empty:
                return None, None, 'error: 无合约满足到期日晚于主力，且持仓量小于主力'
            far = df_mask.index[
                df_mask['OpenInterest'] == df_mask['OpenInterest'].max()][0]

        return near, far, ''

    def initialize_state(self) -> FactorState:
        """增量接口，初始化状态， V1 无需实现。"""
        return FactorState(name=self.name)

    def update(self, state: FactorState, 
               timestamp_data: pd.DataFrame) -> IncrementalFactorResult:
        """增量接口，更新状态， V1 无需实现。"""
        raise NotImplementedError()

