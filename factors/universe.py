"""动态品种池管理。"""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
import pandas as pd

if TYPE_CHECKING:
    from factors.models import UniverseSpec
    from data.facade import DataFacade


@dataclass(frozen=True)
class Universe:
    """动态品种池的构建结果。

    将准入掩码与其排除原因绑定为一个领域对象，避免两者在传递过程中
    失配或丢失，从而保证品种池排除原因可追溯、可审计。

    Parameters
    ----------
    eligible : pd.Series
        品种池准入布尔掩码，索引与主面板一致（MultiIndex[timestamp, product]）。
    exclude_reason : pd.Series
        与 eligible 同索引的排除原因，未被排除的样本为空字符串；
        单个样本可能因多条约束被排除，原因以 ';' 连接。
    """

    eligible: pd.Series
    exclude_reason: pd.Series


class DynamicUniverse:
    """动态品种池。"""

    def __init__(self, spec: UniverseSpec, facade: DataFacade):
        self.spec = spec
        self.facade = facade

    def build(self, panel: pd.DataFrame) -> Universe:
        """构建动态品种池准入。
        
        Parameters
        ------
        panel: pd.DataFrame
            研究主面板。 MultiIndex[timestamp, product]
            内部会新增临时列 'list_date'、'days_from_list_date'、'delist_date'
            （不改动入参）。
            依赖列：
            - 'Volume'：成交量约束（min_daily_volume / min_average_volume）
            - 'OpenInterest'：持仓量约束（min_open_interest）
            - 'True_Return'：有效数据覆盖率（min_data_coverage > 0 时必需，
              否则跳过该约束）

        Returns
        ------
        Universe
            - eligible: 品种池准入布尔掩码，索引与 panel 一致。
            - exclude_reason: 对应的排除原因，索引与 panel 一致。

        Notes
        -----
        退市日约束的判定口径见下方 1.2 的注释：**`timestamp >= delist_date`
        即退出品种池**（含退市当日），这与 `ProductCalendar` 把 `delist_date`
        当作"含当天的最后一个合法交易日"的处理**故意不同**，原因见注释。
        """
        panel = panel.copy()

        eligible = pd.Series([True]* len(panel), index=panel.index)
        exclude_reason = pd.Series('' * len(panel), index=panel.index)

        # 1. 品种存续性约束
        # 1.1 上市日约束
        products = panel.index.get_level_values('product').unique()
        list_date_mapping = {
            product: pd.Timestamp(
                self.facade.rule_query.get_list_date_product(product))
            for product in products
        }
        panel['list_date'] = panel.index.get_level_values('product') \
            .map(list_date_mapping) 
        panel['days_from_list_date'] = \
            (panel.index.get_level_values('timestamp') \
             - panel['list_date']).dt.days

        mask = (panel['days_from_list_date'] \
                       >= self.spec.min_listing_days)
        eligible &= mask
        exclude_reason.loc[~mask] = exclude_reason + 'min_listing_days'  

        # 1.2 退市日约束
        #
        # 【口径与 ProductCalendar 故意不同，勿"修正"为一致】
        # `StaticMeta.delist_date` 的取值是**最后一个合约的最后交易日**，
        # 而 `ProductCalendar._validate_date_in_life_cycle` 把它当作
        # **含当天的最后一个合法交易日**（`trading_date <= delist_date` 判合法，
        # 交易日切片上界 `min(delist_date, end_date)` 同样含端点）。
        #
        # 本约束**故意取相反口径**：`timestamp >= delist_date` 即退出品种池，
        # 即最后交易日当天（收盘后）就判定该品种已退出。
        #
        # 理由是 `eligible` 的语义为"**截至该 timestamp 可得的信息**"
        # （日频下即该交易日收盘后），属于 point-in-time 判定：
        # 最后交易日收盘时，品种退市已确定、不可再开新仓，故当日即应判定其出池。
        #
        # 该差异是下游权重时点对齐所必需：`DiagnosticPortfolio` 的
        # `_apply_universe` 为 `np.where(eligible, w, 0)`，且第 t 期权重对应
        # t→t+1 的收益。若最后交易日仍 eligible=True，则退市后第一天的权重
        # 可能非 0，而该日收益缺失，在
        # `DiagnosticPortfolioSpec.missing_return_policy='raise'` 下
        # （`accounting.compute_contribution`：权重非 0 且收益 NaN 即抛错）
        # 会直接报错。取 `>=` 后，退市后第一天权重为 0、缺失收益按 0 计，
        # 整个实验可以跑通。
        #
        # 仍在市的品种 `delist_date` 为 None，这里统一映射为 NaT。
        # NaT 参与比较恒为 False，因此 `timestamp >= delist_date` 对在市品种恒为
        # False（未退市），不会把它们误杀；反之若写成 `timestamp < delist_date`
        # 作为准入条件，则 NaT 会让**所有在市品种全部被排除**，务必避免。
        delist_date_mapping = {
            product: self.facade.rule_query.get_delist_date_product(product)
            for product in products
        }
        panel['delist_date'] = panel.index.get_level_values('product') \
            .map({p: pd.NaT if d is None else pd.Timestamp(d)
                  for p, d in delist_date_mapping.items()})
        # 已退市（含退市当日）→ 排除；仍在市（delist_date 为 NaT）→ 保留。
        # 注：索引层与 Series 直接比较返回 ndarray，需先转回 Series 才能
        # 保持与 panel.index 对齐（eligible / exclude_reason 依赖该对齐）。
        delisted = (
            panel.index.get_level_values('timestamp')
            .to_series(index=panel.index)
            >= panel['delist_date']
        ).fillna(False)
        mask = ~delisted
        eligible &= mask
        exclude_reason.loc[~mask] = exclude_reason + ';delist_date'

        def gen_mask(df: pd.DataFrame, col: str, agg: str, threshold_value: float | int):
            """生成逐时点品种池掩码。
            
            对列 col 按品种分组做滚动 agg（mean/min），产出阈值掩码。
            droplevel(-1) 移除 pandas groupby+rolling 自动附加的冗余索引层级，
            然后确保 MultiIndex 层级顺序与输入 df 一致（timestamp, product）。
            """
            if agg not in ('mean', 'min'):
                raise ValueError(f'agg 必须为 "mean" 或 "min"，收到 "{agg}"')
            rolled = (df.groupby(level='product', group_keys=False)[col]
                      .rolling(self.spec.liquidity_lookback, min_periods=1)
                      .agg(agg))
            if rolled.index.nlevels > df.index.nlevels:
                rolled.index = rolled.index.droplevel(-1)
            # groupby+rolling 返回的 MultiIndex 第一层级是 group key（product），
            # 需要 swap 为 (timestamp, product) 才能与 universe 对齐
            if isinstance(rolled.index, pd.MultiIndex) \
                    and rolled.index.nlevels == 2 \
                    and rolled.index.names[0] != df.index.names[0]:
                rolled.index = rolled.index.swaplevel()
            return rolled >= threshold_value
        
        # 2. 成交量约束
        # 2.1 （回看窗口内）单日最低成交量
        mask = gen_mask(
            df=panel, col='Volume', agg='min',
            threshold_value=self.spec.min_daily_volume)
        eligible &= mask
        exclude_reason.loc[~mask] = exclude_reason + ';min_daily_volume'

        # 2.2 （回看窗口内）平均成交量
        mask = gen_mask(
            df=panel, col='Volume', agg='mean',
            threshold_value=self.spec.min_average_volume
        )
        eligible &= mask
        exclude_reason.loc[~mask] = exclude_reason + ';min_average_volume'

        # 3. （回看窗口内）单日最低持仓量
        mask = gen_mask(
            df=panel, col='OpenInterest', agg='min',
            threshold_value=self.spec.min_open_interest
        )
        eligible &= mask
        exclude_reason.loc[~mask] = exclude_reason + ';min_open_interest'

        # 4. 有效数据覆盖率：在回看窗口内，True_Return 的非 NaN 占比
        if self.spec.min_data_coverage > 0 and 'True_Return' in panel.columns:
            col = 'True_Return'
            rolled = (panel.groupby(level='product', group_keys=False)[col]
                      .rolling(self.spec.liquidity_lookback, min_periods=1)
                      .count())
            if rolled.index.nlevels > panel.index.nlevels:
                rolled.index = rolled.index.droplevel(-1)
            if isinstance(rolled.index, pd.MultiIndex) \
                    and rolled.index.nlevels == 2 \
                    and rolled.index.names[0] != panel.index.names[0]:
                rolled.index = rolled.index.swaplevel()
            coverage_mask = (
                rolled / self.spec.liquidity_lookback
                >= self.spec.min_data_coverage
            )
            eligible &= coverage_mask
            exclude_reason.loc[~coverage_mask] = (
                exclude_reason + ';min_data_coverage'
            )
        
        return Universe(eligible=eligible, exclude_reason=exclude_reason)

