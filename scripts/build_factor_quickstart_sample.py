"""从完整本地数据库生成 factors Quickstart 的小型真实行情样本。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from factors.models import UniverseSpec
from factors.presets import build_dataset


PRODUCTS = ('CU', 'AO', 'MA', 'LC', 'JD', 'EG', 'RM', 'B', 'PK', 'TF')
START_DATE = dt.date(2023, 1, 1)
END_DATE = dt.date(2024, 12, 31)
PANEL_COLUMNS = (
    'Dominant_Contract',
    'Adj_Close',
    'Volume',
    'OpenInterest',
    'True_Return',
)
DEFAULT_OUTPUT_DIR = (
    REPOSITORY_ROOT / 'examples' / 'data' / 'factor_quickstart')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def build_sample(output_dir: Path, *, overwrite: bool = False) -> None:
    """构建并保存真实研究面板样本及其来源元数据。"""
    parquet_path = output_dir / 'research_sample.parquet'
    metadata_path = output_dir / 'metadata.json'
    existing = [path for path in (parquet_path, metadata_path) if path.exists()]
    if existing and not overwrite:
        paths = ', '.join(str(path) for path in existing)
        raise FileExistsError(f'样本文件已存在，使用 --overwrite 覆盖：{paths}')

    dataset = build_dataset(
        products=PRODUCTS,
        start_date=START_DATE,
        end_date=END_DATE,
        universe=UniverseSpec(min_average_volume=10_000),
    )
    sample = dataset.main_panel.loc[:, PANEL_COLUMNS].copy()
    sample['eligible'] = dataset.universe.eligible.astype(bool)
    sample['exclude_reason'] = dataset.universe.exclude_reason.astype(str)
    sample = sample.sort_index()

    if not sample.index.is_unique:
        raise ValueError('Quickstart 样本索引必须唯一。')
    if sample.index.names != ['timestamp', 'product']:
        raise ValueError(
            'Quickstart 样本索引必须命名为 timestamp、product。')

    output_dir.mkdir(parents=True, exist_ok=True)
    sample.to_parquet(parquet_path)

    timestamps = sample.index.get_level_values('timestamp')
    product_counts = sample.groupby(level='product').size().to_dict()
    metadata = {
        'schema_version': 1,
        'description': 'factors Quickstart 使用的小型真实日频研究面板',
        'source': (
            '由项目本地日频快照经 DataFacade 和 ResearchDatasetBuilder '
            '构造；日频快照目录标识为 bar_1d_tushare/cleaned。'),
        'processing': '后复权主连，动态品种池最低20日平均成交量为10000手',
        'products': list(PRODUCTS),
        'requested_date_range': [START_DATE.isoformat(), END_DATE.isoformat()],
        'actual_date_range': [
            timestamps.min().date().isoformat(),
            timestamps.max().date().isoformat(),
        ],
        'row_count': int(len(sample)),
        'product_sample_counts': {
            product: int(count) for product, count in product_counts.items()
        },
        'columns': list(sample.columns),
        'index_names': list(sample.index.names),
        'parquet_file': parquet_path.name,
        'parquet_sha256': _sha256(parquet_path),
        'license_note': (
            '该衍生样本仅用于项目运行演示；正式数据来源与授权说明待补入 README。'),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )

    print(f'已生成样本：{parquet_path}')
    print(f'样本行数：{len(sample)}')
    print(f'SHA-256：{metadata["parquet_sha256"]}')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='从完整本地数据库生成 factors Quickstart 样本。')
    parser.add_argument(
        '--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR,
        help='样本输出目录。')
    parser.add_argument(
        '--overwrite', action='store_true', help='覆盖已有样本文件。')
    args = parser.parse_args()
    build_sample(args.output_dir.resolve(), overwrite=args.overwrite)


if __name__ == '__main__':
    main()
