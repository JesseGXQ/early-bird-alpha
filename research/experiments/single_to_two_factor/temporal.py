"""年度候选验证与检查点：参数、候选、选择规则均由 notebook 显式传入。"""
from pathlib import Path
import time
import numpy as np
import pandas as pd
from .support import simulate_signal, combine_positions, score_run


def validation_candidates(panel, signals, candidates, specs, start, end, *, cache_dir, identity, progress=True):
    """按共同输入成对评价候选，成分持仓只计算一次，保留失败原因。"""
    cache = Path(cache_dir)/f'validation_{pd.Timestamp(start).year}_{identity}.parquet'
    if cache.exists():
        table = pd.read_parquet(cache)
        if progress:
            print(f'验证 {pd.Timestamp(start).year}: 已核对输入/代码指纹，读取 {len(table)} 条候选记录。', flush=True)
        return table
    rows = []
    for profile, spec in specs.items():
        started = time.perf_counter()
        singles = {}
        for candidate in candidates.itertuples(index=False):
            record = candidate._asdict()
            record.update(profile=profile, validation_start=str(start), validation_end=str(end),
                selection_cutoff=str(end), status='ok', error='')
            try:
                if candidate.path in ('M', 'C'):
                    key = candidate.momentum if candidate.path == 'M' else candidate.carry
                    run = simulate_signal(panel, signals[key], spec, start, end)
                    singles[key] = run
                elif candidate.path == 'S':
                    signal = candidate.alpha*signals[candidate.momentum]+(1-candidate.alpha)*signals[candidate.carry]
                    run = simulate_signal(panel, signal, spec, start, end)
                else:
                    run = combine_positions(panel, singles[candidate.momentum], singles[candidate.carry], candidate.alpha, spec)
                record.update(score_run(panel, run, spec))
                if not np.isfinite(record['sharpe']):
                    record.update(status='undefined', error='无可定义的收益波动或 Sharpe。')
            except (ValueError, KeyError, ArithmeticError) as error:
                record.update(status='error', error=f'{type(error).__name__}: {error}',
                              sharpe=np.nan, turnover=np.nan, net_mean=np.nan, n_observations=0)
            rows.append(record)
        if progress:
            print(f'验证 {pd.Timestamp(start).year} / {profile}: {len(candidates)} 个候选，{time.perf_counter()-started:.1f}s。', flush=True)
    table = pd.DataFrame(rows)
    cache.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(cache, index=False)
    return table
