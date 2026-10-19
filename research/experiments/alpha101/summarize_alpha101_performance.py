# type: ignore
"""Alpha101 组合实验绩效汇总。

读取 ``DiagnosticPortfolioStore`` 中标签为 ``alpha101`` 的产物，按收获范式分别
生成两份汇总表：

- ``artifacts/performance_summary_market_rank_ls.parquet``
- ``artifacts/performance_summary_ts_inv_vol_gross_one.parquet``

行索引为 101 个因子（``alpha101_001`` ~ ``alpha101_101``），列为
``total_return / cagr / sharpe / n_periods``，全部取自
``DiagnosticPortfolioResult.performance`` 中 ``return_type='proxy_net'`` 的那一行。

Notes
-----
只读取每个 run 的 ``manifest.json`` 与 ``outputs/performance.parquet``，
不加载完整结果，以降低汇总开销。同一 ``(因子, 收获范式)`` 出现多次运行时，
保留 ``created_at`` 最新的一次。跳过（信号退化）的因子对应行保留为缺失值，
以保证索引始终是完整的 101 个因子。

用法
----
    python research/experiments/alpha101/summarize_alpha101_performance.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd

from factors.diagnostic_portfolio.storage import DiagnosticPortfolioStore
from factors.diagnostic_portfolio.storage.models import RunManifest
from factors.diagnostic_portfolio.storage.serialization import (
    read_pandas_artifact,
)

from research.experiments.alpha101.alpha101_factor_definitions import (
    ALPHA101_FACTORS,
)


DEFAULT_ARTIFACTS_DIR = _REPO_ROOT / 'research/experiments/alpha101/artifacts'
DEFAULT_STORE_ROOT = DEFAULT_ARTIFACTS_DIR / 'diagnostic_portfolio'

#: 汇总表列（对齐 DiagnosticPortfolioResult.performance 的列名）。
SUMMARY_COLUMNS: tuple[str, ...] = (
    'total_return', 'cagr', 'sharpe', 'n_periods',
)
#: 采用的收益口径行标签。
RETURN_TYPE = 'proxy_net'
#: 汇总表行索引（101 个因子，稳定顺序）。
FACTOR_INDEX = pd.Index(
    [factor.name for factor in ALPHA101_FACTORS], name='factor')
#: 两种收获范式 → 汇总表文件名。
PROFILE_FILES: dict[str, str] = {
    'market_rank_ls': 'performance_summary_market_rank_ls.parquet',
    'ts_inv_vol_gross_one': 'performance_summary_ts_inv_vol_gross_one.parquet',
}


def _read_performance(run_dir: Path) -> pd.DataFrame:
    """只读取一次运行的 manifest 与 performance artifact。"""
    manifest = RunManifest.from_dict(json.loads(
        (run_dir / 'manifest.json').read_text(encoding='utf-8')))
    record = manifest.artifacts['output.performance']
    if not record.present or not record.path:
        raise ValueError(f'{run_dir.name} 缺少 performance artifact')
    return read_pandas_artifact(run_dir / record.path, record)


def _latest_runs(store: DiagnosticPortfolioStore) -> dict[tuple[str, str], Any]:
    """取每个 ``(因子, 收获范式)`` 最新的一次运行摘要。"""
    latest: dict[tuple[str, str], Any] = {}
    for summary in store.find(tags=['alpha101']):
        key = (summary.signal_name, summary.harvest_profile)
        current = latest.get(key)
        if current is None or summary.created_at > current.created_at:
            latest[key] = summary
    return latest


def build_summary(
        store: DiagnosticPortfolioStore,
        profile: str,
) -> pd.DataFrame:
    """为单个收获范式构建 101 行汇总表。"""
    latest = _latest_runs(store)
    rows: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for factor_name in FACTOR_INDEX:
        summary = latest.get((factor_name, profile))
        if summary is None:
            missing.append(factor_name)
            continue
        performance = _read_performance(store.runs_dir / summary.run_id)
        if RETURN_TYPE not in performance.index:
            raise ValueError(
                f'{summary.run_id} 的 performance 缺少 {RETURN_TYPE!r} 行')
        row = performance.loc[RETURN_TYPE, list(SUMMARY_COLUMNS)]
        rows[factor_name] = row.to_dict()
    summary_frame = pd.DataFrame.from_dict(rows, orient='index',
                                           columns=list(SUMMARY_COLUMNS))
    summary_frame = summary_frame.reindex(FACTOR_INDEX)
    summary_frame['n_periods'] = summary_frame['n_periods'].astype('Int64')
    if missing:
        print(f'[summary] {profile}: {len(missing)} 个因子无产物，'
              f'行保留为缺失值：{missing}')
    print(f'[summary] {profile}: 覆盖 '
          f'{len(FACTOR_INDEX) - len(missing)}/{len(FACTOR_INDEX)} 个因子')
    return summary_frame


def summarize(
        *,
        store_root: Path = DEFAULT_STORE_ROOT,
        artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR,
) -> dict[str, Path]:
    """生成两份绩效汇总表并返回文件路径。"""
    store = DiagnosticPortfolioStore(root_dir=store_root)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for profile, filename in PROFILE_FILES.items():
        frame = build_summary(store, profile)
        path = artifacts_dir / filename
        frame.to_parquet(path, engine='pyarrow')
        written[profile] = path
        print(f'[summary] 已写出 {path}（shape={frame.shape}）')
    return written


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description='Alpha101 绩效汇总')
    parser.add_argument('--store-root', type=str, default=None,
                        help='组合实验结果存储根目录')
    parser.add_argument('--artifacts-dir', type=str, default=None,
                        help='汇总表输出目录')
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口。"""
    args = _parse_args(argv)
    store_root = Path(args.store_root) if args.store_root \
        else DEFAULT_STORE_ROOT
    artifacts_dir = Path(args.artifacts_dir) if args.artifacts_dir \
        else DEFAULT_ARTIFACTS_DIR
    summarize(store_root=store_root, artifacts_dir=artifacts_dir)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
