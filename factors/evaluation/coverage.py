"""覆盖率统计。"""
from __future__ import annotations
from typing import Literal
import pandas as pd
import numpy as np

# 注意，我们这里没有处理“为什么数据缺失”(data lineage problem)。
# 这不属于本模块任务，而是上游（因子计算、预处理、标签计算、标签处理）等模块的附属结果。

def _coverage_stats_one_group(group: pd.DataFrame) -> pd.Series:
    """统计一组数据的覆盖率相关统计量。"""
    eligible = group['universe']
    factor_valid = group['factor'].notna()
    label_valid = group['label'].notna()

    # 1. eligible: 品种池内所有品种计数
    eligible_count = eligible.sum()
    # 2. out_of_universe: 品种池外所有品种计数
    out_of_universe_count = (~eligible).sum()
    # 3. factor_valid: 品种池内且因子值有效
    factor_valid_count = (eligible & factor_valid).sum()
    # 4. label_valid: 品种池内且标签值有效
    label_valid_count = (eligible & label_valid).sum()
    # 5. joint_valid: 品种池内，因子值有效且标签值有效
    joint_valid_count = (eligible & factor_valid & label_valid).sum()
    # 6. factor_only_missing: 品种池内，因子值无效，但标签值有效
    factor_only_missing_count = (eligible & ~factor_valid & label_valid).sum()
    # 7. label_only_missing: 品种池内，因子值有效，但标签无效
    label_only_missing_count = (eligible & factor_valid & ~label_valid).sum()
    # 8. both_missing_count: 品种池内，因子值无效且标签值无效
    both_missing_count = (eligible & ~factor_valid & ~label_valid).sum()

    def rate(count):
        """有效值覆盖比例。"""
        if count == 0:
            return np.nan
        return count/eligible_count

    return pd.Series({
        "eligible_count": int(eligible_count),
        "out_of_universe_count": int(out_of_universe_count),
        "factor_valid_count": int(factor_valid_count),
        "label_valid_count": int(label_valid_count),
        "joint_valid_count": int(joint_valid_count),
        "factor_coverage": rate(factor_valid_count),
        "label_coverage": rate(label_valid_count),
        "joint_coverage": rate(joint_valid_count),
        "factor_only_missing_count": int(factor_only_missing_count),
        "label_only_missing_count": int(label_only_missing_count),
        "both_missing_count": int(both_missing_count),
    })

def _coverage_stats_by_group(
        df: pd.DataFrame,
        by: Literal['timestamp', 'product'] | None = None,
    ) -> pd.DataFrame:
    """
    根据特定的分组维度，计算覆盖率相关统计量。
    
    Paramters
    ------
    df: pd.DataFrame
        包含单个因子、单个标签以及品种池的数据。
    by: Literal['timestamp', 'product'] or None, default None
        根据哪个维度做分组统计。默认为 None， 不分组。
    
    Returns
    ---
    pd.DataFrame
        覆盖率相关统计量。若 by 不为 None，则索引为分组维度
        (timestamp或product)。
    """
    if by is None:
        return _coverage_stats_one_group(df) \
            .to_frame().T
    return df.groupby(level=by) \
        .apply(_coverage_stats_one_group)

def coverage_stats(
        df: pd.DataFrame # df 包含 factor, label, universe 三列
    ) -> dict[str, pd.DataFrame]:
    """
    计算覆盖率相关统计量。包括总体覆盖率，
    按timestamp分组统计的覆盖率，
    按product分组统计的覆盖率。
    """
    df = df.copy()

    d = {}
    d['overall'] = _coverage_stats_by_group(
        df, by=None)
    d['by_timestamp'] = _coverage_stats_by_group(
        df=df, by='timestamp')
    d['by_product'] = _coverage_stats_by_group(
        df=df, by='product')
    return d
