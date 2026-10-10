"""
`ProductMetaManager` / `StaticMeta` 的读取测试。

重点覆盖 `StaticMeta.delist_date`：

- 因更换代码而退市的品种，`delist_date` 等于**最后一个合约的最后交易日**；
- 仍在市的品种（YAML 里没有 `delist_date` 键），`delist_date` 为 ``None``；
- `delist_date` 与 TIER2 合约注册表（`InstrumentRegistryManager`）可交叉验证。

这里刻意不使用"最后合约已过期"的时间启发式去判定退市品种：
`LR`(晚稻) 是仍在市的活跃品种，只是注册表里暂时没有 2026 年之后的合约。
"""

import datetime

import pytest

from data.rules.product_meta_manager import ProductMetaManager


# name 含 "(旧)" 的 6 个因更换代码而退市的品种。
# (品种, 最后一个合约, 最后一个合约的最后交易日)
DELISTED_PRODUCTS = [
    ("WS", "WS1305", datetime.date(2013, 5, 23)),
    ("WT", "WT1211", datetime.date(2012, 11, 22)),
    ("RO", "RO1305", datetime.date(2013, 5, 15)),
    ("ER", "ER1305", datetime.date(2013, 5, 23)),
    ("ME", "ME1505", datetime.date(2015, 5, 15)),
    ("TC", "TC1604", datetime.date(2016, 4, 8)),
]

DELISTED_CODES = [product for product, _, _ in DELISTED_PRODUCTS]


@pytest.mark.parametrize(
    'product, last_instrument, expected_delist_date',
    DELISTED_PRODUCTS,
    ids=[product for product, _, _ in DELISTED_PRODUCTS],
)
def test_delisted_product_delist_date(
    meta_manager, registry_manager, product, last_instrument, expected_delist_date
):
    """退市品种：delist_date == 最后一个合约的最后交易日。"""
    meta = meta_manager.get_static_meta(product)

    assert meta.delist_date == expected_delist_date

    # 交叉验证 1：等于 TIER2 注册表里最后一个合约的 last_trade_date
    registry = registry_manager.get_instrument_registry(last_instrument)
    assert registry.product == product
    assert meta.delist_date == registry.last_trade_date

    # 交叉验证 2：不是最后交割日，避免口径混淆
    assert registry.end_deliv_date is not None
    assert meta.delist_date != registry.end_deliv_date


@pytest.mark.parametrize('product', ['RB', 'IF', 'MA', 'ZC', 'LR'])
def test_live_product_delist_date_is_none(meta_manager, product):
    """仍在市的品种：YAML 无 delist_date 键，读取时归一为 None。"""
    meta = meta_manager.get_static_meta(product)
    assert meta.delist_date is None


def test_delist_date_never_precedes_list_date(meta_manager):
    """所有品种都应满足 list_date <= delist_date（若已退市）。"""
    for product in meta_manager.get_product_universe():
        meta = meta_manager.get_static_meta(product)
        if meta.delist_date is not None:
            assert meta.list_date <= meta.delist_date, product


def test_delist_date_only_on_replaced_products(meta_manager):
    """有 delist_date 的品种必须同时带有后继代码标记，且 name 含 "(旧)"。"""
    found = []
    for product in meta_manager.get_product_universe():
        meta = meta_manager.get_static_meta(product)
        if meta.delist_date is not None:
            found.append(product)
            assert '(旧)' in meta.name, product
    assert sorted(found) == sorted(DELISTED_CODES)


def test_static_meta_reads_all_products(meta_manager):
    """回归：全市场品种的 static meta 仍能正常读取且字段类型正确。"""
    universe = meta_manager.get_product_universe()
    assert len(universe) > 80

    for product in universe:
        meta = meta_manager.get_static_meta(product)
        assert meta.product == product.upper()
        assert isinstance(meta.exchange, str) and meta.exchange
        assert isinstance(meta.name, str) and meta.name
        assert meta.position_date_type in ('distinguish_today_yesterday', 'undistinguished')
        assert isinstance(meta.max_margin_side_algorithm, bool)
        assert isinstance(meta.combination_type, int)
        assert isinstance(meta.list_date, datetime.date)
        assert meta.delist_date is None or isinstance(meta.delist_date, datetime.date)


def test_unknown_product_raises(meta_manager):
    with pytest.raises(FileNotFoundError):
        meta_manager.get_static_meta('NOT_A_PRODUCT')


def test_get_product_universe_dict_uses_static_meta(meta_manager):
    """get_product_universe('dict') 走 get_static_meta，不应因新字段而报错。"""
    products = meta_manager.get_product_universe(return_type='dict')
    assert products['WS']['name'] == '郑麦(旧)'
    assert products['WS']['exchange'] == 'CZCE'
    assert set(products['RB']) == {'exchange', 'name'}