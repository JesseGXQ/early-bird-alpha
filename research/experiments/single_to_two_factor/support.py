"""复杂研究辅助：公开状态机、持仓风险叠加、实际权重记账与配对推断。"""
from __future__ import annotations
from dataclasses import dataclass, replace, asdict
from pathlib import Path
from typing import Any
import hashlib
import json
import numpy as np
import pandas as pd
from factors.diagnostic_portfolio.models import DiagnosticPortfolioResult, DiagnosticPortfolioSpec
from factors.diagnostic_portfolio.weights import MarketRankLSWeightStateMachine, TSInverseVolatilityWeightMachine
from factors.diagnostic_portfolio.risk import DiagnosticPortfolioRiskManager
from factors.diagnostic_portfolio.accounting import (
    compute_weight_changes_turnover_cost, compute_contribution, compute_portfolio_returns,
    compute_nav, compute_exposure, compute_performance, compute_attribution_product,
    compute_attribution_direction, compute_attribution_sector,
)

CUTOFF = pd.Timestamp('2024-12-31')


def assert_development_only(*objects):
    """在研究输入边界拒绝最终保留样本。"""
    for obj in objects:
        index = obj.index
        dates = index.get_level_values('timestamp') if isinstance(index, pd.MultiIndex) else index
        if len(dates) and pd.to_datetime(dates).max() > CUTOFF:
            raise ValueError('输入越过 2024-12-31，禁止访问最终保留样本。')


def protocol_fingerprint(protocol: dict) -> str:
    """协议独立于结果；不得向后移动截止日期。"""
    if pd.Timestamp(protocol['end_date']) > CUTOFF:
        raise ValueError('协议截止日期越过保留样本边界。')
    return hashlib.sha256(json.dumps(protocol, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def frame_fingerprint(frame) -> str:
    """实际输入内容指纹只包含开发期。"""
    assert_development_only(frame)
    return hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()


@dataclass
class MatrixPanel:
    """已截断的全局交易日 × 品种矩阵；缺失资格只能为 False。"""
    returns: pd.DataFrame
    eligible: pd.DataFrame
    classification: pd.DataFrame | None = None

    @classmethod
    def from_dataset(cls, dataset, eligible=None):
        assert_development_only(dataset.main_panel)
        ret = dataset.main_panel['True_Return'].unstack('product').sort_index().sort_index(axis=1)
        mask = dataset.universe.eligible if eligible is None else eligible
        assert_development_only(mask)
        elig = mask.unstack('product').reindex_like(ret).fillna(False).astype(bool)
        return cls(ret, elig, (dataset.auxiliary or {}).get('classification'))

    def with_eligible(self, eligible):
        return replace(self, eligible=eligible.reindex_like(self.eligible).fillna(False).astype(bool))


@dataclass
class WeightRun:
    """真正参与记账的决策持仓，附加成分抵消前后的审计信息。"""
    weights: pd.DataFrame
    flags: pd.Series
    risk: pd.DataFrame
    warnings: tuple[str, ...] = ()
    raw_mix: pd.DataFrame | None = None
    components: dict[str, pd.DataFrame] | None = None


def _simulation_range(panel, start, end, spec):
    times = panel.returns.index
    first_return = int(times.searchsorted(pd.Timestamp(start)))
    last_return = int(times.searchsorted(pd.Timestamp(end), side='right')) - 1
    if first_return < 1 or last_return < first_return:
        raise ValueError('评价区间需要先行决策时点和至少一期收益。')
    warmup = 0 if spec.risk is None else max(spec.risk.volatility_lookback, spec.risk.covariance_lookback or 0)
    first_decision = first_return - 1
    return times, max(0, first_decision - warmup), first_decision, last_return


def simulate_signal(panel, signal, spec, start, end, *, offset=0, audit=False):
    """复用公开权重状态机，风险预热不建仓，年度切换不重置状态。"""
    assert_development_only(panel.returns, signal)
    if not 0 <= offset < spec.rebalance.interval:
        raise ValueError('offset 必须属于 [0, interval)。')
    times, left, first_decision, last_return = _simulation_range(panel, start, end, spec)
    products = panel.returns.columns.tolist()
    machine_cls = MarketRankLSWeightStateMachine if spec.harvest_profile.name == 'market_rank_ls' else TSInverseVolatilityWeightMachine
    machine = machine_cls(spec, products)
    observer = DiagnosticPortfolioRiskManager(spec.risk, len(products)) if audit and spec.risk else None
    ret, elig = panel.returns.to_numpy(float), panel.eligible.to_numpy(bool)
    sig = signal.reindex_like(panel.returns).to_numpy(float)
    out, flags, records, messages = [], [], [], []
    for i in range(left, last_return):
        ts = times[i]
        if observer is not None:
            observer.append(ret[i], ts)
        active = i >= first_decision
        rebalance = active and i % spec.rebalance.interval == offset
        current = machine.update(rebalance, elig[i] if active else np.zeros(len(products), bool),
                                 sig[i] if active else np.full(len(products), np.nan), ret[i], timestamp=ts)
        if active:
            out.append(current.weights)
            flags.append(rebalance)
            messages.extend(f'{ts.date()}: {x}' for x in current.warnings)
            record = {'timestamp': ts, 'rebalance': rebalance}
            meta = current.metadata.cov_mat_estimation_meta
            if meta is not None:
                record.update(meta.to_dict())
            if observer is not None and rebalance and np.any(current.weights):
                used = current.weights != 0
                record['ex_ante_final_volatility'] = observer.ex_ante_portfolio_volatility_annualized(current.weights[used], spec.annualization_factor, used).value
            if audit:
                records.append(record)
    idx = times[first_decision:last_return]
    risk = pd.DataFrame(records).set_index('timestamp') if records else pd.DataFrame(index=idx)
    return WeightRun(pd.DataFrame(out, index=idx, columns=products),
                     pd.Series(flags, index=idx, name='rebalance_flags'), risk, tuple(messages))


def cap_market_weights(weights, cap):
    """同侧比例分配并封顶，保持两侧共同可达的净零敞口。"""
    positive, negative = weights > 0, weights < 0
    out = np.zeros_like(weights)
    if not positive.any() or not negative.any():
        return out
    target = 0.5 if cap is None else min(0.5, positive.sum()*cap, negative.sum()*cap)
    for mask, sign in ((positive, 1), (negative, -1)):
        base = np.abs(weights[mask])
        assigned, free = np.zeros_like(base), np.ones(len(base), bool)
        remaining = target
        while free.any() and remaining > 1e-15:
            proposal = remaining * base[free] / base[free].sum()
            if cap is None or (proposal <= cap + 1e-15).all():
                assigned[free] = proposal
                break
            saturated = np.flatnonzero(free)[proposal > cap]
            assigned[saturated] = cap
            free[saturated] = False
            remaining = target - assigned.sum()
        out[mask] = sign * assigned
    return out


def combine_positions(panel, first, second, alpha, spec, *, audit=False):
    """合并实际持仓后只做组合风险缩放，禁止再次逆波动率。"""
    if not first.weights.index.equals(second.weights.index) or not first.flags.equals(second.flags):
        raise ValueError('成分必须使用同一决策时间轴与调仓相位。')
    idx, products = first.weights.index, panel.returns.columns
    ratios = pd.Series(alpha, index=idx) if np.isscalar(alpha) else alpha.reindex(idx)
    if ratios.isna().any() or not ratios.between(0, 1).all():
        raise ValueError('组合比例必须为 [0,1] 内已知值。')
    manager = DiagnosticPortfolioRiskManager(spec.risk, len(products)) if spec.risk else None
    if manager is not None:
        end = panel.returns.index.get_loc(idx[0])
        left = max(0, end-max(spec.risk.volatility_lookback, spec.risk.covariance_lookback))
        for ts, row in panel.returns.iloc[left:end].iterrows():
            manager.append(row.to_numpy(), ts)
    out, raw_rows, records, messages = [], [], [], []
    held = np.zeros(len(products))
    ratio_held = float(ratios.iloc[0])
    for i, ts in enumerate(idx):
        elig = panel.eligible.loc[ts].to_numpy(bool)
        if manager is not None:
            manager.append(panel.returns.loc[ts].to_numpy(), ts)
        rebalance = bool(first.flags.iloc[i])
        if rebalance:
            ratio_held = float(ratios.iloc[i])
        raw = ratio_held*first.weights.iloc[i].to_numpy() + (1-ratio_held)*second.weights.iloc[i].to_numpy()
        raw = np.where(elig, raw, 0.0)
        record = {'timestamp': ts, 'raw_mix_gross': np.abs(raw).sum(), 'alpha': ratio_held, 'rebalance': rebalance}
        if not rebalance:
            held = np.where(elig, held, 0.0)
        elif spec.harvest_profile.name == 'market_rank_ls':
            held = cap_market_weights(raw, spec.constraint.max_product_weight)
        else:
            held = raw.copy()
            held[manager.invalid_mask()] = 0.0
            gross = np.abs(held).sum()
            if gross > 0:
                held /= gross
                active = held != 0
                estimate = manager.ex_ante_portfolio_volatility_annualized(held[active], spec.annualization_factor, active)
                record.update(estimate.cov_mat_estimation_meta.to_dict())
                record['ex_ante_base_volatility'] = estimate.value
                messages.extend(f'{ts.date()}: {x}' for x in estimate.warnings)
                scaler = min(spec.constraint.max_volatility_scaler, spec.risk.target_volatility/estimate.value) if estimate.value > 0 else 0.0
                record['scaling_factor'] = scaler
                held *= scaler
                gross = np.abs(held).sum()
                if spec.constraint.max_gross is not None and gross > spec.constraint.max_gross:
                    held *= spec.constraint.max_gross/gross
                if spec.constraint.max_product_weight is not None:
                    held = np.clip(held, -spec.constraint.max_product_weight, spec.constraint.max_product_weight)
                if audit and np.any(held):
                    active = held != 0
                    record['ex_ante_final_volatility'] = manager.ex_ante_portfolio_volatility_annualized(held[active], spec.annualization_factor, active).value
        out.append(held.copy())
        raw_rows.append(raw)
        if audit:
            records.append(record)
    risk = pd.DataFrame(records).set_index('timestamp') if records else pd.DataFrame(index=idx)
    return WeightRun(pd.DataFrame(out, index=idx, columns=products), first.flags.copy(), risk,
                     tuple(messages), pd.DataFrame(raw_rows, index=idx, columns=products),
                     {'first': first.weights, 'second': second.weights})


def to_long(frame, name):
    """显式保留全局网格中的缺失格子。"""
    return frame.rename_axis(index='timestamp', columns='product').stack(dropna=False).rename(name)


def target_ledger(panel, weights, spec):
    """全局下一交易日对齐；包含首次建仓、退出和跨年变仓成本。"""
    assert_development_only(panel.returns, weights)
    if weights.empty or not np.isfinite(weights.to_numpy()).all():
        raise ValueError('决策持仓为空或含非有限值。')
    if not weights.columns.equals(panel.returns.columns):
        raise ValueError('持仓品种顺序必须与主面板完全一致。')
    times = panel.returns.index
    positions = times.get_indexer(weights.index)
    if (positions < 0).any() or (positions+1 >= len(times)).any() or np.any(np.diff(positions) != 1):
        raise ValueError('持仓必须覆盖连续决策时间轴，并有对应下一期收益。')
    w = to_long(weights, 'weight')
    changes, turnover, cost = compute_weight_changes_turnover_cost(w, spec.cost)
    realized = times[positions+1]
    shifted = weights.copy()
    shifted.index = realized
    aligned = pd.concat([to_long(panel.returns.loc[realized], 'true_return'), to_long(shifted, 'weight')], axis=1)
    contribution, warning, metadata = compute_contribution(aligned, missing_return_policy=spec.missing_return_policy)
    returns = compute_portfolio_returns(contribution, pd.Series(cost.to_numpy(), index=realized, name='cost'))
    return w, changes, turnover, cost, contribution, aligned['weight'], returns, warning, metadata


def evaluate_target_weights(panel, run, spec, *, name, metadata=None):
    """只用公开记账组件，返回项目既有结果类型。"""
    w, changes, turnover, cost, contribution, shifted, returns, warning, facts = target_ledger(panel, run.weights, spec)
    exposure = compute_exposure(w, 1e-12)
    nonzero = exposure.index[exposure.gross_exposure > 1e-12]
    performance, perf_warning = compute_performance(returns.gross_return, returns.proxy_net_return, nonzero[0] if len(nonzero) else None, spec.annualization_factor)
    sector = compute_attribution_sector(contribution, panel.classification, 'sector') if panel.classification is not None else None
    risk = run.risk.copy()
    risk['realized_volatility'] = (returns.proxy_net_return.rolling(60, min_periods=40).std()*np.sqrt(spec.annualization_factor)).reindex(risk.index)
    return DiagnosticPortfolioResult(signal_name=name, spec=spec, target_weights=w, rebalance_flags=run.flags,
        weight_changes=changes, turnover=turnover, decision_cost=cost, product_contributions=contribution,
        portfolio_returns=returns, nav=compute_nav(returns.gross_return, returns.proxy_net_return), exposures=exposure,
        risk=risk, product_attribution=compute_attribution_product(contribution, shifted, 1e-12),
        direction_attribution=compute_attribution_direction(contribution, shifted), sector_attribution=sector,
        performance=performance, status=None, warnings=(*run.warnings, *warning, *perf_warning),
        metadata={'time_convention': '决策 t → 全局下一交易日收益；成本后移一期',
                  'replay_kind': 'actual_target_weights', **facts, **(metadata or {})})


def score_run(panel, run, spec):
    """候选采用与正式实验相同的实际权重记账。"""
    _, _, turnover, _, _, _, ret, _, _ = target_ledger(panel, run.weights, spec)
    values = ret.proxy_net_return
    std = values.std(ddof=1)
    return {'n_observations': len(values), 'sharpe': float(values.mean()/std*np.sqrt(spec.annualization_factor)) if std > 0 else np.nan,
            'turnover': float(turnover.mean()), 'net_mean': float(values.mean())}


def paired_block_bootstrap(returns, comparisons, *, block_size, reps, seed, annualization, family_size, practical_delta):
    """同一日期块共同抽取所有策略，返回家族校正后的配对区间。"""
    if returns.isna().any().any() or not returns.index.is_unique:
        raise ValueError('配对收益必须是相同日期上的完整序列。')
    a, n = returns.to_numpy(float), len(returns)
    if n < 2 or not 1 <= block_size <= n or reps < 2 or family_size < len(comparisons):
        raise ValueError('bootstrap 配置或样本不足。')
    positions = [(returns.columns.get_loc(x), returns.columns.get_loc(y)) for x, y in comparisons]
    rng = np.random.default_rng(seed)
    def sharpes(values):
        std = values.std(axis=0, ddof=1)
        return np.divide(values.mean(axis=0)*np.sqrt(annualization), std, out=np.full(values.shape[1], np.nan), where=std > 0)
    observed = sharpes(a)
    deltas = np.empty((reps, len(comparisons)))
    mean_deltas = np.empty_like(deltas)
    for b in range(reps):
        starts = rng.integers(n, size=int(np.ceil(n/block_size)))
        indices = ((starts[:, None]+np.arange(block_size)) % n).ravel()[:n]
        sampled, sr = a[indices], sharpes(a[indices])
        for j, (x, y) in enumerate(positions):
            deltas[b, j] = sr[x]-sr[y]
            mean_deltas[b, j] = (sampled[:, x]-sampled[:, y]).mean()*annualization
    tail, rows = 0.05/family_size/2, []
    for j, ((x, y), names) in enumerate(zip(positions, comparisons)):
        valid = np.isfinite(deltas[:, j])
        low, high = np.quantile(deltas[valid, j], [tail, 1-tail]) if valid.any() else (np.nan, np.nan)
        mean_low, mean_high = np.quantile(mean_deltas[:, j], [tail, 1-tail])
        delta = observed[x]-observed[y]
        rows.append({'new': names[0], 'base': names[1], 'delta_sharpe': delta, 'ci_low': low, 'ci_high': high,
            'annualized_mean_difference': (a[:, x]-a[:, y]).mean()*annualization, 'mean_ci_low': mean_low, 'mean_ci_high': mean_high,
            'confidence_level': 1-2*tail, 'family_size': family_size, 'block_size': block_size, 'reps': reps,
            'valid_reps': int(valid.sum()), 'decision': '改善且达到教学幅度' if low > 0 and delta >= practical_delta else '显著下降' if high < 0 else '证据不足或幅度较小'})
    return pd.DataFrame(rows)


def save_json(path, value):
    """中文 JSON 显式 UTF-8；非有限统计量记录为 null。"""
    def clean(x):
        if isinstance(x, dict):
            return {str(k): clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        if isinstance(x, (pd.Timestamp, Path)):
            return str(x)
        if isinstance(x, np.generic):
            return clean(x.item())
        if isinstance(x, float) and not np.isfinite(x):
            return None
        return x
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def save_result(root, result, panel, run, metadata):
    """保存真实持仓与实际输入；记录哈希和权重重放的准确语义。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    artifacts = {'target_weights': run.weights, 'returns': result.portfolio_returns,
        'exposures': result.exposures, 'risk': result.risk, 'performance': result.performance,
        'product_attribution': result.product_attribution, 'direction_attribution': result.direction_attribution,
        'input_returns': panel.returns.loc[run.weights.index.union(result.portfolio_returns.index)],
        'input_eligible': panel.eligible.loc[run.weights.index], 'rebalance_flags': run.flags.to_frame()}
    if run.raw_mix is not None:
        artifacts['raw_mix'] = run.raw_mix
    if result.sector_attribution is not None:
        artifacts['sector_attribution'] = result.sector_attribution.to_frame('contribution')
    if panel.classification is not None:
        artifacts['classification'] = panel.classification
    if run.components is not None:
        artifacts.update({f'component_{key}': value for key, value in run.components.items()})
    hashes = {}
    for name, frame in artifacts.items():
        path = root/f'{name}.parquet'
        frame.to_parquet(path)
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    save_json(root/'manifest.json', {'replay_kind': 'actual_target_weights', 'metadata': metadata,
        'warnings': result.warnings, 'sha256': hashes, 'full_spec': asdict(result.spec), 'spec': {'profile': result.spec.harvest_profile.name,
        'cost_rate': result.spec.cost.rate, 'annualization_factor': result.spec.annualization_factor,
        'missing_return_policy': result.spec.missing_return_policy}})


def verify_snapshot(root):
    """校验哈希并仅从实际权重重放账本，不重新估计信号。"""
    from factors.diagnostic_portfolio.models import CostConfig, ConstraintConfig, RebalanceConfig
    from factors.profiles import market_rank_ls
    root = Path(root)
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for file, expected in manifest['sha256'].items():
        if hashlib.sha256((root/file).read_bytes()).hexdigest() != expected:
            raise ValueError(f'快照完整性校验失败：{file}')
    ret = pd.read_parquet(root/'input_returns.parquet')
    weights = pd.read_parquet(root/'target_weights.parquet')
    elig = pd.read_parquet(root/'input_eligible.parquet').reindex_like(ret).fillna(False)
    spec = DiagnosticPortfolioSpec(name='weight_replay', harvest_profile=market_rank_ls,
        rebalance=RebalanceConfig(1), constraint=ConstraintConfig(), cost=CostConfig(manifest['spec']['cost_rate']),
        min_cross_section_size=2, annualization_factor=manifest['spec']['annualization_factor'], missing_return_policy='raise')
    replayed = target_ledger(MatrixPanel(ret, elig), weights, spec)[6]
    saved = pd.read_parquet(root/'returns.parquet')
    error = float(np.max(np.abs(replayed.to_numpy()-saved.to_numpy())))
    if error > 1e-10:
        raise ValueError(f'权重快照重放误差过大：{error}')
    return {'snapshot': root.name, 'max_absolute_error': error, 'files_verified': len(manifest['sha256'])}


