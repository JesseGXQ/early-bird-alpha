"""方向命中率诊断（时序模式适用）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from factors.evaluation.common import select_group_sample

HIT_RATE_INVALID_CODES = {
    1: 'has_missing_nan_policy_strict',
    9: 'empty_universe',
    3: 'insufficient_valid_obs',
    4: 'insufficient_hit_evaluation_obs', 
}

_STATISTICS_COLUMNS = [
    'threshold', 
    'activation_rate', 'long_opportunity_rate', 
    'short_opportunity_rate', 'long_share_among_active',
    'up_base_rate_global', 'down_base_rate_global', 
    'up_base_rate_active', 'down_base_rate_active',
    'hit_rate', 'long_hit_rate', 'short_hit_rate',
    'chance_hit_rate', 'excess_hit_rate', 'kappa',
    'long_lift_global','short_lift_global',
    'long_lift_active','short_lift_active'
]

_META_COLUMNS = [
    'n_eligible', 'n_valid', 'n_dropped', 
    'n_direction', 
    'threshold', 'n_active', 
    'n_hit',
    'invalid_code'
]

_SUMMARY_COLUMNS = ['mean', 'median', 'std', 'n_valid']

@dataclass(frozen=True)
class HitRateResult:
    """单个品种的方向命中率评估结果。

    Parameters
    ----------
    statistics : pd.DataFrame or None
        合法阈值对应的方向诊断统计量，每个阈值一行，使用 RangeIndex，列为
        ``_STATISTICS_COLUMNS``。其中包括激活率、多空机会率、全局及激活样本
        方向基准、总体及多空命中率、偶然命中率、超额命中率、Cohen's kappa
        和多空 lift。各列含义如下：

        - ``threshold``：绝对因子激活阈值；
        - ``activation_rate``：``n_active / n_valid``；
        - ``long_opportunity_rate``、``short_opportunity_rate``：全部联合有效
          样本中的多头、空头激活比例；
        - ``long_share_among_active``：全部激活信号中的多头比例；
        - ``up_base_rate_global``、``down_base_rate_global``：全局方向样本中的
          上涨、下跌比例；
        - ``up_base_rate_active``、``down_base_rate_active``：命中评价样本中的
          上涨、下跌比例；
        - ``hit_rate``、``long_hit_rate``、``short_hit_rate``：总体、多头、
          空头方向命中率；
        - ``chance_hit_rate``：由命中评价样本的预测方向和实际方向边际分布
          推导的偶然一致概率；
        - ``excess_hit_rate``：命中率超过偶然一致概率的差值；
        - ``kappa``：对偶然一致进行归一化调整后的 Cohen's kappa；
        - ``long_lift_global``、``short_lift_global``：多空命中率相对全局
          方向基准的增量；
        - ``long_lift_active``、``short_lift_active``：多空命中率相对激活
          方向基准的增量。

        若该品种在所有请求阈值下均无合法结果，则为 ``None``。
    metadata : pd.DataFrame
        每个请求阈值的样本筛选元数据，每个阈值一行，使用 RangeIndex，列为
        ``_META_COLUMNS``。``n_eligible`` 是品种池内样本数；``n_valid`` 是完成
        缺失值处理后的联合有效样本数；``n_dropped`` 是被缺失值策略剔除的
        样本数；``n_direction`` 是标签非零样本数；``n_active`` 是满足
        ``abs(factor) > threshold`` 的样本数；``n_hit`` 是同时激活且标签非零
        的方向可比较样本数；``invalid_code`` 为 ``HIT_RATE_INVALID_CODES`` 中的
        稳定失效码，合法行取 ``None``。

    Notes
    -----
    ``statistics`` 只保存合法阈值，而 ``metadata`` 始终保留全部请求阈值，
    因而两张表不能依靠行位置直接对齐，应使用 ``threshold`` 关联。
    """
    statistics: pd.DataFrame | None
    metadata: pd.DataFrame

@dataclass(frozen=True)
class HitRateEvaluationResults:
    """多品种时序方向命中率评估结果。

    Parameters
    ----------
    label_horizon : int
        标签的前瞻期数。标签须由上游按该期限完成前瞻对齐。
    min_observations : int
        单个品种以及单个品种阈值下方向命中统计所需的最低有效观测数。
    nan_policy: {'strict', 'drop}
        缺失值处理策略。``strict`` 表示品种内任一合资格 factor/label 缺失即
        整个品种失效；``drop`` 表示剔除缺失行后再判断样本数。
    thresholds : tuple[float, ...] or None
        请求的绝对因子阈值。公共入口会将 ``None`` 规范化为 ``(0.0,)``；
        返回对象由公共入口创建时通常不为 ``None``。
    results : dict[str, HitRateResult]
        分品种明细，键为 product，值为该品种的统计量及样本元数据。
    summary : pd.DataFrame
        合法品种结果的等品种汇总。索引为名称分别为 ``threshold``、``term``
        的 MultiIndex；``term`` 为 ``_STATISTICS_COLUMNS`` 中除 ``threshold``
        外的统计量；列为 ``mean``、``median``、``std``、``n_valid``，其中
        ``n_valid`` 是参与该统计量汇总的非缺失品种数。若没有任何合法结果，
        则返回仅含这些列的空 DataFrame。

    Notes
    -----
    dataclass 的 ``frozen=True`` 只禁止属性重新绑定，不会使其中的 DataFrame
    真正不可变；调用方仍应遵守不原地修改结果表的约定。

    """
    label_horizon: int
    min_observations: int
    nan_policy: Literal['strict', 'drop']
    thresholds: tuple[float, ...] | None
    results: dict[str, HitRateResult]  # key is product
    summary: pd.DataFrame

def _metadata_invalid_whole(n_eligible: int, n_valid: int | None, 
                            n_dropped: int | None, 
                            invalid_code: int, 
                            thresholds: tuple[float, ...] | None
                            ) -> pd.DataFrame:
    """组整体为非法时，返回元数据 DataFrame。
    Columns 为 ``_META_COLUMNS``。
    """
    n = len(thresholds) if thresholds is not None else 1
    meta = pd.DataFrame(
        {
            'n_eligible': [n_eligible] * n,
            'n_valid': [n_valid] * n,
            'n_dropped': [n_dropped] * n,
            'invalid_code': [invalid_code] * n,
            'threshold': thresholds if thresholds is not None else [None]
        },
        dtype='object'
    )
    meta = meta.reindex(columns=_META_COLUMNS)
    return meta

def _meta_partial_invalid(
        n_eligible: int, n_valid: int, n_dropped: int | None, 
        n_direction: int, 
        threshold: float, 
        n_active: int,
        n_hit: int, 
) -> pd.DataFrame:
    meta = pd.DataFrame(
        {
            'n_eligible': [n_eligible],
            'n_valid': [n_valid],
            'n_dropped': [n_dropped], 
            'n_active': [n_active],
            'threshold': [threshold],
            'invalid_code': [4],
            'n_direction': [n_direction],
            'n_hit': [n_hit],
        },
        dtype='object'
    )
    meta = meta.reindex(columns=_META_COLUMNS)
    assert len(meta) == 1
    return meta

def _meta_row_valid(
        n_eligible: int, n_valid: int, n_dropped: int | None,
        n_direction: int, 
        threshold: float | None, 
        n_active: int, 
        n_hit: int,
) -> pd.DataFrame:
    meta = pd.DataFrame(
        {
            'n_eligible': [n_eligible],
            'n_valid': [n_valid],
            'n_dropped': [n_dropped],
            'threshold': [threshold],
            'n_active': [n_active],
            'invalid_code': [None],
            'n_direction': [n_direction],
            'n_hit': [n_hit]
        },
        dtype='object'
    )
    meta = meta.reindex(columns=_META_COLUMNS)
    assert len(meta) == 1
    return meta

def _hit_rate(df: pd.DataFrame) -> float:
    """计算方向命中率。"""
    return (df['factor'] * df['label'] >0).mean()

def _long_hit_rate(df: pd.DataFrame) -> float:
    """多头方向命中率。"""
    sub = df[df['factor'] > 0]
    return (sub['label'] > 0).mean() if not sub.empty else np.nan

def _short_hit_rate(df: pd.DataFrame) -> float:
    """空头方向命中率。"""
    sub = df[df['factor'] < 0]
    return (sub['label'] < 0).mean() if not sub.empty else np.nan

def _summary(results: dict[str, HitRateResult]) -> pd.DataFrame:
    """
    以(阈值，统计量）分类，对统计量做跨品种汇总，包括
    均值、中位数、标准差、有效计数。    
    """
    dfs: list[pd.DataFrame] = []
    for prod, hit_rate_result in results.items():
        if isinstance(hit_rate_result.statistics, pd.DataFrame):
            df = hit_rate_result.statistics.copy()
            df['product'] = prod
            dfs.append(df)
    if not dfs: # 若所有品种的方向命中率结果都为 None，返回空的 summary DataFrame
        return pd.DataFrame([], columns=_SUMMARY_COLUMNS)
    data = pd.concat(dfs, axis=0, ignore_index=True) 
    summary =  data.groupby('threshold')[_STATISTICS_COLUMNS[1:]] \
        .agg(['mean', 'median', 'std', 'count'])
    summary = summary.stack(level=-2)
    assert isinstance(summary, pd.DataFrame)
    assert isinstance(summary.index, pd.MultiIndex)
    summary.index.names = ['threshold', 'term']
    summary.rename(columns={'count': 'n_valid'}, inplace=True)
    return summary

def _get_sample(df_valid: pd.DataFrame, 
                name: Literal['direction', 'active', 'hit'], 
                threshold: float | None = None) -> pd.DataFrame:
    """
    获取待评估的数据样本集合。
    - direction: label != 0；
    - active: |factor| > c；
    - hit: active，且 label 不为 0 。
    """
    if name == 'direction':
        return df_valid[df_valid['label'] != 0]

    assert threshold is not None and threshold >= 0
    df_active = df_valid[df_valid['factor'].abs() > threshold]

    if name == 'active': return df_active
    if name == 'hit': return df_active[df_active['label'] != 0]
    

def _stats(
        df_valid: pd.DataFrame, threshold: float,
        min_observations: int
) -> tuple[
    tuple[int, int, int], 
    pd.DataFrame | None, 
    Literal[4] | None
]:
    """
    计算命中率相关统计量，返回一个三元组
    `(sample_counts, statistics, missing_reason)`。

    第一个元素 `sample_counts` 为样本数量三元组
    `(n_direction, n_active, n_hit)`，分别表示 label 非零样本数、
    满足阈值条件 `|factor| > threshold` 的活跃样本数、以及同时满足
    这两个条件的命中样本数。

    第二个元素 `statistics`：若命中样本数 `n_hit` 满足
    `min_observations` 标准，则为命中率统计量的 DataFrame，其 columns 为
    `_STATISTICS_COLUMNS`；否则为 `None`。

    第三个元素 `missing_reason`：当命中样本数不足时返回 `4`，否则返回 `None`。"""
    c = threshold # simplify variable name
    df_direction = _get_sample(df_valid, 'direction')
    df_active = _get_sample(df_valid, 'active', c)
    df_hit = _get_sample(df_valid, 'hit', c)

    if len(df_hit) < min_observations:
        return (
            (len(df_direction), len(df_active), len(df_hit)), 
            None, 
            4
        )
    # 激活率相关统计量
    activation_rate = len(df_active) / len(df_valid)
    long_opportunity_rate   = (df_valid['factor'] > c).mean() # 包含标签零值
    short_opportunity_rate  = (df_valid['factor'] < -c).mean() # 包含标签零值
    long_share_among_active = (df_active['factor'] > c).mean() # 包含标签零值
    # benchmark stats
    up_base_rate_global   = (df_direction['label'] > 0).mean() # 不包含标签零值
    down_base_rate_global = (df_direction['label'] < 0).mean() # 不包含标签零值
    up_base_rate_active   = (df_hit['label'] > 0).mean() # 不包含标签零值
    down_base_rate_active = (df_hit['label'] < 0).mean() # 不包含标签零值
    # hit rates
    hit_rate, long_hit_rate, short_hit_rate = _hit_rate(df_hit), \
        _long_hit_rate(df_hit), _short_hit_rate(df_hit) # 全部不包含标签零值
    # lift-related stats
    q_long_hit = (df_hit['factor'] > c).mean() # 不包含标签零值的激活样本内多头信号比例
    chance_hit_rate = q_long_hit * up_base_rate_active + \
    (1 - q_long_hit) * down_base_rate_active
    excess_hit_rate = hit_rate - chance_hit_rate
    kappa = excess_hit_rate / (1 - chance_hit_rate) \
        if chance_hit_rate < 1 else np.nan
    long_lift_global = long_hit_rate - up_base_rate_global
    short_lift_global = short_hit_rate - down_base_rate_global
    long_lift_active = long_hit_rate - up_base_rate_active
    short_lift_active = short_hit_rate - down_base_rate_active

    stats_df = pd.DataFrame(
        {
            'threshold': [threshold],
            'activation_rate': [activation_rate],
            'long_opportunity_rate': [long_opportunity_rate],
            'short_opportunity_rate': [short_opportunity_rate],
            'long_share_among_active': [long_share_among_active],
            'up_base_rate_global': [up_base_rate_global],
            'down_base_rate_global': [down_base_rate_global],
            'up_base_rate_active': [up_base_rate_active],
            'down_base_rate_active': [down_base_rate_active],
            'hit_rate': [hit_rate],
            'long_hit_rate': [long_hit_rate],
            'short_hit_rate': [short_hit_rate],
            'chance_hit_rate': [chance_hit_rate],
            'excess_hit_rate': [excess_hit_rate],
            'kappa': [kappa],
            'long_lift_global': [long_lift_global],
            'short_lift_global': [short_lift_global],
            'long_lift_active': [long_lift_active],
            'short_lift_active': [short_lift_active]
        }
    )
    assert len(stats_df.columns) == len(_STATISTICS_COLUMNS)
    stats_df = stats_df.reindex(columns=_STATISTICS_COLUMNS)
    assert len(stats_df) == 1
    return (
            (len(df_direction), len(df_active), len(df_hit)),
            stats_df, 
            None
        )

def hit_rate_details(
        df: pd.DataFrame,
        label_horizon: int,
        min_observations: int,
        nan_policy: Literal['strict', 'drop'],
        thresholds: tuple[float, ...] | None
) -> HitRateEvaluationResults:
    """计算多品种时序模式下的方向命中率诊断。

    函数按 product 沿时间轴独立评价因子方向与未来标签方向的关系。每个品种
    先依据动态品种池、缺失值策略和最低样本数取得联合有效样本，再对每个阈值
    分别计算信号激活、方向基准、命中率和相对基准的增量统计，最后对合法的
    分品种结果执行等品种汇总。

    Parameters
    ----------
    df : pd.DataFrame
        因子评估面板。MultiIndex 索引须包含 ``product`` 层级；数据列须包含
        ``factor``（因子值）、``label``（已由上游完成前瞻对齐的未来标签）和
        ``universe``（无缺失的布尔型动态品种池标记）。同一 product 内的行
        应按时间排序，且索引应唯一。
    label_horizon : int
        标签的前瞻期数，必须为大于等于 1 的整数。标签必须已由上游按该期限
        完成构造和对齐；本函数将该值保存在返回结果中。
    min_observations : int
        判定评价有效所需的最低观测数。该门槛首先用于联合有效样本筛选，随后
        对每个阈值再次作用于同时满足 ``abs(factor) > threshold`` 且
        ``label != 0`` 的方向可比较样本。
    nan_policy : {'strict', 'drop'}
        缺失值处理策略。``strict`` 表示某品种的合资格样本中只要 factor 或
        label 存在缺失，该品种整体失效；``drop`` 表示剔除 factor 或 label
        缺失的行，再判断是否满足 ``min_observations``。
    thresholds : tuple[float, ...] or None
        激活信号使用的绝对因子阈值。阈值 ``c`` 的激活条件为
        ``abs(factor) > c``，多头及空头机会分别为 ``factor > c`` 和
        ``factor < -c``。``None`` 等价于 ``(0.0,)``，即所有非零因子信号
        均视为激活。

    Returns
    -------
    HitRateEvaluationResults
        包含原始评价配置、分品种 ``HitRateResult`` 和跨品种等权汇总。某个
        品种或阈值无效时不会返回误导性的零值；详细样本数和失效原因保存在
        对应 ``metadata`` 中。

    Notes
    -----
    本函数采用非零方向口径：标签为零的行仍参与激活率、机会率和激活方向
    构成统计，但不参与全局涨跌基准、激活样本涨跌基准、命中率、lift、偶然
    命中率和 kappa。全局方向基准在该品种所有 ``label != 0`` 的联合有效
    样本上计算；激活方向基准与命中率在同一 ``abs(factor) > c`` 且
    ``label != 0`` 的样本上计算。输入 DataFrame 不会被原地修改。
    """
    # 根据阈值筛选时，若不设阈值，实质上等同于阈值为0，
    # 即阈值为 0 时，所有非零 factor 信号均视为激活。
    if thresholds is None: 
        thresholds = (0.0,)
    results: dict[str, HitRateResult] = {}
    products = df.index.get_level_values('product') \
    .unique()

    for prod in products:
        prod_df = df.xs(prod, level='product')
        assert isinstance(prod_df, pd.DataFrame)
        sg = select_group_sample(
            prod_df,
            min_observations=min_observations,
            nan_policy=nan_policy,
            missing_rejection_scope_mode_strict='factor_and_label',
            missing_drop_scope_mode_drop='factor_and_label',
            single_value_handle_scope=None
        )
        if sg.invalid_code is not None: # 非法组
            meta = _metadata_invalid_whole(
                n_eligible=sg.n_eligible, 
                n_valid=sg.n_valid, 
                n_dropped=sg.n_dropped, 
                invalid_code=sg.invalid_code,
                thresholds=thresholds)
            results[prod] = HitRateResult(None, meta)
            continue
        # ------------------- 合法组 ---------------------
        assert sg.n_valid is not None
        assert isinstance(sg.data, pd.DataFrame)
        assert len(sg.data) == sg.n_valid

        meta_rows: list[pd.DataFrame] = []
        stats_rows: list[pd.DataFrame] = []
        for c in thresholds:
            ((n_direction, n_active, n_hit), 
            stats_row, 
            code) = _stats(
                df_valid=sg.data, 
                threshold=c,
                min_observations=min_observations
            )
            if code is None: # 成功计算了命中率相关统计量
                assert isinstance(stats_row, pd.DataFrame)
                stats_rows.append(stats_row)
                meta_row = _meta_row_valid(
                    n_eligible=sg.n_eligible, 
                    n_valid=sg.n_valid, 
                    n_dropped=sg.n_dropped,
                    threshold=c, 
                    n_active=n_active,
                    n_direction=n_direction,
                    n_hit=n_hit
                )
            else: # 由于样本不足，未能成功计算命中率相关统计量
                meta_row = _meta_partial_invalid(
                    n_eligible=sg.n_eligible, 
                    n_valid=sg.n_valid, 
                    n_dropped=sg.n_dropped, 
                    threshold=c,
                    n_active=n_active,
                    n_direction=n_direction,
                    n_hit=n_hit
                )
            meta_rows.append(meta_row)
        if stats_rows:
            stats_df = pd.concat(stats_rows, axis=0, ignore_index=True)
        else:
            stats_df = None
        meta = pd.concat(meta_rows, axis=0, ignore_index=True)
        results[prod] = HitRateResult(statistics=stats_df, metadata=meta)

    return HitRateEvaluationResults(
        label_horizon=label_horizon, 
        min_observations=min_observations,
        nan_policy=nan_policy,
        thresholds=thresholds,
        results=results,
        summary=_summary(results)
    )


#==============================
# inline smoke test
# ==============================
if __name__ == '__main__':

    rng = np.random.default_rng(42)
    products = ['A', 'B', 'C']
    dates = pd.date_range('2025-05-10', periods=15, freq='D')
    index = pd.MultiIndex.from_product(
        [products, dates], names=['product', 'date'])
    n = len(index)
    df = pd.DataFrame(
        {
            'factor': rng.standard_normal(n),
            'label': rng.uniform(-0.3, 0.3, n),
            'universe': True,
        },
        index=index,
    )
    print(df)
    res = hit_rate_details(
        df, label_horizon=1, min_observations=5, nan_policy='drop',
        thresholds=(0.02, 0.03, 0.05, 0.07, 1.5))

    # 打印结果
    print('-'*30)
    print('Evaluation results: \n')
    for prod, re in res.results.items():
        print(f'product is {prod} \n')
        print('hit rate result: ')
        print(re.statistics.to_string(line_width=120) \
              if re.statistics is not None else None)
        print('metadata: ')
        print(re.metadata.to_string())
        print('-' * 15)
    print('-' * 30)
    print('Summary over products: ')
    print(res.summary.to_string())




                





                
                



        




    





