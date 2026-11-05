# AGENTS.md

个人期货量化研究平台。分层管道：`data` → `factors` → `strategy` → `portfolio` → `backtester` → `executor` → `account` → `analytics`。代码注释、docstring、提交信息均用中文。

## 运行环境

- Conda `base` env（Anaconda，`C:\Users\Jesse\AppData\Local\anaconda3\python.exe`），Python 3.12。日常开发、跑脚本、pytest 都在 base 里执行。
- 依赖清单是根目录 `environment.yml`（conda 环境名 `early-bird-alpha`，Python 3.12 + 各库版本区间）：用于新机器复现、对外发布和给第三方用户，与 base 的版本区间保持一致。**不要新增 requirements.txt / pyproject.toml 等其他形式的依赖清单**，需要加依赖时改 `environment.yml`。
- `quantstats`（`analytics/recorder.py`）与 `bs4` / `websocket-client` / `tushare` / `chinese-calendar` / `bigquant`（`db/rules_ingestion/**`、`db/fundamental_ingestion/script/**` 的一次性入库脚本）**不在 environment.yml 里**，前者是函数内延迟导入的可选依赖，后者不属于运行期依赖，都不需要补进清单。
- 没有 lint / typecheck / CI 配置，验证一律靠 pytest。

## 测试

- pytest，**必须从仓库根目录运行**：`python -m pytest`。包以顶层目录名导入（`from account.account import Account`），且 `tests/data/conftest.py` 的 fixture 与 `data/config.py` 的默认路径都是相对根目录的 `./db/...`。
- 单测：`python -m pytest tests/data/rules/test_trading_rule_service.py -q`（VS Code launch.json 已配好当前文件调试）。
- 测试目录镜像包结构（`tests/<package>/`）。`tests/data/conftest.py` 提供 session 级 fixture，直接指向真实 `db/` 数据（非 mock）。
- 部分 data 测试依赖 gitignored 的真实数据文件（`db/**/*.parquet` 及 `D:\量化策略\CTA策略\RAW_DATA\TICK|1min`），数据缺失时可能失败——先确认数据存在再判断是代码 bug 还是缺数据。

## 架构速览

依赖方向 `data ← factors ← strategy ← backtester ← ...`；`factors/` 只允许依赖 `data`，不得依赖 `strategy/`、`portfolio/`、`backtester/`、`executor/`、`account/`。`research/` 是可调用 `data`/`factors` 的研究工作区，核心模块不得反向依赖它。

- `data/` 数据引擎：统一入口 `data/facade.py::DataFacade`（`get_historical_vector` / `get_snapshot_stream` / `get_term_structure_data` / `get_warehouse_receipt_data` 及多品种并行变体），组件由 `data/factory.py` 组装；feed 有 `ResearchFeed`（向量化研究，后复权主连）、`SimulationFeed`（逐笔事件）、`TermStructureFeed`、`FundamentalFeed`；配置在 `data/config.py`（`EnvironmentConfig` 硬编码 `D:\量化策略\CTA策略\RAW_DATA\...`）。
- `data/rules/`：规则库 manager（`ProductMetaManager`、`InstrumentRegistryManager`、`InstrumentDailyStatusManager`、`ExchangeHolidayManager`、`ProductClassificationManager`）与门面 `TradingRuleService`，研究侧查询统一走 `data/query_service.py::RuleQueryService`。
- `backtester/` V1 Bar 级最小闭环：`engine.py::BacktestEngine` 调度 Bar 事件 → 目标仓位 → 订单 → 成交 → 账户 → 绩效。`strategy/`、`portfolio/`、`executor/`、`account/`、`analytics/` 目前只有最小骨架，尚未与因子研究层打通。设计文档 `docs/Development Notes/20260722 V1 Bar级最小闭环系统设计文档.md`。
- `research/`：探索性研究工作区。`experiments/<experiment_id>/` 放可复现实验脚本（如 `alpha101`）与产物，`notebooks/` 做交互探索，`io.py` 是轻量评估结果导出器（`save_evaluation_result()` 及按结果类型分发的导出函数）。

### `factors/` 因子研究模块

当前最活跃的模块，分「单因子研究」与「组合实验」两层，V1 只支持日频。研究链路：`DataFacade` → `ResearchDataset` → `DynamicUniverse` → `Factor.compute` → `PreprocessingPipeline` → `LabelBuilder` → `evaluation/*` →（可选）`combination.py` / `diagnostic_portfolio/` → `research/` 脚本。架构文档见 `docs/Development Notes/20260723 因子研究模块系统架构设计.md` 及其补充、`20260924 组合实验层组件开发方案.md`。

- 数据与领域模型：`models.py` 集中定义领域对象（`ResearchDataset`、`FactorState`/`FactorResult`、`LabelSpec`/`LabelResult`、`HarvestProfile`、`CandidateFactor` 等）；`dataset.py::ResearchDatasetBuilder` 负责「取数 → schema 校验 → 动态品种池」；`universe.py::DynamicUniverse`（准入掩码 + 排除原因绑定为 `Universe`）；`labels.py::LabelBuilder`；`common.py` 放跨模块工具（补齐交易日、`walk_forward_split`）。
- `bridge.py::FactorDataBridge`：按因子声明的 `DataRequirement` 向引擎或本地 manager 补数，分 T1（引擎补列）→ T4（因子内部 join/对齐）四层，避免每试一个因子就改 `ResearchDatasetBuilder`。
- `presets.py`：「一键开局」样板与默认设定（`build_dataset()` / `build_dataset_with_facade()` / `make_facade()` / `default_products()`，以及组合实验的样板 spec）。写研究脚本优先复用它，不要手拼配置。
- `library/` 因子库：`base.py::Factor` ABC（`name` / `computation_scope` / `data_requirements` / `compute` / `parameters`，可选 `initialize_state` + `update` 增量接口）；`basic.py` 有 `MomentumFactor`、`RollYieldFactor`、`CurveSlope`；`others/future_native/` 按经济机制分文件（term_structure / open_interest / delivery / settlement / price_limit / margin / session），各因子用 `WarningMixin` 输出结构化警告。
- `preprocess/`：`PreprocessingPipeline` 串联缺失处理、缩尾、缩放（截面 / 滚动 / 池化 / 可拟合）、中性化（板块 / 波动率 / 流动性）、平滑、截断；`base.py::WalkForwardTransformer` 借 `walk_forward_split` 做推进拟合变换。
- `evaluation/`：入口是模块级函数而非统一 Evaluator 类——`coverage_stats`、`ic_details`、`grouped_details`、`regression_details`、`hit_rate_details`，多数支持 `mode='cross_sectional' | 'time_series'` 并内置 HAC / bootstrap 推断。`robustness.py` 目前只有 `adjust_pvalues`（BH 多重检验校正），其余稳健性工具仍在 `20261022 因子研究的稳健性检验开发方案.md` 阶段。
- `combination.py`：合成算子 `EqualWeightCombiner`、`VoteCombiner`、`RankCombiner`、`ICWeightedCombiner`、`RidgeRegressionCombiner`，统一 `transform()`，监督类另有 `fit()`。
- `diagnostic_portfolio/`：组合实验层。`DiagnosticPortfolioExperimentRunner` + `DiagnosticPortfolioSpec` 把单列信号按 harvest profile（V1 配方见 `profiles.py`：`ts_inv_vol_gross_one` / `ts_inv_vol_target_vol` / `market_rank_ls`）换成连续研究权重，输出下一期研究收益、换手、代理成本、净值、暴露与归因，**不引入订单、成交、保证金**；`storage/` 的 `DiagnosticPortfolioStore` 以 manifest + 哈希落盘（默认 `research/artifacts/diagnostic_portfolio/`）并支持重放，产物落在仓库内或工作区不干净时会降级可复现性等级并告警。
- `exploration/xray.py`：探索期因子体检（`quick_glance_at`、`plot_values_over_time` 等）。
- `scanning.py`：参数扫描，`gen_candidate_grid()` + `run_scan(evaluate_func)` → `ScanResult`（候选参数 × 指标表，附错误/警告记录）。

## 数据与数据库

- 三层规则数据库在 `db/rules/`（YAML，已入库 git）：`product_meta`(tier1)、`instrument_registry`(tier2)、`instrument_daily_status`(tier3)，另有 `exchange_holiday`、`product_classification`。
- 行情数据 `db/market/daily_snapshots/...`、派生 `db/derived/master_tables/`、日历 `db/calendar/trading_calendars/`。**所有 parquet/csv/db 数据文件被 gitignore**（`db/**/*.parquet` 等），git 只跟踪规则 YAML 和代码。
- 原始行情在仓库外：`D:\量化策略\CTA策略\RAW_DATA\{1min,TICK}`，绝对路径暂时硬编码在 `data/config.py`。
- `db/rules_ingestion/` 是"外部数据源 → 规则库"的入库工作区。

## 注意事项

- 详细设计记录按日期归档在 `docs/Development Notes/YYYYMMDD 主题.md`，按需参阅，注意，架构设计文档是代码写作方案的**原始参考**，最终代码架构决策可能偏离。
- 不要读取 `docs/Development Notes/*-log.md`, `docs\Development Notes\* 代码[重构|修改]笔记.md`。这类文档是开发日志，包含大量过时和遗弃设计。
