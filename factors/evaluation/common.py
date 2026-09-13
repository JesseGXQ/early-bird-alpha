"""evaluation 子包的模块级公共项。

存放 ic、grouped 等评估模块共享的常量、数据类与函数。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

SELECTED_GROUP_INVALID_CODES = {
    1: 'has_missing_nan_policy_strict',
    9: 'empty_universe',
    3: 'insufficient_valid_obs',
    4: 'factor_single_value',
    5: 'label_single_value',
}

@dataclass(frozen=True)
class SelectedGroup:
    nan_policy: Literal['strict', 'drop']
    min_observations: int
    data: pd.DataFrame | None
    n_eligible: int # 品种池内样本数，未考虑是否合格（是否有缺失值）
    n_valid: int | None
    n_dropped: int | None
    invalid_code: int | None

def _single_value(
        values: pd.Series
    ) -> bool:
    if values.nunique(dropna=True) == 1:
        return True
    return False

def select_group_sample(
        group: pd.DataFrame,
        min_observations: int,
        nan_policy: Literal['strict', 'drop'],
        missing_rejection_scope_mode_strict: Literal[
            'factor_and_label', 'factor'] = 'factor_and_label',
        missing_drop_scope_mode_drop: Literal[
            'factor_and_label', 'factor'] = 'factor_and_label',
        single_value_handle_scope: Literal[
            'factor_and_label', 'factor'] | None = 'factor_and_label',
) -> SelectedGroup:
    """从单个评价单元（一个时间截面或一个品种的 factor/label 面板）中，
    依据品种池、缺失值策略与样本数门槛筛选合格样本。

    筛选流程：
    1. 取 ``universe`` 为真的行作为 eligible 样本；为空则 invalid_code=9；
    2. 按 ``nan_policy`` 处理缺失值；
    3. 有效样本数不足 ``min_observations`` 则 invalid_code=3；
    4. single value 检查：factor 常数则 invalid_code=4，label 常数则
       invalid_code=5（仅当 ``single_value_handle_scope='factor_and_label'``）；
    5. 通过检查则返回 data，invalid_code=None。

    Parameters
    ----------
    group : pd.DataFrame
        单个评价单元的数据，须含列 ``factor``、``label``、``universe``。
    min_observations : int
        判定有效所需的最小样本数。
    nan_policy : {'strict', 'drop'}
        缺失值处理策略。strict 表示存在缺失即整组失效（invalid_code=1）；
        drop 表示剔除缺失样本后再判断样本数。
    missing_rejection_scope_mode_strict : {'factor_and_label', 'factor'}, optional
        strict 策略下缺失检查的范围。'factor_and_label' 表示 factor 或
        label 任一缺失即整组失效；'factor' 表示只检查 factor 的缺失，
        label 缺失不影响整组有效性（缺失 label 的行会随 data 传给下游）。
        默认 'factor_and_label'。
    missing_drop_scope_mode_drop : {'factor_and_label', 'factor'}, optional
        drop 策略下剔除缺失样本的范围。'factor_and_label' 表示 factor 或
        label 任一缺失的行都剔除；'factor' 表示只剔除 factor 缺失的行。
        默认 'factor_and_label'。
    single_value_handle_scope : {'factor_and_label', 'factor'} or None, optional
        single value 检查的范围。'factor_and_label' 表示 factor 与 label
        都做常数检查（invalid_code=4/5）；'factor' 表示只检查 factor
        （只可能返回 invalid_code=4，不会返回 5）；None 表示不做任何
        single value 检查（不会返回 invalid_code=4 或 5）。
        默认 'factor_and_label'。

    Returns
    -------
    SelectedGroup
        筛选结果。data 仅在 invalid_code 为 None 时非空。
    """
    if missing_rejection_scope_mode_strict not in {'factor_and_label', 'factor'}:
        raise ValueError(
            f'非法的 missing_rejection_scope_mode_strict: '
            f'{missing_rejection_scope_mode_strict}。')
    if missing_drop_scope_mode_drop not in {'factor_and_label', 'factor'}:
        raise ValueError(
            f'非法的 missing_drop_scope_mode_drop: '
            f'{missing_drop_scope_mode_drop}。')
    if single_value_handle_scope not in {'factor_and_label', 'factor', None}:
        raise ValueError(
            f'非法的 single_value_handle_scope: '
            f'{single_value_handle_scope}。')
    if not pd.api.types.is_bool_dtype(group["universe"].dtype):
        raise TypeError("universe 必须是布尔列。")
    if group["universe"].isna().any():
        raise ValueError("universe 不能包含缺失值。")

    # 1. 首先根据品种池做筛选
    eligible = group.loc[
        group['universe'], ['factor', 'label']
    ]
    n_eligible = len(eligible)
    if n_eligible == 0: # 空品种池
        return SelectedGroup(
            nan_policy = nan_policy,
            min_observations = min_observations,
            data = None,
            n_eligible = 0,
            n_valid = None,
            n_dropped = None,
            invalid_code = 9
        )

    # 2. 按 nan_policy 处理缺失值
    if nan_policy == 'strict':
        if missing_rejection_scope_mode_strict == 'factor':
            has_missing = eligible['factor'].isna().any()
        else:
            has_missing = eligible.isna().any(axis=None)
        if has_missing:
            return SelectedGroup(
                nan_policy = nan_policy,
                min_observations = min_observations,
                data = None,
                n_eligible = n_eligible,
                n_valid = None,
                n_dropped = None,
                invalid_code = 1
            )
        valid = eligible
        n_valid = n_eligible
        n_dropped = None
    else: # nan_policy = 'drop'
        if missing_drop_scope_mode_drop == 'factor':
            valid = eligible.dropna(subset=['factor'], how='any')
        else:
            valid = eligible.dropna(
                subset=['factor', 'label'],
                how='any'
            )
        n_valid = len(valid)
        n_dropped = n_eligible - n_valid

    # 3. 有效样本数门槛
    if n_valid < min_observations:
        return SelectedGroup(
            nan_policy = nan_policy,
            min_observations = min_observations,
            data = None,
            n_eligible = n_eligible,
            n_valid = n_valid,
            n_dropped = n_dropped,
            invalid_code = 3
        )

    # 4. single value 检查（scope 为 None 时跳过）
    if single_value_handle_scope is None:
        factor_single = False
        label_single = False
    else:
        factor_single = _single_value(valid['factor'])
        label_single = (
            _single_value(valid['label'])
            if single_value_handle_scope == 'factor_and_label'
            else False
        )
    if factor_single:
        return SelectedGroup(
            nan_policy = nan_policy,
            min_observations = min_observations,
            data = None,
            n_eligible = n_eligible,
            n_valid = n_valid,
            n_dropped = n_dropped,
            invalid_code = 4
        )
    if label_single:
        return SelectedGroup(
            nan_policy = nan_policy,
            min_observations = min_observations,
            data = None,
            n_eligible = n_eligible,
            n_valid = n_valid,
            n_dropped = n_dropped,
            invalid_code = 5
        )

    # factor_single 与 label_single 均 False
    return SelectedGroup(
        nan_policy = nan_policy,
        min_observations = min_observations,
        data = valid,
        n_eligible = n_eligible,
        n_valid = n_valid,
        n_dropped = n_dropped,
        invalid_code = None
    )

def group_to_series(
        g: SelectedGroup
    )-> pd.Series:
    d = {
        'n_eligible': g.n_eligible,
        'n_valid': g.n_valid,
        'n_dropped': g.n_dropped,
        'invalid_code': g.invalid_code
    }
    return pd.Series(d)