"""PIT 时序分组的轻量性能测试。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from time import perf_counter

import numpy as np
import pandas as pd

# 支持从仓库根目录直接执行本脚本。
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from factors.evaluation.grouped import _pit_select_assign_quantile_groups


def benchmark(
        n_points: int,
        method: str,
        rolling_window: int | None,
) -> float:
    """构造单品种数据并返回一次 PIT 分组的运行秒数。"""
    rng = np.random.default_rng(20260905)
    index = pd.date_range(
        "2000-01-01",
        periods=n_points,
        freq="min",
        name="timestamp",
    )
    product_df = pd.DataFrame(
        {
            "factor": rng.standard_normal(n_points),
            "label": rng.standard_normal(n_points),
            "universe": True,
        },
        index=index,
    )

    started_at = perf_counter()
    _pit_select_assign_quantile_groups(
        product_df=product_df,
        nan_policy="drop",
        min_observations=20,
        ts_grouping_method=method,  # type: ignore[arg-type]
        rolling_window=rolling_window,
        n_groups=5,
        grouping_direction="ascending",
    )
    return perf_counter() - started_at


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--points", type=int, required=True)
    parser.add_argument(
        "--method",
        choices=("expanding", "rolling"),
        required=True,
    )
    parser.add_argument("--window", type=int)
    args = parser.parse_args()

    elapsed = benchmark(args.points, args.method, args.window)
    print(
        f"method={args.method} points={args.points} "
        f"window={args.window} elapsed={elapsed:.3f}s"
    )
