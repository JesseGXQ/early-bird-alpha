"""探索性阶段因子体检用功能。"""
from __future__ import annotations
from typing import Sequence, Any
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from factors.models import INDEX_NAMES

def inspect_outliers(
        values: pd.Series,
        threshold: int | float | None = None,
        quantile: float | None = 0.99,
        by_products: bool = True
):
    assert not (threshold is None and quantile is None)

    

def quick_glance_at(
        values: pd.Series,
        percentiles: list[float] = [0.01, 0.25, 0.5, 0.75, 0.99],
        print_out: bool = True,
        plot_box: bool = False,
        figsize: tuple[int, int] = (6, 4)
    ) -> dict[str, Any]:
    description = values.describe(percentiles=percentiles)
    pct_0 = (values==0).mean()
    pct_nan = values.isna().mean()
    n_unique = values.nunique()
    pct_positive = (values > 0).mean()
    pct_negative = (values < 0).mean()
    if print_out:
        print(description.to_string())
        print(f'Percentage of 0: {pct_0*100:.2f}%')
        print(f'Percentage of NaN: {pct_nan*100:.2f}%, length={len(values)}')
        print(f'N unique values: {n_unique}')
        print(f'Percentage of positive: {pct_positive*100:.2f}%')
        print(f'Percentage of negative: {pct_negative*100:.2f}%')
    if plot_box:
        _, ax = plt.subplots(figsize=figsize)
        ax.boxplot(values.dropna().to_numpy(), vert=True)
        ax.set_ylabel('Factor value')
        ax.set_title(values.name if values.name is not None else 'Factor value') # pyright: ignore[reportArgumentType]
        ax.grid(True, alpha=0.3)
        plt.show()
    return {
        'description': description,
        'percentage_zero': 'pct_0',
        'percentage_NaN': pct_nan,
        'n_unique': n_unique,
        'percentage_positive': pct_positive,
        'percentage_negative': pct_negative
    }

def plot_values_over_time(
        values: pd.Series, 
        ax: Axes | None = None, 
        kind: str = 'scatter',
        s: int = 4,
        alpha: float = 0.5, 
        figsize: tuple[int, int] = (12, 5),
        **plot_kwargs):
    assert values.index.names == INDEX_NAMES

    if ax is None:
        _, ax = plt.subplots(figsize=figsize)

    valid = values.notna()
    timestamps = values.index.get_level_values('timestamp')[valid]
    if kind == 'scatter':
        ax.scatter(timestamps, values[valid], s=s, alpha=alpha, **plot_kwargs)
    elif kind == 'line':
        ax.plot(timestamps, values[valid], alpha=alpha, **plot_kwargs)
    else:
        raise ValueError(f"Unsupported plot kind: {kind!r}")

    ax.set_xlabel('Timestamp')
    ax.set_ylabel('Factor value')
    ax.set_title(values.name if values.name is not None else 'Factor value') # pyright: ignore[reportArgumentType]
    ax.grid(True, alpha=0.3)
    return ax
