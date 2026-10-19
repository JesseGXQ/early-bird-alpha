# type: ignore
"""Alpha101 组合实验批量执行入口。

工作流（对应 ``prompt.md`` 的工程工作流）：

1. 按 ``DatasetSpec`` 构建一次研究数据集（2000-01-01 ~ 2026-06-26），
   2000-01-01 ~ 2001-05-31 仅作为因子暖机；
2. 把主面板 unstack 成宽表，并组装一次 :class:`Alpha101Inputs`（含合约乘数、
   名义成交额、cap 与三级分类），避免每个因子重复加载/整形数据；
3. 逐个因子计算一次信号（全历史），再与 ``main_panel`` / ``universe`` 一起
   同步截取到共同样本区间 ``[2001-06-01, 2026-06-26]``；
4. 每个信号分别在 ``market_rank_ls`` 与 ``ts_inv_vol_gross_one`` 两个收获范式下
   跑组合实验，成功一个立即保存一个；
5. 单个因子/单个实验失败不阻塞其余实验，失败与跳过原因写入
   ``artifacts/failures.jsonl``，逐条尝试记录写入 ``artifacts/run_log.jsonl``。

无需断点续跑：默认全量运行；若目标 ``(因子, 收获范式)`` 已有同标签产物则跳过
（``--overwrite`` 可忽略跳过检查，Store 会为新运行分配新的 run_id）。

用法
----
    python research/experiments/alpha101/run_alpha101_experiment.py --smoke
    python research/experiments/alpha101/run_alpha101_experiment.py
    python research/experiments/alpha101/run_alpha101_experiment.py --alphas 12,48,101
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Iterable, Sequence

# 仓库根目录：本文件在 research/experiments/alpha101/ 下。
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import pandas as pd

from data import DataFacade
from data.config import (
    AdjustmentConfig,
    EnvironmentConfig,
    RolloverConfig,
    SamplingConfig,
)
from factors.dataset import ResearchDatasetBuilder
from factors.diagnostic_portfolio import (
    ConstraintConfig,
    CostConfig,
    DiagnosticPortfolioExperimentRunner,
    DiagnosticPortfolioSpec,
    DiagnosticSignal,
    RebalanceConfig,
    RiskConfig,
)
from factors.diagnostic_portfolio.storage import DiagnosticPortfolioStore
from factors.models import (
    DatasetSpec,
    DataRequirement,
    ResearchDataset,
    UniverseSpec,
)
from factors.profiles import market_rank_ls, ts_inv_vol_gross_one
from factors.universe import Universe
from research.experiments.alpha101.alpha101_factor_definitions import (
    ALPHA101_FACTORS,
    Alpha101Factor,
    Alpha101Inputs,
    get_alpha101_factor,
)


# ============================================================
# 实验常量
# ============================================================

#: 共同样本区间起点（= 调仓锚点）。
EVAL_START = pd.Timestamp('2001-06-01')
#: 数据请求结束时间 / 共同样本区间终点。
EVAL_END = pd.Timestamp('2026-06-26')
#: 数据请求开始时间（仅用于因子暖机）。
DATA_START = datetime.date(2000, 1, 1)

#: 固定的调仓频率（全局研究时点数）。
REBALANCE_INTERVAL = 10
#: 时序配方的单品种波动率回看窗口与最少有效数。
VOLATILITY_LOOKBACK = 15
VOLATILITY_MIN_PERIODS = 7
#: 单边单位权重成本率。
COST_RATE = 3e-5
#: market_rank_ls 的最少有效品种数。
MIN_CROSS_SECTION_SIZE = 5

#: 三个无行情的月均价产品，需从品种池中排除。
EXCLUDED_PRODUCTS = ('L_F', 'PP_F', 'V_F')

#: 研究报告/失败清单所在目录。
DEFAULT_ARTIFACTS_DIR = _REPO_ROOT / 'research/experiments/alpha101/artifacts'
#: 组合实验结果存储根目录。
DEFAULT_STORE_ROOT = DEFAULT_ARTIFACTS_DIR / 'diagnostic_portfolio'

TAGS = ('alpha101',)


# ============================================================
# 数据准备
# ============================================================

def build_facade_config() -> tuple:
    """构造默认的 facade 配置四元组。"""
    return (RolloverConfig(), AdjustmentConfig(),
            SamplingConfig(), EnvironmentConfig())


def build_product_universe(facade: DataFacade) -> list[str]:
    """取全市场品种并排除三个无行情的月均价产品。"""
    products = facade.rule_query.get_product_universe(return_type='list')
    return [p for p in products if p not in EXCLUDED_PRODUCTS]


def build_full_dataset(facade_config: tuple, products: Sequence[str]
                       ) -> ResearchDataset:
    """构建全历史研究数据集（含主面板与产业分类）。"""
    data_spec = DatasetSpec(
        facade_config=facade_config,
        products=tuple(products),
        data_requirements=(
            DataRequirement(source='main_panel'),
            DataRequirement(source='classification'),
        ),
        start_date=DATA_START,
        end_date=EVAL_END.date(),
        universe=UniverseSpec(min_average_volume=10000),
    )
    return ResearchDatasetBuilder(spec=data_spec).build()


def build_alpha101_inputs(dataset: ResearchDataset, facade: DataFacade
                          ) -> Alpha101Inputs:
    """把长表主面板组装成 Alpha101 所需的宽表字段集合。

    Notes
    -----
    ``multi`` 为 ``Dominant_Contract`` 对应的合约乘数；``Volume * multiplier``
    作为 ``volume``，``Volume * multiplier * Settle`` 作为名义成交额（``amount``，
    使用原始结算价），``OpenInterest * Raw_Close * multiplier`` 作为 ``cap``。
    主力缺失（``Dominant_Contract`` 为 NaN）的行乘数缺失，相应字段为 NaN，
    这是数据固有情形，预期内。
    """
    panel = dataset.main_panel
    contracts = panel['Dominant_Contract'].dropna().unique()
    multiplier_map = {
        contract: facade.rule_query.get_multiplier(contract)
        for contract in contracts
    }
    multiplier = panel['Dominant_Contract'].map(multiplier_map).astype('float64')

    def wide(series: pd.Series) -> pd.DataFrame:
        frame = series.unstack('product')
        frame.index.name = 'timestamp'
        frame.columns.name = 'product'
        return frame

    scaled_volume = panel['Volume'] * multiplier
    amount = panel['Volume'] * multiplier * panel['Settle']
    cap = panel['OpenInterest'] * panel['Raw_Close'] * multiplier

    classification = dataset.auxiliary['classification'].set_index('product')
    return Alpha101Inputs(
        open=wide(panel['Adj_Open']),
        high=wide(panel['Adj_High']),
        low=wide(panel['Adj_Low']),
        close=wide(panel['Adj_Close']),
        volume=wide(scaled_volume),
        vwap=wide(panel['Adj_Settle']),
        amount=wide(amount),
        cap=wide(cap),
        returns=wide(panel['True_Return']),
        asset_class=classification['asset_class'],
        sector=classification['sector'],
        subsector=classification['subsector'],
        panel_index=panel.index,
    )


def build_eval_dataset(dataset: ResearchDataset,
                       eval_start: pd.Timestamp = EVAL_START,
                       eval_end: pd.Timestamp = EVAL_END) -> ResearchDataset:
    """构造评价用数据集：主面板与品种池同步截取到共同样本区间。

    Notes
    -----
    ``spec`` 沿用原始数据请求规格（记录全历史请求），``quality`` 沿用原始
    质量摘要；只有 ``main_panel``、``universe.eligible``、``universe.exclude_reason``
    被同步截取，与因子信号截取口径一致。
    """
    panel = dataset.main_panel
    timestamps = panel.index.get_level_values('timestamp')
    mask = (timestamps >= eval_start) & (timestamps <= eval_end)
    return ResearchDataset(
        spec=dataset.spec,
        main_panel=panel.loc[mask],
        quality=dataset.quality,
        universe=Universe(
            eligible=dataset.universe.eligible.loc[mask],
            exclude_reason=dataset.universe.exclude_reason.loc[mask],
        ),
        auxiliary=dataset.auxiliary,
    )


def resolve_data_cutoff(dataset: ResearchDataset) -> str:
    """主面板实际行情截止日期（最后一个有复权收盘价的时间戳）。"""
    priced = dataset.main_panel['Adj_Close'].dropna()
    if priced.empty:
        return EVAL_END.date().isoformat()
    return priced.index.get_level_values('timestamp').max().date().isoformat()


# ============================================================
# 组合实验配置
# ============================================================

def build_market_rank_ls_spec(factor_name: str) -> DiagnosticPortfolioSpec:
    """构造 market_rank_ls 的完整实验配置。"""
    return DiagnosticPortfolioSpec(
        name=f'{factor_name}_market_rank_ls',
        harvest_profile=market_rank_ls,
        rebalance=RebalanceConfig(interval=REBALANCE_INTERVAL, anchor=EVAL_START),
        constraint=ConstraintConfig(),
        cost=CostConfig(rate=COST_RATE),
        min_cross_section_size=MIN_CROSS_SECTION_SIZE,
        missing_return_policy='default_to_zero_and_warning',
    )


def build_ts_inv_vol_spec(factor_name: str) -> DiagnosticPortfolioSpec:
    """构造 ts_inv_vol_gross_one 的完整实验配置。"""
    return DiagnosticPortfolioSpec(
        name=f'{factor_name}_ts_inv_vol_gross_one',
        harvest_profile=ts_inv_vol_gross_one,
        rebalance=RebalanceConfig(interval=REBALANCE_INTERVAL, anchor=EVAL_START),
        constraint=ConstraintConfig(),
        cost=CostConfig(rate=COST_RATE),
        risk=RiskConfig(
            volatility_lookback=VOLATILITY_LOOKBACK,
            volatility_min_periods=VOLATILITY_MIN_PERIODS,
        ),
        clip_bound=None,
        missing_return_policy='default_to_zero_and_warning',
    )


#: ``(收获范式名, spec 工厂)`` 列表。
PROFILE_SPEC_BUILDERS: tuple[tuple[str, Any], ...] = (
    ('market_rank_ls', build_market_rank_ls_spec),
    ('ts_inv_vol_gross_one', build_ts_inv_vol_spec),
)


# ============================================================
# 日志
# ============================================================

def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """向 jsonl 追加一行（UTF-8，``ensure_ascii=False``）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str))
        handle.write('\n')


def _failure_record(
        *, stage: str, factor: str, reason_type: str, message: str,
        profile: str | None = None, exc: BaseException | None = None,
) -> dict[str, Any]:
    """构造一条失败/跳过记录。"""
    record: dict[str, Any] = {
        'timestamp': datetime.datetime.now().astimezone().isoformat(),
        'stage': stage,
        'factor': factor,
        'profile': profile,
        'reason_type': reason_type,
        'message': message,
    }
    if exc is not None:
        record['traceback'] = ''.join(traceback.format_exception(
            type(exc), exc, exc.__traceback__))
    return record


# ============================================================
# 主流程
# ============================================================

def run(
        *,
        overwrite: bool = False,
        smoke: bool = False,
        alpha_numbers: Sequence[int] | None = None,
        store_root: Path = DEFAULT_STORE_ROOT,
        artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR,
        eval_start: pd.Timestamp = EVAL_START,
        eval_end: pd.Timestamp = EVAL_END,
) -> dict[str, Any]:
    """执行批量实验并返回运行摘要。

    Parameters
    ----------
    overwrite : bool, optional
        为 True 时忽略“已存在同 (因子, 收获范式) 产物”的跳过检查。
    smoke : bool, optional
        冒烟模式：只跑少量因子并缩短评价区间。
    alpha_numbers : sequence of int or None, optional
        限定只跑的因子编号；None 表示全部 101 个。
    store_root : pathlib.Path, optional
        组合实验结果存储根目录。
    artifacts_dir : pathlib.Path, optional
        失败清单与运行日志目录。
    eval_start, eval_end : pd.Timestamp, optional
        评价区间。

    Returns
    -------
    dict
        运行摘要（成功/跳过/失败计数与耗时等）。
    """
    if alpha_numbers is None:
        alpha_numbers = list(range(1, 102))
    if smoke:
        alpha_numbers = list(alpha_numbers)
        eval_end = min(pd.Timestamp(eval_end), pd.Timestamp('2004-12-31'))

    failures_path = artifacts_dir / 'failures.jsonl'
    run_log_path = artifacts_dir / 'run_log.jsonl'
    for path in (failures_path, run_log_path):
        if path.exists():
            path.unlink()

    print('=' * 72)
    print(f'[alpha101] 评价区间 [{eval_start.date()}, {eval_end.date()}]，'
          f'因子 {len(alpha_numbers)} 个，收获范式 {len(PROFILE_SPEC_BUILDERS)} 个')
    print('=' * 72)

    t_start = time.time()
    facade_config = build_facade_config()
    facade = DataFacade(*facade_config)
    products = build_product_universe(facade)
    print(f'[数据] 品种池 {len(products)} 个（已排除 {list(EXCLUDED_PRODUCTS)}）')

    dataset = build_full_dataset(facade_config, products)
    print(f'[数据] 主面板 {dataset.main_panel.shape}，'
          f'构建耗时 {time.time() - t_start:.1f}s')

    inputs = build_alpha101_inputs(dataset, facade)
    print(f'[数据] 宽表 {inputs.close.shape}')

    eval_dataset = build_eval_dataset(dataset, eval_start, eval_end)
    print(f'[数据] 评价主面板 {eval_dataset.main_panel.shape}')

    data_cutoff = resolve_data_cutoff(dataset)
    data_provenance = {'main_panel': data_cutoff}
    print(f'[数据] 主面板行情截止日 {data_cutoff}')

    store = DiagnosticPortfolioStore(root_dir=store_root)
    existing: set[tuple[str, str]] = set()
    if not overwrite:
        for summary in store.find(tags=list(TAGS)):
            existing.add((summary.signal_name, summary.harvest_profile))
    print(f'[存储] 既有同标签产物 {len(existing)} 条（overwrite={overwrite}）')

    eval_panel_index = eval_dataset.main_panel.index
    eval_panel = eval_dataset.main_panel
    counters = {
        'ok': 0, 'failed': 0, 'factor_compute_failed': 0,
        'degenerate_factors': 0, 'skipped_existing': 0,
    }
    planned_experiments = len(alpha_numbers) * len(PROFILE_SPEC_BUILDERS)

    for index, number in enumerate(alpha_numbers, start=1):
        factor: Alpha101Factor = get_alpha101_factor(number)
        factor_name = factor.name
        t_factor = time.time()
        try:
            full_signal = factor.compute(inputs)
            signal_values = full_signal.reindex(eval_panel_index)
        except Exception as exc:  # noqa: BLE001 - 单因子失败不阻塞
            counters['factor_compute_failed'] += 1
            _append_jsonl(failures_path, _failure_record(
                stage='factor_compute', factor=factor_name,
                reason_type='factor_compute_error', message=str(exc), exc=exc))
            print(f'[{index:3d}/{len(alpha_numbers)}] {factor_name} '
                  f'因子计算失败：{exc}')
            continue

        coverage = float(signal_values.notna().mean())
        try:
            signal = DiagnosticSignal(
                name=factor_name,
                values=signal_values,
                source_stage='raw',
                factor_name=factor_name,
                recipe=dict(factor.parameters),
            )
        except Exception as exc:  # 全 NaN / 单值 / inf 等
            counters['degenerate_factors'] += 1
            _append_jsonl(failures_path, _failure_record(
                stage='signal_construction', factor=factor_name,
                reason_type='degenerate_signal', message=str(exc)))
            _append_jsonl(run_log_path, {
                'factor': factor_name, 'status': 'skipped',
                'signal_coverage': coverage,
                'signal_nunique': int(signal_values.nunique()),
                'factor_seconds': round(time.time() - t_factor, 3),
            })
            print(f'[{index:3d}/{len(alpha_numbers)}] {factor_name} '
                  f'信号退化跳过：{exc}')
            continue

        factor_seconds = time.time() - t_factor
        for profile_name, spec_builder in PROFILE_SPEC_BUILDERS:
            spec = spec_builder(factor_name)
            key = (factor_name, profile_name)
            if key in existing:
                counters['skipped_existing'] += 1
                _append_jsonl(run_log_path, {
                    'factor': factor_name, 'profile': profile_name,
                    'status': 'skipped_existing', 'spec_name': spec.name,
                })
                print(f'[{index:3d}/{len(alpha_numbers)}] {spec.name} '
                      f'已有产物，跳过')
                continue
            t_exp = time.time()
            try:
                result = DiagnosticPortfolioExperimentRunner(
                    signal=signal, dataset=eval_dataset, spec=spec).run()
                run_id = store.save(
                    dataset=eval_dataset, signal=signal, spec=spec,
                    result=result, note=None, tags=list(TAGS),
                    data_provenance=data_provenance,
                )
            except Exception as exc:  # noqa: BLE001 - 单实验失败不阻塞
                counters['failed'] += 1
                _append_jsonl(failures_path, _failure_record(
                    stage='experiment', factor=factor_name, profile=profile_name,
                    reason_type='experiment_error', message=str(exc), exc=exc))
                _append_jsonl(run_log_path, {
                    'factor': factor_name, 'profile': profile_name,
                    'status': 'failed', 'spec_name': spec.name,
                    'message': str(exc),
                })
                print(f'[{index:3d}/{len(alpha_numbers)}] {spec.name} 失败：{exc}')
                continue

            perf = result.performance
            counters['ok'] += 1
            _append_jsonl(run_log_path, {
                'factor': factor_name, 'profile': profile_name,
                'status': 'ok', 'spec_name': spec.name, 'run_id': run_id,
                'signal_coverage': coverage,
                'signal_nunique': int(signal_values.nunique()),
                'factor_seconds': round(factor_seconds, 3),
                'experiment_seconds': round(time.time() - t_exp, 3),
                'proxy_net_sharpe': float(perf.loc['proxy_net', 'sharpe']),
                'proxy_net_total_return': float(
                    perf.loc['proxy_net', 'total_return']),
            })
            print(f'[{index:3d}/{len(alpha_numbers)}] {spec.name} 完成 '
                  f'(coverage={coverage:.2%}, '
                  f'sharpe={perf.loc["proxy_net", "sharpe"]:.3f}, '
                  f'{time.time() - t_exp:.1f}s) run_id={run_id}')

    elapsed = time.time() - t_start
    executed_experiments = counters['ok'] + counters['failed']
    summary = {
        'eval_start': str(eval_start.date()),
        'eval_end': str(eval_end.date()),
        'data_cutoff': data_cutoff,
        'products': len(products),
        'alpha_numbers': list(alpha_numbers),
        'planned_experiments': planned_experiments,
        'executed_experiments': executed_experiments,
        'not_attempted_experiments': (
            planned_experiments - executed_experiments
            - counters['skipped_existing']),
        'ok': counters['ok'],
        'failed': counters['failed'],
        'factor_compute_failed': counters['factor_compute_failed'],
        'degenerate_factors': counters['degenerate_factors'],
        'skipped_existing': counters['skipped_existing'],
        'elapsed_seconds': round(elapsed, 1),
        'store_root': str(store_root),
    }
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / 'run_summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

    print('=' * 72)
    print(f'[alpha101] 完成：计划 {planned_experiments} 个实验，'
          f'执行 {executed_experiments} 个，成功 {counters["ok"]}，'
          f'失败 {counters["failed"]}，'
          f'退化因子 {counters["degenerate_factors"]}，'
          f'总耗时 {elapsed / 60:.1f} 分钟')
    print(f'[alpha101] 失败清单：{failures_path}')
    print('=' * 72)
    return summary


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description='Alpha101 组合实验批量执行入口')
    parser.add_argument('--overwrite', action='store_true',
                        help='忽略“已有同产物则跳过”的检查')
    parser.add_argument('--smoke', action='store_true',
                        help='冒烟模式：少量因子 + 缩短评价区间')
    parser.add_argument('--alphas', type=str, default=None,
                        help='逗号分隔的因子编号，例如 12,48,101')
    parser.add_argument('--store-root', type=str, default=None,
                        help='组合实验结果存储根目录')
    parser.add_argument('--eval-start', type=str, default=None,
                        help='评价区间起点，例如 2001-06-01')
    parser.add_argument('--eval-end', type=str, default=None,
                        help='评价区间终点，例如 2026-06-26')
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口。"""
    args = _parse_args(argv)
    alpha_numbers: Iterable[int] | None = None
    if args.alphas:
        alpha_numbers = [int(item) for item in args.alphas.split(',') if item]
    elif args.smoke:
        alpha_numbers = [12, 48, 100, 101]

    store_root = Path(args.store_root) if args.store_root \
        else DEFAULT_STORE_ROOT
    if args.smoke and args.store_root is None:
        store_root = Path(tempfile_dir()) / 'alpha101_smoke_store'

    eval_start = pd.Timestamp(args.eval_start) if args.eval_start else EVAL_START
    eval_end = pd.Timestamp(args.eval_end) if args.eval_end else EVAL_END

    run(overwrite=args.overwrite, smoke=args.smoke,
        alpha_numbers=alpha_numbers, store_root=store_root,
        eval_start=eval_start, eval_end=eval_end)
    return 0


def tempfile_dir() -> str:
    """冒烟模式默认存储目录（系统临时目录下的固定子目录）。"""
    import tempfile
    return str(Path(tempfile.gettempdir()) / 'alpha101_smoke')


if __name__ == '__main__':
    raise SystemExit(main())
