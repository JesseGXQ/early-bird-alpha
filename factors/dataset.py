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
from utils.validation_utils import check_df_columns_exist

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


def _check_single_product_result(
    df: pd.DataFrame,
    product: str,
) -> None:
    """校验内容：非空、索引唯一，必须列存在。"""
    if df.empty:
        raise ValueError(
            f"品种 {product} 返回空数据，请检查。"
        )

    if not df.index.is_unique:
        raise ValueError(
            f"品种 {product} 的索引存在重复值。"
        )

    # 检查必需列是否存在
    check_df_columns_exist(df, list(REQUIRED_COLUMNS))


# ============================================================
# 面板 schema 校验
# ============================================================


def validate_research_frame(df: pd.DataFrame) -> DataQualitySummary:
    """
    对已合并的多品种研究面板执行完整 schema 校验。

    Parameters
    ----------
    df : pd.DataFrame
        已按 MultiIndex[timestamp, product] 组织的研究面板。

    Returns
    -------
    DataQualitySummary
        结构化的数据质量摘要，is_valid=False 时 errors 非空。

    """
    warnings: list[str] = []
    errors: list[str] = []

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

    构建流程：
    1. 逐品种调用 DataFacade.get_historical_vector()。
    2. 校验单品种结果（非空、索引唯一、必需列齐全）。
    3. 添加 product 维度并合并为长表面板。
    4. 设置 MultiIndex[timestamp, product] 并排序。
    5. 运行 validate_research_frame 生成质量摘要。
    6. 创建全 True 的 universe 占位符（后续由 DynamicUniverse 替换）。

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

    def build(self, spec: DatasetSpec) -> ResearchDataset:
        """
        执行完整的数据集构建流程。

        Parameters
        ----------
        spec : DatasetSpec
            研究数据请求规格。

        Returns
        -------
        ResearchDataset
            校验后的统一研究面板，含质量摘要和 universe 占位符。

        Raises
        ------
        ValueError
            若 products 为空、日期为 None，或某品种加载失败。

        """
        # ── 前置校验 ─────────────────────────────────────────────
        if not spec.products:
            raise ValueError("DatasetSpec.products 不能为空，请指定至少一个品种代码。")

        # 规范化所有品种代码（去空白 + 转大写）
        products = tuple(_normalize_product_code(p) for p in spec.products)

        # ── 逐品种加载 ────────────────────────────────────────────
        dfs: list[pd.DataFrame] = []

        for product in products:
            # 从数据引擎获取单一品种的历史复权价格向量
            # freq 和 view 直接透传即可 —— spec 已在 __post_init__ 中
            # 保证 freq='1d'，且 view 默认为 'scheduled' 或其显式值
            _df_product = self._facade.get_historical_vector(
                product=product,
                start_date=spec.start_date,
                end_date=spec.end_date,
                freq=spec.freq,
                view=spec.view,
            )

            # 单品种结果基础校验（空、重复、缺列）
            _check_single_product_result(_df_product, product)

            # 添加品种代码列，供后续合并索引使用
            _df_product['product'] = product
            dfs.append(_df_product)

        # ── 合并为长表面板 ────────────────────────────────────────
        # 纵向拼接所有品种的 DataFrame
        df = pd.concat(dfs, axis=0)

        # 将 product 列提升为索引的第二层级，形成 MultiIndex
        df.set_index('product', append=True, inplace=True)

        # 统一命名索引层级为架构契约名称
        df.index.names = INDEX_NAMES

        # 按时间戳优先、品种次之的顺序稳定排序
        df.sort_index(inplace=True)

        # ── 面板校验 ──────────────────────────────────────────────
        quality = validate_research_frame(df)

        # ── universe 占位符 ──────────────────────────────────────
        # 初始化为全 True，表示所有品种在所有时点均可研究。
        # 后续由 DynamicUniverse 根据准入条件生成真实的布尔掩码并替换。
        universe = pd.Series(True, index=df.index, name='eligible')

        return ResearchDataset(
            df=df,
            spec=spec,
            quality=quality,
            universe=universe,
        )
