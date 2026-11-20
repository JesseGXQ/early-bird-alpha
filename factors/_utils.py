"""因子研究模块：校验工具等。"""

from __future__ import annotations
import pandas as pd
#import numpy as np
from collections.abc import Sequence
    
def verify_index_inclusion(
        values: pd.Series, eligible: pd.Series
) -> None:
    if values.index.equals(eligible.index):
        return
    if not values.index.isin(eligible.index).all():
        raise ValueError(f'因子{values.name}的索引中存在不属于品种池准入'
                            '(eligible)索引的元素。')

def apply_eligible_mask(
        values: pd.Series, eligible: pd.Series | None
) -> pd.Series:
    """把品种池外的观测置为缺失值；``eligible`` 为 None 时原样返回。"""
    if eligible is None:
        return values
    verify_index_inclusion(values, eligible)
    return values.where(eligible.reindex(values.index))

def validate_unique_names(names: Sequence[str]) -> None:
    if len(names) != len(set(names)):
        raise ValueError(f'Duplicated factor names: {names}.')