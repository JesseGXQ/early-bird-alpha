"""因子研究-参数扫描组件箱。"""
from __future__ import annotations
from dataclasses import field, replace, dataclass
from typing import Any, Literal
from collections.abc import Callable, Mapping, Sequence, Hashable
from factors.library.base import Factor
import pandas as pd
from uuid import uuid4
from logging import getLogger
from itertools import product

from tqdm import tqdm

logger = getLogger(__name__)

@dataclass(frozen=True)
class ScanCandidate: 
    cid: str 
    params: Mapping[str, Any] # 提供给 run_scan 中 evaluate_func 的参数

@dataclass(frozen=True)
class EvaluationResult:
    metrics: Mapping[str, float]
    warnings: tuple[str, ...] = ()

@dataclass(frozen=True)
class ScanResult:
    sid: str 
    eval_metrics: pd.DataFrame # 以 candidate_id 为索引
    candidate_params: pd.DataFrame # 以 candidate_id 为索引
    errors: Mapping[str, str] | None # 以 candidate_id 为key
    warnings: Mapping[str, tuple[str,...]] # 以 candidate_id 为key

def gen_candidate_grid(
        param_grid: Mapping[str, Sequence[Any]] 
 ) -> list[ScanCandidate]:
    # param_gird: 
    # like {'lookback': (5, 10, 15,), 'rebalance_interval': (5, 10, 15, 20)}, 
    # here str(key) is parameter name
    param_names = list(param_grid.keys())
    param_sequences = param_grid.values()
    cart_product = product(*param_sequences) # cartesian product
    candidates: list[ScanCandidate] = []
    for k, pvals in enumerate(cart_product):
        cid = f"cand_{k:04d}"
        params = dict(zip(param_names, pvals))
        candidates.append(ScanCandidate(cid, params))
    return candidates

def join_candidates_sequences(
        *seq: Sequence[ScanCandidate] 
) -> list[ScanCandidate]:
    """将多个候选集合合并起来，给每个候选生成新的、唯一的 cid。"""
    joined_cands: list[ScanCandidate] = []
    k = 0
    for candidates in seq:
        for c in candidates:
            cid = f'cand_{k:04d}'
            cand_new_cid = ScanCandidate(cid=cid, params=c.params)
            joined_cands.append(cand_new_cid)
            k +=1
    return joined_cands


# 这里 factor_param_grid 的值是 Sequence[Hashable]，是因为这里的想法是
# 把“因子参数”作为返回的字典的键。但是注意，这里的局限是，不是每个因子的参数都能被哈希，
# 例如 CurveSlop 因子有个参数contract_filter_rule : Mapping[str, int] or None
# 这个参数本身可以接受字典，因而是不可哈希的。所以这个函数的设计并不完善。
# 这里返回的字典的键，其本质是表达“因子计算时”的唯一决定身份的东西，而因子参数很自然地是，
# 但是也并不意味着不能通过构造一个字符串来作为键，例如 curve_slope.ipynb 中的做法，只要
# 这个字符串可以唯一决定返回中的因子值Series即可。
def precompute_shared_factors(
        factor_cls: type[Factor], 
        factor_param_grid: dict[str, Sequence[Hashable]],
        data_for_compute: pd.DataFrame | Mapping[str, pd.DataFrame],
        print_out: bool = True
) -> dict[tuple[Hashable,...], pd.Series]:
    """生成共享的原始因子值字典。
    
    Parameters
    ------
    factor_cls: type[Factor]
        一个继承 `Factor` 基类的因子。
    factor_param_grid: dict[str, Sequence[Hashable]]
        传给因子实例构造的参数的网格。
        例： `MomentumFactor`，可以为 ``{'volnorm': [True],
        'lookback': range(5, 15, 5), 'vol_lookback': range(10, 60, 20), 
        'vol_min_periods': [10],}``，也可以为 ``{'volnorm': [False],
        'lookabck': range(5, 15, 5)}``。
    data_for_compute: pd.DataFrame or Mapping[str, pd.DataFrame]
        用于透传给 `Factor.compute` 方法的参数。
    print_out: bool, default True
        是否打印计算过程。

    Returns
    ------
    dict[tuple[Hashable, ...], pd.Series]
        字典的key为 `factor_param_grid` 的值的 cartesian proudct，value为因子值。 

    Examples
    ------
    >>> grid_norm = {'volnorm': [True], 'lookback': range(5, 15, 5), 
        'vol_lookback': range(10, 60, 20), 'vol_min_periods': [10],}
    >>> grid_raw = {'volnorm': [False], 'lookback': range(5, 15, 5)}
    >>> data_norm = precompute_shared_factors(MomentumFactor, grid_norm, dataset.main_panel)
    >>> data_raw = precompute_shared_factors(MomentumFactor, grid_raw, datset.main_panel)
    >>> data = data_norm | data_raw

    """
    cands = gen_candidate_grid(factor_param_grid)
    datas: dict[tuple[Hashable,...], pd.Series] = {}
    for c in cands:
        factor = factor_cls(**c.params)
        if print_out:
            print(f'computing fator values for: {factor.name},'
                  f'parameters are `{c.params}` ', flush=True)
        key = tuple(c.params.values())
        datas[key] = factor.compute(data_for_compute)
    return datas

def run_scan(
        evaluate_func: Callable[[Mapping[str, Any]], EvaluationResult],
        candidates: Sequence[ScanCandidate],
        error_policy: Literal['raise', 'skip_and_record'] = 'raise',
        progress_bar: bool = False,
        desc: str = 'scanning parameters'
) -> ScanResult:

    # 首先检查每个 ScanCandidate，必须有唯一的cid
    cids = [c.cid for c in candidates]
    if len(cids) != len(set(cids)):
        raise ValueError(f'candidates 中的 ScanCandiadate 存在重复的 cid。'
                         '修改输入，保证所有扫描候选的 cid 唯一。')
    
    metrics: Mapping[str, Mapping[str, float]] = {}
    warnings: Mapping[str, tuple[str, ...]] = {}
    errors: Mapping[str, str] = {}

    with tqdm(total=len(candidates), 
              desc=desc, dynamic_ncols=True, disable=not progress_bar
    ) as pbar:
        for cand in candidates:
            pbar.update(1) 
            cid, params = cand.cid, cand.params
            try:
                rst = evaluate_func(params)
            except Exception as e:
                if error_policy == 'raise':
                    raise RuntimeError(f'运行参数扫描 cid={cid} 失败。') from e
                else:
                    errors[cid] = str(e)
            else:
                metrics[cid] = rst.metrics
                warnings[cid] = rst.warnings
    
    if not metrics: # 选了 'skip_and_record'，且所有扫描全部失败
        logger.warning(f'参数扫描{len(candidates)}次全部失败！'
                       '返回的 `ScanResult.eval_metrics` 为空 DataFrame。')
        eval_metrics = pd.DataFrame()
    else:
        eval_metrics = pd.DataFrame.from_dict(metrics, orient='index')
    
    if progress_bar:
        pbar.close() # pyright: ignore[reportPossiblyUnboundVariable]

    cand_params = pd.DataFrame(
        [c.params for c in candidates],
        index=[c.cid for c in candidates]
    )

    return ScanResult(
        sid=uuid4().hex,
        eval_metrics=eval_metrics,
        candidate_params=cand_params,
        errors=errors if error_policy == 'skip_and_record' else None,
        warnings=warnings
    )




    
    





