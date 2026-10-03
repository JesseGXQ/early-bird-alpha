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
            内部会新增临时列 'list_date'、'days_from_list_date'（不改动入参）。
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
        """
        panel = panel.copy()

        eligible = pd.Series([True]* len(panel), index=panel.index)
        exclude_reason = pd.Series('' * len(panel), index=panel.index)

        # 1. 品种上市日约束
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


# ============================================================
# inline testing
# ============================================================
if __name__ == '__main__':
    import numpy as np
    from datetime import date, timedelta
    from factors.models import UniverseSpec

    class FakeFacade:
        """模拟 DataFacade，仅提供 get_list_date_product。"""
        def __init__(self, listing_dates: dict[str, date]):
            self.rule_query = self._FakeRuleQuery(listing_dates)

        class _FakeRuleQuery:
            def __init__(self, listing_dates):
                self._dates = listing_dates
            def get_list_date_product(self, product: str) -> date:
                if product not in self._dates:
                    raise KeyError(f"Unknown product: {product}")
                return self._dates[product]

    def make_panel(
        products: list[str],
        n_days: int,
        start_date: date,
        vol: float = 100.0,
        oi: float = 1000.0,
    ) -> pd.DataFrame:
        """构建测试用研究主面板。"""
        records = []
        for prod in products:
            for i in range(n_days):
                ts = pd.Timestamp(start_date + timedelta(days=i))
                records.append({
                    'timestamp': ts,
                    'product': prod,
                    'trading_date': start_date + timedelta(days=i),
                    'Adj_Close': 100.0 + i,
                    'Volume': vol + i,
                    'OpenInterest': oi + i,
                    'True_Return': 0.01,
                })
        df = pd.DataFrame(records)
        df.set_index(['timestamp', 'product'], inplace=True)
        return df

    print("="*60)
    print("Test 1: 上市日约束 — 品种上市不足 min_listing_days 应被排除")
    listing = {
        'RB': date(2020, 1, 1),
        'MA': date(2020, 2, 1),
    }
    panel = make_panel(['RB', 'MA'], n_days=10, start_date=date(2020, 2, 1))
    spec = UniverseSpec(min_listing_days=60, min_data_coverage=0)
    facade = FakeFacade(listing)
    du = DynamicUniverse(spec, facade)
    result = du.build(panel)
    universe, reason = result.eligible, result.exclude_reason

    for (ts, prod), val in universe.items():
        if prod == 'RB':
            assert not val, f"RB should be excluded (only 31 days), got {val}"
        else:
            assert not val, f"MA should be excluded (0 days), got {val}"
    print("  → PASS\n")

    print("="*60)
    print("Test 2: 排除原因记录 — 被排除的样本应标记原因")
    # RB 上市不足，reason 应包含 'min_listing_days'
    rb_reasons = reason.xs('RB', level='product')
    for ts in rb_reasons.index:
        r = rb_reasons.loc[ts]
        assert 'min_listing_days' in str(r), \
            f"({ts.date()}, RB) 的排除原因应含 'min_listing_days'，实际: {r}"
    print(f"  RB reasons sample: {rb_reasons.iloc[0]}")
    print("  → PASS\n")

    print("="*60)
    print("Test 3: 上市日约束 — 品种上市已满 min_listing_days 应保留")
    panel3 = make_panel(['RB'], n_days=5, start_date=date(2020, 7, 1))
    spec3 = UniverseSpec(min_listing_days=60, min_data_coverage=0)
    du3 = DynamicUniverse(spec3, FakeFacade({'RB': date(2020, 1, 1)}))
    result3 = du3.build(panel3)
    universe3, reason3 = result3.eligible, result3.exclude_reason
    assert universe3.all(), f"RB should pass, got {universe3.tolist()}"
    # 通过的样本 reason 应为空字符串
    assert (reason3 == '').all(), f"Passed samples should have empty reason, got: {reason3.iloc[0]}"
    print("  → PASS\n")

    print("="*60)
    print("Test 4: 成交量约束（min_daily_volume > 0）")
    listing4 = {'RB': date(2020, 1, 1), 'MA': date(2020, 1, 1)}
    panel4 = make_panel(['RB', 'MA'], n_days=10, start_date=date(2020, 6, 1),
                        vol=50.0, oi=1000.0)
    spec4 = UniverseSpec(min_listing_days=0, min_daily_volume=100,
                         liquidity_lookback=5, min_data_coverage=0)
    du4 = DynamicUniverse(spec4, FakeFacade(listing4))
    result4 = du4.build(panel4)
    universe4, reason4 = result4.eligible, result4.exclude_reason
    assert not universe4.any(), f"All volumes=50<100, should all be False, got {universe4.sum()} True"
    print("  → All excluded — PASS\n")

    print("="*60)
    print("Test 5: 成交量约束 — 阈值=0 时全部通过")
    panel5 = make_panel(['RB'], n_days=10, start_date=date(2020, 6, 1),
                        vol=50.0)
    spec5 = UniverseSpec(min_listing_days=0, min_daily_volume=0,
                         min_average_volume=0, min_open_interest=0,
                         min_data_coverage=0)
    du5 = DynamicUniverse(spec5, FakeFacade({'RB': date(2020, 1, 1)}))
    universe5 = du5.build(panel5).eligible
    assert universe5.all(), f"got {universe5.sum()}/{len(universe5)}"
    print("  → PASS\n")

    print("="*60)
    print("Test 6: NaN Volume — 应被排除")
    panel6 = make_panel(['RB'], n_days=10, start_date=date(2020, 6, 1),
                        vol=100.0)
    panel6.loc[panel6.index[:3], 'Volume'] = np.nan
    spec6 = UniverseSpec(min_listing_days=0, min_daily_volume=1,
                         liquidity_lookback=3, min_data_coverage=0)
    du6 = DynamicUniverse(spec6, FakeFacade({'RB': date(2020, 1, 1)}))
    universe6 = du6.build(panel6).eligible
    first_3 = universe6.iloc[:3]
    assert not first_3.any(), f"got {first_3.tolist()}"
    print("  → PASS\n")

    print("="*60)
    print("Test 7: 持仓量约束")
    panel7 = make_panel(['RB'], n_days=10, start_date=date(2020, 6, 1),
                        vol=200.0, oi=500.0)
    spec7 = UniverseSpec(min_listing_days=0, min_daily_volume=0,
                         min_open_interest=1000, liquidity_lookback=3,
                         min_data_coverage=0)
    du7 = DynamicUniverse(spec7, FakeFacade({'RB': date(2020, 1, 1)}))
    universe7 = du7.build(panel7).eligible
    assert not universe7.any(), f"OI=500 < 1000, should all be False"
    print("  → PASS\n")

    print("="*60)
    print("Test 8: Index alignment — universe & mask 索引是否兼容")
    panel8 = make_panel(['RB', 'MA'], n_days=5, start_date=date(2020, 6, 1),
                        vol=200.0, oi=2000.0)
    spec8 = UniverseSpec(min_listing_days=0, min_daily_volume=100,
                         min_average_volume=100, min_open_interest=500,
                         liquidity_lookback=3, min_data_coverage=0)
    du8 = DynamicUniverse(spec8, FakeFacade({'RB': date(2020, 1, 1), 'MA': date(2020, 1, 1)}))
    universe8 = du8.build(panel8).eligible
    assert universe8.index.equals(panel8.index), "Index mismatch"
    assert universe8.sum() == len(universe8), f"Expected all True, got {universe8.sum()}/{len(universe8)}"
    print("  → PASS\n")

    print("="*60)
    print("Test 9: 交叉验证 — 一半产品通过、一半不通过")
    panel9 = make_panel(['RB', 'MA'], n_days=5, start_date=date(2020, 3, 1),
                        vol=200.0, oi=2000.0)
    spec9 = UniverseSpec(min_listing_days=60, min_daily_volume=0,
                         min_average_volume=0, min_open_interest=0,
                         liquidity_lookback=3, min_data_coverage=0)
    facade9 = FakeFacade({'RB': date(2020, 2, 1), 'MA': date(2019, 1, 1)})
    du9 = DynamicUniverse(spec9, facade9)
    universe9 = du9.build(panel9).eligible
    rb_excluded = int((~universe9.xs('RB', level='product')).sum())
    ma_passed = int(universe9.xs('MA', level='product').sum())
    print(f"  RB excluded: {rb_excluded}/5, MA passed: {ma_passed}/5")
    assert rb_excluded == 5 and ma_passed == 5
    print("  → PASS\n")

    print("="*60)
    print("Test 10: 有效数据覆盖率 — NaN 过多的品种被排除")
    panel10 = make_panel(['RB', 'MA'], n_days=10, start_date=date(2020, 6, 1),
                         vol=200.0, oi=2000.0)
    panel10.loc[panel10.index[:7], 'True_Return'] = np.nan  # RB 前 7 天 NaN，MA 也前 7 天 NaN
    spec10 = UniverseSpec(min_listing_days=0, min_daily_volume=0,
                          min_data_coverage=0.5, liquidity_lookback=5)
    facade10 = FakeFacade({'RB': date(2019, 1, 1), 'MA': date(2019, 1, 1)})
    du10 = DynamicUniverse(spec10, facade10)
    result10 = du10.build(panel10)
    universe10, reason10 = result10.eligible, result10.exclude_reason
    # lookback=5, 前 7 天真_ret NaN → rolling count：前 5 天约 0~2 非 NaN
    # 覆盖率 < 0.5 → 大量被排除
    excluded = (~universe10).sum()
    print(f"  Total rows: {len(universe10)}, excluded: {excluded}")
    print(f"  Sample reasons: {reason10[reason10 != ''].iloc[:3].tolist()}")
    assert excluded > 0, "Expected some exclusions due to low data coverage"
    assert (reason10.loc[~universe10].str.contains('min_data_coverage').all()), \
        "Excluded entries should have 'min_data_coverage' in reason"
    print("  → PASS\n")

    print("="*60)
    print("ALL TESTS COMPLETE")
    print("="*60)


