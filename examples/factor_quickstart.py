"""使用小型真实行情样本运行一个最小 factors 研究闭环。

命令行使用范例（在仓库根目录执行）::

    # 1) 默认运行：使用随仓库发布的样本，净值曲线写入默认路径
    python examples/factor_quickstart.py

    # 2) 自定义净值曲线输出路径
    python examples/factor_quickstart.py --plot-output artifacts/nav.png

    # 3) 关闭转圈提示（适合重定向日志或 CI 环境）
    python examples/factor_quickstart.py --no-spinner

    # 4) 组合参数
    python examples/factor_quickstart.py \\
        --plot-output artifacts/momentum_nav.png --no-spinner

    # 5) 查看帮助
    python examples/factor_quickstart.py --help

    # 6) 以模块方式运行（PYTHONPATH 已包含仓库根目录）
    python -m examples.factor_quickstart --no-spinner
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib
import pandas as pd


matplotlib.use('Agg')
import matplotlib.pyplot as plt


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from data.config import (
    AdjustmentConfig,
    EnvironmentConfig,
    RolloverConfig,
    SamplingConfig,
)
from examples.progress import set_enabled, stage
from factors.diagnostic_portfolio import (
    ConstraintConfig,
    CostConfig,
    DiagnosticPortfolioExperimentRunner,
    DiagnosticPortfolioSpec,
    DiagnosticSignal,
    RebalanceConfig,
)
from factors.evaluation.coverage import coverage_stats
from factors.evaluation.ic import ic_details
from factors.labels import LabelBuilder
from factors.library.basic import MomentumFactor
from factors.models import (
    DataQualitySummary,
    DataRequirement,
    DatasetSpec,
    LabelSpec,
    ResearchDataset,
    UniverseSpec,
)
from factors.profiles import market_rank_ls
from factors.universe import Universe


SAMPLE_DIR = REPOSITORY_ROOT / 'examples' / 'data' / 'factor_quickstart'
DEFAULT_PLOT_PATH = (
    REPOSITORY_ROOT / 'examples' / 'artifacts' / 'factor_quickstart_nav.png')
MOMENTUM_LOOKBACK = 10
LABEL_HORIZON = 5
REBALANCE_INTERVAL = 5


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load_sample_dataset(sample_dir: Path = SAMPLE_DIR) -> ResearchDataset:
    """校验并读取随仓库发布的真实日频研究样本。"""
    metadata = json.loads(
        (sample_dir / 'metadata.json').read_text(encoding='utf-8'))
    parquet_path = sample_dir / metadata['parquet_file']
    actual_hash = _sha256(parquet_path)
    if actual_hash != metadata['parquet_sha256']:
        raise ValueError(
            'Quickstart 样本校验失败：research_sample.parquet 的 '
            'SHA-256 与 metadata.json 不一致。')

    sample = pd.read_parquet(parquet_path).sort_index()
    if sample.index.names != ['timestamp', 'product']:
        raise ValueError('样本索引必须命名为 timestamp、product。')
    required = {'True_Return', 'eligible', 'exclude_reason'}
    missing = sorted(required.difference(sample.columns))
    if missing:
        raise ValueError(f'Quickstart 样本缺少必要列：{missing}')

    universe = Universe(
        eligible=sample.pop('eligible').astype(bool).rename('eligible'),
        exclude_reason=sample.pop('exclude_reason').astype(str).rename(
            'exclude_reason'),
    )
    products = tuple(metadata['products'])
    spec = DatasetSpec(
        facade_config=(
            RolloverConfig(),
            AdjustmentConfig(),
            SamplingConfig(),
            EnvironmentConfig(),
        ),
        products=products,
        data_requirements=(DataRequirement(source='main_panel'),),
        start_date=dt.date.fromisoformat(metadata['actual_date_range'][0]),
        end_date=dt.date.fromisoformat(metadata['actual_date_range'][1]),
        universe=UniverseSpec(min_average_volume=10_000),
    )
    quality = DataQualitySummary(
        is_valid=True,
        total_rows=len(sample),
        product_sample_counts=metadata['product_sample_counts'],
        missing_rate_by_column=sample.isna().mean().to_dict(),
        warnings=(),
        errors=(),
    )
    return ResearchDataset(
        spec=spec,
        main_panel=sample,
        quality=quality,
        universe=universe,
    )


def run_quickstart(
        plot_output: Path = DEFAULT_PLOT_PATH) -> dict[str, Any]:
    """运行因子计算、评价和诊断组合实验，并保存净值曲线。"""
    with stage('校验并读取样本数据'):
        dataset = load_sample_dataset()

    factor = MomentumFactor(lookback=MOMENTUM_LOOKBACK, volnorm=False)
    with stage(f'计算因子值：{factor.name}'):
        factor_values = factor.compute(dataset.main_panel)

    label_spec = LabelSpec(
        name='forward_return_5d',
        type='forward_return',
        horizon=LABEL_HORIZON,
        min_valid_obs=LABEL_HORIZON,
    )
    with stage(f'构建标签：{LABEL_HORIZON}D 未来收益'):
        label = LabelBuilder(label_spec, dataset).compute().labels.squeeze()
    evaluation_frame = pd.concat(
        [
            factor_values.rename('factor'),
            label.rename('label'),
            dataset.universe.eligible.rename('universe'),
        ],
        axis=1,
    )
    with stage('因子评价：覆盖度与 IC'):
        coverage = coverage_stats(evaluation_frame)['overall'].iloc[0]
        ic_result, rank_ic_result = ic_details(
            evaluation_frame,
            mode='cross_sectional',
            label_horizon=LABEL_HORIZON,
            min_observations=5,
            annualization_factor=252,
            bs_seed=42,
            bs_reps=200,
        )

    signal = DiagnosticSignal(
        name=factor.name,
        values=factor_values,
        source_stage='raw',
        factor_name=factor.name,
        recipe={'lookback': MOMENTUM_LOOKBACK, 'volnorm': False},
    )
    portfolio_spec = DiagnosticPortfolioSpec(
        name='factor_quickstart_market_rank_ls',
        harvest_profile=market_rank_ls,
        rebalance=RebalanceConfig(interval=REBALANCE_INTERVAL, anchor=None),
        constraint=ConstraintConfig(),
        cost=CostConfig(rate=3e-5),
        min_cross_section_size=5,
        missing_return_policy='default_to_zero_and_warning',
    )
    with stage('诊断组合回测：market_rank_ls'):
        portfolio_result = DiagnosticPortfolioExperimentRunner(
            signal=signal,
            dataset=dataset,
            spec=portfolio_spec,
        ).run()
    performance = portfolio_result.performance.loc['proxy_net']

    plot_output = plot_output.resolve()
    plot_output.parent.mkdir(parents=True, exist_ok=True)
    with stage('绘制净值曲线'):
        ax = portfolio_result.nav.plot(
            title='Momentum 10D: market-rank long-short NAV',
            grid=True,
            figsize=(9, 5),
        )
        ax.set_xlabel('Date')
        ax.set_ylabel('NAV')
        ax.figure.tight_layout()
        ax.figure.savefig(plot_output, dpi=150)
        plt.close(ax.figure)

    timestamps = dataset.main_panel.index.get_level_values('timestamp')
    return {
        'products': dataset.spec.products,
        'start_date': timestamps.min().date().isoformat(),
        'end_date': timestamps.max().date().isoformat(),
        'observations': len(dataset.main_panel),
        'label_horizon': LABEL_HORIZON,
        'rebalance_interval': REBALANCE_INTERVAL,
        'factor_coverage': float(coverage['factor_coverage']),
        'joint_coverage': float(coverage['joint_coverage']),
        'ic_mean': float(ic_result.ic_summary.mean),
        'rank_ic_mean': float(rank_ic_result.ic_summary.mean),
        'total_return': float(performance['total_return']),
        'cagr': float(performance['cagr']),
        'sharpe': float(performance['sharpe']),
        'plot_path': plot_output,
        'portfolio_warnings': portfolio_result.warnings,
    }


def _print_summary(summary: dict[str, Any]) -> None:
    print('Factor Quickstart')
    print('-----------------')
    print(f"Products: {', '.join(summary['products'])}")
    print(f"Sample period: {summary['start_date']} ~ {summary['end_date']}")
    print(f"Observations: {summary['observations']}")
    print(f'Factor: momentum_{MOMENTUM_LOOKBACK}')
    print(f"Factor coverage: {summary['factor_coverage']:.2%}")
    print(f"Joint coverage: {summary['joint_coverage']:.2%}")
    h = summary['label_horizon']
    print(
        f"Cross-Sectional Mean IC ({h}D forward-return label): "
        f"{summary['ic_mean']:.4f}")
    print(
        f"Cross-Sectional Mean Rank IC ({h}D forward-return label): "
        f"{summary['rank_ic_mean']:.4f}")
    print(
        'Portfolio: market_rank_ls, '
        f"rebalance={summary['rebalance_interval']}D, cost=3bp")
    print(f"Total return: {summary['total_return']:.2%}")
    print(f"CAGR: {summary['cagr']:.2%}")
    print(f"Sharpe: {summary['sharpe']:.3f}")
    print(f"NAV chart: {summary['plot_path']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description='在小型真实日频样本上运行 factors 最小研究闭环。')
    parser.add_argument(
        '--plot-output', type=Path, default=DEFAULT_PLOT_PATH,
        help='净值曲线 PNG 输出路径。')
    parser.add_argument(
        '--no-spinner', action='store_true',
        help='关闭「正在运行」转圈提示（重定向输出时会自动关闭）。')
    args = parser.parse_args()
    if args.no_spinner:
        set_enabled(False)
    _print_summary(run_quickstart(plot_output=args.plot_output))


if __name__ == '__main__':
    main()
