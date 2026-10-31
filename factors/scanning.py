"""因子研究-参数扫描组件箱。"""
from __future__ import annotations
from dataclasses import field, replace, dataclass
from typing import Any, Literal
from collections.abc import Callable, Mapping, Sequence
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

    
def run_scan(
        evaluate_func: Callable[[Mapping[str, Any]], EvaluationResult],
        candidates: Sequence[ScanCandidate],
        error_policy: Literal['raise', 'skip_and_record'] = 'raise',
        progress_bar: bool = False,
        desc: str = 'scanning parameters'
) -> ScanResult:
    metrics: Mapping[str, Mapping[str, float]] = {}
    warnings: Mapping[str, tuple[str, ...]] = {}
    errors: Mapping[str, str] = {}

    if progress_bar:
        pbar = tqdm(total=len(candidates), desc=desc, dynamic_ncols=True)
    for cand in candidates:
        if progress_bar:
            pbar.update(1) # pyright: ignore[reportPossiblyUnboundVariable]
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




    
    





