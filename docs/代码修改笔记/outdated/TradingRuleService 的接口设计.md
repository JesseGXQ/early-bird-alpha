# TradingRuleService 的接口设计

你的目标既然是工业级标准，我建议把问题再推进一步：不仅品种键使用 `(exchange, product)`，合约键也使用 `(exchange, instrument)`。

## 1. TradingRuleService 应传什么参数？

推荐：

```python
get_contract_rule(
    exchange: str,
    instrument: str,
    trading_date: date,
)
```

而不是：

```python
get_contract_rule(product, contract, trading_date)
```

原因是 `product` 可以通过合约注册表可靠获得，调用者同时传入 `product` 和 `contract` 会产生数据不一致风险：

```python
product="RB"
contract="CU2608"  # 参数互相矛盾
```

服务内部的查询过程应当是：

```text
(exchange, instrument, trading_date)
            │
            ▼
InstrumentRegistryManager
            │
            ├── 查询 product
            ├── 查询 multiplier
            └── 查询生命周期日期
            │
            ▼
(exchange, product, trading_date)
            │
            ▼
ProductMetaManager
```

示意代码：

```python
def get_contract_rule(
    self,
    exchange: str,
    instrument: str,
    trading_date: date,
) -> ContractRule:

    registry = self.registry_manager.get_instrument(
        exchange=exchange,
        instrument=instrument,
    )

    static_meta = self.product_meta_manager.get_static_meta(
        exchange=registry.exchange,
        product=registry.product,
    )

    price_tick = self.product_meta_manager.get_price_tick(
        exchange=registry.exchange,
        product=registry.product,
        instrument=registry.instrument,
        trading_date=trading_date,
    )
```

所以推荐的职责划分是：

- 外部调用者提供：`exchange + instrument + trading_date`
- Tier 2 负责确定：`product`
- Tier 1 接收：`exchange + product`
- Tier 3 接收：`exchange + instrument + trading_date`

还可以将合约键封装起来：

```python
@dataclass(frozen=True)
class InstrumentKey:
    exchange: str
    instrument: str
```

于是接口变成：

```python
get_contract_rule(
    instrument_key: InstrumentKey,
    trading_date: date,
)
```

## 2. 是否需要 product -> exchange mapping？

通常**不需要额外建立一个独立索引文件**。

原因是 `instrument_registry` 已经包含：

```text
exchange + instrument -> product
```

它本身就是合约到品种和交易所的权威映射。

正确流程不是解析合约字符串：

```python
product = parse_product_from_instrument("RB2610")
```

而是查询注册表：

```python
registry = registry_manager.get_instrument(
    exchange="SHFE",
    instrument="RB2610",
)

product = registry.product
exchange = registry.exchange
```

字符串解析只能作为数据导入或异常诊断的辅助方法，不应成为生产查询的权威逻辑。它无法可靠处理期权、组合合约、历史代码变化及其他特殊编码。

对于直接查询品种规则的场景，调用者必须同时提供：

```python
product_meta_manager.get_static_meta(
    exchange="SHFE",
    product="RB",
)
```

文件便可直接定位为：

```text
SHFE_RB.yaml
```

对应实现：

```python
def _load(self, exchange: str, product: str):
    key = (exchange.upper(), product.upper())

    if key not in self._cache:
        path = self._meta_dir / f"{key[0]}_{key[1]}.yaml"
        ...
```

## 最终建议

统一使用以下主键：

```text
品种主键：(exchange, product)
合约主键：(exchange, instrument)
动态状态主键：(exchange, instrument, trading_date)
```

`TradingRuleService` 的主要接口使用：

```text
(exchange, instrument, trading_date)
```

不要额外传 `product`，也不要从合约字符串解析 `product`。让 `instrument_registry` 承担合约到品种的权威映射即可，不需要再维护一份重复的索引文件。