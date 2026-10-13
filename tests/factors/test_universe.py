"""`DynamicUniverse` 的品种池准入测试。

覆盖两类内容：

- 各条品种池约束（上市时长、成交量、持仓量、数据覆盖率）的行为；
- 退市日约束 `timestamp >= delist_date → eligible=False`。

退市日口径提醒（详见 `factors/universe.py` 中 1.2 段注释）：
`StaticMeta.delist_date` 是"最后一个合约的最后交易日"，而
`ProductCalendar` 把它当作**含当天的最后一个合法交易日**；`DynamicUniverse`
**故意**取相反口径——退市当日即退出品种池。这里以 `WS`（delist_date=
2013-05-23）作为基准用例，把这一差异固化成断言。
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from factors.models import UniverseSpec
from factors.universe import DynamicUniverse


# ------------------------------------------------------------
# 测试替身
# ------------------------------------------------------------
class FakeRuleQuery:
    """模拟 `RuleQueryService`，只提供品种池需要的两个生命周期日期接口。"""

    def __init__(self,
                 listing_dates: dict[str, date],
                 delist_dates: dict[str, date | None] | None = None):
        self._listing_dates = listing_dates
        # 未显式给出的品种视为仍在市（delist_date 为 None）
        self._delist_dates = delist_dates or {}

    def get_list_date_product(self, product: str) -> date:
        if product not in self._listing_dates:
            raise KeyError(f"Unknown product: {product}")
        return self._listing_dates[product]

    def get_delist_date_product(self, product: str) -> date | None:
        if product not in self._listing_dates:
            raise KeyError(f"Unknown product: {product}")
        return self._delist_dates.get(product)


class FakeFacade:
    """模拟 `DataFacade`，仅暴露 `rule_query`。"""

    def __init__(self,
                 listing_dates: dict[str, date],
                 delist_dates: dict[str, date | None] | None = None):
        self.rule_query = FakeRuleQuery(listing_dates, delist_dates)


def make_panel(
    products: list[str],
    n_days: int,
    start_date: date,
    vol: float = 100.0,
    oi: float = 1000.0,
) -> pd.DataFrame:
    """构建测试用研究主面板，MultiIndex[timestamp, product]。"""
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


def _loose_spec(**overrides) -> UniverseSpec:
    """关闭除被测项外的全部约束的 spec。"""
    params = dict(
        min_listing_days=0,
        min_daily_volume=0,
        min_average_volume=0,
        min_open_interest=0,
        min_data_coverage=0,
        liquidity_lookback=3,
    )
    params.update(overrides)
    return UniverseSpec(**params)


# ------------------------------------------------------------
# 上市日约束
# ------------------------------------------------------------
def test_listing_days_excludes_insufficient():
    """上市不足 min_listing_days 的品种应被排除。"""
    panel = make_panel(['RB', 'MA'], n_days=10, start_date=date(2020, 2, 1))
    spec = _loose_spec(min_listing_days=60)
    facade = FakeFacade({'RB': date(2020, 1, 1), 'MA': date(2020, 2, 1)})

    result = DynamicUniverse(spec, facade).build(panel)

    assert not result.eligible.any()


def test_listing_days_records_reason():
    """因上市时长被排除的样本，排除原因应含 'min_listing_days'。"""
    panel = make_panel(['RB'], n_days=10, start_date=date(2020, 2, 1))
    spec = _loose_spec(min_listing_days=60)
    facade = FakeFacade({'RB': date(2020, 1, 1)})

    result = DynamicUniverse(spec, facade).build(panel)

    assert result.exclude_reason.str.contains('min_listing_days').all()


def test_listing_days_passes_after_threshold():
    """上市已满 min_listing_days 的品种应保留，且排除原因为空串。"""
    panel = make_panel(['RB'], n_days=5, start_date=date(2020, 7, 1))
    spec = _loose_spec(min_listing_days=60)
    facade = FakeFacade({'RB': date(2020, 1, 1)})

    result = DynamicUniverse(spec, facade).build(panel)

    assert result.eligible.all()
    assert (result.exclude_reason == '').all()


def test_listing_days_mixed_products():
    """交叉验证：一半品种通过、一半不通过。"""
    panel = make_panel(['RB', 'MA'], n_days=5, start_date=date(2020, 3, 1))
    spec = _loose_spec(min_listing_days=60)
    facade = FakeFacade({'RB': date(2020, 2, 1), 'MA': date(2019, 1, 1)})

    result = DynamicUniverse(spec, facade).build(panel)

    assert int((~result.eligible.xs('RB', level='product')).sum()) == 5
    assert int(result.eligible.xs('MA', level='product').sum()) == 5


# ------------------------------------------------------------
# 成交量 / 持仓量 / 覆盖率约束
# ------------------------------------------------------------
def test_min_daily_volume_excludes():
    """窗口内单日成交量低于阈值应被排除。"""
    panel = make_panel(['RB', 'MA'], n_days=10, start_date=date(2020, 6, 1),
                       vol=50.0)
    spec = _loose_spec(min_daily_volume=100, liquidity_lookback=5)

    result = DynamicUniverse(spec, FakeFacade(
        {'RB': date(2020, 1, 1), 'MA': date(2020, 1, 1)})).build(panel)

    assert not result.eligible.any()
    assert result.exclude_reason.str.contains('min_daily_volume').all()


def test_zero_volume_threshold_passes():
    """阈值为 0 时成交量约束不生效，全部通过。"""
    panel = make_panel(['RB'], n_days=10, start_date=date(2020, 6, 1),
                       vol=50.0)
    spec = _loose_spec()

    result = DynamicUniverse(
        spec, FakeFacade({'RB': date(2020, 1, 1)})).build(panel)

    assert result.eligible.all()


def test_nan_volume_excluded():
    """Volume 为 NaN 的样本应被排除。"""
    panel = make_panel(['RB'], n_days=10, start_date=date(2020, 6, 1),
                       vol=100.0)
    panel.loc[panel.index[:3], 'Volume'] = np.nan
    spec = _loose_spec(min_daily_volume=1, liquidity_lookback=3)

    result = DynamicUniverse(
        spec, FakeFacade({'RB': date(2020, 1, 1)})).build(panel)

    assert not result.eligible.iloc[:3].any()


def test_min_open_interest_excludes():
    """持仓量低于阈值应被排除。"""
    panel = make_panel(['RB'], n_days=10, start_date=date(2020, 6, 1),
                       vol=200.0, oi=500.0)
    spec = _loose_spec(min_open_interest=1000, liquidity_lookback=3)

    result = DynamicUniverse(
        spec, FakeFacade({'RB': date(2020, 1, 1)})).build(panel)

    assert not result.eligible.any()


def test_min_data_coverage_excludes():
    """True_Return 缺失过多导致覆盖率不达标时应被排除。"""
    panel = make_panel(['RB', 'MA'], n_days=10, start_date=date(2020, 6, 1),
                       vol=200.0, oi=2000.0)
    panel.loc[panel.index[:7], 'True_Return'] = np.nan
    spec = _loose_spec(min_data_coverage=0.5, liquidity_lookback=5)
    facade = FakeFacade({'RB': date(2019, 1, 1), 'MA': date(2019, 1, 1)})

    result = DynamicUniverse(spec, facade).build(panel)

    excluded = (~result.eligible).sum()
    assert excluded > 0
    assert result.exclude_reason.loc[~result.eligible]\
        .str.contains('min_data_coverage').all()


# ------------------------------------------------------------
# 退市日约束（本次新增）
# ------------------------------------------------------------
# 基准口径：WS 的最后一个合约 WS1305 最后交易日为 2013-05-23。
DELIST_DATE = date(2013, 5, 23)


def test_live_product_not_excluded():
    """仍在市的品种（delist_date 为 None）不得被排除。

    这是最容易踩的坑：None 若被映射成 NaT 而准入条件写成
    `timestamp < delist_date`，会把所有在市品种全部误杀。
    """
    panel = make_panel(['RB'], n_days=10, start_date=date(2020, 6, 1))
    facade = FakeFacade({'RB': date(2010, 1, 1)},
                        {'RB': None})

    result = DynamicUniverse(_loose_spec(), facade).build(panel)

    assert result.eligible.all()
    assert (result.exclude_reason == '').all()


def test_live_product_explicit_missing_key_not_excluded():
    """delist_dates 里没有该品种（等价于 None）时同样不得被排除。"""
    panel = make_panel(['RB'], n_days=10, start_date=date(2020, 6, 1))
    facade = FakeFacade({'RB': date(2010, 1, 1)})  # 不传 delist_dates

    result = DynamicUniverse(_loose_spec(), facade).build(panel)

    assert result.eligible.all()


def test_before_delist_date_kept():
    """退市日之前应正常保留在池内。"""
    panel = make_panel(['WS'], n_days=10, start_date=date(2013, 5, 13))
    facade = FakeFacade({'WS': date(2012, 1, 1)}, {'WS': DELIST_DATE})

    result = DynamicUniverse(_loose_spec(), facade).build(panel)

    # 2013-05-13 ~ 2013-05-22 共 10 天，全部早于退市日
    assert result.eligible.all()


def test_on_delist_date_excluded():
    """退市日**当日**即退出品种池。

    这是与 `ProductCalendar`（`<= delist_date` 判合法）刻意不同的口径：
    `eligible` 是"截至该 timestamp 收盘"的 point-in-time 判定，
    最后一个交易日收盘时退市已确定、不可再开新仓，故当日即出池。
    """
    panel = make_panel(['WS'], n_days=11, start_date=date(2013, 5, 13))
    facade = FakeFacade({'WS': date(2012, 1, 1)}, {'WS': DELIST_DATE})
    on_delist = pd.Timestamp(DELIST_DATE)

    result = DynamicUniverse(_loose_spec(), facade).build(panel)

    assert result.eligible.xs('WS', level='product').loc[on_delist] == False
    assert result.exclude_reason.xs('WS', level='product')\
        .loc[on_delist].endswith('delist_date')


def test_after_delist_date_excluded():
    """退市日之后的样本一律排除。"""
    panel = make_panel(['WS'], n_days=15, start_date=date(2013, 5, 13))
    facade = FakeFacade({'WS': date(2012, 1, 1)}, {'WS': DELIST_DATE})
    after = panel.index.get_level_values('timestamp') > pd.Timestamp(DELIST_DATE)

    result = DynamicUniverse(_loose_spec(), facade).build(panel)

    assert not result.eligible[after].any()


def test_delist_cuts_off_exactly_at_delist_date():
    """边界回归：退市日之前的最后一刻仍保留，自退市日起全被排除。

    与 `ProductCalendar.get_trading_dates_in_date_range` 的
    `slice_end = min(delist_date, end_date)`（含端点）对照，
    此处预期退市日当天被排除。
    """
    panel = make_panel(['WS'], n_days=11, start_date=date(2013, 5, 13))
    facade = FakeFacade({'WS': date(2012, 1, 1)}, {'WS': DELIST_DATE})
    ts = panel.index.get_level_values('timestamp')
    delist_ts = pd.Timestamp(DELIST_DATE)

    result = DynamicUniverse(_loose_spec(), facade).build(panel)

    assert result.eligible[ts < delist_ts].all()
    assert not result.eligible[ts >= delist_ts].any()


def test_delist_reason_only_on_excluded_samples():
    """未被排除的样本不得带 'delist_date' 原因。"""
    panel = make_panel(['WS'], n_days=11, start_date=date(2013, 5, 13))
    facade = FakeFacade({'WS': date(2012, 1, 1)}, {'WS': DELIST_DATE})

    result = DynamicUniverse(_loose_spec(), facade).build(panel)

    kept_reasons = result.exclude_reason[result.eligible]
    assert not kept_reasons.str.contains('delist_date').any()


def test_delist_combines_with_listing_reason():
    """同时命中上市时长与退市日时，两条原因应拼接而非互相覆盖。"""
    # 面板覆盖 2013-05-23 ~ 05-27：全部 >= 退市日，且全部早于上市日
    panel = make_panel(['WS'], n_days=5, start_date=date(2013, 5, 23))
    facade = FakeFacade({'WS': date(2013, 6, 1)}, {'WS': DELIST_DATE})
    spec = _loose_spec(min_listing_days=60)

    result = DynamicUniverse(spec, facade).build(panel)

    assert not result.eligible.any()
    assert result.exclude_reason.str.contains('min_listing_days').all()
    assert result.exclude_reason.str.contains('delist_date').all()


def test_delist_does_not_disturb_other_products():
    """退市品种的排除不应波及其它品种的准入。"""
    panel = make_panel(['WS', 'MA'], n_days=11, start_date=date(2013, 5, 13))
    facade = FakeFacade({'WS': date(2012, 1, 1), 'MA': date(2012, 1, 1)},
                        {'WS': DELIST_DATE, 'MA': None})

    result = DynamicUniverse(_loose_spec(), facade).build(panel)

    assert result.eligible.xs('MA', level='product').all()
    assert not result.eligible.xs('WS', level='product')\
        .loc[pd.Timestamp(DELIST_DATE):].any()


# ------------------------------------------------------------
# 通用契约
# ------------------------------------------------------------
def test_index_alignment_and_full_pass():
    """索引与主面板一致，且无约束触发时全部通过。"""
    panel = make_panel(['RB', 'MA'], n_days=5, start_date=date(2020, 6, 1),
                       vol=200.0, oi=2000.0)
    spec = _loose_spec(min_daily_volume=100, min_average_volume=100,
                       min_open_interest=500)
    facade = FakeFacade({'RB': date(2020, 1, 1), 'MA': date(2020, 1, 1)})

    result = DynamicUniverse(spec, facade).build(panel)

    assert result.eligible.index.equals(panel.index)
    assert result.exclude_reason.index.equals(panel.index)
    assert result.eligible.sum() == len(result.eligible)


def test_build_does_not_mutate_input_panel():
    """build 只在副本上加临时列，不得污染入参。"""
    panel = make_panel(['WS'], n_days=11, start_date=date(2013, 5, 13))
    before = panel.columns.tolist()
    facade = FakeFacade({'WS': date(2012, 1, 1)}, {'WS': DELIST_DATE})

    DynamicUniverse(_loose_spec(), facade).build(panel)

    assert panel.columns.tolist() == before


def test_all_constraints_enabled_end_to_end():
    """全约束开启的端到端冒烟：索引不变、原因非空处必有因。"""
    panel = make_panel(['RB', 'MA'], n_days=20, start_date=date(2020, 6, 1),
                       vol=200.0, oi=2000.0)
    panel.loc[panel.index[:5], 'Volume'] = np.nan
    spec = UniverseSpec(min_listing_days=60, liquidity_lookback=5,
                        min_daily_volume=100, min_average_volume=100,
                        min_open_interest=500, min_data_coverage=0.5)
    facade = FakeFacade({'RB': date(2020, 1, 1), 'MA': date(2020, 1, 1)})

    result = DynamicUniverse(spec, facade).build(panel)

    assert result.eligible.index.equals(panel.index)
    # 通过的样本原因必须为空，被排除的样本原因必须非空
    assert (result.exclude_reason[result.eligible] == '').all()
    assert (result.exclude_reason[~result.eligible] != '').all()
