"""
从 Tushare Pro 拉取仓单日报，标准化后写入本地基本面数据库。

支持全量和增量两种模式。采集过程会保留原始 JSON Lines 快照和 JSON 审计，
正式 Parquet 文件按交易所、品种拆分，并通过临时文件原子替换。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import pandas as pd
import requests

from data.rules.product_meta_manager import ProductMetaManager
from data.storage.warehouse_receipt_loader import WAREHOUSE_RECEIPT_COLUMNS


API_NAME = "fut_wsr"
API_URL = "https://api.tushare.pro"
API_LIMIT = 1000
SOURCE_COLUMNS = [
    "trade_date",
    "symbol",
    "fut_name",
    "warehouse",
    "wh_id",
    "pre_vol",
    "vol",
    "vol_chg",
    "area",
    "year",
    "grade",
    "brand",
    "place",
    "pd",
    "is_ct",
    "unit",
    "exchange",
]
STRING_COLUMNS = [
    "product",
    "product_name",
    "exchange",
    "source_symbol",
    "source_product_name",
    "source_exchange",
    "warehouse",
    "warehouse_id",
    "unit",
    "area",
    "year",
    "grade",
    "brand",
    "place",
    "is_converted_receipt",
]
INTEGER_COLUMNS = [
    "previous_receipt_quantity",
    "receipt_quantity",
    "receipt_change_quantity",
    "premium_discount",
]
NATURAL_KEY_COLUMNS = [
    "trading_date",
    "product",
    "source_product_name",
    "warehouse",
    "warehouse_id",
    "area",
    "year",
    "grade",
    "brand",
    "place",
    "unit",
    "is_converted_receipt",
]
EXCHANGE_ALIASES = {
    "CCFX": "CFFEX",
    "CFFEX": "CFFEX",
    "CZCE": "CZCE",
    "ZCE": "CZCE",
    "DCE": "DCE",
    "GFEX": "GFEX",
    "INE": "INE",
    "SHF": "SHFE",
    "SHFE": "SHFE",
}
EMBEDDED_PRODUCT_PATTERN = re.compile(r"[（(]\s*([A-Za-z][A-Za-z0-9_]*)\s*[)）]")
LINKED_SOURCE_PRODUCTS = ("BC", "CU")


class WarehouseReceiptClient(Protocol):
    """Tushare 仓单接口的最小客户端协议。"""

    def query(
        self,
        product: str,
        start_date: date,
        end_date: date,
        offset: int = 0,
    ) -> pd.DataFrame:
        """查询一个品种在日期闭区间内的原始仓单数据。"""


class TushareApiError(RuntimeError):
    """Tushare API 返回失败状态。"""


class TushareHttpClient:
    """直接调用 Tushare Pro HTTPS API，避免绑定特定 Conda 环境。"""

    def __init__(self, token: str, timeout: float = 60.0) -> None:
        self._token = token
        self._timeout = timeout
        self._session = requests.Session()

    def query(
        self,
        product: str,
        start_date: date,
        end_date: date,
        offset: int = 0,
    ) -> pd.DataFrame:
        payload = {
            "api_name": API_NAME,
            "token": self._token,
            "params": {
                "symbol": product,
                "start_date": start_date.strftime("%Y%m%d"),
                "end_date": end_date.strftime("%Y%m%d"),
                "offset": offset,
            },
            "fields": ",".join(SOURCE_COLUMNS),
        }
        response = self._session.post(API_URL, json=payload, timeout=self._timeout)
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise TushareApiError(
                f"Tushare {API_NAME} 调用失败：code={body.get('code')}, msg={body.get('msg')}"
            )
        data = body.get("data") or {}
        fields = data.get("fields") or SOURCE_COLUMNS
        items = data.get("items") or []
        return pd.DataFrame(items, columns=fields)


class TushareSdkClient:
    """使用 Tushare Python SDK 调用仓单接口。"""

    def __init__(self, token: str) -> None:
        try:
            import tushare as ts
        except ImportError as exc:
            raise RuntimeError("当前环境未安装 tushare，请改用 --transport http。") from exc
        self._pro = ts.pro_api(token)

    def query(
        self,
        product: str,
        start_date: date,
        end_date: date,
        offset: int = 0,
    ) -> pd.DataFrame:
        return self._pro.fut_wsr(
            symbol=product,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            offset=offset,
            fields=",".join(SOURCE_COLUMNS),
        )


@dataclass
class FetchStats:
    """单品种采集统计。"""

    api_calls: int = 0
    retries: int = 0
    paginated_pages: int = 0


class WarehouseReceiptFetcher:
    """负责请求节流、失败重试和 1000 行上限拆分。"""

    def __init__(
        self,
        client: WarehouseReceiptClient,
        api_limit: int = API_LIMIT,
        request_interval: float = 0.35,
        max_retries: int = 5,
    ) -> None:
        if api_limit <= 0:
            raise ValueError("api_limit 必须为正整数。")
        self.client = client
        self.api_limit = api_limit
        self.request_interval = max(request_interval, 0.0)
        self.max_retries = max_retries
        self._last_request_at: float | None = None

    def _query_with_retry(
        self,
        product: str,
        start_date: date,
        end_date: date,
        offset: int,
        stats: FetchStats,
    ) -> pd.DataFrame:
        for attempt in range(self.max_retries + 1):
            if self._last_request_at is not None:
                elapsed = time.monotonic() - self._last_request_at
                if elapsed < self.request_interval:
                    time.sleep(self.request_interval - elapsed)
            try:
                stats.api_calls += 1
                result = self.client.query(product, start_date, end_date, offset=offset)
                self._last_request_at = time.monotonic()
                return result
            except (requests.RequestException, TushareApiError, RuntimeError):
                self._last_request_at = time.monotonic()
                if attempt >= self.max_retries:
                    raise
                stats.retries += 1
                time.sleep(min(2**attempt, 30))
        raise AssertionError("重试循环不应运行到此处。")

    def _fetch_interval(
        self,
        product: str,
        start_date: date,
        end_date: date,
        stats: FetchStats,
    ) -> list[pd.DataFrame]:
        pages: list[pd.DataFrame] = []
        offset = 0
        while True:
            df = self._query_with_retry(product, start_date, end_date, offset, stats)
            if df.empty:
                break
            if pages and df.equals(pages[-1]):
                raise RuntimeError(
                    f"{product} {start_date}~{end_date} 的 offset={offset} 返回重复页，"
                    "为防止静默截断已停止入库。"
                )
            pages.append(df)
            if len(df) < self.api_limit:
                break
            offset += len(df)
            stats.paginated_pages += 1
        return pages

    def fetch(
        self,
        product: str,
        start_date: date,
        end_date: date,
    ) -> tuple[pd.DataFrame, FetchStats]:
        """按自然年分块抓取指定品种，并在达到上限时递归细分。"""
        if start_date > end_date:
            raise ValueError("start_date 不能晚于 end_date。")
        stats = FetchStats()
        frames: list[pd.DataFrame] = []
        cursor = start_date
        while cursor <= end_date:
            chunk_end = min(date(cursor.year, 12, 31), end_date)
            frames.extend(self._fetch_interval(product, cursor, chunk_end, stats))
            cursor = chunk_end + timedelta(days=1)

        nonempty = [frame for frame in frames if not frame.empty]
        if not nonempty:
            return pd.DataFrame(columns=SOURCE_COLUMNS), stats
        # 各交易所的可选字段常整段为空；先移除全空列再拼接，避免 Pandas
        # 在未来版本改变全空列的 dtype 推断行为，随后恢复完整源 schema。
        result = pd.concat(
            [frame.dropna(axis=1, how="all") for frame in nonempty],
            ignore_index=True,
        ).reindex(columns=SOURCE_COLUMNS)
        result = result.drop_duplicates().reset_index(drop=True)
        return result, stats


def load_tushare_token(
    explicit_token: str | None,
    credential_path: Path,
) -> str:
    """按 CLI、环境变量、凭证文件的顺序读取 Tushare Token。"""
    if explicit_token:
        return explicit_token.strip()
    env_token = os.environ.get("TUSHARE_TOKEN")
    if env_token:
        return env_token.strip()
    if not credential_path.exists():
        raise FileNotFoundError(f"未找到 Tushare 凭证文件：{credential_path}")

    lines = [line.strip() for line in credential_path.read_text(encoding="utf-8").splitlines()]
    for index, line in enumerate(lines):
        if "tushare" not in line.lower():
            continue
        for separator in ("：", ":", "="):
            if separator in line:
                candidate = line.split(separator, 1)[1].strip()
                if candidate:
                    return candidate
        for following in lines[index + 1 :]:
            if following:
                return following
    raise ValueError(f"无法从凭证文件识别 Tushare Token：{credential_path}")


def empty_canonical_frame() -> pd.DataFrame:
    """创建具有稳定仓单 schema 的空表。"""
    result = pd.DataFrame({"trading_date": pd.Series(dtype="datetime64[ns]")})
    for column in STRING_COLUMNS:
        result[column] = pd.Series(dtype="string")
    for column in INTEGER_COLUMNS:
        result[column] = pd.Series(dtype="Int64")
    return result.loc[:, WAREHOUSE_RECEIPT_COLUMNS]


def deduplicate_canonical_records(df: pd.DataFrame) -> pd.DataFrame:
    """
    按业务自然键去重；来源品种等于目标品种的直接记录优先于跨品种补充记录。

    例如 Tushare 的 CU 响应含 ``铜(BC)``，用于补齐 BC 直接接口未覆盖的历史；
    两个来源重叠时保留 BC 直接记录，避免仓单量重复计算。
    """
    if df.empty:
        return df.copy()
    result = df.copy()
    result["_is_direct_source"] = result["source_symbol"].eq(result["product"])
    result.sort_values(
        NATURAL_KEY_COLUMNS + ["_is_direct_source"],
        na_position="last",
        kind="stable",
        inplace=True,
    )
    result = result.drop_duplicates(NATURAL_KEY_COLUMNS, keep="last")
    result.drop(columns="_is_direct_source", inplace=True)
    return result


def normalize_records(
    raw: pd.DataFrame,
    product: str,
    meta_manager: ProductMetaManager,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """将 Tushare 原始字段转换为本地权威 schema。"""
    product = product.upper()
    source_static_meta = meta_manager.get_static_meta(product)
    stats = {
        "exchange_conflicts": 0,
        "quantity_inconsistencies": 0,
        "rerouted_rows": 0,
        "duplicate_rows_dropped": 0,
    }
    if raw.empty:
        return empty_canonical_frame(), stats

    missing = set(SOURCE_COLUMNS).difference(raw.columns)
    if missing:
        raise ValueError(f"Tushare 仓单响应缺少字段：{sorted(missing)}")

    source_symbols = set(raw["symbol"].dropna().astype(str).str.strip().str.upper())
    unexpected_symbols = source_symbols.difference({product})
    if unexpected_symbols:
        raise ValueError(
            f"请求品种 {product} 时收到其他品种：{sorted(unexpected_symbols)}"
        )

    source_exchange_codes = raw["exchange"].astype("string").str.strip().str.upper()
    nonempty_exchange = source_exchange_codes.notna() & source_exchange_codes.ne("")
    normalized_source_exchanges = source_exchange_codes.map(EXCHANGE_ALIASES)
    stats["exchange_conflicts"] = int(
        (
            nonempty_exchange
            & normalized_source_exchanges.fillna(source_exchange_codes).ne(
                source_static_meta.exchange
            )
        ).sum()
    )

    result = raw.rename(
        columns={
            "trade_date": "trading_date",
            "symbol": "source_symbol",
            "fut_name": "source_product_name",
            "exchange": "source_exchange",
            "wh_id": "warehouse_id",
            "pre_vol": "previous_receipt_quantity",
            "vol": "receipt_quantity",
            "vol_chg": "receipt_change_quantity",
            "pd": "premium_discount",
            "is_ct": "is_converted_receipt",
        }
    ).copy()
    result["trading_date"] = pd.to_datetime(
        result["trading_date"], format="%Y%m%d", errors="raise"
    )
    try:
        universe = {
            item.upper() for item in meta_manager.get_product_universe("list")
        }
    except AttributeError:
        universe = {product}

    def resolve_target_product(source_name: Any) -> str:
        if pd.isna(source_name):
            return product
        for candidate in EMBEDDED_PRODUCT_PATTERN.findall(str(source_name)):
            candidate = candidate.upper()
            if candidate in universe:
                return candidate
        return product

    result["product"] = result["source_product_name"].map(resolve_target_product)
    stats["rerouted_rows"] = int(result["product"].ne(product).sum())
    result["product_name"] = pd.NA
    result["exchange"] = pd.NA
    for target_product in result["product"].unique():
        target_meta = meta_manager.get_static_meta(str(target_product))
        target_mask = result["product"].eq(target_product)
        result.loc[target_mask, "product_name"] = target_meta.name
        result.loc[target_mask, "exchange"] = target_meta.exchange
    result["source_exchange"] = normalized_source_exchanges.fillna(source_exchange_codes)

    for column in STRING_COLUMNS:
        result[column] = result[column].astype("string").str.strip()
        result[column] = result[column].mask(result[column].eq(""), pd.NA)
    for column in INTEGER_COLUMNS:
        numeric = pd.to_numeric(result[column], errors="coerce")
        non_integral = numeric.notna() & numeric.mod(1).ne(0)
        if non_integral.any():
            raise ValueError(f"字段 {column} 出现非整数仓单值。")
        result[column] = numeric.astype("Int64")

    comparable = result[
        [
            "previous_receipt_quantity",
            "receipt_quantity",
            "receipt_change_quantity",
        ]
    ].notna().all(axis=1)
    inconsistent = comparable & (
        result["previous_receipt_quantity"] + result["receipt_change_quantity"]
        != result["receipt_quantity"]
    )
    stats["quantity_inconsistencies"] = int(inconsistent.sum())

    result = result.loc[:, WAREHOUSE_RECEIPT_COLUMNS]
    rows_before_deduplication = len(result)
    result = deduplicate_canonical_records(result)
    stats["duplicate_rows_dropped"] = rows_before_deduplication - len(result)
    result.sort_values(
        [
            "trading_date",
            "product",
            "source_product_name",
            "warehouse",
            "warehouse_id",
            "brand",
            "grade",
        ],
        na_position="last",
        kind="stable",
        inplace=True,
    )
    result.reset_index(drop=True, inplace=True)
    return result, stats


def merge_source_records(
    existing: pd.DataFrame,
    incoming: pd.DataFrame,
    source_symbol: str,
    refresh_start: date,
    refresh_end: date,
) -> pd.DataFrame:
    """仅替换指定来源品种的日期区间，保留同一目标品种的其他来源行。"""
    if existing.empty:
        result = incoming.copy()
    else:
        existing = existing.copy()
        existing["trading_date"] = pd.to_datetime(existing["trading_date"])
        in_refresh_window = existing["trading_date"].between(
            pd.Timestamp(refresh_start), pd.Timestamp(refresh_end), inclusive="both"
        )
        from_requested_source = existing["source_symbol"].eq(source_symbol.upper())
        result = pd.concat(
            [existing.loc[~(in_refresh_window & from_requested_source)], incoming],
            ignore_index=True,
        )
    result = deduplicate_canonical_records(result)
    result.sort_values(
        [
            "trading_date",
            "product",
            "source_product_name",
            "warehouse",
            "warehouse_id",
            "brand",
            "grade",
        ],
        na_position="last",
        kind="stable",
        inplace=True,
    )
    result.reset_index(drop=True, inplace=True)
    return result.loc[:, WAREHOUSE_RECEIPT_COLUMNS]


def merge_incremental_records(
    existing: pd.DataFrame,
    incoming: pd.DataFrame,
    refresh_start: date,
    refresh_end: date,
) -> pd.DataFrame:
    """用重新下载的数据完整替换回看区间，再合并更早历史。"""
    if existing.empty:
        result = incoming.copy()
    else:
        existing = existing.copy()
        existing["trading_date"] = pd.to_datetime(existing["trading_date"])
        keep = ~existing["trading_date"].between(
            pd.Timestamp(refresh_start), pd.Timestamp(refresh_end), inclusive="both"
        )
        result = pd.concat([existing.loc[keep], incoming], ignore_index=True)
    result = deduplicate_canonical_records(result)
    result.sort_values(
        ["trading_date", "warehouse", "warehouse_id", "brand", "grade"],
        na_position="last",
        kind="stable",
        inplace=True,
    )
    result.reset_index(drop=True, inplace=True)
    return result.loc[:, WAREHOUSE_RECEIPT_COLUMNS]


def write_parquet_atomic(df: pd.DataFrame, output_path: Path) -> None:
    """先写同目录临时文件，再原子替换正式文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        df.to_parquet(temporary_path, index=False, engine="pyarrow")
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def parse_cli_date(value: str) -> date:
    """解析 YYYY-MM-DD 或 YYYYMMDD 日期参数。"""
    compact = value.replace("-", "")
    try:
        return datetime.strptime(compact, "%Y%m%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"无效日期：{value}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tushare 仓单日报全量/增量入库")
    parser.add_argument("--mode", choices=["full", "incremental"], required=True)
    parser.add_argument("--products", nargs="*", help="品种代码；默认 product_meta 全集")
    parser.add_argument("--start-date", type=parse_cli_date)
    parser.add_argument("--end-date", type=parse_cli_date, default=date.today())
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--request-interval", type=float, default=0.35)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--token")
    parser.add_argument("--transport", choices=["http", "sdk"], default="http")
    parser.add_argument("--product-meta-dir", default="db/rules/product_meta")
    parser.add_argument("--output-dir", default="db/fundamental/warehouse_receipts")
    parser.add_argument(
        "--credential-path",
        default="db/rules_ingestion/supplements/API凭证.txt",
    )
    parser.add_argument(
        "--workspace-data-dir",
        default="db/fundamental_ingestion/data",
    )
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    """执行入库并返回不含凭证的审计字典。"""
    if args.lookback_days < 0:
        raise ValueError("lookback_days 不能为负数。")
    token = load_tushare_token(args.token, Path(args.credential_path))
    client: WarehouseReceiptClient
    if args.transport == "sdk":
        client = TushareSdkClient(token)
    else:
        client = TushareHttpClient(token)
    fetcher = WarehouseReceiptFetcher(
        client,
        request_interval=args.request_interval,
        max_retries=args.max_retries,
    )

    meta_manager = ProductMetaManager(args.product_meta_dir)
    universe = [product.upper() for product in meta_manager.get_product_universe("list")]
    requested = [product.upper() for product in (args.products or universe)]
    unknown = sorted(set(requested).difference(universe))
    if unknown:
        raise ValueError(f"以下品种不在 product_meta 中：{unknown}")
    requested = list(dict.fromkeys(requested))
    if set(requested).intersection(LINKED_SOURCE_PRODUCTS):
        # Tushare 的 CU 响应包含一部分 BC 仓单，二者必须成组刷新；先处理
        # BC 直接接口，再用 CU 响应补齐其未覆盖历史。
        requested = list(LINKED_SOURCE_PRODUCTS) + [
            item for item in requested if item not in LINKED_SOURCE_PRODUCTS
        ]

    # 微秒和进程号避免多个互斥品种分组并行入库时共享运行目录或覆盖审计。
    run_id = f"{datetime.now().strftime('%Y%m%dT%H%M%S%f')}_p{os.getpid()}"
    workspace_data_dir = Path(args.workspace_data_dir)
    raw_run_dir = workspace_data_dir / "raw" / "warehouse_receipts" / run_id
    audit_path = workspace_data_dir / "audit" / f"warehouse_receipts_{run_id}.json"
    output_dir = Path(args.output_dir)
    product_audits: dict[str, dict[str, Any]] = {}

    for sequence, product in enumerate(requested, start=1):
        static_meta = meta_manager.get_static_meta(product)
        source_output_path = output_dir / f"{static_meta.exchange}_{product}.parquet"
        if source_output_path.exists():
            source_existing = pd.read_parquet(source_output_path)
            if source_existing.columns.tolist() != WAREHOUSE_RECEIPT_COLUMNS:
                if args.mode == "incremental":
                    raise ValueError(
                        f"{source_output_path} 是旧版仓单 schema，请先执行 full 模式重建。"
                    )
                source_existing = empty_canonical_frame()
        else:
            source_existing = empty_canonical_frame()

        if args.start_date is not None:
            fetch_start = args.start_date
        elif args.mode == "incremental" and not source_existing.empty:
            own_source_rows = source_existing[
                source_existing["source_symbol"].eq(product)
            ]
            latest = (
                static_meta.list_date
                if own_source_rows.empty
                else pd.to_datetime(own_source_rows["trading_date"]).max().date()
            )
            fetch_start = latest - timedelta(days=args.lookback_days)
        else:
            fetch_start = static_meta.list_date
        fetch_end = args.end_date
        if fetch_start > fetch_end:
            raw = pd.DataFrame(columns=SOURCE_COLUMNS)
            fetch_stats = FetchStats()
        else:
            raw, fetch_stats = fetcher.fetch(product, fetch_start, fetch_end)

        raw_run_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_run_dir / f"{static_meta.exchange}_{product}.jsonl.gz"
        raw.to_json(
            raw_path,
            orient="records",
            lines=True,
            force_ascii=False,
            compression="gzip",
        )

        normalized, normalization_stats = normalize_records(raw, product, meta_manager)
        target_products = sorted(set(normalized["product"].dropna().astype(str)) | {product})
        output_audits: dict[str, dict[str, Any]] = {}
        for target_product in target_products:
            target_meta = meta_manager.get_static_meta(target_product)
            output_path = output_dir / f"{target_meta.exchange}_{target_product}.parquet"
            if output_path.exists():
                existing = pd.read_parquet(output_path)
                if existing.columns.tolist() != WAREHOUSE_RECEIPT_COLUMNS:
                    if args.mode == "incremental":
                        raise ValueError(
                            f"{output_path} 是旧版仓单 schema，请先执行 full 模式重建。"
                        )
                    existing = empty_canonical_frame()
            else:
                existing = empty_canonical_frame()
            incoming = normalized[normalized["product"].eq(target_product)].copy()
            final = merge_source_records(
                existing,
                incoming,
                source_symbol=product,
                refresh_start=fetch_start,
                refresh_end=fetch_end,
            )
            write_parquet_atomic(final, output_path)
            output_audits[target_product] = {
                "rows_before": len(existing),
                "incoming_rows": len(incoming),
                "rows_after": len(final),
                "output_file": str(output_path),
            }

        product_audits[product] = {
            "sequence": sequence,
            "exchange": static_meta.exchange,
            "fetch_start": fetch_start.isoformat(),
            "fetch_end": fetch_end.isoformat(),
            "api_calls": fetch_stats.api_calls,
            "retries": fetch_stats.retries,
            "paginated_pages": fetch_stats.paginated_pages,
            "source_rows": len(raw),
            "normalized_rows": len(normalized),
            "outputs": output_audits,
            "exchange_conflicts": normalization_stats["exchange_conflicts"],
            "quantity_inconsistencies": normalization_stats["quantity_inconsistencies"],
            "rerouted_rows": normalization_stats["rerouted_rows"],
            "duplicate_rows_dropped": normalization_stats["duplicate_rows_dropped"],
            "raw_file": str(raw_path),
        }
        print(
            f"[{sequence}/{len(requested)}] {product}: "
            f"source_rows={len(raw)} normalized_rows={len(normalized)} "
            f"targets={','.join(target_products)} api_calls={fetch_stats.api_calls}",
            flush=True,
        )

    audit = {
        "run_id": run_id,
        "api_name": API_NAME,
        "mode": args.mode,
        "transport": args.transport,
        "started_product_count": len(requested),
        "completed_product_count": len(product_audits),
        "products": product_audits,
        "totals": {
            "api_calls": sum(item["api_calls"] for item in product_audits.values()),
            "retries": sum(item["retries"] for item in product_audits.values()),
            "source_rows": sum(item["source_rows"] for item in product_audits.values()),
            "normalized_rows": sum(
                item["normalized_rows"] for item in product_audits.values()
            ),
            "exchange_conflicts": sum(item["exchange_conflicts"] for item in product_audits.values()),
            "rerouted_rows": sum(item["rerouted_rows"] for item in product_audits.values()),
            "duplicate_rows_dropped": sum(
                item["duplicate_rows_dropped"] for item in product_audits.values()
            ),
            "quantity_inconsistencies": sum(
                item["quantity_inconsistencies"] for item in product_audits.values()
            ),
        },
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"audit={audit_path}")
    return audit


def main() -> None:
    args = build_parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()
