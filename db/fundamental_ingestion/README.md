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
