"""因子合成组件。"""
from __future__ import annotations
from abc import ABC, abstractmethod
import pandas as pd
import numpy as np
from typing import TYPE_CHECKING, Literal, Any
import logging

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from factors.models import LabelResult
    from factors.universe import Universe

type MissingPolicy = Literal['strict', 'renormalize']

def validate_inputs(
        *factors: pd.Series,
        label: pd.Series | None = None
    ) -> None:
    """校验输入是否合法。"""
    if len(factors) < 2:
        raise ValueError(f'输入的因子数量必须大于等于2。')
    names = [f.name for f in factors]
    if len(names) != len(set(names)):
        raise ValueError('不允许因子名称出现重复。')
    for f in factors:
        if not isinstance(f, pd.Series):
            raise TypeError('输入的 *factors 必须全部为 pandas Series。'
                             f'现在为 {type(f)}。')
        if np.isinf(f).any():
            raise ValueError(f'因子 {f.name} 有不被允许的 inf。')
        if list(f.index.names) != ['timestamp', 'product']:
            raise ValueError(f'因子 {f.name} 的索引名称为 {f.index.names}，'
                             '必须设置为 ["timestamp", "product"]')
    if label is not None:
        if np.isinf(label).any():
            raise ValueError('标签中存在不允许的 inf。')

def align_inputs(*factors: pd.Series, 
                 label: pd.Series | None = None,
                 order: list[str] | None = None) -> pd.DataFrame:
    """将多个因子和标签（若有）拼接为一个 DataFrame 返回，可选根据 order 对因子列排序。"""
    df =  pd.concat(factors, axis=1, join='outer')
    df = df[order] if order is not None else df
    if label is not None:
        df = df.join(label.rename('label'), how='left')
    return df

class EqualWeightCombiner:
    """
    等权合成算子。
    
    Parameters
    ------
    missing_policy: {'strict', 'renormalize'}
        因子合成时，成分缺失的处理： 'strict' 下缺失任一成分缺失，复合因子即缺失；
        'renormalize' 下忽略缺失成分，并重新归一化。默认为 'strict'。
    
    """

    def __init__(
            self,
            missing_policy: MissingPolicy = 'strict'
    ): 
        assert missing_policy in {'strict', 'renormalize'}
        self._missing_policy = missing_policy

    def transform(self, *factors: pd.Series,) -> pd.Series:
        """使用等权法批量合成因子。
        
        Parameters
        ------
        *factors: pd.Series
            多个因子（大于等于2个）。
        
        Returns
        ------
        pd.Series
            复合因子，名称为 'composite_factor'。
        """
        validate_inputs(*factors)
        data = align_inputs(*factors)
        composite_factor = data.mean(
            axis=1,
            skipna=True if self._missing_policy == 'renormalize' else False
            ).rename('composite_factor')
        return composite_factor

    @property
    def config(self) -> dict[str, Any]:
        return {'missing_policy': self._missing_policy}

class ICWeightedCombiner:
    """IC/ICIR 加权合成算子。
    
    Parameters
    ------
    mode: {'cross_sectional', 'time_series'}
        截面或时序模式，必填。
    method: {'ic', 'icir'}, optional
        拟合权重的方法。默认为 'ic'。
    correlation: {'pearson', 'rank'}, optional
        计算 Pearson IC 或 RankIC， 默认 'pearson'。
    min_observations: int, optional
        拟合过程中，计算IC时所需最小观测值数。默认为 2。
    min_valid_groups: int, optional
        拟合过程中，跨时期或跨品种计算IC均值时，所需最少组数。默认为 2。
    missing_policy: {'strict', 'renormalize'}, optional
        因子合成时，成分缺失的处理： 'strict' 下缺失任一成分缺失，复合因子即缺失；
        'renormalize' 下忽略缺失成分，并重新归一化。默认为 'strict'。
    
    Methods
    ------
    fit(*training_factors, label)
        拟合训练期因子的IC或RankIC。
    transform(*factors)
        使用IC加权法，批量合成多个因子。
    
    """
    

    def __init__(
            self, 
            mode: Literal['cross_sectional', 'time_series'],
            method: Literal['ic', 'icir'] = 'ic',
            correlation: Literal['pearson', 'rank'] = 'pearson',
            min_observations: int = 2, # 拟合过程中，计算IC时，所需最小观测值数
            min_valid_groups: int = 2, # 拟合过程中，跨时期或跨品种计算IC均值时，所需最少组数
            missing_policy: MissingPolicy = 'strict', # 因子合成时的缺失成分处理政策
    ):
        
        assert mode in {'cross_sectional', 'time_series'}
        assert method in {'ic', 'icir'}
        assert correlation in {'pearson', 'rank'}
        assert min_observations >= 2 and isinstance(min_observations, int)
        assert isinstance(min_valid_groups, int) and \
            (min_valid_groups >= 1 if method == 'ic' else 2)
        assert missing_policy in {'strict', 'renormalize'}

        self._mode = mode
        self._method = method
        self._correlation = correlation
        self._ic_stat_name = 'IC' if self._method == 'ic' else 'ICIR'
        self._min_obs = min_observations
        self._min_groups = min_valid_groups
        self._missing_policy = missing_policy

        self._fitted: dict[str, float] = {}

    def _corr(self, df: pd.DataFrame) -> float:
        """计算单个因子的组内 IC 或 RankIC。
        截面模式下，组是某一个时期（计算跨品种的因子和标签相关系数），
        时序模式下，组是某一个品种（计算跨时间的因子和标签相关系数）。"""
        corr_df = df.corr(
            method='pearson' if self._correlation == 'pearson' else 'spearman',
            min_periods=self._min_obs
        )
        return corr_df.iloc[0, 1]  # pyright: ignore[reportReturnType]

    def fit(
            self, 
            *training_factors: pd.Series,
            label: pd.Series
    ) -> ICWeightedCombiner:
        """
        拟合训练期因子的IC或RankIC。
        
        Parameters
        ------
        *training_factors: pd.Series
            训练期的多个因子。调用方需要先应用品种池，将池外的品种因子值设为 NaN。
        label: pd.Series
            训练期标签。调用方需确认在训练截止日期，标签已成熟可观测。
        
        Returns
        ------
        self
            返回自身。
        
        """
        validate_inputs(*training_factors, label=label)

        self._fitted.clear() 

        df = align_inputs(*training_factors, label=label.rename('label'))
        df = df.dropna() # 仅在所有因子的共同有效样本上做拟合
        if df.empty:
            raise ValueError(
                f'每个时期剔除因子或标签的缺失值后，得到空 DataFrame，无法拟合。'
            )
        
        for f in training_factors:
            subdf = df[[f.name, 'label']]
            if self._mode == 'cross_sectional':
                ic_series = subdf.groupby(level='timestamp').apply(self._corr)
            else: # 'time_series'
                ic_series = subdf.groupby(level='product').apply(self._corr)
            n_valid = ic_series.count()
            if n_valid < self._min_groups:
                raise ValueError(
                    f'有效{self._ic_stat_name}'
                    f'数量为 {n_valid}，小于最低要求 {self._min_groups}，拟合失败。')
            if self._method == 'ic':
                q = ic_series.mean()
            else: # 'icir'
                q = ic_series.mean()/ic_series.std()
            
            # 检查 q 是否小于 0 ，或者为非有限值
            if q < 0:
                logger.warning(f'因子 {f.name} 的 {self._ic_stat_name} 为 {q}，'
                               '方向是否未对齐？')
            if np.isinf(q):
                raise ValueError(f'因子 {f.name} 的 {self._ic_stat_name} 为 inf，'
                                 '拟合失败。')
            
            self._fitted[f.name] = q # pyright: ignore[reportArgumentType] 

        if all([v < 0 for v in self._fitted.values()]):
            raise ValueError(f'所有因子的 {self._ic_stat_name} 都为负，拟合失败。')
        return self

    def _check_is_fitted(self) -> None:
        if not self._fitted:
            raise ValueError(f'未执行拟合，无因子权重。')

    @property
    def weights_(self) -> dict[str, float]:
        """返回 {因子名称: 权重} 的字典。"""
        scores = self.score_
        weights = {}
        total = sum([max(0, val) for val in scores.values()])
        for name, val in scores.items():
            weights[name] = max(0, val) / total
        return weights

    @property
    def score_(self) -> dict[str, float]:
        """返回 {因子名称：ic或icir} 的字典。"""
        self._check_is_fitted()
        return self._fitted.copy()
        
    def transform(
            self, 
            *factors: pd.Series,
    ) -> pd.Series:
        """使用IC加权法，批量合成多个因子。"""
        self._check_is_fitted()
        if len(factors) != len(self._fitted):
            raise ValueError(f'参与合成的因子数量为 {len(factors)}，'
                             f'参与拟合的因子数量为 {len(self._fitted)}，两者不一致。')

        # 将参与拟合的成分拼接起来，同时将列排序对齐到拟合成分的排序
        factor_df = align_inputs(*factors, order=list(self._fitted))
        
        # 先制作一个和 factor_df 相同shape 的 weight matrix,
        # 并将 factor_df 中为 NaN 的对应位置设为 NAN，随后按行做 renormalization
        w_arr = np.array(list(self.weights_.values()), dtype=float)
        w_mat = pd.DataFrame(
            np.repeat(w_arr[np.newaxis, :], len(factor_df), axis=0),
            columns=factor_df.columns,
            index=factor_df.index
        ) 
        w_mat[factor_df.isna()] = np.nan
        row_total = w_mat.sum(axis=1, skipna=True, min_count=1) # min_count = 1 => 某一期全部成分缺失，则 row_total 该行等于 NaN
        w_mat_norm = w_mat.div(row_total, axis=0)

        if self._missing_policy == 'strict':
            return (factor_df * w_mat_norm).sum(axis=1, skipna=False)\
            .rename('composite_factor')
        return (factor_df * w_mat_norm).sum(axis=1, skipna=True, min_count=1)\
            .rename('composite_factor') # min_count = 1 => 某一期全部成分缺失，则复合因子取值为 NaN

    @property
    def config(self) -> dict[str, Any]:
        return {
            'mode': self._mode,
            'method': self._method,
            'correlation': self._correlation,
            'min_observations': self._min_obs,
            'min_valid_groups': self._min_groups,
            'missing_policy': self._missing_policy
        }

class RankCombiner:
    """截面模式下的排序合成。"""
    def __init__(self, missing_policy: MissingPolicy = 'strict',
                 mode: Literal['cross_sectional'] = 'cross_sectional'):
        assert missing_policy in {'strict', 'renormalize'}
        self._missing_policy = missing_policy
        self._mode = mode

    def _rank(self, values: pd.Series) -> pd.Series:
        """计算排名映射：
        q_i = 2(rank_i -1) / (N - 1) -1，即映射到 [-1, 1]，
        其中 rank_i 为升序平均秩。
        """
        N = values.dropna().size
        if N > 1:
            q_values = 2 * (values.rank(method='average') -1) / (N - 1) - 1
        else: # 若截面上有效品种小于2个，输出全 NaN 排名
            q_values = pd.Series(np.nan, index=values.index)
        return q_values

    def transform(self, *factors: pd.Series) -> pd.Series:
        """使用 Rank 法批量合成多个因子。"""
        validate_inputs(*factors)
        factor_df = align_inputs(*factors)

        rank_df: pd.DataFrame = factor_df.groupby(level='timestamp').transform(self._rank)  # type: ignore
        return rank_df.mean(
            axis=1, 
            skipna=True if self._missing_policy == 'renormalize' else False
        ).rename('composite_factor')

    @property
    def config(self) -> dict[str, Any]:
        return {'missing_policy': self._missing_policy, 
                'mode': self._mode}

class VoteCombiner:
    """时序模式下的投票合成。"""
    def __init__(
            self, 
            missing_policy: Literal['strict', 'abstain'],
            mode: Literal['time_series'] = 'time_series',
            threshold: float = 0.0
    ):
        assert missing_policy in {'strict', 'abstain'}
        assert threshold >= 0
        self._missing_policy = missing_policy
        self._threshold = threshold
        self._mode = mode

    def _vote(self, value: float | int) -> int | None:
        if value > self._threshold: return 1
        elif value < -self._threshold: return -1
        return 0

    def transform(self, *factors: pd.Series) -> pd.Series:
        """使用投票法批量合成多个因子。"""
        validate_inputs(*factors)
        factor_df = align_inputs(*factors)

        vote_df = factor_df.map(self._vote, na_action='ignore')
        return vote_df.mean(
            axis=1, 
            skipna=True if self._missing_policy == 'abstain' else False
        ).rename('composite_factor')

    @property
    def config(self) -> dict[str, Any]:
        return {'missing_policy': self._missing_policy,
                'mode': self._mode,
                'threshold': self._threshold}

class RidgeRegressionCombiner:
    """岭回归合成。"""
    ...

# ==================================================
# inline test
# ==================================================
if __name__ == '__main__':
    rng = np.random.default_rng(20260721)

    # ---------------- 构造测试数据：3 个品种 × 30 个 timestamp ----------------
    n_ts = 30
    products = ['RB', 'CU', 'AL']
    ts = pd.date_range('2026-01-05', periods=n_ts, freq='B')
    index = pd.MultiIndex.from_product([ts, products], names=['timestamp', 'product'])

    # 品种层面的截面效应（每个 timestamp 相同），用于制造稳定的截面相关
    product_effect = pd.Series({'RB': 1.0, 'CU': 0.0, 'AL': -1.0})
    effect = product_effect.reindex(index.get_level_values('product')).to_numpy()

    def make_factor(name: str, loading: float, noise_scale: float) -> pd.Series:
        """按 品种效应 × 载荷 + 噪声 生成因子。"""
        return pd.Series(
            loading * effect + rng.normal(scale=noise_scale, size=len(index)),
            index=index, name=name
        )

    factor_1 = make_factor('factor_1', 1.0, 0.1)    # 与标签强正相关
    factor_2 = make_factor('factor_2', 0.6, 0.3)    # 与标签弱正相关
    factor_3 = make_factor('factor_3', -1.0, 0.1)   # 与标签负相关，用于验证方向 warning
    label = pd.Series(effect + rng.normal(scale=0.1, size=len(index)),
                      index=index, name='forward_return')

    assert len(index) == n_ts * len(products) == 90

    # ---------------- EqualWeightCombiner：strict 无缺失 ----------------
    print('---------- EqualWeightCombiner: strict（无缺失） ----------')
    composite_ew = EqualWeightCombiner().transform(factor_1, factor_2, factor_3)
    expected = (factor_1 + factor_2 + factor_3) / 3
    assert composite_ew.name == 'composite_factor', composite_ew.name
    assert list(composite_ew.index.names) == ['timestamp', 'product']
    assert composite_ew.index.equals(expected.index)
    assert np.allclose(composite_ew.to_numpy(), expected.to_numpy(), equal_nan=True)
    print(composite_ew.head(3))

    # ---------------- EqualWeightCombiner：缺失值两种策略 ----------------
    print('---------- EqualWeightCombiner: strict vs renormalize（含缺失） ----------')
    factor_2_missing = factor_2.copy()
    factor_2_missing.iloc[0] = np.nan  # 首个 (timestamp, product) 位置缺失

    strict_res = EqualWeightCombiner(missing_policy='strict').transform(
        factor_1, factor_2_missing, factor_3)
    renorm_res = EqualWeightCombiner(missing_policy='renormalize').transform(
        factor_1, factor_2_missing, factor_3)

    assert np.isnan(strict_res.iloc[0]), strict_res.iloc[0]
    assert strict_res.notna().sum() == len(strict_res) - 1
    assert np.isclose(renorm_res.iloc[0],
                      (factor_1.iloc[0] + factor_3.iloc[0]) / 2)
    assert renorm_res.notna().all()
    print(f'strict 首行: {strict_res.iloc[0]}，renormalize 首行: {renorm_res.iloc[0]}')


    # ---------------- ICWeightedCombiner：默认参数（mode 为必填项，其余全部默认） ----------------
    print('---------- ICWeightedCombiner: 默认参数 ----------')
    ic = ICWeightedCombiner(mode='cross_sectional')

    # 未拟合时 transform 应报错
    try:
        ic.transform(factor_1, factor_2)
    except ValueError as e:
        print(f'未拟合调用 transform 正确抛出 ValueError: {e}')
    else:
        raise AssertionError('未拟合时 transform 应抛出 ValueError')

    ic.fit(factor_1, factor_2, factor_3, label=label)
    print(f'IC 得分: {ic.score_}')
    print(f'权重: {ic.weights_}')

    # factor_1/2 的 IC 为正、factor_3 为负 → 负权重被截断为 0，权重和为 1
    assert set(ic.weights_) == {'factor_1', 'factor_2', 'factor_3'}
    assert ic.weights_['factor_3'] == 0.0, ic.weights_
    assert all(w >= 0 for w in ic.weights_.values()), ic.weights_
    assert np.isclose(sum(ic.weights_.values()), 1.0), ic.weights_
    assert ic.weights_['factor_1'] > 0 and ic.weights_['factor_2'] > 0

    composite_ic = ic.transform(factor_1, factor_2, factor_3)
    assert composite_ic.name == 'composite_factor', composite_ic.name
    assert list(composite_ic.index.names) == ['timestamp', 'product']
    assert composite_ic.index.equals(composite_ew.index)
    assert composite_ic.notna().all()
    assert not np.allclose(composite_ic.to_numpy(), composite_ew.to_numpy())
    print(composite_ic.head(3))

    print(ic.config)

    # ---------------- RankCombiner：截面排序合成（默认参数） ----------------
    print('---------- RankCombiner: 截面排序合成 ----------')
    factor_df = pd.concat([factor_1, factor_2, factor_3], axis=1)
    rank = RankCombiner()  # 默认 missing_policy='strict'
    composite_rank = rank.transform(factor_1, factor_2, factor_3)

    # 逐 timestamp、逐因子手工复算：q = 2(rank-1)/(N-1) - 1，再按行取均值
    n_products = len(products)
    manual_rank = factor_df.groupby(level='timestamp').rank(method='average')
    q_df = 2 * (manual_rank - 1) / (n_products - 1) - 1
    expected_rank = q_df.mean(axis=1)

    assert composite_rank.name == 'composite_factor', composite_rank.name
    assert list(composite_rank.index.names) == ['timestamp', 'product']
    assert composite_rank.index.equals(expected_rank.index)
    assert np.allclose(composite_rank.to_numpy(), expected_rank.to_numpy(),
                       equal_nan=True)
    assert composite_rank.between(-1, 1).all(), \
        (composite_rank.min(), composite_rank.max())
    # 3 个品种 → 每个 timestamp 内每个因子的 q 恰为 {-1, 0, 1}
    assert set(np.unique(q_df.to_numpy())) == {-1.0, 0.0, 1.0}, \
        np.unique(q_df.to_numpy())
    # factor_1 的截面排序与品种效应一致：RB 最高、AL 最低
    assert q_df.loc[(ts[0], 'RB'), 'factor_1'] == 1.0
    assert q_df.loc[(ts[0], 'AL'), 'factor_1'] == -1.0
    print(composite_rank.head(3))

    # 缺失值：strict 该行 NaN；renormalize 用剩余成分的 q 均值
    rank_strict = RankCombiner(missing_policy='strict').transform(
        factor_1, factor_2_missing, factor_3)
    rank_renorm = RankCombiner(missing_policy='renormalize').transform(
        factor_1, factor_2_missing, factor_3)
    first_row = factor_1.index[0]
    assert np.isnan(rank_strict.loc[first_row]), rank_strict.loc[first_row]
    assert rank_strict.notna().sum() == len(rank_strict) - 1
    assert np.isclose(rank_renorm.loc[first_row],
                      (q_df.loc[first_row, 'factor_1']
                       + q_df.loc[first_row, 'factor_3']) / 2)
    assert rank_renorm.notna().all()
    print(f'strict 首行: {rank_strict.loc[first_row]}，'
          f'renormalize 首行: {rank_renorm.loc[first_row]}')

    # ---------------- VoteCombiner：时序投票合成 ----------------
    print('---------- VoteCombiner: 时序投票合成 ----------')
    vote = VoteCombiner(missing_policy='strict')  # 默认 mode='time_series'、threshold=0.0
    composite_vote = vote.transform(factor_1, factor_2, factor_3)

    expected_vote = np.sign(factor_df).mean(axis=1)  # threshold=0 => 等价于点符号
    assert composite_vote.name == 'composite_factor', composite_vote.name
    assert list(composite_vote.index.names) == ['timestamp', 'product']
    assert np.allclose(composite_vote.to_numpy(), expected_vote.to_numpy())
    assert composite_vote.between(-1, 1).all()
    # 3 个因子 → 均值只能是 {-1, -2/3, -1/3, 0, 1/3, 2/3, 1}
    allowed_votes = {round(k / 3, 10) for k in range(-3, 4)}
    assert {round(v, 10) for v in composite_vote.unique()} <= allowed_votes, \
        composite_vote.unique()
    print(composite_vote.head(3))

    # threshold=0.5：仅 |因子值| > 0.5 才投 ±1，否则投 0（中性）
    vote_thr = VoteCombiner(missing_policy='strict', threshold=0.5)
    composite_vote_thr = vote_thr.transform(factor_1, factor_2, factor_3)
    expected_vote_thr = (np.sign(factor_df) * (factor_df.abs() > 0.5)).mean(axis=1)
    assert np.allclose(composite_vote_thr.to_numpy(),
                       expected_vote_thr.to_numpy())
    print(composite_vote_thr.head(3))

    # 缺失值：strict 该行 NaN；abstain 用剩余成分的票均值
    vote_strict = VoteCombiner(missing_policy='strict').transform(
        factor_1, factor_2_missing, factor_3)
    vote_abstain = VoteCombiner(missing_policy='abstain').transform(
        factor_1, factor_2_missing, factor_3)
    assert np.isnan(vote_strict.loc[first_row]), vote_strict.loc[first_row]
    assert vote_strict.notna().sum() == len(vote_strict) - 1
    expected_abstain = (np.sign(factor_1.loc[first_row])
                        + np.sign(factor_3.loc[first_row])) / 2
    assert np.isclose(vote_abstain.loc[first_row], expected_abstain), \
        (vote_abstain.loc[first_row], expected_abstain)
    assert vote_abstain.notna().all()
    print(f'strict 首行: {vote_strict.loc[first_row]}，'
          f'abstain 首行: {vote_abstain.loc[first_row]}')

    print(rank.config)
    print(vote.config)

    print('全部 inline test 通过。')
