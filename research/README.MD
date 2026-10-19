# Research

`research/` 是依托项目基础设施开展探索性因子研究的工作区。它可以调用
`data/` 和 `factors/`，核心模块不得反向依赖本目录。

## 目录约定

```text
research/
├── experiments/   # 可重复执行的研究脚本与配置
├── notebooks/     # 交互探索与绘图
├── artifacts/     # 运行产物，原则上不纳入 Git
└── io.py          # 探索阶段的轻量结果导出器
```

实验脚本、配置和结论应纳入 Git；Parquet、图片等生成结果写入
`artifacts/<experiment_id>/`。重要参数不得只保留在 notebook 变量中。

## 结果保存

公共入口 `save_evaluation_result()` 会根据返回类型选择导出器；也可直接调用：

- `save_coverage_result()`；
- `save_ic_result()`；
- `save_grouped_result()`；
- `save_regression_result()`；
- `save_hit_rate_result()`。

每个结果包都包含 `config.json`、`metadata.json`、结构化 Parquet/JSON 结果和
完整写入标记 `_SUCCESS`。时序结果中的分品种对象会合并为带 `product` 外层
索引的长表。`save_ic_result()` 可直接接收 `ic_details()` 返回的
`(IC, RankIC)` 元组，也兼容单个 IC 结果。

该导出器仅服务于探索阶段，不承担实验编排、缓存和数据指纹计算。正式评价
流水线建立后，可将其文件约定迁移至 `factors/storage.py`。
