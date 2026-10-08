from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Any, Literal
import numpy as np
import pandas as pd
from dataclasses import dataclass


if TYPE_CHECKING:
    from factors.diagnostic_portfolio.weights import WeightMeta
    from factors.diagnostic_portfolio.models import CostConfig

@dataclass(frozen=True)
class DecisionRecord:
    timestamp: pd.Timestamp
    weights: pd.Series
    rebalance: bool
    weight_warnings: tuple[str, ...]
    weight_meta: WeightMeta

class DecisionLedger:

    def __init__(self):
        self._book: list[DecisionRecord] = []

    def append(self, record: DecisionRecord) -> None:
        self._book.append(record)

    def to_weights_series(self,) -> pd.Series:
        """返回所记录的权重数据。
        返回Series, Index[timestamp, product]
        """
        return pd.concat(
            [record.weights for record in self._book],
            keys=[record.timestamp for record in self._book], # 在最外侧新增一层索引
            axis=0
        ).rename('weight').rename_axis(['timestamp', 'product'])

    @property
    def warnings(self, ) -> tuple[str,...]:
        """返回所记录的警告。"""
        warnings: list[str] = []
        for record in self._book:
            if record.weight_warnings:
                _text = f'{record.timestamp.isoformat()} : ' + \
                f'{"；".join(record.weight_warnings)}' # looks like： 2025-01-02T00:00:00 : 警告1；警告2；警告3
                warnings.append(_text)
        return tuple(warnings)

    @property
    def metadata(self) -> dict[str, Any]:
        """返回所记录的metadata。

        
        Returns
        ------
        Mapping[str, Any]
            包含的元素如下：
            - 'covariance_estimation': pd.DataFrame(Index[timestamp]) or None, 

        """
        re_dict = {}

        # 1. weight_meta
        rows: list[pd.DataFrame] = []
        for record in self._book:
            m = record.weight_meta.cov_mat_estimation_meta
            if isinstance(m, pd.Series):
                row = m.rename(record.timestamp).to_frame().T
                rows.append(row)
        if rows:
            cov_est_meta_df = pd.concat(rows, axis=0)
        else:
            cov_est_meta_df = None
        re_dict['covariance_estimation'] = cov_est_meta_df

        return re_dict

def compute_weight_changes_turnover_cost(
        weights: pd.Series, cost_config: CostConfig) \
    -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    计算权重变化+换手+决策成本（换手成本），使用决策时间轴（尚未与收益实现时间对齐）。

    Parameters
    -------
    weights: pd.Series
        Index[timestamp, product]

    Returns
    ------
    tuple[pd.Series, pd.Series, pd.Series]
        - weight_changes: pd.Series
            权重变化。
        - turnover: pd.Series
            换手。
        - cost: pd.Series
            决策（换手）成本。
    
    """
    assert weights.index.is_unique, '权重的 MultiIndex 必须唯一。'
    assert weights.notna().all()
    weights = weights.sort_index()
    # 计算权重变化，第一期权重变化为其本身（假设初始时空仓）
    weight_changes = weights.groupby(level='product').diff().fillna(weights)
    weight_changes.name = 'weight_change'
    # 计算换手率
    turnover = weight_changes.abs().groupby(level='timestamp').sum()
    turnover.name = 'turnover'
    
    # 计算决策成本
    if cost_config.is_uniform:
        # 统一成本率：Cost = kappa * Turnover。CostConfig 允许标量为 int，
        # 因此这里只断言“不是按品种 Mapping”，不能断言为 float。
        assert not isinstance(cost_config.rate, Mapping)
        cost = cost_config.rate * turnover
    else:
        df = weight_changes.to_frame().reset_index()
        assert 'timestamp' in df.columns and 'product' in df.columns
        # CostConfig 在构造时已把产品代码规范化为大写，这里需保持一致，
        # 否则小写品种代码会静默映射为 NaN 成本。
        df['rate'] = df['product'].str.upper().map(cost_config.rate)
        if df['rate'].isna().any():
            missing = sorted(df.loc[df['rate'].isna(), 'product'].unique())
            raise ValueError(
                f'CostConfig.rate 未覆盖品种 {missing}，按品种成本率必须'
                '覆盖全部品种。'
            )
        df['cost'] = df['weight_change'].abs() * df['rate']
        cost = df.groupby('timestamp')['cost'].sum()
    cost = cost.rename('cost')

    return weight_changes, turnover, cost

def compute_contribution(
        aligned_df: pd.DataFrame,
        missing_return_policy: Literal[
            'raise', 'default_to_zero_and_warning']
    ) -> tuple[pd.Series, tuple[str,...], dict[str, pd.DataFrame]]:
    """
    计算每个时间戳 t ，各品种的收益贡献（w_{i,t} * r_{i, t+1} ）。
    若 w 为 0，则无论 r 是否缺失，贡献一律为 0；
    若 w 为非缺失且不为 0，而 r 缺失，（V1）立即报错，整个实验失败。

    Parameters
    ------
    aligned_df: pd.DataFrame
        对齐后的数据集，Index[timestamp, product], columns: true_return,
        eligible, weight
    missing_return_policy: {'raise', 'default_to_zero_and_warning'}

    Returns
    ------
    tuple[pd.Series, tuple[str,...], dict[str, pd.DataFrame]]
    ~~~
        - contributions: pd.Series
            每个时间戳上，各品种的收益贡献。Index[timestamp, product]。
        - warnings: tuple of str
            警告：若policy为'default_to_zero_and_warning'，且出现
            用0值填充时，包含稳定代码前缀的警告；无则为空tuple。
        - metadata: dict[str, pd.DataFrame]
            当权重不为0而收益率缺失的原始数据，供审计。
            - key: 'missing_return_0_filled_raw_entries'
            - value: pd.DataFrame, Index[timestamp, product],
            columns: true_return, eligible, weight 
    """
    df = aligned_df.copy()

    assert df['weight'].notna().all()
    # 将 w 为 0 且 r 缺失处的收益按 0 处理，避免 0 * NaN 产生污染；
    # 只改写 true_return，保留原始行（含品种池已有的时间戳与品种）。
    df.loc[(df['weight'] == 0) & df['true_return'].isna(), 'true_return'] = 0.0

    #检查是否存在 w 不为 0 时，r 缺失
    mask = (df['weight'] != 0) & df['true_return'].isna()
    if mask.any():
        if missing_return_policy == 'raise':
            raise ValueError(
                '存在权重不为0，但下一期收益为缺失。'
                'V1 不允许这种情况，先修正数据才能运行实验。'
                '问题数据为: \n'
                f'{df[mask]}'
            )
        else:
            # 记录 metadata
            metadata = {
                'missing_return_0_filled_raw_entries':df[mask].copy()
            }
            # 记录警告
            cell_count = mask.sum()
            prod_count = df[mask].index.get_level_values(level='product')\
            .unique().size
            share = mask.mean()
            warnings = (
                f'MISSING_RETURN_ZERO_FILLED:policy=default_to_zero_and_warning,' 
                f'cells={cell_count},products={prod_count},share={share}',
            )
            # 用0填充缺失
            df.loc[mask, 'true_return'] = 0.0
    else:
        warnings = ()
        metadata = {}
    return (
        df['weight'] * df['true_return'],
        warnings,
        metadata
    )


def compute_portfolio_returns(contributions: pd.Series, costs_shifted: pd.Series)\
    -> pd.DataFrame:
    """对每个时间戳，加总收益贡献数据，计算组合总收益。
    在组合总收益基础上，减去成本，得到净收益。
    
    返回 DataFrame, Index[timestamp], columns: gross_return, cost, 
    proxy_net_return
    """
    gross_returns = contributions.groupby(level='timestamp').sum()\
    .rename('gross_return')
    # 成本必须与收益实现时间对齐：成本列与净收益共用同一份 reindex 结果，
    # 否则 concat 会把 gross/net 索引之外的时点并入结果表。
    costs = costs_shifted.reindex(gross_returns.index).rename('cost')
    net_returns = (gross_returns - costs).rename('proxy_net_return')
    return pd.concat(
        [gross_returns, costs, net_returns], 
        axis=1
    )

def compute_nav(gross_returns: pd.Series, net_returns: pd.Series)\
    -> pd.DataFrame:
    """计算组合总净值和剔除成本后的净值。
    返回DataFrame, Index[timestamp], columns: gross_nav, proxy_net_nav
    """
    assert gross_returns.notna().all()
    assert net_returns.notna().all()
    # 收益 <= -1 时净值不再具有有效复利含义（开发方案 §16.3 / §21.1）。
    for label, series in (('gross_return', gross_returns),
                          ('proxy_net_return', net_returns)):
        if (series <= -1).any():
            bad = series[series <= -1]
            raise ValueError(
                f'{label} 存在小于等于 -1 的单期收益，净值无法计算：\n{bad}'
            )
    return pd.concat(
        [(gross_returns + 1).cumprod().rename('gross_nav')
         , (net_returns +1).cumprod().rename('proxy_net_nav')],
        axis=1,
    )

def compute_exposure(weights: pd.Series,
                     active_weight_threshold: float) \
    -> pd.DataFrame:
    """对每个时间戳，计算暴露：总暴露、净暴露，多头暴露和空头暴露和暴露品种数。
    返回的 DataFrame, Index[timestamp], columns: gross_exposure, net_exposure, 
    long_exposure, short_exposure, active_count
    """
    a = active_weight_threshold
    return pd.concat(
        [
            weights.abs().groupby(level='timestamp').sum().rename('gross_exposure'),
            weights.groupby(level='timestamp').sum().rename('net_exposure'),
            weights.where(weights>=0, 0).groupby(level='timestamp').sum()\
                .rename('long_exposure'),
            weights.where(weights<=0, 0).groupby(level='timestamp').sum()\
            .rename('short_exposure'),
            (weights.abs() > a).groupby(level='timestamp').sum()\
            .rename('active_count')
        ],
        axis=1
    ) 


    
# ====================================================
# PnL Attribution
# ====================================================
def compute_attribution_product(
        contributions: pd.Series,
        weights_shifted: pd.Series,
        active_weight_threshold: float
)-> pd.DataFrame:
    """输出 PnL 归因。
    返回的 DataFrame：Index[product], 
    columns: gross_contribution_sum, contribution_share, 
    positive_period_count, active_period_count,
    long_contribution_sum, short_contribution_sum
    """
    gross_contribution_sum = contributions.groupby(level='product').sum()\
    .rename('gross_contribution_sum')
    # 总贡献为零时，占比不具定义，按开发方案 §17.4 返回 NaN（而非 inf）。
    total_contribution = gross_contribution_sum.sum()
    if total_contribution == 0:
        contribution_share = pd.Series(
            np.nan, index=gross_contribution_sum.index,
            name='contribution_share')
    else:
        contribution_share = (
            gross_contribution_sum / total_contribution
        ).rename('contribution_share')
    positive_period_count = (weights_shifted > 0).groupby(level='product').sum()\
    .rename('positive_period_count')
    negative_period_count = (weights_shifted < 0).groupby(level='product').sum()\
    .rename('negative_period_count')
    a = active_weight_threshold
    active_period_count = (weights_shifted.abs() > a).groupby(level='product').sum()\
    .rename('active_period_count')

    df = pd.concat(
        [contributions.rename('contribution'), weights_shifted.rename('weight')],
        axis=1
    )
    df_long = df[df['weight'] > 0]
    df_short = df[df['weight'] < 0]
    long_contribution_sum = df_long.groupby(level='product')['contribution']\
    .sum().rename('long_contribution_sum')
    short_contribution_sum = df_short.groupby(level='product')['contribution']\
    .sum().rename('short_contribution_sum')

    attribution_df =  pd.concat(
        [gross_contribution_sum, contribution_share, positive_period_count,
         negative_period_count, active_period_count, long_contribution_sum, 
         short_contribution_sum],
         axis=1
    )
    assert not isinstance(attribution_df.index, pd.MultiIndex) and \
    attribution_df.index.name == 'product'

    return attribution_df

def compute_attribution_direction(
        contributions: pd.Series,
        weights_shifted: pd.Series
) -> pd.DataFrame:
    """
    按形成收益时的权重符号，计算每个时间戳上的多头和空头贡献。

    方向由权重符号决定（开发方案 §17.4），而不是由贡献本身的符号决定：
    持有多头（w>0）即使亏损，其贡献仍计入 long_contribution。

    返回 DataFrame, Index[timestamp], 
    columns: long_contribution, short_contribution
    """
    df = pd.concat(
        [contributions.rename('contribution'),
         weights_shifted.rename('weight')],
        axis=1,
    )
    timeline = contributions.index.get_level_values('timestamp')\
        .unique().sort_values()
    long = df.loc[df['weight'] > 0, 'contribution']\
        .groupby(level='timestamp').sum()\
        .reindex(timeline, fill_value=0.0).rename('long_contribution')
    short = df.loc[df['weight'] < 0, 'contribution']\
        .groupby(level='timestamp').sum()\
        .reindex(timeline, fill_value=0.0).rename('short_contribution')
    return pd.concat(
        [long, short], axis=1
    )

def compute_attribution_sector(
        contributions: pd.Series, 
        classification: pd.DataFrame,
        sector_col: str
) -> pd.Series:
    """根据板块分类，计算每个时间戳上的收益贡献。
    传入的 `classification` 需要有 `product` 列，同时提供板块分类列
    `sector_col`。

    返回 Series， Index[timestamp, `sector_col`]
    """
    assert 'product' in classification.columns, \
        '所提供的 `classification` 并不包含 product 列。'
    assert sector_col in classification.columns, f'所提供的 `classification` 并不包含 {sector_col} 列。'
    df = contributions.rename('contribution').reset_index().merge(
        classification[['product', sector_col]], on='product', how='left'
    )
    # 分类缺失的产品归入显式的 unclassified，不静默删除（开发方案 §17.4）。
    df[sector_col] = df[sector_col].fillna('unclassified')
    return df.groupby(['timestamp', sector_col])['contribution'].sum()
    

# ====================================================
# Performance 核心绩效指标
# ====================================================

#: performance 表的行标签：毛收益与代理净收益。
PERFORMANCE_INDEX: tuple[str, ...] = ('gross', 'proxy_net')

#: performance 表的列顺序（开发方案 §18.7）。
PERFORMANCE_COLUMNS: tuple[str, ...] = (
    'total_return',
    'annualized_arithmetic_return',
    'cagr',
    'annualized_volatility',
    'sharpe',
    'max_drawdown',
    'n_periods',
)


def _compute_performance_row(
        returns: pd.Series,
        annualization_factor: int,
) -> tuple[dict[str, float], bool]:
    """
    对单条收益序列计算核心绩效指标。

    Parameters
    ----------
    returns : pd.Series
        单期组合收益序列，Index[收益实现时间]，按时间升序排列。
    annualization_factor : int
        年化因子 A，V1 日频取 252。

    Returns
    -------
    row : dict of str -> float
        各绩效指标取值，键与 :data:`PERFORMANCE_COLUMNS` 完全一致。
    sharpe_undefined : bool
        标准差为零或有效期数少于 2，导致 Sharpe 不具定义时为 True。

    Notes
    -----
    口径与开发方案 §18 一致：无风险收益固定为 0；年化波动率使用
    ``ddof=1`` 的样本标准差；最大回撤取净值相对历史高点的最小回撤。

    """
    returns = returns.sort_index()
    n_periods = int(returns.shape[0])
    n_valid = int(returns.count())

    # 总收益与 CAGR 基于复利增长因子。
    growth = float((returns + 1.0).prod()) if n_periods > 0 else np.nan # pyright: ignore[reportArgumentType]
    total_return = growth - 1.0 if n_periods > 0 else np.nan

    # 算术年化收益：A * Mean(R)。
    mean = float(returns.mean()) if n_valid > 0 else np.nan
    annualized_arithmetic_return = annualization_factor * mean

    # 年化波动率：sqrt(A) * Std(R, ddof=1)；不足 2 期时为 NaN。
    sample_std = float(returns.std(ddof=1)) if n_valid >= 2 else np.nan
    annualized_volatility = np.sqrt(annualization_factor) * sample_std

    # CAGR：(prod(1+R))^(A/T) - 1；增长因子非正时无有效复利含义。
    if n_periods > 0 and np.isfinite(growth) and growth > 0:
        cagr = growth ** (annualization_factor / n_periods) - 1.0
    else:
        cagr = np.nan

    # Sharpe：sqrt(A) * Mean(R) / Std(R)，无风险收益为 0。
    sharpe_undefined = n_valid < 2 or not np.isfinite(sample_std) or \
        sample_std == 0
    if sharpe_undefined:
        sharpe = np.nan
    else:
        sharpe = np.sqrt(annualization_factor) * mean / sample_std

    # 最大回撤：净值相对历史最高点的最小回撤。
    # 初始净值约定为 1（§16.3），故峰值取 max(1, 历史累计最高净值)，
    # 使首个收益为负时也能计入回撤。
    if n_periods > 0:
        nav = (returns + 1.0).cumprod()
        running_peak = nav.cummax().clip(lower=1.0)
        max_drawdown = float((nav / running_peak - 1.0).min())
    else:
        max_drawdown = np.nan

    row = {
        'total_return': total_return,
        'annualized_arithmetic_return': annualized_arithmetic_return,
        'cagr': cagr,
        'annualized_volatility': annualized_volatility,
        'sharpe': sharpe,
        'max_drawdown': max_drawdown,
        'n_periods': n_periods,
    }
    return row, sharpe_undefined


def compute_performance(
        gross_returns: pd.Series,
        net_returns: pd.Series,
        annualization_factor: int,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """
    对毛收益与代理净收益分别计算核心绩效指标（开发方案 §18）。

    Parameters
    ----------
    gross_returns : pd.Series
        组合毛收益序列，Index[收益实现时间]。
    net_returns : pd.Series
        代理净收益序列（毛收益减成本），Index[收益实现时间]。
    annualization_factor : int
        年化因子 A，V1 日频取 252。

    Returns
    -------
    performance : pd.DataFrame
        Index 为 ``gross`` / ``proxy_net``，列为
        :data:`PERFORMANCE_COLUMNS`。
    warnings : tuple of str
        Sharpe 无法定义时的稳定代码前缀警告；无则为空元组。

    Raises
    ------
    AssertionError
        任一收益序列含缺失值时抛出。

    Notes
    -----
    两条收益序列使用完全相同的年度化因子和公式口径。Sharpe 在标准差为
    零或有效期数少于 2 时返回 NaN，并输出 ``SHARPE_UNDEFINED`` 警告，
    不返回误导性的零。

    """
    assert gross_returns.notna().all(), '毛收益序列不允许包含缺失值。'
    assert net_returns.notna().all(), '代理净收益序列不允许包含缺失值。'

    warnings: list[str] = []
    rows: dict[str, dict[str, float]] = {}
    for label, series in (
        ('gross', gross_returns),
        ('proxy_net', net_returns),
    ):
        row, sharpe_undefined = _compute_performance_row(
            series, annualization_factor)
        rows[label] = row
        if sharpe_undefined:
            warnings.append(f'SHARPE_UNDEFINED:{label}')

    performance = pd.DataFrame.from_dict(rows, orient='index')
    performance = performance.loc[list(PERFORMANCE_INDEX),
                                  list(PERFORMANCE_COLUMNS)]
    performance.index.name = 'return_type'
    performance['n_periods'] = performance['n_periods'].astype('int64')
    return performance, tuple(warnings)
