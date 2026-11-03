"""探索性阶段因子体检用功能。"""
from __future__ import annotations
from collections.abc import Sequence
from typing import Any
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
        print_out: bool = False,
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
        print(f'Summary Statistics of {values.name or 'Factor values:'}')
        print(description.to_string())
        print(f'Percentage of 0: {pct_0*100:.2f}%')
        print(f'Percentage of NaN: {pct_nan*100:.2f}%; total length={len(values)}')
        print(f'N unique values: {n_unique}')
        print(f'Percentage of positive: {pct_positive*100:.2f}%')
        print(f'Percentage of negative: {pct_negative*100:.2f}%')
        print()
    if plot_box:
        _, ax = plt.subplots(figsize=figsize)
        ax.boxplot(values.dropna().to_numpy(), vert=True)
        ax.set_ylabel('Factor value')
        ax.set_title(values.name or 'Factor value') # pyright: ignore[reportArgumentType]
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

def plot_hist(
        *factors: pd.Series,
        bins: int = 100,
        suptitle: str = '',
        **plot_kwargs
    ) -> list[Axes]:
    """并排绘制若干因子值的分布直方图，用于横向对比（如原始值 vs 标准化值）。

    子图数量随传入的序列个数自适应，各子图共享 y 轴（沿用原始对比写法），
    以便直观看出不同因子取值分布的形态差异。`plot_kwargs` 会透传给
    `Series.plot`，常见用法如 `alpha=0.6`、`density=True`、`color='steelblue'`、
    `cumulative=True` 等。

    Parameters
    ----------
    *factors
        待对比的因子值序列，序列的 `name` 用作子图标题。
    bins
        直方图分箱数。
    **plot_kwargs
        透传给 matplotlib `hist` 的额外绘图参数。

    Returns
    -------
    list[Axes]
        按传入顺序排列的各子图 Axes 列表。

    Raises
    ------
    ValueError
        未传入任何因子序列。
    TypeError
        传入的对象不是 `pd.Series`。
    """
    if not factors:
        raise ValueError('至少需要传入一个因子序列')
    for i, factor in enumerate(factors):
        if not isinstance(factor, pd.Series):
            raise TypeError(
                f'第 {i} 个参数必须是 pd.Series，收到 {type(factor).__name__}'
            )

    n_axes = len(factors)
    # 每个子图保持原始代码里 6x4 的比例；squeeze=False 保证单因子时 axes 仍是二维数组
    fig, axes_array = plt.subplots(
        1, n_axes, figsize=(6 * n_axes, 4), sharey=True, squeeze=False
    )
    axes = axes_array[0]

    for ax, values in zip(axes, factors):
        title = values.name if values.name is not None else 'Factor value'
        cleaned = values.dropna()
        if cleaned.empty:
            ax.text(0.5, 0.5, 'All NaN', ha='center', va='center',
                    transform=ax.transAxes)
        else:
            cleaned.plot(kind='hist', bins=bins, ax=ax, **plot_kwargs)
        ax.set_xlabel('Factor value')
        ax.set_title(title) # pyright: ignore[reportArgumentType]
        ax.grid(True, alpha=0.3)
    fig.suptitle(suptitle)
    plt.tight_layout()
    plt.show()
    return list(axes)
