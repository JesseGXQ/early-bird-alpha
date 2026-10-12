# Diagnostic Portfolio Storage 快速入门

本模块用于保存、加载、查询和复跑 `factors/diagnostic_portfolio` 的组合实验。

它解决的核心问题是：

- 保存本次实验的配置、实际输入和完整结果；
- 记录 Git、数据截止时间和运行环境；
- 后续不访问当前数据库，也能复跑组合实验层；
- 检测结果文件是否缺失或被修改；
- 根据因子、组合范式、样本范围和标签查找历史实验。

详细设计见[《诊断组合实验结果持久化设计方案》](<../../../docs/Development Notes/20261012 诊断组合实验结果持久化设计方案.md>)。

---

## 1. 最常用的工作流

假设研究代码已经产生以下四个对象：

```python
dataset  # ResearchDataset
signal   # DiagnosticSignal
spec     # DiagnosticPortfolioSpec
result   # DiagnosticPortfolioResult
```

保存实验：

```python
from factors.diagnostic_portfolio import DiagnosticPortfolioStore

store = DiagnosticPortfolioStore()

run_id = store.save(
    dataset=dataset,
    signal=signal,
    spec=spec,
    result=result,
    note='动量 15 日、每 10 日调仓的基准实验',
    tags=('momentum', 'baseline'),
    data_provenance={
        'main_panel': '2026-10-09',
    },
)

print(run_id)
```

默认会写入：

```text
research/artifacts/diagnostic_portfolio/runs/<run_id>/
```

该目录已位于仓库的 Git 忽略范围内。

加载实验：

```python
stored = store.load(run_id)

result = stored.result
print(result.performance)
result.nav.plot(grid=True)
```

`load()` 默认会验证全部 Parquet 文件的大小、SHA-256 和表格结构。不要为了日常加载关闭验证。

---

## 2. 在现有 notebook 中加入保存步骤

现有组合实验通常按下面的方式运行：

```python
runner = DiagnosticPortfolioExperimentRunner(
    signal=signal,
    dataset=dataset,
    spec=spec,
)
result = runner.run()
```

只需在成功运行后增加：

```python
from factors.diagnostic_portfolio import DiagnosticPortfolioStore

store = DiagnosticPortfolioStore()
run_id = store.save(dataset, signal, spec, result)
```

Store 是显式调用，不会改变 Runner：

- `runner.run()` 只计算；
- `store.save()` 只持久化；
- 计算失败时不会留下看似成功的运行目录。

建议在创建信号时填写完整 recipe：

```python
signal = DiagnosticSignal(
    name='momentum_15',
    values=mom_values,
    source_stage='raw',
    factor_name='momentum',
    recipe={
        'factor': {
            'class': 'factors.library.momentum.MomentumFactor',
            'params': {
                'lookback': 15,
            },
        },
        'preprocess': [],
    },
)
```

recipe 用来解释信号如何产生；真正进入组合实验的信号值则保存在输入快照中。recipe 为空不会阻止保存，但 manifest 会记录审计警告。

---

## 3. 一个完整的最小示例

下面的示例只使用合成数据，不访问 `db/` 或仓库外行情目录。

```python
import numpy as np
import pandas as pd

from factors.diagnostic_portfolio import (
    ConstraintConfig,
    CostConfig,
    DiagnosticPortfolioExperimentRunner,
    DiagnosticPortfolioSpec,
    DiagnosticPortfolioStore,
    DiagnosticSignal,
    RebalanceConfig,
)
from factors.models import (
    DataQualitySummary,
    DatasetSpec,
    ResearchDataset,
)
from factors.profiles import market_rank_ls
from factors.universe import Universe


# 1. 构造一个很小的研究数据集
timestamps = pd.date_range('2025-01-02', periods=5, freq='D')
products = ('A', 'B', 'C')
index = pd.MultiIndex.from_product(
    [timestamps, products],
    names=['timestamp', 'product'],
)

true_returns = pd.Series(
    np.array([
        0.010, -0.005, 0.002,
        0.004, 0.003, -0.002,
        -0.006, 0.008, 0.001,
        0.007, -0.004, 0.005,
        0.002, 0.006, -0.003,
    ]),
    index=index,
    name='True_Return',
)

eligible = pd.Series(True, index=index, name='eligible')

dataset = ResearchDataset(
    spec=DatasetSpec(
        facade_config=(),
        products=products,
        data_requirements=(),
    ),
    main_panel=true_returns.to_frame(),
    quality=DataQualitySummary(
        is_valid=True,
        total_rows=len(index),
        product_sample_counts={'A': 5, 'B': 5, 'C': 5},
        missing_rate_by_column={'True_Return': 0.0},
        warnings=(),
        errors=(),
    ),
    universe=Universe(
        eligible=eligible,
        exclude_reason=pd.Series('', index=index),
    ),
)


# 2. 构造信号和组合实验配置
signal = DiagnosticSignal(
    name='example_signal',
    values=pd.Series(
        np.tile([-1.0, 0.0, 1.0], len(timestamps)),
        index=index,
        name='example_signal',
    ),
    source_stage='raw',
    factor_name='example_factor',
    recipe={
        'factor': {
            'class': 'example.ExampleFactor',
            'params': {},
        },
        'preprocess': [],
    },
)

spec = DiagnosticPortfolioSpec(
    name='quickstart',
    harvest_profile=market_rank_ls,
    rebalance=RebalanceConfig(interval=1),
    constraint=ConstraintConfig(),
    cost=CostConfig(rate=3e-5),
    min_cross_section_size=2,
    missing_return_policy='default_to_zero_and_warning',
)


# 3. 运行并保存
result = DiagnosticPortfolioExperimentRunner(
    signal=signal,
    dataset=dataset,
    spec=spec,
).run()

store = DiagnosticPortfolioStore()
run_id = store.save(
    dataset,
    signal,
    spec,
    result,
    note='README 合成数据示例',
    tags=('example', 'market_rank_ls'),
)

print(run_id)
print(result.performance)
```

---

## 4. 指定其他存储位置

默认根目录是：

```text
research/artifacts/diagnostic_portfolio/
```

可以显式指定其他位置：

```python
store = DiagnosticPortfolioStore(
    root_dir=r'D:\research_results\diagnostic_portfolio'
)
```

`root_dir` 是模块根目录，Store 会在其中自动创建 `runs/`。

不要把实验文件保存到：

```text
factors/diagnostic_portfolio/storage/
```

这里是 Python 代码目录，不是研究产物目录。

---

## 5. 加载和查看 Manifest

```python
stored = store.load(run_id)
```

返回的是 `StoredDiagnosticPortfolioRun`，主要成员包括：

```python
stored.path              # 本次运行目录
stored.manifest          # RunManifest
stored.result            # DiagnosticPortfolioResult
stored.experiment_panel  # 实际进入组合实验的对齐输入
stored.auxiliary_inputs  # 本次实际消费的附属输入
```

查看实验身份：

```python
manifest = stored.manifest

print(manifest.run_id)
print(manifest.created_at)
print(manifest.reproducibility_level)
print(manifest.identity['config_fingerprint'])
print(manifest.identity['input_fingerprint'])
print(manifest.identity['experiment_fingerprint'])
```

查看样本边界：

```python
print(manifest.resolved_input['products'])
print(manifest.resolved_input['start_timestamp'])
print(manifest.resolved_input['end_timestamp'])
print(manifest.resolved_input['row_count'])
```

查看存储层警告：

```python
for warning in manifest.storage_warnings:
    print(warning)
```

---

## 6. 不访问当前数据库进行复跑

加载后的运行包含组合层所需的实际输入快照，可以直接复跑：

```python
from factors.diagnostic_portfolio import replay_stored_run

stored = store.load(run_id)
replayed_result = replay_stored_run(stored)
```

默认行为是：

1. 从 `experiment_panel` 和必要的附属输入重建最小输入对象；
2. 重新运行 `DiagnosticPortfolioExperimentRunner`；
3. 将新结果与保存结果逐字段比较；
4. 结构、警告、metadata 或数值超出容差时抛出 `ArtifactIntegrityError`。

如果只想取得复跑输入：

```python
from factors.diagnostic_portfolio import build_replay_inputs

signal, dataset, spec = build_replay_inputs(stored)
```

如需复跑但暂时不比较结果：

```python
replayed_result = replay_stored_run(stored, compare=False)
```

一般研究审计应保留默认的 `compare=True`。

---

## 7. 查找历史实验

`find()` 只扫描 manifest，不加载大型 Parquet 文件。

按信号和组合范式查找：

```python
runs = store.find(
    signal_name='momentum_15',
    harvest_profile='market_rank_ls',
)

for run in runs:
    print(run.run_id, run.created_at, run.tags)
```

按多个条件查找：

```python
runs = store.find(
    factor_name='momentum',
    products=('A', 'AG', 'AL'),
    start_timestamp='2020-01-02',
    end_timestamp='2026-10-09',
    tags=('baseline',),
)
```

其他常用过滤条件：

```python
store.find(run_id=run_id)
store.find(git_commit='完整的 40 位 commit')
store.find(config_fingerprint='sha256:...')
store.find(experiment_fingerprint='sha256:...')
store.find(created_after='2026-10-01', created_before='2026-10-31')
```

多个 tags 采用“全部包含”语义。`products` 采用排序后的完整品种集合精确匹配。

---

## 8. 复现等级

```python
stored.manifest.reproducibility_level
```

可能取值：

| 值 | 含义 |
|---|---|
| `strict` | 输入快照完整，运行代码来自干净且可定位的 Git commit |
| `input_replayable_code_dirty` | 输入可以复跑，但运行时存在未提交代码 |
| `incomplete` | Git 来源无法确定，不能严格恢复执行代码 |

个人探索阶段可以保存脏工作区的实验，系统会如实标记。但准备作为正式研究证据的运行，建议：

1. 提交代码；
2. 确认 `git status` 干净；
3. 重新运行并保存；
4. 检查复现等级为 `strict`。

---

## 9. 常见错误

### 9.1 文件被修改或缺失

```python
from factors.diagnostic_portfolio.storage import ArtifactIntegrityError

try:
    stored = store.load(run_id)
except ArtifactIntegrityError as exc:
    print(exc)
```

Store 不会在校验失败时偷偷返回部分结果。

### 9.2 recipe 或 metadata 不能 JSON 化

recipe 应只包含声明式数据，例如字符串、数字、布尔值、日期、列表和字典。不要放入：

- 因子实例；
- 函数或 lambda；
- 优化器实例；
- 数据库连接；
- 任意依赖 `repr()` 的对象。

不支持的值会引发 `SerializationError`，而不是被静默转换。

### 9.3 `result` 与输入不匹配

以下调用会被拒绝：

```python
store.save(dataset, signal_a, spec_a, result_from_other_run)
```

Store 会检查：

- `result.signal_name == signal.name`；
- `result.spec == spec`。

### 9.4 没有找到运行

```python
from factors.diagnostic_portfolio.storage import RunNotFoundError

try:
    stored = store.load('不存在的_run_id')
except RunNotFoundError:
    print('没有找到该运行')
```

---

## 10. 推荐实践

建议每次正式保存时做到：

- signal recipe 填写因子类、参数和预处理顺序；
- note 解释本次实验目的，而不是重复参数；
- tags 使用少量稳定词汇，例如 `momentum`、`baseline`、`robustness`；
- `data_provenance` 记录能够确认的数据源截止日期；
- 正式研究运行前提交代码并保持工作区干净；
- 保存后记录 `run_id`，报告和图表引用该 ID；
- 需要审计时使用默认校验加载和 `replay_stored_run()`。

不建议：

- 手工修改已发布运行目录中的 manifest 或 Parquet；
- 使用相同目录覆盖旧实验；
- 把 CSV 当作权威结果；
- 把 `run_id` 当成配置身份；
- 因为配置相同就假定输入数据也相同。

同一配置可以运行多次，每次都会获得不同 `run_id`；是否为相同研究内容，应比较 `experiment_fingerprint`。
