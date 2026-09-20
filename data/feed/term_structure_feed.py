"""
期限结构数据馈送器，提供某品种全部上市合约的日频快照数据。

将每日的快照数据、主力合约标识和合约注册信息合并为统一的多合约长表面板，
供因子研究模块进行近月/远月合约识别和展期收益率计算。
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Iterable, Literal
from datetime import date
from unittest import result
import pandas as pd

from data.feed.research_feed import validate_start_end_dates


if TYPE_CHECKING:
    from data.storage.daily_snapshot_manager import DailySnapshotManager
    from data.rules.instrument_registry_manager import InstrumentRegistryManager
    from data.stitching.stitching import ContinuousContractManager


class TermStructureFeed:
    """
    期限结构数据流。

    汇聚某品种全部上市合约的每日快照数据（价格、成交量、持仓量），
    并标注主力合约和合约最后交易日，输出统一的长表格式。

    Parameters
    ------
    product: str
        品种代码，例如 'RB'。
    daily_snapshot_manager: DailySnapshotManager
        日频快照数据管家，提供全量合约日频数据。
    instrument_registry_manager: InstrumentRegistryManager
        合约注册表管家，提供合约的 last_trade_date 等注册信息。
    continuous_contract_manager: ContinuousContractManager
        连续合约大管家，提供主力合约映射表（用于标记 dominant 列）。

    Methods
    ------
    get_term_structure_data(start_date, end_date, price_col='Close')
        返回指定日期范围内，该品种所有上市合约的期限结构长表。
    """

    def __init__(
        self,
        product: str,
        daily_snapshot_manager: DailySnapshotManager,
        registry_manager: InstrumentRegistryManager,
        contract_manager: ContinuousContractManager,
    ):
        self.product = product.upper()
        self.daily_snapshot_manager = daily_snapshot_manager
        self.instrument_registry_manager = registry_manager
        self.contract_manager = contract_manager

    def get_term_structure_data(
        self,
        start_date: date | None,
        end_date: date | None,
        price_col: Literal['Close', 'Settle'] = 'Close',
    ) -> pd.DataFrame:
        """
        返回指定日期范围内，该品种所有上市合约的期限结构长表。

        Parameters
        ------
        start_date: datetime.date or None, default None
            起始交易日。默认为 None ，即最早可用。
        end_date: datetime.date or None, default None
            结束交易日。默认为 None ，即最晚可用。
        price_col: {'Close', 'Settle'}, default 'Close'
            价格变量口径。为 'Close' 时使用收盘价，为 'Settle' 时使用结算价；
            构造的价格列以所选口径命名。

        Returns
        -------
        pd.DataFrame
            期限结构长表，MultiIndex[trading_date, contract]。

        Columns
        ~~~~~~~
            product: str
                品种代码。
            is_dominant: bool
                是否为当日主力合约。
            Close / Settle: float
                价格列，列名与参数 price_col 一致（收盘价或结算价）。
            last_trade_date: datetime.date | NaT
                合约最后交易日。
            OpenInterest: float
                持仓量。
            Volume: float
                成交量。
        """
        if price_col not in ('Close', 'Settle'):
            raise ValueError(
                f"[TermStructureFeed] price_col 仅支持 'Close' 或 'Settle'，"
                f"收到 {price_col!r}。"
            )

        product_df = self.daily_snapshot_manager.get_product_df(
            self.product, normalize_col_names=True
        )

        product_df["trading_date"] = pd.to_datetime(product_df["Datetime"])

        # 处理 start_date 和 end_date 逻辑
        earliest: date = product_df['trading_date'].min().date()
        latest: date = product_df['trading_date'].max().date()
        if start_date is None:
            start_date = earliest
        if end_date is None:
            end_date = latest
        validate_start_end_dates(start_date, end_date, earliest, latest)

        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        product_df = product_df[
            (product_df["trading_date"] >= start_ts)
            & (product_df["trading_date"] <= end_ts)
        ]
        product_df.rename(columns={"Contract": "contract"}, inplace=True)

        result_df = product_df[
            ["trading_date", "contract", price_col, "Volume", "OpenInterest"]
        ].copy()

        dominant_s = self._build_dominant_series(result_df)
        result_df["is_dominant"] = result_df["contract"] == dominant_s

        ltd_map = self._build_last_trade_date_map(result_df["contract"].unique())
        result_df["last_trade_date"] = result_df["contract"].map(ltd_map)

        result_df["product"] = self.product

        # 设置 MultiIndex 为索引
        result_df = result_df.set_index(["trading_date", "contract"])
        result_df = result_df.sort_index()

        result_df = result_df[
            ["product", "is_dominant", price_col, "last_trade_date", "OpenInterest", "Volume"]
        ]

        return result_df

    def _build_dominant_series(self, df: pd.DataFrame) -> pd.Series:
        """
        根据 ContinuousContractManager 的主力合约映射表，
        生成一条与 df 行对齐的 Dominant_Contract 序列。
        """
        master_table = self.contract_manager.master_table
        if "Dominant_Contract" not in master_table.columns:
            raise ValueError(f'[TermStructureFeed] 品种 {self.product} 的 master table 中缺少 Dominant_Contract 列。')

        dominant_by_date = master_table["Dominant_Contract"]
        dominant_s = df["trading_date"].map(
            dict(zip(dominant_by_date.index, dominant_by_date.values))
        )
        return dominant_s

    def _build_last_trade_date_map(
        self, contracts: Iterable[str]
    ) -> dict[str, date]:
        """
        通过 InstrumentRegistryManager 为每个合约查询 last_trade_date。
        """
        ltd_map: dict[str, date] = {}
        for c in contracts:
            try:
                registry = self.instrument_registry_manager.get_instrument_registry(c)
                ltd_map[c] = registry.last_trade_date
            except KeyError as err: # 不要静默返回None，直接把合约注册表发现的未找到错误抛出来
                raise ValueError(f'[TermstructureFeed] 在合约注册表中无法找到合约 {c}') from err
        return ltd_map


# ===============================
# inline testing
# ===============================
if __name__ == "__main__":
    from datetime import date
    from data.config import RolloverConfig, EnvironmentConfig
    from data.rules.product_meta_manager import ProductMetaManager
    from data.rules.instrument_registry_manager import InstrumentRegistryManager
    from data.storage.daily_snapshot_loader import DailySnapshotLoader
    from data.storage.daily_snapshot_manager import DailySnapshotManager
    from data.stitching.stitching import ContinuousContractManager

    env_config = EnvironmentConfig()
    rollover_config = RolloverConfig()

    meta_manager = ProductMetaManager(env_config.product_meta_dir)
    registry_manager = InstrumentRegistryManager(env_config.instrument_registry_dir)

    daily_snapshot_loader = DailySnapshotLoader(
        daily_snapshot_dir=env_config.daily_snapshot_dir,
        meta_manager=meta_manager,
    )
    daily_snapshot_manager = DailySnapshotManager(
        daily_snapshot_loader=daily_snapshot_loader,
        registry_manager=registry_manager,
    )

    product = "AP"
    contract_manager = ContinuousContractManager(
        product=product,
        rollover_config=rollover_config,
        master_table_dir=env_config.master_table_dir,
    )

    feed = TermStructureFeed(
        product=product,
        daily_snapshot_manager=daily_snapshot_manager,
        registry_manager=registry_manager,
        contract_manager=contract_manager,
    )

    df = feed.get_term_structure_data(
        start_date=date(2021, 8, 1),
        end_date=date(2021, 8, 10),
    )
    print(f"品种 {product} 期限结构数据 (2021-08-01 ~ 2021-08-10):")
    print(df.to_string())
    print(f"\nDataFrame 索引名称: {df.index.names}")
    print(f"DataFrame 列: {df.columns.tolist()}")
    print(f"行数: {len(df)}")
    print(f"唯一合约数: {df.index.get_level_values('contract').nunique()}")
