# 换月总表 (Master Tables)

本目录用于存放各期货品种的换月总表（master_table）文件。
最新换月总表和换月日历生成时间为 2026-10-07 07:55:38。

## 换月价差与复权口径

`Price_Gap` 采用成对价格口径：优先使用旧主力最后一个交易日的新、旧主力
**收盘价**；任意一方收盘价缺失时，双方统一 fall back to 当日**结算价**。若结算价对
仍不完整，直接报错，不把未知价差当作 0。`Price_Gap_Basis` 在实际换月边界
记录 `close` 或 `settle_fallback`。ResearchFeed 以此构造 close-preferred,
settlement-fallback backward additive adjustment。

## 执行结果

成功生成 94 个品种：

- `A`
- `AD`
- `AG`
- `AL`
- `AO`
- `AP`
- `AU`
- `B`
- `BB`
- `BC`
- `BR`
- `BU`
- `BZ`
- `C`
- `CF`
- `CJ`
- `CS`
- `CU`
- `CY`
- `EB`
- `EC`
- `EG`
- `ER`
- `FB`
- `FG`
- `FU`
- `HC`
- `I`
- `IC`
- `IF`
- `IH`
- `IM`
- `J`
- `JD`
- `JM`
- `JR`
- `L`
- `LC`
- `LG`
- `LH`
- `LR`
- `LU`
- `M`
- `MA`
- `ME`
- `NI`
- `NR`
- `OI`
- `OP`
- `P`
- `PB`
- `PD`
- `PF`
- `PG`
- `PK`
- `PL`
- `PM`
- `PP`
- `PR`
- `PS`
- `PT`
- `PX`
- `RB`
- `RI`
- `RM`
- `RO`
- `RR`
- `RS`
- `RU`
- `SA`
- `SC`
- `SF`
- `SH`
- `SI`
- `SM`
- `SN`
- `SP`
- `SR`
- `SS`
- `T`
- `TA`
- `TC`
- `TF`
- `TL`
- `TS`
- `UR`
- `V`
- `WH`
- `WR`
- `WS`
- `WT`
- `Y`
- `ZC`
- `ZN`

失败 3 个品种：

- `L_F`
- `PP_F`
- `V_F`

## 失败品种报错信息

### L_F

```text
【DailySnapshotLoader】未找到 L_F 的日频快照数据文件，请检查路径：db\market\daily_snapshots\bar_1d\bar_1d_tushare\cleaned\DCE_L_F.parquet
```

### PP_F

```text
【DailySnapshotLoader】未找到 PP_F 的日频快照数据文件，请检查路径：db\market\daily_snapshots\bar_1d\bar_1d_tushare\cleaned\DCE_PP_F.parquet
```

### V_F

```text
【DailySnapshotLoader】未找到 V_F 的日频快照数据文件，请检查路径：db\market\daily_snapshots\bar_1d\bar_1d_tushare\cleaned\DCE_V_F.parquet
```
