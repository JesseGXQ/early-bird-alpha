from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Any
import pandas as pd
import numpy as np

from factors.evaluation.common import (
    select_group_sample, SelectedGroup, group_to_series
)
from utils.validation_utils import check_df_columns_exist

@dataclass(frozen=True)
class CSGroupedEvaluationResults:
    mode = 'cross_sectional'
    min_observations: int
    nan_policy: Literal['strict', 'drop']
    n_groups: int
    grouping_direction: Literal['ascending', 'descending']
    group_assignment: pd.Series 
    group_metadata: pd.DataFrame  
    group_missing_reason: pd.Series
    grouped_forward_returns: pd.DataFrame 
    high_minus_low: pd.Series 
        
@dataclass(frozen=True)
class TSGroupedEvaluationResults:
    mode = 'time_series'
    min_observations: int
    nan_policy: Literal['strict', 'drop']
    n_groups: int
    grouping_direction: Literal['ascending', 'descending']
    ts_grouping_method: Literal[
        'expanding', 'rolling', 'full_sample'] 
    rolling_window: int | None
    group_assignment:  dict[str, pd.Series] 
    group_metadata: dict[str, pd.DataFrame | pd.Series] 
    group_missing_reason: dict[str, pd.Series] 
    grouped_forward_returns: dict[str, pd.Series] 
    high_minus_low: dict[str, float] 

def _assign_quantile_groups(
    factor: pd.Series,
    n_groups: int,
    grouping_direction: Literal['ascending', 'descending']
)-> pd.Series:
    """根据 factor 取值将数据分为 n_groups 组。
    若 grouping_direction 为 ``ascending``，则因子值最小组为第1组；
    若为 ``descending``，则因子值最大组为第1组。

    若分组失败，抛出 ValueError。
    
    注意，可能出现分组不完全情况，例如 n_groups = 4, 但没有观测值被分到第2组。
    """
    ascending_groups = pd.qcut(
        factor,
        q=n_groups,
        labels=range(1, n_groups + 1),
        duplicates="raise",
    ).astype("Int64")
    ascending_groups.name = 'group'
    if grouping_direction == "ascending":
        return ascending_groups
    return (n_groups + 1 - ascending_groups).astype("Int64")  

def _forward_mean_returns_by_group(
        df: pd.DataFrame, # 必须包含 label, group 两列
)-> pd.Series:
    """对数据根据分组信息计算标签均值。"""
    return df.groupby(['group'])['label'].mean()

def _high_minus_low(
        grouped_forward_returns: pd.DataFrame, # Index[timestamp], cols: group1, group2, ...
        grouping_direction: Literal['ascending', 'descending'],
        n_groups: int
)-> pd.Series:
    if grouping_direction == 'ascending':
        low, high = 1, n_groups
    else:
        low, high = n_groups, 1
    res = (grouped_forward_returns[f'group{high}']
            - grouped_forward_returns[f'group{low}']
    )
    res.name = 'high_minus_low'
    return res

type GroupAssignment = pd.Series
type GroupMeta = pd.DataFrame # Index[timestamp], cols: n_eligible, n_valid, n_dropped, invalid_code
type GroupMissingReason = pd.Series
type TSPITAssignmentResults = tuple[
    GroupAssignment, GroupMeta, GroupMissingReason
]

GROUP_MISSING_REASON_CODES = { 
    1: 'out_of_universe', # universe = False
    2: 'factor_self_missing', # 当前 factor 缺失
    3: 'invalid_group', # 历史窗口无效
    4: 'qcut_failed' # qcut 失败
} # 分组结果缺失的失败码及其对应原因，遵循1234的优先级设定；若成功则失败码为NA

def _pit_select_assign_quantile_groups(
        product_df: pd.DataFrame, # Index[timestamp], cols: factor, label, universe
        nan_policy: Literal['strict', 'drop'],
        min_observations: int,
        ts_grouping_method: Literal['expanding','rolling'],
        rolling_window: int | None,
        n_groups: int,
        grouping_direction: Literal['ascending','descending']
) -> TSPITAssignmentResults:
    """
    时序模式下做 point-in-time expanding/rolling window 分组。

    时间 ``t`` 的分组边界仅使用截至 ``t-1`` 的历史窗口。第三个返回值
    记录组号缺失原因；成功分组时为 NA，失败时使用
    ``GROUP_MISSING_REASON_CODES`` 中的代码。
    """
    # 参数校验
    if product_df.empty:
        raise ValueError('product_df 不能为空。')

    assignments = pd.Series(
        pd.NA, # type: ignore
        index=product_df.index,
        dtype='Int64',
        name='group_assignment',
    )  
    missing_reasons = pd.Series(
        pd.NA,  # type: ignore
        index=product_df.index,
        dtype='Int64',
        name='group_missing_reason',
    )
    metas: list[tuple] = []
    reason_codes = {
        reason: code for code, reason in GROUP_MISSING_REASON_CODES.items()
    }

    def get_group(
            bins: np.ndarray, 
            value: Any, 
            n_groups: int,
            grouping_direction: \
                Literal['ascending','descending']
    ) -> int:
        g = int(np.clip(np.searchsorted(bins, value), 1, n_groups))
        if grouping_direction == 'ascending':
            return g
        return 1 + n_groups - g

    for position, timestamp in enumerate(product_df.index):
        if ts_grouping_method == 'expanding':
            history = product_df.iloc[:position]
        else:
            assert rolling_window is not None
            start = max(0, position - rolling_window)
            history = product_df.iloc[start:position]

        sg = select_group_sample(
            history,
            min_observations=min_observations,
            nan_policy=nan_policy,
            missing_rejection_scope_mode_strict='factor',
            missing_drop_scope_mode_drop='factor',
            single_value_handle_scope='factor',
        )

        # metadata 以待分组时点 t 对齐，但统计内容来自截至 t-1 的历史窗口；
        # 因而 metadata、assignment 和 missing_reason 可按同一 timestamp 连接。
        metas.append(
            (
                timestamp,
                sg.n_eligible,
                sg.n_valid,
                sg.n_dropped,
                sg.invalid_code,
            )
        )

        current_row = product_df.iloc[position]
        if not bool(current_row['universe']):
            missing_reasons.iat[position] = reason_codes['out_of_universe']
            continue
        current_factor = current_row['factor']
        if pd.isna(current_factor):
            missing_reasons.iat[position] = reason_codes['factor_self_missing']
            continue
        if sg.invalid_code is not None:
            missing_reasons.iat[position] = reason_codes['invalid_group']
            continue

        assert sg.data is not None
        try:
            _, bins = pd.qcut(
                sg.data['factor'],
                q=n_groups,
                retbins=True,
                duplicates='raise',
            )
        except ValueError:
            missing_reasons.iat[position] = reason_codes['qcut_failed']
            continue

        assignments.iat[position] = get_group(
            bins=bins,
            value=current_factor,
            n_groups=n_groups,
            grouping_direction=grouping_direction,
        )

    # 组装结果并返回
    group_meta = pd.DataFrame(
        metas, 
        columns=['timestamp', 'n_eligible',
                 'n_valid','n_dropped','invalid_code'
                ]
    ).set_index('timestamp')
    return assignments, group_meta, missing_reasons

def _cs_grouped_details(
        df: pd.DataFrame,
        min_observations: int,
        nan_policy: Literal['strict', 'drop'],
        n_groups: int,
        grouping_direction: Literal['ascending', 'descending'],
) -> CSGroupedEvaluationResults:
    # grouped 只关心 factor 的缺失与常数性：
    # strict 仅按 factor 判整组失效；drop 仅剔除 factor 缺失行；single value 仅检查 factor
    selected: pd.Series = df.groupby(level='timestamp') \
    .apply(select_group_sample,  # pyright: ignore[reportArgumentType]
           min_observations=min_observations,
           nan_policy=nan_policy,
           missing_rejection_scope_mode_strict='factor',
           missing_drop_scope_mode_drop='factor',
           single_value_handle_scope='factor')  # type: ignore
    group_metadata = selected.apply(group_to_series)

    reason_codes = {
        reason: code for code, reason in GROUP_MISSING_REASON_CODES.items()
    }
    reason = pd.Series(pd.NA, index=df.index,  # type: ignore
                       dtype='Int64', name='group_missing_reason')
    reason[~df['universe']] = reason_codes['out_of_universe'] # 先记录分组缺失原因第1优先级——不在universe以内
    reason[
        reason.isna() & df['factor'].isna()
    ] = reason_codes['factor_self_missing'] # 在 universe 以内但 factor 缺失，则分组必然缺失，此时按第2优先级赋予

    parts: list[pd.Series] = []
    for ts, sg in selected.items(): 
        sg: SelectedGroup = sg 
        if sg.invalid_code is None: # 合法组
            assert sg.data is not None
            try:
                _group = _assign_quantile_groups(
                            factor=sg.data['factor'], n_groups=n_groups, # pyright: ignore[reportOptionalSubscript]
                            grouping_direction=grouping_direction # MultiIndex[timestamp, product]，只包含 in universe 且 factor 非空的那些
                        )
            except ValueError:
                _group = pd.Series(
                    pd.NA, # type: ignore
                    index=sg.data.index,
                    name='group'
                ).astype('Int64') # 若分组出错，直接合并全 nan
                reason[
                    reason.isna() & reason.index.isin(sg.data.index)
                ] = reason_codes['qcut_failed'] # 记录分组缺失原因——qcut失败
            parts.append(_group)
        else: # 非法组
            reason[
                reason.isna() & 
                (reason.index.get_level_values('timestamp') == ts)
            ] = reason_codes['invalid_group'] #记录分组缺失原因——非法组
    if parts:
        group_assignment = pd.concat(parts, axis=0) # MultiIndex[timestamp, product], name is 'group'
        group_assignment = group_assignment.reindex(df.index) # 将非法组（非法的那些timestamp）和合法组中 factor 列缺失的那些行补齐
    else:
        group_assignment = pd.Series(
            pd.NA, index=df.index, # type: ignore
            dtype='Int64', name='group')
    merged_df = pd.merge(df['label'],group_assignment,
                         how='left',left_index=True, 
                         right_index=True)# MultiIndex[timestamp, product], columns: group, label
    
    assert isinstance(merged_df.index, pd.MultiIndex) \
    and sorted(list(merged_df.index.names)) == ['product','timestamp']
    grouped_forward_returns = merged_df\
        .groupby(['timestamp','group'])['label'].mean() # MultiIndex[timestamp, group]
    grouped_forward_returns.name='ret'
    grouped_forward_returns = grouped_forward_returns\
        .reset_index()\
        .pivot(index='timestamp',columns='group',values='ret') 

    # 补齐可能缺失的timestamp（若某timestamp的group全为nan，
    # 前方groupby会丢失该timestamp)、group列。补充值设为nan。
    grouped_forward_returns.columns = [
        f'group{int(n)}' for n in grouped_forward_returns.columns
    ]
    grouped_forward_returns = grouped_forward_returns.reindex(
        index=df.index.get_level_values('timestamp').drop_duplicates(),
        columns=[f'group{n}' for n in range(1, n_groups+1)]
    )

    # 高低组差
    high_minus_low = _high_minus_low(
        grouped_forward_returns=grouped_forward_returns,
        grouping_direction=grouping_direction,
        n_groups=n_groups
    )
    return CSGroupedEvaluationResults(
        min_observations=min_observations,
        nan_policy=nan_policy,
        n_groups=n_groups,
        grouping_direction=grouping_direction,
        group_assignment=group_assignment,
        group_metadata=group_metadata,
        group_missing_reason=reason,
        grouped_forward_returns=grouped_forward_returns,
        high_minus_low=high_minus_low
    )


def _ts_grouped_details(
        df: pd.DataFrame,
        min_observations: int,
        nan_policy: Literal['strict','drop'],
        n_groups: int,
        grouping_direction: Literal['ascending','descending'],
        ts_grouping_method: Literal[
        'expanding', 'rolling', 'full_sample'],
        rolling_window: int | None
) -> TSGroupedEvaluationResults:
    products: list[str] = df.index.get_level_values('product')\
        .unique().tolist()
    group_assignment:  dict[str, pd.Series] = {}
    group_metadata: dict[str, pd.DataFrame | pd.Series] = {}
    group_missing_reason: dict[str, pd.Series] = {}
    grouped_forward_returns: dict[str, pd.Series] = {}
    high_minus_low: dict[str, float] = {}

    for prod in products:
        product_df = df.xs(prod, level='product') # Index[timestamp]
        assert isinstance(product_df, pd.DataFrame)
        # 1. 计算分组、记录元信息和分组缺失原因
        if ts_grouping_method in {'rolling','expanding'}:
            assignment, meta, reason = \
                _pit_select_assign_quantile_groups(
                product_df=product_df,
                nan_policy=nan_policy,
                min_observations=min_observations,
                ts_grouping_method=ts_grouping_method,  # pyright: ignore[reportArgumentType]
                rolling_window=rolling_window,
                n_groups=n_groups,
                grouping_direction=grouping_direction
            )
            group_assignment[prod] = assignment
            group_metadata[prod] = meta
            group_missing_reason[prod] = reason

        else: # full_sample
            sg = select_group_sample(
                product_df,
                min_observations=min_observations,
                nan_policy=nan_policy,
                missing_rejection_scope_mode_strict='factor',
                missing_drop_scope_mode_drop='factor',
                single_value_handle_scope='factor'
            )
            meta = group_to_series(sg)
            group_metadata[prod] = meta

            reason_codes = {
                reason: code for code, reason in \
                    GROUP_MISSING_REASON_CODES.items()
            } 
            reason = pd.Series(
                pd.NA, index=product_df.index, dtype='Int64', # type: ignore
                name='group_missing_reason'
            )
            reason[~product_df['universe']] \
                = reason_codes['out_of_universe'] # 第1优先级，写入 out_of_universe 
            reason[
                reason.isna() &
                product_df['factor'].isna()
            ] = reason_codes['factor_self_missing'] # 第二优先级，写入 factor_self_missing
                    
            if sg.invalid_code is not None: # 非法组
                assignment = pd.Series(
                    pd.NA, # type: ignore
                    index=product_df.index,
                    name='group_assignment',
                    dtype='Int64',
                ) 
                reason[reason.isna()] = reason_codes['invalid_group'] # 第3优先级，写入 invalid_group
            else: # 合法组->尝试做分组，若分组失败则分组值设为全nan
                try:
                    assignment = _assign_quantile_groups(
                        factor=sg.data['factor'],
                        n_groups=n_groups,
                        grouping_direction=grouping_direction
                    )
                except ValueError:
                    assignment = pd.Series(
                        pd.NA, # type: ignore
                        index=product_df.index,
                        name='group_assignment',
                        dtype='Int64',
                    ) 
                    reason[reason.isna()] = reason_codes['qcut_failed'] # 第4优先级，写入 qcut_failed
                else:
                    assignment = assignment.reindex(product_df.index) 
                    reason[
                        product_df['factor'].isna() & 
                        product_df['universe']
                        ] = reason_codes['factor_self_missing']  
            group_assignment[prod] = assignment
            group_metadata[prod] = meta
            group_missing_reason[prod] = reason
        # 2. 计算分组收益和高低组收益差
        merged = pd.merge(assignment.rename('group').dropna(), 
                          product_df['label'], how='left', 
                          left_index=True, right_index=True)
        forward_returns = _forward_mean_returns_by_group(merged)
        forward_returns = forward_returns.reindex(range(1, n_groups+1)) # 确保每一个组都存在
        ret_frame = forward_returns.to_frame().T
        ret_frame.columns = [f'group{int(n)}' for n in ret_frame.columns]
        high_low_diff = _high_minus_low(ret_frame, 
                                        grouping_direction,n_groups)
        assert high_low_diff.size == 1
        high_low_diff = float(high_low_diff.squeeze())

        grouped_forward_returns[prod] = forward_returns
        high_minus_low[prod] = high_low_diff

    return TSGroupedEvaluationResults(
        min_observations=min_observations,
        nan_policy=nan_policy,
        n_groups=n_groups,
        grouping_direction=grouping_direction,
        ts_grouping_method=ts_grouping_method,
        rolling_window=rolling_window,
        group_assignment=group_assignment,
        group_metadata=group_metadata,
        group_missing_reason=group_missing_reason,
        grouped_forward_returns=grouped_forward_returns,
        high_minus_low=high_minus_low
    )

def grouped_details(
        mode: Literal['cross_sectional', 'time_series'], 
        df: pd.DataFrame,
        n_groups: int,
        min_observations: int,
        nan_policy: Literal['strict','drop'] = 'drop',
        grouping_direction: Literal[
            'ascending','descending']='ascending',
        ts_grouping_method: Literal[
        'expanding', 'rolling', 'full_sample']='expanding',
        rolling_window: int | None = None
) -> CSGroupedEvaluationResults | TSGroupedEvaluationResults:
    # 校验输入参数
    if not isinstance(df, pd.DataFrame):
        raise TypeError('df 必须是 pandas DataFrame。')
    if df.empty:
        raise ValueError('df 不能为空。')
    if mode not in {'cross_sectional', 'time_series'}:
        raise ValueError(f'mode 非法:{mode!r}。')
    if nan_policy not in {'strict', 'drop'}:
        raise ValueError(f'非法的 nan_policy: {nan_policy!r}。')
    if min_observations < 1:
        raise ValueError('min_observations 必须大于等于 1。')
    if n_groups < 2:
        raise ValueError('n_groups 必须大于等于 2。')
    if grouping_direction not in {'ascending', 'descending'}:
        raise ValueError(
            f'非法的 grouping_direction: {grouping_direction!r}。')
    
    if mode == 'time_series':
        if ts_grouping_method not in {'expanding', 'rolling','full_sample'}:
            raise ValueError(
                f'非法的 ts_grouping_method: {ts_grouping_method!r}。')
        if ts_grouping_method == 'rolling':
            if rolling_window is None:
                raise ValueError('rolling 分组必须提供 rolling_window。')
            if isinstance(rolling_window, bool) or not isinstance(
                    rolling_window, int):
                raise TypeError('rolling_window 必须是整数。')
            if rolling_window < min_observations:
                raise ValueError(
                    f'rolling_window={rolling_window} 必须大于等于 '
                    f'min_observations={min_observations}。')
            if rolling_window < n_groups:
                raise ValueError(
                    f'rolling_window={rolling_window} 必须大于等于 '
                    f'n_groups={n_groups}。')
    if (mode == 'time_series' and ts_grouping_method != 'rolling'):
        if rolling_window is not None:
            raise ValueError(f'ts_grouping_method为{ts_grouping_method}，\
                             不接受 rolling 参数。')
    if mode == 'cross_sectional':
        if rolling_window is not None:
            raise ValueError(f'mode 为 corss_sectional 时不接受 rolling 参数。')

    # 校验输入参数df相关属性
    check_df_columns_exist(df, ['factor','label','universe'])
    if not isinstance(df.index, pd.MultiIndex):
        raise TypeError('df 的索引必须是 MultiIndex。')
    if list(df.index.names) != ['timestamp', 'product']:
        raise ValueError(
            "df 的索引层级必须依次命名为 ['timestamp', 'product']。")
    timestamp_index = df.index.get_level_values('timestamp')
    if not isinstance(timestamp_index, pd.DatetimeIndex):
        raise TypeError('df 的 timestamp 索引层级必须是 DatetimeIndex。')
    for column in ('factor', 'label'):
        if not pd.api.types.is_numeric_dtype(df[column].dtype):
            raise TypeError(f'df 的 {column} 列必须是数值类型。')
        non_missing = df[column].dropna().to_numpy(dtype=float)
        if np.isinf(non_missing).any():
            raise ValueError(f'df 的 {column} 列不能包含正负无穷。')
    if not pd.api.types.is_bool_dtype(df['universe'].dtype):
        raise TypeError('df 的 universe 列必须是布尔类型。')
    if df['universe'].isna().any():
        raise ValueError('df 的 universe 列不能包含缺失值。')
    if not timestamp_index.is_monotonic_increasing:
        raise ValueError('df 的索引的时间戳层级非单调递增。')
    if not df.index.is_unique:
        raise ValueError('df 的索引并非 unique。')
    
    if mode == 'cross_sectional':
        return _cs_grouped_details(
            df=df,
            min_observations=min_observations,
            nan_policy=nan_policy,
            n_groups=n_groups,
            grouping_direction=grouping_direction
        )
    return _ts_grouped_details(
        df=df,
        min_observations=min_observations,
        nan_policy=nan_policy,
        n_groups=n_groups,
        grouping_direction=grouping_direction,
        ts_grouping_method=ts_grouping_method,
        rolling_window=rolling_window
    )


if __name__ == '__main__':
    # 构造确定性的虚构多品种面板，供人工观察整个 grouped 模块能否跑通。
    demo_timestamps = pd.date_range(
        '2026-01-01', periods=40, freq='D', name='timestamp'
    )
    demo_products = [f'P{i:02d}' for i in range(1, 9)]
    demo_index = pd.MultiIndex.from_product(
        [demo_timestamps, demo_products],
        names=['timestamp', 'product'],
    )
    time_position = np.repeat(
        np.arange(len(demo_timestamps)), len(demo_products)
    )
    product_position = np.tile(
        np.arange(len(demo_products)), len(demo_timestamps)
    )
    demo_df = pd.DataFrame(
        {
            'factor': (
                product_position
                + 0.15 * time_position
                + 0.05 * np.sin(time_position + product_position)
            ),
            'label': (
                0.001 * (product_position - 3.5)
                + 0.0002 * time_position
                + 0.0005 * np.cos(time_position + product_position)
            ),
            'universe': True,
        },
        index=demo_index,
    )
    demo_df.loc[(demo_timestamps[5], 'P02'), 'universe'] = False
    demo_df.loc[(demo_timestamps[18], 'P07'), 'universe'] = False
    demo_df.loc[(demo_timestamps[9], 'P03'), 'factor'] = np.nan
    demo_df.loc[(demo_timestamps[25], 'P07'), 'factor'] = np.nan

    demo_cases = [
        (
            'cross_sectional',
            {
                'mode': 'cross_sectional',
            },
        ),
        (
            'time_series / full_sample',
            {
                'mode': 'time_series',
                'ts_grouping_method': 'full_sample',
            },
        ),
        (
            'time_series / expanding',
            {
                'mode': 'time_series',
                'ts_grouping_method': 'expanding',
            },
        ),
        (
            'time_series / rolling',
            {
                'mode': 'time_series',
                'ts_grouping_method': 'rolling',
                'rolling_window': 12,
            },
        ),
    ]
    preview_attributes = (
        'group_assignment',
        'group_metadata',
        'group_missing_reason',
        'grouped_forward_returns',
        'high_minus_low',
    )

    for case_name, case_parameters in demo_cases:
        print(f'\n{"=" * 24} {case_name} {"=" * 24}')
        demo_result = grouped_details(
            df=demo_df,
            n_groups=4,
            min_observations=8,
            nan_policy='drop',
            grouping_direction='ascending',
            **case_parameters,
        )

        for attribute_name in preview_attributes:
            attribute_value = getattr(demo_result, attribute_name)
            print(f'\n[{attribute_name}]')
            if isinstance(attribute_value, dict):
                for product in demo_products[:2]:
                    product_value = attribute_value[product]
                    print(f'\n{product}:')
                    if isinstance(product_value, (pd.Series, pd.DataFrame)):
                        print(product_value.head(10))
                    else:
                        print(product_value)
            else:
                print(attribute_value.head(10))
