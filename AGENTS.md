# AGENTS.md

个人期货量化研究/回测平台。分层管道：`data` → `factors` → `strategy` → `portfolio` → `backtester` → `executor` → `account` → `analytics`。代码注释、docstring、提交信息均用中文，跟随该惯例。

## 行为约定

### 代码编辑
- 用户提出任何代码编辑指令（添加功能/修改逻辑/重构代码等）时，**必须先执行至少一轮设计和规划**，提出修改方案、影响范围、当前修改意图延伸的其他可能修改。若发现已就该修改指令做出讨论，将已讨论内容总结成总结方案。方案得到用户确认时才能动手。
- 用户给出方案确认并执行指令，但实际执行前发现仍有设计未决点或需求模糊处，**必须拒绝执行**，并按重要程度分级，列出仍需讨论的问题；始终遵循该约定，持续讨论和迭代，直至需求完全清晰、设计完全明确。
- 若用户提出代码写作或修改指令时，明确表明“跳过设计和规划”，忽略上述约定。

- 文档编辑指令不受上述约定控制。

## 输出格式

- 控制台打印数学公式：使用 KaTeX 兼容格式。

## 运行环境

- Conda `base` env（Anaconda，`C:\Users\Jesse\AppData\Local\anaconda3\python.exe`），Python 3.12。**仓库没有 requirements.txt / pyproject.toml，依赖只在 conda env 里**；不要擅自引入依赖清单。
- 没有 lint / typecheck / CI 配置，验证一律靠 pytest。

## 测试

- pytest，**必须从仓库根目录运行**：`python -m pytest`。包以顶层目录名导入（`from account.account import Account`），且 `tests/data/conftest.py` 的 fixture 与 `data/config.py` 的默认路径都是相对根目录的 `./db/...`。
- 单测：`python -m pytest tests/data/rules/test_trading_rule_service.py -q`（VS Code launch.json 已配好当前文件调试）。
- 测试目录镜像包结构（`tests/<package>/`）。`tests/data/conftest.py` 提供 session 级 fixture，直接指向真实 `db/` 数据（非 mock）。
- 部分 data 测试依赖 gitignored 的真实数据文件（`db/**/*.parquet` 及 `D:\量化策略\CTA策略\RAW_DATA\TICK|1min`），数据缺失时可能失败——先确认数据存在再判断是代码 bug 还是缺数据。
- 个人项目 + 初版开发，部分代码仅有/仅需要 inline test。
- **无需每次代码编辑都重跑全部测试**；除非用户明确要求，每次代码编辑后仅运行针对所改代码的有限测试（如对应模块/文件的单测）。

## 架构速览

- `data/` 数据引擎，统一入口 `data/facade.py::DataFacade`（`get_historical_vector` / `get_snapshot_stream` / `get_term_structure_data` 及多品种并行变体）。组件由 `data/factory.py` 组装；三条 feed：`ResearchFeed`（向量化研究，复权主连）、`SimulationFeed`（逐笔事件仿真）、`TermStructureFeed`。配置类在 `data/config.py`（`EnvironmentConfig` 硬编码了 `D:\量化策略\CTA策略\RAW_DATA\...` 绝对路径）。
- `data/rules/`：规则数据库的 manager（`ProductMetaManager`、`InstrumentRegistryManager`、`InstrumentDailyStatusManager`、`ExchangeHolidayManager`、`ProductClassificationManager`）与统一门面 `TradingRuleService`；查询入口 `data/query_service.py::RuleQueryService`。
- `factors/`（最新模块，V1 因子研究）：`factors/models.py` 定义 frozen dataclass 契约（`INDEX_NAMES=('timestamp','product')`、`REQUIRED_COLUMNS`），`factors/dataset.py::ResearchDatasetBuilder` 从 `DataFacade` 构建研究面板。架构文档见 `docs/Development Notes/2026072x 因子研究...`。
- `backtester/` V1 Bar 级最小闭环：`engine.py::BacktestEngine` 调度 Bar 事件 → 目标仓位 → 订单 → 成交 → 账户 → 绩效。设计文档 `docs/Development Notes/20260722 V1 Bar级最小闭环系统设计文档.md`。

## 数据与数据库

- 三层规则数据库在 `db/rules/`（YAML，已入库 git）：`product_meta`(tier1)、`instrument_registry`(tier2)、`instrument_daily_status`(tier3)，另有 `exchange_holiday`、`product_classification`。
- 行情数据 `db/market/daily_snapshots/...`、派生 `db/derived/master_tables/`、日历 `db/calendar/trading_calendars/`。**所有 parquet/csv/db 数据文件被 gitignore**（`db/**/*.parquet` 等），git 只跟踪规则 YAML 和代码。
- 原始行情在仓库外：`D:\量化策略\CTA策略\RAW_DATA\{1min,TICK}`，绝对路径暂时硬编码在 `data/config.py`。
- `db/rules_ingestion/` 是"外部数据源 → 规则库"的入库工作区（设计/执行记录 + `script/` 下的入库脚本），与运行时 `data/rules/` 分离，勿混淆。

## 注意事项

- `.pyc` 文件被历史提交进 git（12 个，`data/__pycache__/...`），改代码后 `git status` 会显示 pyc 改动——正常现象，不要主动删除或添加新的。
- 详细设计记录按日期归档在 `docs/Development Notes/YYYYMMDD 主题.md`，按需参阅即可，注意，架构设计文档是代码写作方案的**原始参考**，最终代码架构决策可能偏离。当发现现有代码大幅偏离架构文档，需报告。
- `db/rules_ingestion/supplements/API凭证.txt` 含 API 凭证，已被 gitignore，勿提交。
- 为避免污染上下文，不要读取 `docs/Development Notes/*-log.md`, `docs\Development Notes\* 代码[重构|修改]笔记.md`。这类文档是开发日志，包含大量过时和遗弃设计。
