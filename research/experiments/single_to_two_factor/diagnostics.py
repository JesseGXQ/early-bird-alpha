"""与公开分组组件同语义的 rolling/drop 专用加速，避免逐日创建历史 DataFrame。"""
import numpy as np
import pandas as pd
from factors.evaluation.grouped import TSGroupedEvaluationResults


def rolling_time_series_groups(frame, *, window, minimum, n_groups):
    """边界只使用 t-1 以前的合资格因子；缺失标签不影响历史分组阈值。"""
    if window < minimum or minimum < 2 or n_groups < 2:
        raise ValueError('滚动分组窗口或最少样本不合法。')
    assignments, metadata, reasons, returns, differences = {}, {}, {}, {}, {}
    for product, part in frame.groupby(level='product', sort=False):
        part = part.droplevel('product').sort_index()
        eligible = part.universe
        values = part.factor.where(eligible)
        historical = values.shift(1).rolling(window, min_periods=1)
        count_eligible = eligible.astype(float).shift(1).rolling(window, min_periods=0).sum()
        count_valid = values.shift(1).rolling(window, min_periods=0).count()
        bins = np.column_stack([historical.quantile(q).to_numpy() for q in np.linspace(0,1,n_groups+1)])
        invalid = np.full(len(part), np.nan)
        invalid[count_eligible.eq(0)] = 9
        insufficient = np.isnan(invalid) & count_valid.lt(minimum).to_numpy()
        invalid[insufficient] = 3
        constant = np.isnan(invalid) & (historical.max().to_numpy() == historical.min().to_numpy())
        invalid[constant] = 4
        duplicate = np.any(np.diff(bins, axis=1) <= 0, axis=1)
        reason = np.full(len(part), np.nan)
        reason[~eligible.to_numpy()] = 1
        missing = np.isnan(reason) & part.factor.isna().to_numpy()
        reason[missing] = 2
        reason[np.isnan(reason) & np.isfinite(invalid)] = 3
        reason[np.isnan(reason) & duplicate] = 4
        groups = np.clip((bins < part.factor.to_numpy()[:,None]).sum(axis=1), 1, n_groups).astype(float)
        groups[np.isfinite(reason)] = np.nan
        assignment = pd.Series(groups, index=part.index, name='group_assignment').astype('Int64')
        meta = pd.DataFrame({'n_eligible': count_eligible,
            'n_valid': count_valid.where(count_eligible.gt(0)),
            'n_dropped': (count_eligible-count_valid).where(count_eligible.gt(0)), 'invalid_code': invalid})
        meta.index = pd.DatetimeIndex(meta.index.to_numpy(), name=meta.index.name)
        grouped = pd.concat([assignment.rename('group'),part.label],axis=1).dropna(subset=['group']).groupby('group')['label'].mean().reindex(range(1,n_groups+1))
        assignments[product],metadata[product] = assignment,meta
        reasons[product] = pd.Series(reason,index=part.index,name='group_missing_reason').astype('Int64')
        returns[product] = grouped
        differences[product] = float(grouped.iloc[-1]-grouped.iloc[0])
    return TSGroupedEvaluationResults(min_observations=minimum,nan_policy='drop',n_groups=n_groups,
        grouping_direction='ascending',ts_grouping_method='rolling',rolling_window=window,
        group_assignment=assignments,group_metadata=metadata,group_missing_reason=reasons,
        grouped_forward_returns=returns,high_minus_low=differences)

