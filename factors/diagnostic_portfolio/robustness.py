"""组合实验层稳健性检验组件。

V1 只实现调仓时点（anchor）稳健性检查：固定信号、数据集与组合 spec，
只改变 ``RebalanceConfig.anchor``，复用
:class:`DiagnosticPortfolioExperimentRunner` 运行每个phase，输出每个phase的
绩效明细，以及同一持有期 ``interval`` 下的phase分布。

设计边界
--------
- 不计算因子、不加载数据、不复制组合记账逻辑；
- 不替代 walk-forward / 样本外检验；
- V1 只支持 ``evaluation_sample='per_anchor_full'``，即直接使用 Runner
  给出的 ``performance.loc['proxy_net']`` 口径；``'common'`` 需要截取
  日期并复算 performance，当前直接抛 ``NotImplementedError``；
- 不持久化完整phase家族，不引入配置/结果容器；
- 并行化边界：只在 ``examine_rebalance_timing_luck`` 的 interval 层用
  :class:`~concurrent.futures.ProcessPoolExecutor` 并行（``n_jobs`` 参数）；
  ``run_anchor_robustness`` 内部保持串行，禁止嵌套进程池。
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from numbers import Integral
from typing import Any, Literal, get_args
import numpy.typing as npt

import numpy as np
import pandas as pd
from matplotlib.axes import Axes

from factors.diagnostic_portfolio.experiment import (
    DiagnosticPortfolioExperimentRunner,
    build_global_timeline,
)
from factors.diagnostic_portfolio.models import (
    DiagnosticPortfolioResult,
    DiagnosticPortfolioSpec,
    DiagnosticSignal
)
from factors.models import ResearchDataset


#: 可用于汇总与绘图的数值型结果字段。
MetricName = Literal[
    'gross_sharpe',
    'proxy_net_sharpe',
    'proxy_net_cagr',
    'annualized_volatility',
    'max_drawdown',
    'avg_turnover',
    'avg_active_count',
    'n_rebalances',
    'n_periods_from_anchor',
]


#: summary 的固定列顺序。
_SUMMARY_COLUMNS: tuple[str, ...] = (
    'count',
    'mean',
    'std',
    'min',
    'median',
    'max',
    'anchor0',
    'anchor0_percentile',
)

#: 绘图纵轴的可读标签；未登记的 indicator 直接使用字段名。
_INDICATOR_LABELS: dict[str, str] = {
    'gross_sharpe': 'gross Sharpe',
    'proxy_net_sharpe': 'proxy-net Sharpe',
    'proxy_net_cagr': 'proxy-net CAGR',
    'annualized_volatility': 'annualized volatility',
    'max_drawdown': 'max drawdown',
    'avg_turnover': 'average turnover',
    'avg_active_count': 'average active count',
    'n_rebalances': 'rebalance count',
    'n_periods_from_anchor': 'periods from anchor',
}


@dataclass(frozen=True)
class AnchorVariantResult:
    """单个 anchor phase的绩效与审计信息。

    Parameters
    ----------
    interval : int
        调仓周期（或称持有期），即 ``RebalanceConfig.interval``。
    offset : int
        anchor 在全局交易日时间轴上的位置偏移（交易日索引，不是自然日）。
    anchor : pd.Timestamp
        实际调仓锚点日期。
    gross_sharpe : float
        ``performance.loc['gross', 'sharpe']``。
    proxy_net_sharpe : float
        ``performance.loc['proxy_net', 'sharpe']``。
    proxy_net_cagr : float
        ``performance.loc['proxy_net', 'cagr']``。
    annualized_volatility : float
        ``performance.loc['proxy_net', 'annualized_volatility']``。
    max_drawdown : float
        ``performance.loc['proxy_net', 'max_drawdown']``。
    avg_turnover : float
        决策时点换手的均值。
    avg_active_count : float
        决策时点有效品种数的均值。
    n_rebalances : int
        决策时间轴上的调仓次数。
    n_periods_from_anchor : int
        从 anchor 决策期开始到最后一个收益实现期的收益期数。
        与 ``accounting.py`` 中 ``n_periods`` 的口径一致：数的是收益实现
        观测数，不是交易日个数。anchor 位于 offset=k 时为
        ``len(timeline) - k - 1``。
    warnings : tuple of str, optional
        Runner 返回的警告，默认为空。
    """

    interval: int
    offset: int
    anchor: pd.Timestamp
    gross_sharpe: float
    proxy_net_sharpe: float
    proxy_net_cagr: float
    annualized_volatility: float
    max_drawdown: float
    avg_turnover: float
    avg_active_count: float
    n_rebalances: int
    n_periods_from_anchor: int
    warnings: tuple[str, ...] = ()


def run_anchor_robustness(
        signal: DiagnosticSignal,
        dataset: ResearchDataset,
        spec: DiagnosticPortfolioSpec,
        n_anchors: int,
        evaluation_sample: Literal['per_anchor_full', 'common'] = \
        'per_anchor_full',
) -> list[AnchorVariantResult]:
    """运行单个 interval（调仓周期/持有期） 的 anchor phase 扫描。

    Parameters
    ----------
    signal : DiagnosticSignal
        已计算好的单列信号；所有 anchor phase复用同一份信号值。
    dataset : ResearchDataset
        因子研究统一数据集，提供主面板（包含收益率）与动态品种池。
    spec : DiagnosticPortfolioSpec
        基础组合实验配置。函数只替换 
        ``spec.rebalance.anchor``，不修改调用方传入的对象。
    n_anchors : int
        phase 数量，必须显式给出。实际数量会被截断到
        ``min(n_anchors, interval, len(timeline) - 1)``，见
        :func:`_generate_anchor_offsets`。
    evaluation_sample : {'per_anchor_full', 'common'}, optional
        V1 只支持 ``'per_anchor_full'``；传入 ``'common'`` 会抛
        ``NotImplementedError``。

    Returns
    -------
    list of AnchorVariantResult
        该 ``interval`` 下每个 anchor phase一行，按 offset 升序。

    Raises
    ------
    NotImplementedError
        ``evaluation_sample='common'`` 时抛出。
    ValueError
        ``n_anchors`` 非正整数、时间轴过短或 ``evaluation_sample``
        非法时抛出。
    """
    _validate_evaluation_sample(evaluation_sample)

    timeline = _resolve_timeline(dataset)
    interval = spec.rebalance.interval
    offsets = _generate_anchor_offsets(timeline, interval, n_anchors)

    results: list[AnchorVariantResult] = []
    for offset in offsets:
        anchor = timeline[int(offset)]
        variant_spec = replace(
            spec,
            rebalance=replace(spec.rebalance, anchor=anchor),
        )
        runner = DiagnosticPortfolioExperimentRunner(
            signal=signal, dataset=dataset, spec=variant_spec)
        result = runner.run()
        results.append(_extract_variant_result(
            result=result,
            interval=interval,
            offset=int(offset),
            anchor=anchor,
        ))
    return results


def examine_rebalance_timing_luck(
        signal: DiagnosticSignal,
        dataset: ResearchDataset,
        spec: DiagnosticPortfolioSpec,
        intervals: int | Sequence[int] | npt.NDArray[np.int_],
        n_anchors: int | Literal['auto'] = 'auto',
        evaluation_sample: Literal['per_anchor_full', 'common'] = \
        'per_anchor_full',
        summary_indicator: MetricName = 'proxy_net_sharpe',
        n_jobs: int = 1,
        plot: bool = False,
        **plot_kwargs: Any,
) -> tuple[dict[int, list[AnchorVariantResult]], pd.DataFrame] \
        | tuple[dict[int, list[AnchorVariantResult]], pd.DataFrame, Axes]:
    """检查多个持有期 ``interval`` 对调仓时点的敏感性。

    Parameters
    ----------
    signal, dataset, spec
        同 :func:`run_anchor_robustness`。
    intervals : int or Sequence of int
        要检查的持有期集合；重复值去重，输出升序排列。
    n_anchors : int or {'auto'}, optional
        每个 ``interval`` 的 phase 数量。``'auto'`` 解析为
        ``min(interval, 20)``；显式整数超过 ``interval`` 时会被截断。
    evaluation_sample : {'per_anchor_full', 'common'}, optional
        V1 只支持 ``'per_anchor_full'``。
    summary_indicator : MetricName, optional
        同时决定 summary 的统计对象与图的纵轴，默认
        ``'proxy_net_sharpe'``。
    n_jobs : int, optional
        并行进程数，默认 1（纯串行，行为与历史版本一致）。``n_jobs > 1``
        时在 interval 层用 :class:`~concurrent.futures.ProcessPoolExecutor`
        并行运行各持有期，任务粒度为"一个 interval 的完整 anchor 扫描"。
        实际进程数会被截断到 ``min(n_jobs, len(intervals))``。结果与串行
        版逐位一致（``executor.map`` 保序、phase 无随机源）。详见 Notes。
    plot : bool, optional
        是否绘图，默认 False。
    **plot_kwargs
        透传给 phase 散点；``ax`` 与 ``figsize`` 由本函数消费。

    Returns
    -------
    runs_by_interval : dict of int to list of AnchorVariantResult
        key 为持有期 ``interval`` ，value 为其下各 phase 的明细。
    summary : pd.DataFrame
        以 ``interval`` 为索引，列为
        ``count/mean/std/min/median/max/anchor0/anchor0_percentile``。
        ``count`` 只统计 ``summary_indicator`` 非 NaN 且有限的 phase ；
        ``anchor0_percentile`` 使用弱分位定义
        ``mean(x <= anchor0)``，并在 anchor0 无有效值时返回 NaN。
        指标名记录在 ``summary.attrs['summary_indicator']``。
    ax : matplotlib.axes.Axes
        仅当 ``plot=True`` 时返回。

    Notes
    -----
    phase分布是敏感性分布，不是显著性检验。不要把它写成 p 值，也不要
    设置"稳健性通过阈值"。

    并行化（``n_jobs > 1``）
    ------------------------
    - 只在 interval 层并行：一个任务 = 一个 interval 的完整
      :func:`run_anchor_robustness` 扫描（内部仍串行）。绝不嵌套进程池。
    - 大输入（``signal`` / ``dataset`` / ``spec``，pickle 后几十 MB 量级）
      通过 ``initializer`` 每个 worker 进程只传输一次；跨进程的任务参数
      只有 ``(interval, n_anchors)`` 这样的标量。
    - worker 函数必须定义在本模块顶层：Windows spawn 模式下子进程按
      引用重新 import 函数所在模块，notebook 单元格里的函数无法跨进程。
    - 单个 interval 时并行没有收益（进程池固定开销数秒级），自动退回
      串行路径。
    """
    _validate_evaluation_sample(evaluation_sample)
    _validate_summary_indicator(summary_indicator)
    _validate_positive_int(n_jobs, 'n_jobs')
    normalized_intervals = _normalize_intervals(intervals)
    if n_anchors != 'auto':
        _validate_positive_int(n_anchors, 'n_anchors')

    # 在主进程预先解析好每个 interval 的任务参数 (interval, n_anchors)。
    # 原则：所有"决策"在提交前完成，跨进程只传纯数据（小标量），
    # 不把解析规则（n_anchors='auto' 等）带进 worker。
    interval_tasks = tuple(
        (interval, _resolve_auto_n_anchors(n_anchors, interval))
        for interval in normalized_intervals
    )

    runs_by_interval: dict[int, list[AnchorVariantResult]] = {}
    if n_jobs == 1 or len(interval_tasks) == 1:
        # ------------------ 串行路径（默认） ------------------
        # 与历史版本逐语句一致。n_jobs > 1 但只有单个 interval 时也走
        # 这里：Windows spawn 下起进程池的固定开销（子进程重新 import +
        # payload 传输，数秒级）不可能被一个任务摊薄。
        for interval, resolved_n_anchors in interval_tasks:
            variant_spec = replace(
                spec,
                rebalance=replace(spec.rebalance, interval=interval),
            )
            runs_by_interval[interval] = run_anchor_robustness(
                signal=signal,
                dataset=dataset,
                spec=variant_spec,
                n_anchors=resolved_n_anchors,
                evaluation_sample=evaluation_sample,
            )
    else:
        # ------------------ 并行路径（interval 层） ------------------
        # 进程数截断到任务数：worker 多于任务只会白白多传几份 payload
        # （每个 worker 启动时都要 pickle 一次 signal/dataset/spec）。
        effective_n_jobs = min(n_jobs, len(interval_tasks))

        # initializer 模式的三个要点：
        # 1. initargs=(signal, dataset, spec, ...) 里的"大 payload"随
        #    _init_examine_worker 在每个 worker 启动时传输一次（共
        #    effective_n_jobs 次），而不是随每个任务重复传输；
        # 2. executor.map 的任务参数只有 (interval, n_anchors) 小标量；
        # 3. map 按提交顺序返回结果，且 phase 内部无随机源，因此
        #    runs_by_interval 的构建顺序与数值和串行版逐位一致。
        #
        # chunksize 保持默认值 1：单个任务很重（约 n_anchors 次完整
        # run()，秒级），逐个分发才能让先做完的 worker 立刻领取下一个
        # 任务；worker 抛出的异常会在主进程迭代结果时原样 re-raise，
        # 报错语义与串行版相同。
        with ProcessPoolExecutor(
                max_workers=effective_n_jobs,
                initializer=_init_examine_worker,
                initargs=(signal, dataset, spec, evaluation_sample),
        ) as executor:
            task_intervals, task_n_anchors = zip(*interval_tasks)
            variant_runs = executor.map(
                _run_interval_variant, task_intervals, task_n_anchors)
            for interval, runs in zip(normalized_intervals, variant_runs):
                runs_by_interval[interval] = runs

    summary = _summarize_anchor_runs(runs_by_interval, summary_indicator)
    if not plot:
        return runs_by_interval, summary

    ax = _plot_anchor_runs(runs_by_interval, summary_indicator, plot_kwargs)
    return runs_by_interval, summary, ax


# ============================================================
# 内部辅助
# ============================================================

def _validate_evaluation_sample(evaluation_sample: str) -> None:
    """校验评价口径；V1 只实现 ``per_anchor_full``。"""
    if evaluation_sample == 'common':
        raise NotImplementedError(
            "V1 只支持 evaluation_sample='per_anchor_full'；"
            "'common' 需要截取日期并复算 performance，尚未实现。"
        )
    if evaluation_sample != 'per_anchor_full':
        raise ValueError(
            "evaluation_sample 必须是 'per_anchor_full' 或 'common'，"
            f'收到 {evaluation_sample!r}。'
        )


def _validate_summary_indicator(indicator: str) -> None:
    """校验 summary/绘图指标名。"""
    allowed = get_args(MetricName)
    if indicator not in allowed:
        raise ValueError(
            f'summary_indicator 必须是 {allowed} 之一，收到 {indicator!r}。'
        )


def _validate_positive_int(value: Any, field_name: str) -> None:
    """校验正整数（排除 bool）。"""
    if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
        raise ValueError(f'{field_name} 必须是正整数，收到 {value!r}。')


def _resolve_timeline(dataset: ResearchDataset) -> pd.DatetimeIndex:
    """取得与 Runner 一致的全局交易日时间轴。"""
    panel = dataset.main_panel
    if panel is None or panel.empty:
        raise ValueError('dataset.main_panel 为空，无法生成 anchor phase。')

    timeline = build_global_timeline(panel)
    if not isinstance(timeline, pd.DatetimeIndex):
        timeline = pd.DatetimeIndex(timeline)
        assert isinstance(timeline, pd.DatetimeIndex)
    if timeline.isna().any():
        raise ValueError('dataset.main_panel 的时间戳包含 NaT。')
    if len(timeline) < 2:
        raise ValueError(
            'dataset.main_panel 至少需要 2 个时间戳才能比较调仓phase。'
        )
    return timeline


def _generate_anchor_offsets(
        timeline: pd.DatetimeIndex,
        interval: int,
        n_anchors: int,
) -> tuple[int, ...]:
    """生成确定性的 anchor 交易日偏移。

    偏移从 0 开始，必含 ``anchor0``。实际数量会被截断到
    ``min(n_anchors, interval, len(timeline) - 1)``，避免 anchor 落在
    最后一个时间戳上，也避免 ``n_anchors > interval`` 时产生重复 offset。
    """
    _validate_positive_int(interval, 'interval')
    _validate_positive_int(n_anchors, 'n_anchors')

    max_offset = interval - 1
    if max_offset > len(timeline) - 2:
        raise ValueError(f'interval 为 {interval}, timeline 长度为 {len(timeline)}。'
                         'timeline 长度相比于 interval 而言太短。')
    offsets = np.unique(
        np.linspace(0, max_offset, n_anchors).round().astype(int)
    )
    return tuple(int(offset) for offset in offsets)


def _resolve_auto_n_anchors(
        n_anchors: int | Literal['auto'],
        interval: int,
) -> int:
    """把 ``'auto'`` 解析为 ``min(interval, 20)``。"""
    if n_anchors == 'auto':
        return min(interval, 20)
    _validate_positive_int(n_anchors, 'n_anchors')
    return int(n_anchors)


# ============================================================
# 进程池 worker（examine_rebalance_timing_luck 专用）
# ============================================================

#: worker 进程内的共享上下文：signal / dataset / spec / evaluation_sample。
#: 只在子进程中由 _init_examine_worker 赋值；主进程永远不读写它
#: （initializer 不会在主进程执行，所以无需"清理"）。
_EXAMINE_WORKER_CONTEXT: tuple[
    DiagnosticSignal,
    ResearchDataset,
    DiagnosticPortfolioSpec,
    Literal['per_anchor_full', 'common'],
] | None = None


def _init_examine_worker(
        signal: DiagnosticSignal,
        dataset: ResearchDataset,
        spec: DiagnosticPortfolioSpec,
        evaluation_sample: Literal['per_anchor_full', 'common'],
) -> None:
    """进程池 initializer：每个 worker 进程启动时执行一次，且仅一次。

    把大 payload（signal / dataset / spec，pickle 后几十 MB 量级）存进
    模块级全局变量 ``_EXAMINE_WORKER_CONTEXT``，供本 worker 后续的每一个
    任务复用——这是"payload 每 worker 传一次、而非每任务传一次"的关键。

    Notes
    -----
    本函数在子进程中运行：参数在创建进程池时被 pickle 一次、经管道传输
    一次、在子进程中反序列化一次。整个池的生命周期里不会再执行第二次。
    """
    global _EXAMINE_WORKER_CONTEXT
    _EXAMINE_WORKER_CONTEXT = (signal, dataset, spec, evaluation_sample)


def _run_interval_variant(
        interval: int,
        n_anchors: int,
) -> list[AnchorVariantResult]:
    """worker 任务函数：串行运行单个 interval 的完整 anchor 扫描。

    设计约束
    --------
    - 必须定义在模块顶层（不能是闭包、lambda 或 notebook 单元格函数）：
      Windows spawn 模式下子进程通过"按引用 pickle"定位本函数，即重新
      import 它所在的模块；notebook 里的函数在子进程中不存在。
    - 函数内部严禁再创建进程池：并行只允许发生在 interval 层，
      嵌套并行会让进程数相乘、耗尽系统资源。
    """
    assert _EXAMINE_WORKER_CONTEXT
    signal, dataset, spec, evaluation_sample = _EXAMINE_WORKER_CONTEXT

    # 与串行路径完全相同的 spec 派生：base spec 只替换 interval，
    # anchor 的替换发生在 run_anchor_robustness 内部。
    variant_spec = replace(
        spec,
        rebalance=replace(spec.rebalance, interval=interval),
    )
    # 内部对 offset 的循环保持串行：并行只发生在 interval 层。
    return run_anchor_robustness(
        signal=signal,
        dataset=dataset,
        spec=variant_spec,
        n_anchors=n_anchors,
        evaluation_sample=evaluation_sample,
    )


def _normalize_intervals(
        intervals: int | Sequence[int] | npt.NDArray[np.int_],
) -> tuple[int, ...]:
    """把单值或序列统一成去重、升序的正整数元组。"""
    if isinstance(intervals, bool):
        raise ValueError(f'intervals 必须是正整数或其序列，收到 {intervals!r}。')
    if isinstance(intervals, Integral):
        values: list[Any] = [intervals]
    elif isinstance(intervals, Sequence) or isinstance(intervals, np.ndarray) and \
            not isinstance(intervals, (str, bytes)):
        values = list(intervals)
    else:
        raise ValueError(
            f'intervals 必须是正整数或其序列，收到 {intervals!r}。'
        )

    normalized: list[int] = []
    for interval in values:
        _validate_positive_int(interval, 'interval')
        normalized.append(int(interval))
    return tuple(sorted(set(normalized)))


def _extract_variant_result(
        result: DiagnosticPortfolioResult,
        interval: int,
        offset: int,
        anchor: pd.Timestamp,
) -> AnchorVariantResult:
    """从 Runner 结果中提取一行phase明细。"""
    performance = result.performance
    return AnchorVariantResult(
        interval=interval,
        offset=offset,
        anchor=anchor,
        gross_sharpe=float(performance.loc['gross', 'sharpe']), # pyright: ignore[reportArgumentType]
        proxy_net_sharpe=float(performance.loc['proxy_net', 'sharpe']), # pyright: ignore[reportArgumentType]
        proxy_net_cagr=float(performance.loc['proxy_net', 'cagr']), # pyright: ignore[reportArgumentType]
        annualized_volatility=float(
            performance.loc['proxy_net', 'annualized_volatility']), # pyright: ignore[reportArgumentType]
        max_drawdown=float(performance.loc['proxy_net', 'max_drawdown']), # pyright: ignore[reportArgumentType]
        avg_turnover=float(result.turnover.mean()),
        avg_active_count=float(result.exposures['active_count'].mean()),
        n_rebalances=int(result.rebalance_flags.sum()),
        # 与 accounting.py 的 n_periods 一致：数收益实现观测，不数交易日。
        n_periods_from_anchor=int(
            (result.portfolio_returns.index > anchor).sum()),
        warnings=result.warnings,
    )


def _summarize_anchor_runs(
        runs_by_interval: dict[int, list[AnchorVariantResult]],
        indicator: MetricName,
) -> pd.DataFrame:
    """按 ``interval`` 汇总 phase 分布。

    ``count`` 只统计指标非 NaN 且有限的phase；``anchor0_percentile`` 使用
    ``mean(x <= anchor0)``，anchor0 无有效值时返回 NaN。
    """
    if not runs_by_interval:
        summary = pd.DataFrame(
            {
                column: pd.Series(
                    dtype='int64' if column == 'count' else 'float64')
                for column in _SUMMARY_COLUMNS
            },
            index=pd.Index([], name='interval'),
        )
        summary.attrs['summary_indicator'] = indicator
        return summary

    rows: list[dict[str, float]] = []
    index: list[int] = []
    for interval in sorted(runs_by_interval):
        runs = runs_by_interval[interval]
        values = pd.Series(
            [getattr(run, indicator) for run in runs], dtype='float64')
        valid = values.replace([np.inf, -np.inf], np.nan).dropna()

        anchor0_values = [
            getattr(run, indicator) for run in runs if run.offset == 0
        ]
        anchor0 = float(anchor0_values[0]) if anchor0_values else np.nan
        if not np.isfinite(anchor0):
            anchor0_percentile = np.nan
        else:
            anchor0_percentile = float((valid <= anchor0).mean())

        rows.append({
            'count': int(valid.shape[0]),
            'mean': float(valid.mean()) if len(valid) else np.nan,
            'std': float(valid.std(ddof=1)) if len(valid) >= 2 else np.nan,
            'min': float(valid.min()) if len(valid) else np.nan,
            'median': float(valid.median()) if len(valid) else np.nan,
            'max': float(valid.max()) if len(valid) else np.nan,
            'anchor0': anchor0,
            'anchor0_percentile': anchor0_percentile,
        })
        index.append(int(interval))

    summary = pd.DataFrame(
        rows,
        index=pd.Index(index, name='interval'),
        columns=list(_SUMMARY_COLUMNS),
    )
    summary.attrs['summary_indicator'] = indicator
    return summary


def _plot_anchor_runs(
        runs_by_interval: dict[int, list[AnchorVariantResult]],
        indicator: MetricName,
        plot_kwargs: dict[str, Any],
) -> Axes:
    """绘制phase散点、phase均值线与 anchor0 标记。"""
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    ax = plot_kwargs.pop('ax', None)
    figsize = plot_kwargs.pop('figsize', (10, 5))
    created_figure = ax is None
    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    else:
        fig = ax.figure

    intervals = sorted(runs_by_interval)
    for interval in intervals:
        runs = runs_by_interval[interval]
        values = np.asarray(
            [getattr(run, indicator) for run in runs], dtype=float)
        finite = values[np.isfinite(values)]
        if finite.size:
            scatter_kwargs: dict[str, Any] = {
                'color': 'steelblue',
                'alpha': 0.55,
                's': 28,
                'zorder': 2,
            }
            scatter_kwargs.update(plot_kwargs)
            ax.scatter(
                np.full(finite.shape, interval, dtype=float),
                finite,
                **scatter_kwargs,
            )
            mean = float(finite.mean())
            ax.plot(
                [interval - 0.35, interval + 0.35], [mean, mean],
                color='black', linewidth=1.5, zorder=3,
            )

        anchor0 = next(
            (getattr(run, indicator) for run in runs if run.offset == 0),
            np.nan,
        )
        if np.isfinite(anchor0):
            ax.scatter(
                [interval], [anchor0], color='crimson', marker='*',
                s=130, zorder=4,
            )

    label = _INDICATOR_LABELS.get(indicator, indicator)
    ax.set_xticks(intervals)
    ax.set_xlabel('interval (holding period)')
    ax.set_ylabel(label)
    ax.set_title(f'Anchor robustness: {label} by rebalance phase')
    ax.grid(True, alpha=0.3)
    ax.legend(
        handles=[
            Line2D(
                [], [], color='steelblue', marker='o', linestyle='',
                alpha=0.55, label='anchor phase'),
            Line2D([], [], color='black', linestyle='-', label='phase mean'),
            Line2D(
                [], [], color='crimson', marker='*', linestyle='',
                markersize=10, label='anchor=0'),
        ],
        fontsize=9,
    )
    if created_figure:
        fig.tight_layout()
    return ax


__all__ = [
    'MetricName',
    'AnchorVariantResult',
    'run_anchor_robustness',
    'examine_rebalance_timing_luck',
]
