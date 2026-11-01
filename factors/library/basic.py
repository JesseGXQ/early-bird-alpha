"""趋势/动量、期限结构与合约生命周期类基础因子。"""

from __future__ import annotations

from datetime import date
import logging
from typing import TYPE_CHECKING, Any, Mapping, Literal

import numpy as np
import pandas as pd
import statsmodels.api as sm

from factors.library.base import Factor
from factors.models import (DataRequirement, FactorState,
                            IncrementalFactorResult, INDEX_NAMES)
from factors.common import rolling_volatility

logger = logging.getLogger(__name__)


class MomentumState(FactorState):
    """增量接口，动量因子状态类。"""

    __slots__ = ('lookback', 'product_returns')

    def __init__(self, lookback: int, name: str):
        super().__init__(name=name)
        self.lookback = lookback
        self.product_returns: dict[str, tuple[float, ...]] = {} # key is product

    def push(self, product: str, ret: float) -> None:
        """将最新收益率值追加到品种历史窗口。"""
        if product not in self.product_returns: # 若是第一期，则初始化
            self.product_returns[product] = (ret,)
            return
        rets = self.product_returns[product]
        rets += (ret, ) # 追加最新的一期收益率值
        if len(rets) > self.lookback: # 历史数据个数超出窗口长度
            rets = rets[-self.lookback:] # 取最新 lookback 个值
        self.product_returns[product] = rets

    def ready(self, product: str) -> bool:
        """品种历史窗口内数据是否足够产出因子值。"""
        return len(self.product_returns.get(product, ())) == self.lookback

    def compute_window(self, product: str) -> float:
        """计算品种窗口内的动量因子值（累计收益率）。"""
        rets = self.product_returns.get(product)
        if rets is None or len(rets) != self.lookback:
            return float('nan')
        return float(np.prod(1.0 + np.array(rets)) - 1.0)

    def reset(self) -> None:
        self.product_returns.clear()

    def to_dict(self) -> dict[str, Any]:
        re_dict = super().to_dict() # 父类包含一个 name 属性
        re_dict['lookback'] = self.lookback
        re_dict['product_returns'] = self.product_returns
        return re_dict

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MomentumState:
        momstate = cls(lookback=d['lookback'], name=d['name'])
        momstate.product_returns = d['product_returns']
        return momstate
    

class MomentumFactor(Factor):
    """动量因子类。

    公式：窗口内收益率累乘 ``prod(1 + True_Return) - 1``。

    Parameters
    ----------
    lookback : int
        动量计算窗口长度（期数）。
    volnorm : bool, default True
        是否使用 ``True_Return`` 的滚动波动率做结构化归一。
    vol_method : {'std'}, default 'std'
        波动率估计方法，目前仅支持滚动标准差。
    vol_lookback : int or None
        波动率滚动窗口；``volnorm=True`` 时必填且不小于 2。
    vol_min_periods : int or None
        波动率滚动窗口的最小样本数；``volnorm=True`` 时必填且不小于 2。

    Notes
    -----
    结构化归一公式： ``窗口累计收益率 / (rolling_std(True_Return) * sqrt(lookback))``。
    """

    def __init__(
            self, lookback: int, volnorm: bool = True, *, 
            vol_method: Literal['std'] = 'std',
            vol_lookback: int | None = None, vol_min_periods: int | None = None):
        self._lookback = lookback
        if volnorm is True:
            if not (isinstance(vol_lookback, int) and vol_lookback >= 2):
                raise ValueError(f'当 volnorm 为 True 时，\
                                 必须传入大于等于 2 的 vol_lookback 参数。')
            if not (isinstance(vol_min_periods, int) and vol_min_periods >= 2):
                raise ValueError(f'当 volnorm 为 True 时，\
                                 必须传入大于等于2 的 vol_min_periods 参数。')
        self._volnorm = volnorm
        self._vol_lookback = vol_lookback
        self._vol_min_periods = vol_min_periods
        self._vol_method: Literal['std'] = vol_method

    @property
    def name(self, ) -> str:
        name = f'momentum_{self._lookback}'
        if self._volnorm:
            name += f'_volnorm'
        return name

    @property
    def computation_scope(self,) -> Literal['per_product']:
        return 'per_product'

    @property
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        return (
    DataRequirement(source='main_panel', columns=('True_Return',)),
    )

    @property
    def warmup_periods(self) -> int | None:
        return None # 窗口类因子，窗口填满即收敛，故暖机参数不适用

    def compute(self, data: pd.DataFrame) -> pd.Series:
        """
        批量计算接口。

        Parameters
        ---
        data: pd.DataFrame
            MultiIndex[timetamp, product]，包含 `True_Return` 列。

        Returns
        ---
        pd.Series
            因子Series。
        """
        def cumulative_ret_window(ret_series: pd.Series) -> pd.Series:
            """内部辅助函数：根据收益率序列计算窗口内的累计收益率。"""
            return (
                (1 + ret_series).rolling(self._lookback).
                apply(np.prod, raw=True) -1 # 此处的rolling方法的min_periods默认为窗口长度，即第 1 个直到 lookback-1 个值都为nan
            )

        ret_series = data['True_Return']
        mom_values = (ret_series
                .groupby('product', group_keys=False)
                .apply(cumulative_ret_window))
        
        if self._volnorm:
            assert self._vol_lookback is not None
            assert self._vol_min_periods is not None
            vol = rolling_volatility(
                ret_series, method=self._vol_method, window=self._vol_lookback, 
                min_periods=self._vol_min_periods)
            denom = vol * np.sqrt(self._lookback)
            denom = denom.where(denom > 0) # 将标准差为0的置NaN，防止除法后出现inf或报错
            if mom_values.index.equals(denom.index):
                mom_values = mom_values / denom
            else:
                mom_values = mom_values / denom.reindex(mom_values.index)

        mom_values.name = self.name
        return mom_values

    def initialize_state(self) -> MomentumState:
        """增量接口，初始化动量因子状态。"""
        raise NotImplementedError()
        return MomentumState(lookback=self._lookback, name=self.name)

    def update(self, state: FactorState, 
               timestamp_data: pd.DataFrame) -> IncrementalFactorResult:
        """增量接口，根据最新一期数据，计算最新因子值。返回增量因子结果。"""
        raise NotImplementedError()
        assert type(state) is MomentumState
        timestamp_df = timestamp_data

        # 初始化{品种：（新）因子值} 的字典
        product_value: dict[str, float] = {}
        # 获取 timestamp
        timestamp = timestamp_df.index.get_level_values('timestamp')[0]

        # 获取每个品种的新一期数据（True_Return），并计算（新）因子值
        products: list[str] = timestamp_df.index.get_level_values('product').tolist()
        for product in products:
            ret: float = timestamp_df.at[(timestamp, product), 'True_Return']
            state.push(product, ret) # 追加新一期收益率数据
            value = state.compute_window(product) if state.ready(product) else np.nan # 若准备就绪，则计算新一期因子值
            product_value[product] = value

        values = pd.Series(product_value, name=self.name)
        return IncrementalFactorResult(values=values, state=state, timestamp=timestamp)

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {
            'lookback': self._lookback,
            'volnorm': self._volnorm,
            'vol_method': self._vol_method,
            'vol_lookback': self._vol_lookback,
            'vol_min_periods': self._vol_min_periods,
        }


type _RY_COMBO = Literal[
    'near_next',        # 近月 + 次近月
    'near_dominant',    # 近月 + 主力
    'near_farthest',    # 近月 + 最远月
    'dominant_secondary',  # 主力 + 次主力
]


class RollYieldFactor(Factor):
    """展期收益率因子。

    公式：(p_near / p_far - 1) / (DTE_far - DTE_near) * 365。

    Parameters
    ----------
    combination : _RY_COMBO, default 'near_dominant'
        近远月合约的选择方式。
    near_dte_min, far_dte_max : int
        参与计算的合约到期天数区间。
    price_col : {'Close', 'Settle'}, default 'Close'
        计算展期收益率所使用的价格列。
    volnorm : bool, default True
        是否使用主连 ``True_Return`` 的滚动波动率做结构化归一；
        开启时必须同时提供 ``main_panel`` 数据源。
    vol_method : {'std'}, default 'std'
        波动率估计方法，目前仅支持滚动标准差。
    vol_lookback : int or None
        波动率滚动窗口；``volnorm=True`` 时必填且不小于 2。
    vol_min_periods : int or None
        波动率滚动窗口的最小样本数；``volnorm=True`` 时必填且不小于 2。

    Notes
    -----
    结构化归一公式： ``年化 roll_yield / (rolling_std(True_Return) * sqrt(252))``。
    年化 roll_yield 的年化参数固定为 365 ，日频波动率的年化参数固定为 252。
    """

    def __init__(self, combination: _RY_COMBO = 'near_dominant',
                 near_dte_min: int = 0, far_dte_max: int = 365,
                 price_col: Literal['Close', 'Settle'] = 'Close',
                 volnorm: bool = True, *,
                 vol_method: Literal['std'] = 'std',
                 vol_lookback: int | None = None,
                 vol_min_periods: int | None = None):
        self.combination = combination
        self.near_dte_min = near_dte_min
        self.far_dte_max = far_dte_max
        self.price_col = price_col
        if volnorm is True:
            if not (isinstance(vol_lookback, int) and vol_lookback >= 2):
                raise ValueError(
                    '当 volnorm 为 True 时，必须传入大于等于 2 的 '
                    'vol_lookback 参数。')
            if not (isinstance(vol_min_periods, int) and vol_min_periods >= 2):
                raise ValueError(
                    '当 volnorm 为 True 时，必须传入大于等于 2 的 '
                    'vol_min_periods 参数。')
        self._volnorm = volnorm
        self._vol_lookback = vol_lookback
        self._vol_min_periods = vol_min_periods
        self._vol_method: Literal['std'] = vol_method
        self.warnings: list[str] = []

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {
            'combination':  self.combination,
            'near_dte_min': self.near_dte_min,
            'far_dte_max':  self.far_dte_max,
            'price_col':    self.price_col,
            'volnorm':      self._volnorm,
            'vol_method':   self._vol_method,
            'vol_lookback': self._vol_lookback,
            'vol_min_periods': self._vol_min_periods,
        }
    
    @property
    def name(self) -> str:
        name = f'roll_yield_{self.combination}'
        if self._volnorm:
            name += '_volnorm'
        return name

    @property
    def computation_scope(self) -> Literal['per_product']:
        return 'per_product'

    @property
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        requirements = [
            DataRequirement(
                source='term_structure',
                columns=(self.price_col, 'last_trade_date',
                         'is_dominant', 'OpenInterest', 'Volume'),
                request_params={'price_col': self.price_col},
            ),
        ]
        if self._volnorm:
            requirements.append(
                DataRequirement(source='main_panel', columns=('True_Return',))
            )
        return tuple(requirements)

    @property
    def warmup_periods(self) -> None:
        """暖机窗口，对展期因子不适用。"""
        return None

    def compute(self, data: Mapping[str, pd.DataFrame] | pd.DataFrame) -> pd.Series:
        """
        批量计算接口。

        Parameters
        ----------
        data: Mapping[str, pd.DataFrame] or pd.DataFrame
            - Mapping: 必须包含 ``'term_structure'``；当 ``volnorm=True`` 时
              还必须包含 ``'main_panel'``（含 ``True_Return`` 列）。
            - pd.DataFrame: 直接传入期限结构数据，仅在 ``volnorm=False`` 时可用。
            期限结构索引为 MultiIndex[timestamp, product, contract]。

        Returns
        -------
        pd.Series
            因子值Series。MultiIndex[timestamp, product]
        """
        self.warnings.clear() # 每次批量计算前清空上一轮累积的警告

        if isinstance(data, Mapping):
            if 'term_structure' not in data:
                raise ValueError(
                    "输入为 Mapping 时必须包含 'term_structure' 数据源。")
            term_df = data['term_structure'].copy()
            panel = data.get('main_panel')
        else:
            term_df = data.copy()
            panel = None

        if self._volnorm:
            if not isinstance(panel, pd.DataFrame):
                raise ValueError(
                    'volnorm=True 时需要同时提供 main_panel 数据源'
                    "（含 'True_Return' 列），请通过 data_requirements 取数后"
                    '以 Mapping 形式传入。')
            if 'True_Return' not in panel.columns:
                raise ValueError(
                    "main_panel 缺少 'True_Return' 列，无法进行波动率归一。")

        term_df['days_to_last_trade'] = (
            (pd.to_datetime(term_df['last_trade_date']) 
            - term_df.index.get_level_values('timestamp'))
            .dt.days # pyright: ignore[reportAttributeAccessIssue]
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
                rst_dict[(ts,prod)] = np.nan
                continue

            roll_yield = self._compute_roll_yield(df, near, far)
            rst_dict[(ts,prod)] = roll_yield

        idx = pd.MultiIndex.from_tuples(rst_dict.keys(), names=INDEX_NAMES)
        values = pd.Series(rst_dict.values(), index=idx, name=self.name) # type: ignore

        if self._volnorm:
            assert isinstance(panel, pd.DataFrame)
            assert self._vol_lookback is not None
            assert self._vol_min_periods is not None
            vol = rolling_volatility(
                panel['True_Return'], method=self._vol_method,
                window=self._vol_lookback,
                min_periods=self._vol_min_periods)
            # 展期收益率已按 365 日年化；用 sqrt(252) 把日频波动率换算到
            # 同一时间尺度，使归一后的因子成为无量纲信号。
            denom = vol * np.sqrt(252.0)
            denom = denom.where(denom > 0) # 零波动率置 NaN，避免 inf
            values = values / denom.reindex(values.index)
            values.name = self.name

        if self.warnings: # 汇总提示，避免刷屏；具体明细见 self.warnings
            logger.warning(
                f'【{self.name}】本次计算共记录 {len(self.warnings)} 条警告，'
                f'明细见该因子的 warnings 属性。')
        return values

    def _compute_roll_yield(self, df: pd.DataFrame, near: str, far: str) -> float:
        """根据近月合约和远月合约计算年化展期收益率。
        公式：( p_{near} / p_{far} -1 ) / (DTE_{far} - DTE_{near}) * 365
        """
        near_price = df.at[near, self.price_col]
        far_price  = df.at[far,  self.price_col]
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
        出错条件：当期无主力；近月=主力；找不到次主力（到期日晚于主力，持仓量小于等于主力）。
        """
        def has_dominant(df: pd.DataFrame) -> bool:
            """判断当前截面是否存在主力合约。"""
            return bool(df['is_dominant'].any())

        assert len(df) > 1 # 该方法必须要求至少两个合约

        df = df.copy() 
        df['lifespan_rank'] = df['days_to_last_trade'].rank()

        if self.combination == 'near_next':
            near = df.index[df['lifespan_rank'] == 1][0]
            far  = df.index[df['lifespan_rank'] == 2][0]

        elif self.combination == 'near_dominant':
            if not has_dominant(df):
                return None, None, 'error: 当期无主力'
            near = df.index[df['lifespan_rank'] == 1][0]
            far  = df.index[df['is_dominant']][0]
            if near == far: # 近月合约就是主力，此时近月=远月
                return None, None, 'error: 近月合约就是主力'
            
        elif self.combination == 'near_farthest':
            near = df.index[df['lifespan_rank'] == 1][0]
            far = df.index[df['lifespan_rank'] == df['lifespan_rank'].max()][0]

        elif self.combination == 'dominant_secondary': 
            # 选出到期日晚于主力，持仓量小于等于主力的合约中，持仓量最大的那张
            if not has_dominant(df):
                return None, None, 'error: 当期无主力'
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

        return near, far, '' # type: ignore

class CurveSlope(Factor):
    """
    全曲线斜率因子，机制：利用全部活跃合约估计 backwardation/contango。
    公式：log P_i = a + b d_i / 365 加权回归，F = -b；
    其中 d 为 DTE，回归权重为 OpenInterest 或 Volume。
    方向：backwardation 越强越多头。
    数据需求：全合约价格、DTE、OI/Volume。

    Parameters
    ----------
    weight_var : Literal['OpenInterest', 'Volume'], default 'OpenInterest'
        回归加权所用的权重变量。
    price_col : Literal['Close', 'Settle'], default 'Close'
        估计曲线所使用的价格列。
    contract_filter_rule : Mapping[str, int] or None, optional
        合约筛选规则，包含 ``min_dte``（剔除临近到期合约）、
        ``max_dte``（剔除过远合约）、``min_volume``（流动性约束）；
        None 使用默认值 ``{'min_dte': 3, 'max_dte': 300, 'min_volume': 100}``。
    min_contract_fit : int, default 3
        估计曲线所需的最少合约数。

    Note
    ---
    是为``RollYieldFactor`` 的扩展。
    """
    def __init__(
            self, 
            weight_var: Literal['OpenInterest', 'Volume'] = 'OpenInterest',
            price_col: Literal['Close', 'Settle'] = 'Close',
            contract_filter_rule: Mapping[str, int] | None = None,
            min_contract_fit: int = 3,
    ):
        default_rule = {'min_dte': 3, 'max_dte': 300, 'min_volume': 100}
        rule = contract_filter_rule or default_rule
        self._weight_var = weight_var
        self._price_col = price_col
        self._rule = rule
        self._min_contract = min_contract_fit

    @property
    def name(self) -> str:
        return (
            f'curve_slope_{self._weight_var}_{self._price_col}'
            f'_dte{self._rule["min_dte"]}-{self._rule["max_dte"]}'
            f'_vol{self._rule["min_volume"]}_n{self._min_contract}'
        )

    @property
    def computation_scope(self) -> Literal['per_product']:
        return 'per_product'

    @property
    def parameters(self) -> Mapping[str, Any]:
        return {
            'weight_var': self._weight_var,
            'price_col': self._price_col,
            'contract_filter_rule': dict(self._rule),
            'min_contract_fit': self._min_contract
        }

    @property
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        req = DataRequirement(
            source='term_structure',
            columns=(self._price_col, 'last_trade_date', 'OpenInterest', 'Volume'),
            request_params={'price_col': self._price_col}
        )
        return (req,)

    def _reg_beta(self, data: pd.DataFrame) -> float:
        """估计有效合约截面的年化对数价格斜率。"""
        if len(data) < self._min_contract:
            return np.nan
        if data['dte'].nunique() < 2:
            return np.nan
        y = np.log(data[self._price_col].to_numpy())
        X = data['dte'] / 365.0
        X = sm.add_constant(X, has_constant='add')
        w = data[self._weight_var].to_numpy()
        res = sm.WLS(y, X, weights=w).fit() # pyright: ignore[reportArgumentType]
        params: pd.Series = res.params
        return float(params['dte'])

    def _filter_contract(self, data: pd.DataFrame) -> pd.DataFrame:
        """剔除无法参与曲线拟合或不满足期限、流动性条件的合约。"""
        mask_invalid = (
            (~np.isfinite(data[self._price_col])) |
            (~np.isfinite(data['dte'])) |
            (~np.isfinite(data[self._weight_var])) |
            (~np.isfinite(data['Volume'])) |
            (data[self._price_col] <= 0) |
            (data[self._weight_var] <= 0) |
            (data['dte'] < self._rule['min_dte']) |
            (data['dte'] > self._rule['max_dte']) | 
            (data['Volume'] < self._rule['min_volume']) 
        )
        return data[~mask_invalid]

    def compute(self, data: pd.DataFrame) -> pd.Series:
        """
        批量计算全曲线斜率因子。

        Parameters
        ------
        data: pd.DataFrame
            期限结构数据。 MultiIndex[timestamp, product, contract]
            columns: Close/Settle, last_trade_date, OpenInterest, Volume
        
        Returns
        ------
        pd.Series
            因子值。 MultiIndex[timestamp, product]
        """
        assert pd.Index(
            [self._price_col, 'last_trade_date', 'OpenInterest', 'Volume']
        ).isin(data.columns).all()
        assert list(data.index.names) == ['timestamp', 'product', 'contract']

        term_df = data.copy()

        term_df['dte'] = (
            (pd.to_datetime(term_df['last_trade_date']) 
            - term_df.index.get_level_values('timestamp'))
            .dt.days # pyright: ignore[reportAttributeAccessIssue]
        )
        term_df = self._filter_contract(term_df)
        if term_df.empty:
            raise ValueError(f'按规则剔除合约后，得到空 DataFrame，无法继续计算。')
        
        inverse_slope_series = - term_df.groupby(level=('timestamp', 'product'))\
            .apply(self._reg_beta).rename(self.name)
        return inverse_slope_series
    
# ===================================
# inline smoke test
# ===================================
if __name__ == '__main__':
    from data import RolloverConfig, AdjustmentConfig, SamplingConfig, EnvironmentConfig
    from data import DataFacade
    from factors.models import UniverseSpec, DatasetSpec
    from factors.dataset import ResearchDatasetBuilder

    roll_config = RolloverConfig()
    adj_config = AdjustmentConfig()
    sample_config = SamplingConfig()
    env_config = EnvironmentConfig()
    facade_config = (roll_config, adj_config, sample_config, env_config)

    facade = DataFacade(*facade_config)

    products = facade.rule_query.get_product_universe(return_type='list')
    products = [x for x in products if x not in ('L_F', 'PP_F', 'V_F')]

    slope_factor = CurveSlope()
    data_req = slope_factor.data_requirements

    uni_spec = UniverseSpec(min_average_volume=10000)

    start_date = date(2000, 1, 1)
    end_date = date(2026, 12, 31)
    data_spec = DatasetSpec(facade_config=facade_config,products=tuple(products),
                            data_requirements=data_req, universe=uni_spec,
                            start_date=start_date, end_date=end_date)

    builder = ResearchDatasetBuilder(spec=data_spec)
    dataset = builder.build()  

    # compute factor values 
    assert dataset.auxiliary is not None
    values = slope_factor.compute(data=dataset.auxiliary['term_structure'])
    print('head of factor values: \n', values.head(10).to_string())
