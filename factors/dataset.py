"""
数据集构建与校验。
负责从 DataFacade 构建供研究的面板数据并做 schema 校验。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from factors.models import (
    INDEX_NAMES,
    REQUIRED_COLUMNS,
    DataQualitySummary,
    DatasetSpec,
    ResearchDataset,
)

if TYPE_CHECKING:
    from data.facade import DataFacade


# ============================================================
# 私有辅助函数
# ============================================================


def _normalize_product_code(product: str) -> str:
    """规范化品种代码：去掉两端空白并转为大写。"""
    code = product.strip().upper()
    if not code:
        raise ValueError(f"品种代码为空或只含空白字符: '{product}'")
    return code


# ============================================================
# 面板 schema 校验
# ============================================================


def validate_research_frame(df: pd.DataFrame) -> DataQualitySummary:
    """
    对已合并的多品种研究主面板执行完整 schema 校验。

    Parameters
    ----------
    df : pd.DataFrame
        已按 MultiIndex[timestamp, product] 组织的研究主面板。

    Returns
    -------
    DataQualitySummary
        结构化的数据质量摘要，is_valid=False 时 errors 非空。

    """
    warnings: list[str] = []
    errors: list[str] = []

    # ── 0. 空面板校验（覆盖原单品种"空数据"检查）──────────────────
    if df.empty:
        errors.append("研究面板为空，没有可用的数据行。")

    # ── 1. 索引结构校验：必须是 MultiIndex 且名称正确 ───────────────
    if not isinstance(df.index, pd.MultiIndex):
        errors.append(
            f"研究面板索引不是 MultiIndex，实际类型为 {type(df.index).__name__}。"
        )
    elif df.index.names != list(INDEX_NAMES):
        errors.append(
            f"MultiIndex 名称不合规，应为 {INDEX_NAMES}，实际为 {df.index.names}。"
        )

    # ── 2. 索引唯一性 ───────────────────────────────────────────
    if not df.index.is_unique:
        errors.append("研究面板存在重复索引，即存在同一 (timestamp, product) 下多行的情况。")

    # ── 3. 必需字段存在 ─────────────────────────────────────────
    missing_cols = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing_cols:
        errors.append(f"缺少必需字段: {sorted(missing_cols)}")

    # ── 4-6. 依赖 MultiIndex 合法才能执行的校验 ─────────────────────
    index_valid = (
        isinstance(df.index, pd.MultiIndex)
        and df.index.names == list(INDEX_NAMES)
    )

    if index_valid:
        products = df.index.get_level_values('product')

        # 6. 每个品种内 trading_date 严格递增
        # 如果某品种的 trading_date 出现倒退，说明数据中存在时序错位。
        # 这是一个致命错误，因为滚动计算和标签构造都依赖时间单调性。
        for product in products.unique():
            mask = products == product
            dates = df.loc[mask, 'trading_date']
            if not dates.is_monotonic_increasing:
                errors.append(
                    f"品种 {product} 的 trading_date 不是严格递增的，"
                    f"数据可能存在时序错位或乱序。"
                )

        # 各品种样本数统计
        product_counts = df.groupby(
            df.index.get_level_values('product')
        ).size().to_dict()
    else:
        # 索引结构不合法时无法获取各品种样本数，设为 None
        product_counts = None

    # ── 7. Volume / OpenInterest 非负值检查（非致命） ──────────────
    if 'Volume' in df.columns:
        neg_vol_count = int((df['Volume'] < 0).sum())
        if neg_vol_count > 0:
            warnings.append(f"Volume 存在 {neg_vol_count} 个负值行。")

    if 'OpenInterest' in df.columns:
        neg_oi_count = int((df['OpenInterest'] < 0).sum())
        if neg_oi_count > 0:
            warnings.append(f"OpenInterest 存在 {neg_oi_count} 个负值行。")

    # ── 8. 汇总统计 ────────────────────────────────────────────
    total_rows = len(df)
    missing_rates: dict[str, float] = {
        col: df[col].isna().mean()
        for col in df.columns
        if col in REQUIRED_COLUMNS
    }

    return DataQualitySummary(
        is_valid=(len(errors) == 0),
        total_rows=total_rows,
        product_sample_counts=product_counts,
        missing_rate_by_column=missing_rates,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


# ============================================================
# 数据集构建器
# ============================================================


class ResearchDatasetBuilder:
    """
    从 DataFacade 构建统一的多品种日频研究面板。

    Parameters
    ----------
    facade : DataFacade
        数据引擎的统一对外接口实例。

    Methods
    -------
    build(spec)
        按 DatasetSpec 构建并返回 ResearchDataset。

    """

    def __init__(self, facade: DataFacade):
        self._facade = facade

    def build(
        self,
        spec: DatasetSpec,
        max_workers: int | None = None,
    ) -> ResearchDataset:
        """
        执行完整的数据集构建流程。

        Parameters
        ----------
        spec : DatasetSpec
            研究数据请求规格。
        max_workers : int or None, default None
            最大线程数。默认为 None ，即使用 ThreadPoolExecutor 的默认值。

        Returns
        -------
        ResearchDataset
            校验后的统一研究面板，含质量摘要和 universe 占位符。
        """
        # 1. 品种设定校验 
        if not spec.products:
            raise ValueError("DatasetSpec.products 不能为空，请指定至少一个品种代码。")

        # 2. 规范化所有品种代码（去空白 + 转大写）
        products = tuple(_normalize_product_code(p) for p in spec.products)

        # 3. 将数据需求从 spec 中整理出来为字典，key 为 source, value 为 columns
        data_requirements = {dr.source: dr.columns for dr in spec.data_requirements}

        # 3. 加载主面板（统一走数据引擎的多线程并行接口）
        panel = self._facade.get_historical_vector_multiple_products(
            products=list(products),
            start_date=spec.start_date,
            end_date=spec.end_date,
            freq=spec.freq,
            view=spec.view,
            max_workers=max_workers,
        )

        # 统一命名索引层级为架构契约名称
        panel.index.names = list(INDEX_NAMES)

        # 请求品种全覆盖校验：多线程 concat 会静默丢弃返回空数据的品种，
        # 此处兜底覆盖原逐品种加载的"某品种返回空数据"检查。
        present = set(panel.index.get_level_values('product'))
        missing = set(products) - present
        if missing:
            raise ValueError(
                f"以下品种返回空数据，未纳入主面板: {sorted(missing)}"
            )

        # MultiIndex 第一层级为 timestamp（元素类型 pd.Timestamp），第二层级为 product（str）
        assert isinstance(panel.index.get_level_values(0)[0], pd.Timestamp)
        assert isinstance(panel.index.get_level_values(1)[0], str)

        # 按时间戳优先、品种次之的顺序稳定排序
        panel.sort_index(inplace=True)

        # 主面板数据质量校验 
        quality = validate_research_frame(panel)

        # universe 占位符 
        # 初始化为全 True，表示所有品种在所有时点均可研究。
        # 后续由 DynamicUniverse 根据准入条件生成真实的布尔掩码并替换。
        universe = pd.Series(True, index=panel.index, name='eligible')

        # 是否需要附属数据集？（'main_panel' 之外请求的数据源）
        auxiliary_sources = set(data_requirements.keys()) - {'main_panel'}

        # 若不需要附属数据集，直接返回 ResearchDataset
        if not auxiliary_sources:
            return ResearchDataset(
                spec=spec,
                main_panel=panel,
                quality=quality,
                universe=universe,
            )

        # 4. 需要附属数据集，按需加载
        auxiliary: dict[str, pd.DataFrame] = {}

        if 'term_structure' in auxiliary_sources:
            # 加载期限结构数据集（统一走数据引擎的多线程并行接口）
            ts_df = self._facade.get_term_structure_data_multiple_products(
                products=list(products),
                start_date=spec.start_date,
                end_date=spec.end_date,
                max_workers=max_workers,
            )

            # 多线程接口返回 MultiIndex[trading_date, contract]，品种在 product 列中；
            # 重建索引为 [timestamp, product, contract]，product 移入索引后移除原列。
            assert isinstance(ts_df.index, pd.MultiIndex)
            ts_df.index = pd.MultiIndex.from_arrays(
                [
                    ts_df.index.get_level_values(0),
                    ts_df['product'],
                    ts_df.index.get_level_values(1),
                ],
                names=['timestamp', 'product', 'contract'],
            )
            ts_df = ts_df.drop(columns=['product'])
            ts_df.sort_index(inplace=True)

            # 按请求列裁剪；columns 为 None 表示不做裁剪，保留数据引擎返回的原始数据
            requested_columns = data_requirements['term_structure']
            if requested_columns is not None:
                missing_columns = set(requested_columns) - set(ts_df.columns)
                if missing_columns:
                    raise ValueError(
                        f"期限结构数据集缺少请求列: {sorted(missing_columns)}"
                    )
                ts_df = ts_df[list(requested_columns)]

            auxiliary['term_structure'] = ts_df

            # ... 按需加载更多附属数据集

        return ResearchDataset(
            spec=spec,
            main_panel=panel,
            quality=quality,
            universe=universe,
            auxiliary=auxiliary,
        )
