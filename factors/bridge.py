"""因子研究期的数据引擎桥接层。

设计目标
--------
因子研究早期数据需求高度探索性，不宜每试一个因子就扩展 ``ResearchDatasetBuilder``。
本模块根据因子声明的 ``DataRequirement``，通过 ``DataFacade`` 或本地数据组件取数，
直接产出 ``Factor.compute`` 所需的数据结构。

分层约定
--------
- T1：字段已在引擎原始数据中，只是未暴露（如 ``Upper_Limit``、``multiplier``），
  由引擎补列，本层直接读取。
- T2：数据在本地库、引擎无接口（如合约日频保证金状态），本层复用现有 manager /
  query service 取数。
- T3：派生、昂贵或外部数据（如 1min 聚合出的夜盘/日盘收益），本层负责取数与派生。
- T4：因子特有的 join、对齐、ffill、标准化留在因子内部，本层不做。

使用示例
--------
>>> bridge = FactorDataBridge(facade, dataset)
>>> data = bridge.resolve(
...     factor.data_requirements,
...     products=['RB', 'M'],
...     start_date=date(2024, 1, 1),
...     end_date=date(2025, 12, 31),
... )
>>> values = factor.compute(data)

单个 ``DataRequirement`` 返回 ``DataFrame``；多个返回 ``dict[source, DataFrame]``。

数据源 schema
-------------
- ``main_panel``：MultiIndex[timestamp, product]。
- ``term_structure``：MultiIndex[timestamp, product, contract]。
- ``warehouse_receipt``：扁平表，至少含 ``trading_date``、``product``。
- ``instrument_daily_status``：扁平表，至少含 ``trading_date``、``product``、
  ``instrument``。
- ``session_return``：MultiIndex[timestamp, product]，含 ``night_return``、
  ``day_return``。

本层 V1 不做缓存；每次 ``resolve`` 重新取数。若传入 ``ResearchDataset``，
``main_panel`` / ``term_structure`` / ``warehouse_receipt`` 优先复用其中已有数据；
若数据集缺少请求列、数据为空或缺少请求品种，则回退到 facade 补取。日期范围
不做额外对齐检查，调用方应保证数据集覆盖所需区间。

注意：``session_return`` 需要读取 1min 行情并按年分块聚合，成本明显高于日频
数据源；研究时应只请求必要的品种和日期区间。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from data.facade import DataFacade
from factors.models import DataRequirement, ResearchDataset

logger = logging.getLogger(__name__)

_PANEL_INDEX = ('timestamp', 'product')
_TERM_STRUCTURE_INDEX = ('timestamp', 'product', 'contract')
_WAREHOUSE_BASE_COLUMNS = ('trading_date', 'product')
_STATUS_BASE_COLUMNS = ('trading_date', 'product', 'instrument')
_SESSION_COLUMNS = ('night_return', 'day_return')

# 为保证区间首日夜盘收益可计算，内部向前多取若干日历日；重叠段最终会被丢弃。
_SESSION_OVERLAP_DAYS = 10
_MAX_WARNINGS = 100

_SourceLoader = Callable[
    [tuple[str, ...], date, date, tuple[str, ...] | None, Mapping[str, Any]],
    pd.DataFrame,
]


class FactorDataBridge:
    """按 ``DataRequirement`` 为因子 ``compute()`` 准备数据。

    Parameters
    ----------
    facade : DataFacade
        数据引擎统一入口。
    dataset : ResearchDataset | None, default None
        可复用的研究数据集。传入后，主面板、期限结构和仓单数据优先从
        ``dataset.main_panel`` / ``dataset.auxiliary`` 读取；缺失时回退到 facade。

    Attributes
    ----------
    warnings : list[str]
        最近一次 ``resolve`` 中记录的警告明细（最多 100 条）。
    """

    def __init__(
            self,
            facade: DataFacade,
            dataset: ResearchDataset | None = None,
    ):
        self._facade = facade
        self._dataset = dataset
        self.warnings: list[str] = []
        self._warning_count = 0
        self._loaders: dict[str, _SourceLoader] = {
            'main_panel': self._load_main_panel,
            'term_structure': self._load_term_structure,
            'warehouse_receipt': self._load_warehouse_receipt,
            'instrument_daily_status': self._load_instrument_daily_status,
            'session_return': self._load_session_return,
        }

    def resolve(
            self,
            requirements: Sequence[DataRequirement],
            *,
            products: Sequence[str],
            start_date: date,
            end_date: date,
    ) -> pd.DataFrame | dict[str, pd.DataFrame]:
        """按数据需求取数并返回因子输入。

        Parameters
        ----------
        requirements : Sequence[DataRequirement]
            因子声明的数据需求。同一 source 出现多次时会合并列，但
            ``request_params`` 必须一致。
        products : Sequence[str]
            品种代码列表；大小写不敏感。
        start_date, end_date : datetime.date
            取数日期闭区间。

        Returns
        -------
        pd.DataFrame or dict[str, pd.DataFrame]
            单个数据源返回 DataFrame；多个数据源返回以 source 为 key 的字典。

        Raises
        ------
        ValueError
            数据需求为空、品种为空、日期区间非法、数据源未知、同一 source
            的 request_params 冲突，或返回数据缺少请求列。
        """
        self._reset_warnings()
        if not requirements:
            raise ValueError('requirements 不能为空。')
        normalized_products = self._normalize_products(products)
        start_day, end_day = self._validate_date_range(start_date, end_date)

        by_source: dict[str, list[DataRequirement]] = {}
        for requirement in requirements:
            if not isinstance(requirement, DataRequirement):
                raise TypeError(
                    'requirements 中的元素必须是 DataRequirement，'
                    f'收到 {type(requirement).__name__}。'
                )
            by_source.setdefault(requirement.source, []).append(requirement)

        results: dict[str, pd.DataFrame] = {}
        for source, source_requirements in by_source.items():
            loader = self._loaders.get(source)
            if loader is None:
                raise ValueError(
                    f'未知数据源 {source!r}。可用数据源: '
                    f'{sorted(self._loaders)}。'
                )
            columns = self._union_columns(source_requirements)
            params = self._merge_request_params(source, source_requirements)
            frame = loader(
                normalized_products, start_day, end_day, columns, params
            )
            self._validate_schema(source, frame)
            results[source] = self._select_columns(source, frame, columns)

        self._log_warnings()
        if len(results) == 1:
            return next(iter(results.values()))
        return results

    # ============================================================
    # 公共辅助
    # ============================================================

    @staticmethod
    def _normalize_products(products: Sequence[str]) -> tuple[str, ...]:
        if products is None or isinstance(products, str):
            raise ValueError('products 必须是品种代码序列，不能为空。')
        normalized: list[str] = []
        for product in products:
            if not isinstance(product, str) or not product.strip():
                raise ValueError(f'非法品种代码: {product!r}。')
            code = product.strip().upper()
            if code not in normalized:
                normalized.append(code)
        if not normalized:
            raise ValueError('products 不能为空。')
        return tuple(normalized)

    @staticmethod
    def _validate_date_range(
            start_date: date,
            end_date: date,
    ) -> tuple[date, date]:
        if start_date is None or end_date is None:
            raise ValueError('start_date 和 end_date 不能为 None。')
        start_day = pd.Timestamp(start_date).date()
        end_day = pd.Timestamp(end_date).date()
        if start_day > end_day:
            raise ValueError(
                f'start_date ({start_day}) 不能晚于 end_date ({end_day})。'
            )
        return start_day, end_day

    @staticmethod
    def _union_columns(
            requirements: Sequence[DataRequirement],
    ) -> tuple[str, ...] | None:
        if any(requirement.columns is None for requirement in requirements):
            return None
        columns: list[str] = []
        for requirement in requirements:
            for column in requirement.columns or ():
                if column not in columns:
                    columns.append(column)
        return tuple(columns)

    @staticmethod
    def _merge_request_params(
            source: str,
            requirements: Sequence[DataRequirement],
    ) -> Mapping[str, Any]:
        params = dict(requirements[0].request_params or {})
        for requirement in requirements[1:]:
            other = dict(requirement.request_params or {})
            if other != params:
                raise ValueError(
                    f'数据源 {source} 同时声明了不同的 request_params：'
                    f'{params} vs {other}。请合并为一个 DataRequirement。'
                )
        return params

    @staticmethod
    def _select_columns(
            source: str,
            frame: pd.DataFrame,
            columns: tuple[str, ...] | None,
    ) -> pd.DataFrame:
        if columns is None:
            return frame
        missing = [column for column in columns if column not in frame.columns]
        if missing:
            raise ValueError(f'{source} 缺少请求列: {missing}。')
        return frame.loc[:, list(columns)]

    @staticmethod
    def _require_base_columns(
            frame: pd.DataFrame,
            base_columns: Sequence[str],
            source: str,
    ) -> None:
        missing = [
            column for column in base_columns if column not in frame.columns
        ]
        if missing:
            raise ValueError(f'{source} 缺少基础列: {missing}。')

    def _validate_schema(self, source: str, frame: pd.DataFrame) -> None:
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f'数据源 {source} 的 loader 必须返回 DataFrame。')
        expected_index: tuple[str, ...] | None = None
        if source in ('main_panel', 'session_return'):
            expected_index = _PANEL_INDEX
        elif source == 'term_structure':
            expected_index = _TERM_STRUCTURE_INDEX

        if expected_index is not None:
            if (
                not isinstance(frame.index, pd.MultiIndex)
                or tuple(frame.index.names) != expected_index
            ):
                raise ValueError(
                    f'{source} 的索引层级应为 {list(expected_index)}，'
                    f'实际为 {list(frame.index.names)}。'
                )

        if source == 'warehouse_receipt':
            self._require_base_columns(
                frame, _WAREHOUSE_BASE_COLUMNS, source
            )
        elif source == 'instrument_daily_status':
            self._require_base_columns(frame, _STATUS_BASE_COLUMNS, source)

    def _reset_warnings(self) -> None:
        self.warnings = []
        self._warning_count = 0

    def _warn(self, message: str) -> None:
        self._warning_count += 1
        if len(self.warnings) < _MAX_WARNINGS:
            self.warnings.append(message)

    def _log_warnings(self) -> None:
        if self._warning_count:
            logger.warning(
                '【FactorDataBridge】本次取数共记录 %d 条警告，'
                '明细见 warnings 属性。',
                self._warning_count,
            )

    # ============================================================
    # 数据集复用 / 日期过滤
    # ============================================================

    @staticmethod
    def _reuse_multiindex_frame(
            frame: pd.DataFrame | None,
            columns: tuple[str, ...] | None,
            index_names: tuple[str, ...],
    ) -> pd.DataFrame | None:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return None
        if tuple(frame.index.names) != index_names:
            return None
        if (
            columns is not None
            and any(column not in frame.columns for column in columns)
        ):
            return None
        return frame.copy()

    @staticmethod
    def _reuse_flat_frame(
            frame: pd.DataFrame | None,
            columns: tuple[str, ...] | None,
            base_columns: tuple[str, ...],
    ) -> pd.DataFrame | None:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return None
        required = list(base_columns) + list(columns or ())
        if any(column not in frame.columns for column in required):
            return None
        return frame.copy()

    @staticmethod
    def _filter_multiindex_frame(
            frame: pd.DataFrame,
            products: tuple[str, ...],
            start_day: date,
            end_day: date,
    ) -> pd.DataFrame:
        if frame.empty:
            return frame
        timestamps = pd.to_datetime(
            frame.index.get_level_values('timestamp'), errors='coerce'
        )
        product_level = frame.index.get_level_values('product')
        mask = (
            (timestamps >= pd.Timestamp(start_day))
            & (timestamps <= pd.Timestamp(end_day))
            & product_level.isin(products)
        )
        return frame.loc[mask].sort_index()

    @staticmethod
    def _filter_flat_frame(
            frame: pd.DataFrame,
            products: tuple[str, ...],
            start_day: date,
            end_day: date,
    ) -> pd.DataFrame:
        if frame.empty:
            return frame
        dates = pd.to_datetime(frame['trading_date'], errors='coerce')
        mask = (
            (dates >= pd.Timestamp(start_day))
            & (dates <= pd.Timestamp(end_day))
        )
        if 'product' in frame.columns:
            product_level = frame['product'].astype('string').str.upper()
            mask &= product_level.isin(products)
        return frame.loc[mask].copy()

    @staticmethod
    def _empty_panel_frame(
            columns: tuple[str, ...] | None,
    ) -> pd.DataFrame:
        index = pd.MultiIndex.from_arrays(
            [[], []], names=list(_PANEL_INDEX)
        )
        return pd.DataFrame(columns=list(columns or ()), index=index)

    @staticmethod
    def _empty_term_structure_frame(
            columns: tuple[str, ...] | None,
    ) -> pd.DataFrame:
        index = pd.MultiIndex.from_arrays(
            [[], [], []], names=list(_TERM_STRUCTURE_INDEX)
        )
        return pd.DataFrame(columns=list(columns or ()), index=index)

    @staticmethod
    def _empty_flat_frame(
            base_columns: tuple[str, ...],
            columns: tuple[str, ...] | None,
    ) -> pd.DataFrame:
        names: list[str] = []
        for column in list(base_columns) + list(columns or ()):
            if column not in names:
                names.append(column)
        return pd.DataFrame(columns=names)

    @staticmethod
    def _restrict_flat_columns(
            frame: pd.DataFrame,
            base_columns: tuple[str, ...],
            columns: tuple[str, ...] | None,
    ) -> pd.DataFrame:
        """在 concat 前裁掉未请求的列，避免全空列触发 pandas 的 dtype 警告。"""
        if columns is None:
            return frame
        keep: list[str] = []
        for column in list(base_columns) + list(columns):
            if column in frame.columns and column not in keep:
                keep.append(column)
        return frame.loc[:, keep]

    # ============================================================
    # 数据源 loader
    # ============================================================

    def _load_main_panel(
            self,
            products: tuple[str, ...],
            start_day: date,
            end_day: date,
            columns: tuple[str, ...] | None,
            params: Mapping[str, Any],
    ) -> pd.DataFrame:
        dataset_frame = (
            self._dataset.main_panel if self._dataset is not None else None
        )
        frame = self._reuse_multiindex_frame(
            dataset_frame, columns, _PANEL_INDEX
        )
        if frame is not None:
            present = set(frame.index.get_level_values('product').unique())
            if not set(products).issubset(present):
                frame = None
        if frame is None:
            frame = self._facade.get_historical_vector_multiple_products(
                products=list(products),
                start_date=start_day,
                end_date=end_day,
                freq='1d',
            )
            frame = self._normalize_panel_frame(frame)
        return self._filter_multiindex_frame(
            frame, products, start_day, end_day
        )

    def _load_term_structure(
            self,
            products: tuple[str, ...],
            start_day: date,
            end_day: date,
            columns: tuple[str, ...] | None,
            params: Mapping[str, Any],
    ) -> pd.DataFrame:
        price_col = str(params.get('price_col', 'Close'))
        if price_col not in ('Close', 'Settle'):
            raise ValueError(
                f"term_structure.request_params['price_col'] 仅支持 "
                f"'Close' 或 'Settle'，收到 {price_col!r}。"
            )

        dataset_frame = None
        if (
            self._dataset is not None
            and self._dataset.auxiliary is not None
        ):
            dataset_frame = self._dataset.auxiliary.get('term_structure')

        frame = self._reuse_multiindex_frame(
            dataset_frame, columns, _TERM_STRUCTURE_INDEX
        )
        if frame is not None:
            present = set(frame.index.get_level_values('product').unique())
            if not set(products).issubset(present):
                frame = None
        if frame is None:
            frame = self._facade.get_term_structure_data_multiple_products(
                products=list(products),
                start_date=start_day,
                end_date=end_day,
                price_col=price_col,  # type: ignore[arg-type]
            )
            frame = self._normalize_term_structure_frame(frame)
        return self._filter_multiindex_frame(
            frame, products, start_day, end_day
        )

    def _load_warehouse_receipt(
            self,
            products: tuple[str, ...],
            start_day: date,
            end_day: date,
            columns: tuple[str, ...] | None,
            params: Mapping[str, Any],
    ) -> pd.DataFrame:
        aggregate = bool(params.get('aggregate', False))
        conversions = params.get('agg_unit_conversions')

        dataset_frame = None
        if (
            self._dataset is not None
            and self._dataset.auxiliary is not None
        ):
            dataset_frame = self._dataset.auxiliary.get('warehouse_receipt')

        frame = self._reuse_flat_frame(
            dataset_frame, columns, _WAREHOUSE_BASE_COLUMNS
        )
        if frame is not None:
            present = set(
                frame['product'].astype('string').str.upper().unique()
            )
            if not set(products).issubset(present):
                frame = None
        if frame is not None and aggregate:
            # 若数据集中的仓单仍是明细，而本次要求聚合，则不能直接复用。
            if frame.duplicated(['trading_date', 'product']).any():
                frame = None
        if frame is not None and not aggregate:
            # 明细接口应保留仓库级信息；聚合帧不能冒充明细复用。
            if 'warehouse' not in frame.columns:
                frame = None

        if frame is None:
            frames: list[pd.DataFrame] = []
            for product in products:
                try:
                    product_frame = self._facade.get_warehouse_receipt_data(
                        product=product,
                        start_date=start_day,
                        end_date=end_day,
                        aggregate=aggregate,
                        agg_unit_conversions=conversions,  # type: ignore[arg-type]
                    )
                except FileNotFoundError as exc:
                    self._warn(
                        f'warehouse_receipt: 品种 {product} 缺少数据文件，'
                        f'已跳过。原因: {exc}'
                    )
                    continue
                if product_frame is not None and not product_frame.empty:
                    product_frame = self._restrict_flat_columns(
                        product_frame, _WAREHOUSE_BASE_COLUMNS, columns
                    )
                    frames.append(product_frame)
            if frames:
                frame = pd.concat(frames, ignore_index=True)
            else:
                frame = self._empty_flat_frame(
                    _WAREHOUSE_BASE_COLUMNS, columns
                )

        if not frame.empty:
            frame = frame.copy()
            frame['trading_date'] = pd.to_datetime(
                frame['trading_date'], errors='coerce'
            )
            sort_columns = [
                column for column in ('trading_date', 'product')
                if column in frame.columns
            ]
            if sort_columns:
                frame = frame.sort_values(
                    sort_columns, kind='stable'
                ).reset_index(drop=True)

        return self._filter_flat_frame(
            frame, products, start_day, end_day
        )

    def _load_instrument_daily_status(
            self,
            products: tuple[str, ...],
            start_day: date,
            end_day: date,
            columns: tuple[str, ...] | None,
            params: Mapping[str, Any],
    ) -> pd.DataFrame:
        dataset_frame = None
        if (
            self._dataset is not None
            and self._dataset.auxiliary is not None
        ):
            dataset_frame = self._dataset.auxiliary.get(
                'instrument_daily_status'
            )

        frame = self._reuse_flat_frame(
            dataset_frame, columns, _STATUS_BASE_COLUMNS
        )
        if frame is not None:
            present = set(
                frame['product'].astype('string').str.upper().unique()
            )
            if not set(products).issubset(present):
                frame = None
        if frame is None:
            frames: list[pd.DataFrame] = []
            for product in products:
                try:
                    product_frame = (
                        self._facade.rule_query.get_product_daily_status(
                            product
                        )
                    )
                except FileNotFoundError as exc:
                    self._warn(
                        f'instrument_daily_status: 品种 {product} 缺少数据文件，'
                        f'已跳过。原因: {exc}'
                    )
                    continue
                if product_frame is None or product_frame.empty:
                    continue
                product_frame = product_frame.copy()
                product_frame['product'] = product
                product_frame = self._restrict_flat_columns(
                    product_frame, _STATUS_BASE_COLUMNS, columns
                )
                frames.append(product_frame)
            if frames:
                frame = pd.concat(frames, ignore_index=True)
            else:
                frame = self._empty_flat_frame(
                    _STATUS_BASE_COLUMNS, columns
                )

        if not frame.empty:
            frame = frame.copy()
            frame['trading_date'] = pd.to_datetime(
                frame['trading_date'], errors='coerce'
            )
            sort_columns = [
                column for column in (
                    'trading_date', 'product', 'instrument'
                )
                if column in frame.columns
            ]
            if sort_columns:
                frame = frame.sort_values(
                    sort_columns, kind='stable'
                ).reset_index(drop=True)

        return self._filter_flat_frame(
            frame, products, start_day, end_day
        )

    def _load_session_return(
            self,
            products: tuple[str, ...],
            start_day: date,
            end_day: date,
            columns: tuple[str, ...] | None,
            params: Mapping[str, Any],
    ) -> pd.DataFrame:
        pieces: list[pd.DataFrame] = []
        for product in products:
            product_frame = self._session_return_for_product(
                product, start_day, end_day
            )
            if product_frame is not None and not product_frame.empty:
                pieces.append(product_frame)

        if not pieces:
            return self._empty_panel_frame(_SESSION_COLUMNS)

        frame = pd.concat(pieces).sort_index()
        frame = frame[~frame.index.duplicated(keep='last')]
        return frame

    def _session_return_for_product(
            self,
            product: str,
            start_day: date,
            end_day: date,
    ) -> pd.DataFrame | None:
        """把某品种的 1min 行情按交易日聚合为夜盘/日盘收益。"""
        pieces: list[pd.DataFrame] = []
        for year in range(start_day.year, end_day.year + 1):
            year_start = date(year, 1, 1)
            year_end = date(year, 12, 31)
            keep_start = max(start_day, year_start)
            keep_end = min(end_day, year_end)
            if keep_start > keep_end:
                continue
            # 向前多取一段数据，保证 keep_start 当天第一根夜盘 Bar 的
            # True_Return 有可比前值；重叠段最终会按 keep_start 过滤掉。
            chunk_start = keep_start - timedelta(days=_SESSION_OVERLAP_DAYS)
            chunk_end = keep_end
            if chunk_start > chunk_end:
                continue

            try:
                minute = self._facade.get_historical_vector(
                    product=product,
                    start_date=chunk_start,
                    end_date=chunk_end,
                    freq='1min',
                )
            except (FileNotFoundError, ValueError) as exc:
                self._warn(
                    f'session_return: 品种 {product} 在 {chunk_start}~'
                    f'{chunk_end} 的 1min 数据读取失败，已跳过。原因: {exc}'
                )
                continue

            if minute is None or minute.empty:
                continue
            aggregated = self._aggregate_session_returns(minute)
            if aggregated.empty:
                continue

            dates = pd.to_datetime(
                aggregated['trading_date'], errors='coerce'
            )
            mask = (
                dates.notna()
                & (dates.dt.date >= keep_start)
                & (dates.dt.date <= keep_end)
            )
            aggregated = aggregated.loc[mask].copy()
            if not aggregated.empty:
                pieces.append(aggregated)

        if not pieces:
            return None

        frame = pd.concat(pieces, ignore_index=True)
        frame = frame.drop_duplicates('trading_date', keep='last')
        frame['product'] = product
        frame['timestamp'] = pd.to_datetime(
            frame['trading_date']
        ).dt.normalize()
        frame = frame.set_index(['timestamp', 'product'])[
            list(_SESSION_COLUMNS)
        ]
        return frame.sort_index()

    @staticmethod
    def _normalize_panel_frame(frame: pd.DataFrame) -> pd.DataFrame:
        """把单/多品种日频输出统一成 MultiIndex[timestamp, product]。"""
        if not isinstance(frame, pd.DataFrame):
            raise TypeError('main_panel loader 必须返回 DataFrame。')
        if frame.empty and frame.index.nlevels == 1:
            result = frame.copy()
            result.index = pd.MultiIndex.from_arrays(
                [[], []], names=list(_PANEL_INDEX)
            )
            return result
        if frame.index.nlevels != 2:
            raise ValueError(
                'main_panel 的索引必须为两级 '
                f'[timestamp, product]，实际为 {frame.index.nlevels} 级。'
            )
        result = frame.copy()
        result.index = result.index.set_names(list(_PANEL_INDEX))
        return result.sort_index()

    @staticmethod
    def _normalize_term_structure_frame(
            frame: pd.DataFrame,
    ) -> pd.DataFrame:
        """把 facade 的 [trading_date, contract] 输出重塑为三级索引。"""
        if not isinstance(frame, pd.DataFrame):
            raise TypeError('term_structure loader 必须返回 DataFrame。')
        if tuple(frame.index.names) == _TERM_STRUCTURE_INDEX:
            return frame.copy().sort_index()

        if frame.empty and 'product' not in frame.columns:
            return FactorDataBridge._empty_term_structure_frame(
                tuple(frame.columns)
            )
        if 'product' not in frame.columns:
            raise ValueError(
                'term_structure 缺少 product 列，无法构造 '
                '[timestamp, product, contract] 索引。'
            )
        if frame.index.nlevels != 2:
            raise ValueError(
                'term_structure 原始输出应为 [trading_date, contract] '
                f'两级索引，实际为 {frame.index.nlevels} 级。'
            )

        result = frame.copy()
        timestamps = pd.to_datetime(
            result.index.get_level_values(0), errors='coerce'
        )
        product_level = (
            result['product'].astype('string').str.upper()
        )
        contract_level = result.index.get_level_values(1)
        result.index = pd.MultiIndex.from_arrays(
            [timestamps, product_level, contract_level],
            names=list(_TERM_STRUCTURE_INDEX),
        )
        result = result.drop(columns=['product'])
        return result.sort_index()

    @staticmethod
    def _compound_return(values: pd.Series) -> float:
        """复合一段收益；空段或存在缺失值时返回 NaN。"""
        array = values.to_numpy(dtype='float64')
        if array.size == 0:
            return np.nan
        return float(np.prod(1.0 + array) - 1.0)

    @classmethod
    def _aggregate_session_returns(
            cls,
            minute: pd.DataFrame,
    ) -> pd.DataFrame:
        """把 1min 行情按交易日拆成夜盘/日盘复合收益。

        夜盘定义为时间戳 ``hour >= 18`` 或 ``hour < 8``，其余为日盘。
        夜盘收益包含前一交易日收盘到夜盘收盘的跳空，因为它由夜盘第一根
        Bar 相对前一日最后一根 Bar 的 ``True_Return`` 复合而来。
        """
        missing = [
            column for column in ('True_Return', 'trading_date')
            if column not in minute.columns
        ]
        if missing:
            raise ValueError(f'1min 行情缺少必需列: {missing}。')
        if not isinstance(minute.index, pd.DatetimeIndex):
            raise ValueError('1min 行情索引必须是 DatetimeIndex。')
        if minute.empty:
            return pd.DataFrame(
                columns=['trading_date', *_SESSION_COLUMNS]
            )

        frame = pd.DataFrame(
            {
                'trading_date': pd.to_datetime(
                    minute['trading_date'], errors='coerce'
                ),
                'ret': pd.to_numeric(
                    minute['True_Return'], errors='coerce'
                ).astype('float64'),
                'is_night': (
                    (minute.index.hour >= 18)
                    | (minute.index.hour < 8)
                ),
            },
            index=minute.index,
        )
        frame = frame.dropna(subset=['trading_date'])
        if frame.empty:
            return pd.DataFrame(
                columns=['trading_date', *_SESSION_COLUMNS]
            )

        frame = frame.set_index('trading_date', drop=True)
        grouped = frame.groupby(level=0, sort=True)
        night_return = grouped.apply(
            lambda group: cls._compound_return(
                group.loc[group['is_night'], 'ret']
            )
        )
        day_return = grouped.apply(
            lambda group: cls._compound_return(
                group.loc[~group['is_night'], 'ret']
            )
        )
        result = pd.DataFrame(
            {
                'night_return': night_return,
                'day_return': day_return,
            }
        ).reset_index()
        return result
