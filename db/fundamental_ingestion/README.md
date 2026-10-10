# Fundamental Ingestion Workspace

本目录管理“外部数据源 → 本地基本面数据库”的采集、标准化和审计流程。
正式数据位于 `db/fundamental/`，本目录下的 `data/` 仅保存可重建的原始响应与运行审计，已被 Git 忽略。

## 仓单日报

数据源为 Tushare Pro `fut_wsr`，正式文件位于：

```text
db/fundamental/warehouse_receipts/{EXCHANGE}_{PRODUCT}.parquet
```

从仓库根目录运行：

```powershell
# 全量拉取 product_meta 中的全部品种
python -m db.fundamental_ingestion.script.warehouse_receipts.ingest_warehouse_receipts --mode full

# 增量更新；默认回看 10 个日历日，以覆盖源端修订
python -m db.fundamental_ingestion.script.warehouse_receipts.ingest_warehouse_receipts --mode incremental

# 只处理指定品种
python -m db.fundamental_ingestion.script.warehouse_receipts.ingest_warehouse_receipts --mode incremental --products ZN CU

# 全库完整性校验
python -m db.fundamental_ingestion.script.warehouse_receipts.validate_warehouse_receipts
```

凭证依次从 `--token`、环境变量 `TUSHARE_TOKEN`、
`db/rules_ingestion/supplements/API凭证.txt` 读取。凭证不会进入输出数据和审计文件。

默认使用 Tushare HTTPS API。可通过 `--transport sdk` 强制使用 Tushare Python SDK；
当前 `bigquant` 环境存在 NumPy/PyArrow 二进制兼容问题，因此正式 Parquet 入库建议在项目 `base` 环境执行。

采集器按自然年切分请求；任何返回达到接口文档所述 1000 行上限的区间会通过
`offset` 继续分页，并检测重复页，从而避免静默截断。增量模式会删除并重建回看区间，
再与更早历史合并，重复执行保持幂等。

正式 schema 同时保存权威的 `product/product_name/exchange` 和来源证据
`source_symbol/source_product_name/source_exchange`。Tushare 的 CU 响应中包含
`铜(BC)` 行：入库器会将其路由到 BC，并自动联动刷新 BC/CU；与 BC 直接接口重叠时，
按业务自然键保留 BC 直接来源，CU 仅用于补齐历史覆盖。

## 已知数据问题：`unit` 字段错标（2026-10-09 发现并修正）

### 问题现象

对 97 个正式 Parquet 全量扫描后，只有 3 个品种出现同一品种两个计量单位并存：

| 品种 | 涉及单位 | 错标行数 | 主单位 |
|---|---|---:|---|
| `AG` | 千克 / 吨 | 1,122 | 千克 |
| `AU` | 千克 / 吨 | 17 | 千克 |
| `SC` | 桶 / 吨 | 3,146 | 桶 |

初看像“历史单位切换”，但核查数值后确认是**数据错误**：少数派单位的行与其余行
属于同一量纲，Tushare 只是把 `unit` 写错了。证据：

- **量级一致**。`AG` `2020-04-09` 上海区仓库行 944592 + 1263848，与同日“合计”行
  2208440 完全相等——该合计行被标成“吨”，实际是千克。2208440 吨等于 220 万吨
  白银，显然不成立。
- **数值连续**。`AU` 上期所交割库 `2020-04-09` 为 2163 千克，`2020-04-10` 为
  2163 **吨**，同一个数只换了标签。`SC` 有 72 个“吨”行的前一交易日同仓库为“桶”，
  其中 98.6% 数值完全相同。
- **判据可验证**。三个品种中，标着少数派单位的行 **100%** 都是
  `previous_receipt_quantity` 为空的行（`AG` 1122/1122、`AU` 17/17、`SC` 3146/3146）；
  反向也成立：`previous_receipt_quantity` 非空的行没有任何一行是少数派单位。
  即“`pre_vol` 非空 ⟺ `unit` 可信”在全库无例外。

### 修正方式

```powershell
# 先看报告，不动任何文件
python -m db.fundamental_ingestion.script.warehouse_receipts.repair_warehouse_receipt_units

# 确认后实际写盘
python -m db.fundamental_ingestion.script.warehouse_receipts.repair_warehouse_receipt_units --apply

# 只处理指定品种
python -m db.fundamental_ingestion.script.warehouse_receipts.repair_warehouse_receipt_units --apply --products AG AU SC
```

脚本以 `previous_receipt_quantity` 非空的行作为可信基准，取其唯一单位作为该品种的
主单位，把所有其他单位的行改写为主单位。**只改写 `unit`，数值列一律不动**——因为
这些行的数值本身就是主单位的量纲，做任何换算都会引入错误。

脚本默认只读。`--apply` 前若改写会导致自然键重复（`unit` 属于自然键），会直接中止
而不写盘。审计输出到 `data/audit/warehouse_receipts_unit_repair.json`。
脚本幂等，重复执行第二次会报告零改写。

有 46 个品种被跳过：18 个空文件，以及 28 个 CZCE 品种（全部为“张”，且无任何
`pre_vol` 非空的行，无法判定主单位）。这些品种单位本就唯一，脚本不做假定。

### 应用范畴（重要）

本脚本修正的是**已经落盘的历史数据**，不是入库器的行为。重新拉取数据以后：

1. **必须先运行本脚本的报告模式（不带 `--apply`）检查问题是否仍存在**；
2. 若报告 `改写行数=0`，说明源端已修正或该品种本无此问题，**不要**再执行 `--apply`；
3. 若报告仍有改写行数，说明源端仍在写入错误单位，此时才执行 `--apply`；
4. 判据本身也需重新验证：若某品种出现 `pre_vol` 非空的行分属多个单位，脚本会
   跳过该品种并说明原因，此时应人工介入而非强行修正。

原因是本地尚未引入数据库版本管理，正式 Parquet 被 Git 忽略（`.gitignore` 的
`*.parquet` 规则），**原地覆盖不可回滚**。因此每次重新入库后都要重新走一遍
“报告 → 判断 → 必要时 apply”的流程，不能假定脚本只需执行一次。

## 已在数据引擎侧修复：聚合重复计入“合计/总计”行（2026-10-09）

`warehouse` 为 `合计` / `总计` 的行是**分地区小计**，不是全国总计。例如
`ZN` `2022-12-08` 的天津合计 25 = 同日天津仓库行之和，而全库仓库行之和为 25；
`CU` 同日浙江合计 926，而全库为 19753；`AG` `2022-12-20` 上海合计 1733464，
而含广东的明细之和为 2008214。

`FundamentalFeed._aggregate_warehouse_receipts` 原先把小计行与明细行一起 `sum`，
造成重复计数：`AG` 有 1,040 个交易日的小计行恰好等于当日明细之和，其日度聚合是
精确的 2 倍；全库共 21 个品种存在非零的小计行。

已在数据引擎侧修复：`aggregate=True` 时加总前剔除 `warehouse ∈ {合计, 总计}` 的行，
识别只依据 warehouse 标签，不依赖 `area` 等可能缺失的字段；`aggregate=False` 的
明细接口保持原样返回，便于自行核对小计。相关测试见
`tests/data/feed/test_fundamental_feed.py`。
