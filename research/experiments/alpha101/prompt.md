请利用 `factors/diagnostic_portfolio` 层的因子组合实验研究基础设施，对经典的 alpha101 执行因子检验，需求如下。

## 一、研究配置方面。

1. `DatasetSpec`

```python
# 换月、复权、抽样、环境，采用默认配置
roll_config = RolloverConfig()
adj_config = AdjustmentConfig()
sample_config = SamplingConfig()
env_config = EnvironmentConfig()
facade_config = (roll_config, adj_config, sample_config, env_config)

facade = DataFacade(*facade_config)

# 所有品种，排除三个无数据的
products = facade.rule_query.get_product_universe(return_type='list')
products = [x for x in products if x not in ('L_F', 'PP_F', 'V_F')]

# 数据需求为主面板（OHLCV、Settle）以及产业分类数据集（用于行业中性化）
req1 = DataRequirement(source='main_panel', )
req2 = DataRequirement(source='classification')
data_req = (req1, req2)

# 品种池在默认设置以外，额外增加一项平均成交量限制
uni_spec = UniverseSpec(min_average_volume=10000)

data_spec = DatasetSpec(
    facade_config=facade_config,
    products=tuple(products),
    data_requirements=data_req, 
    start_date=datetime.date(2000,1,1), # 固定数据请求开始时间
    end_date=datetime.date(2026, 6, 26), # 固定数据请求结束时间
    universe=uni_spec
)
```

2. `DiagnosticSignal` 配置参考

```python
signal = DiagnosticSignal(
    name=f'...', # 替换 
    values=values, 
    source_stage='raw',
    factor_name='...', 
    recipe={...}) # factor_name 替换, recipe 采用 Factor.parameters
```

notes:
- signal.name = factor.name = f'alpha101_{x:03d}'

3. `DiagnosticPortfolioSpec`配置：

测试 `harvest_profile={'market_rank_ls','ts_inv_vol_gross_one'}` 这两个收获范式。

当 `harvest_profile='market_rank_ls'`时：

```python
H = 10 # 固定的调仓频率
rebalance_config = RebalanceConfig(
    interval=H, anchor=pd.Timestamp('2001-06-01')
) 
constraint_config = ConstraintConfig()
cost_config = CostConfig(rate=3e-5)

spec = DiagnosticPortfolioSpec(
    name='...',  # 替换
    harvest_profile=market_rank_ls, 
    rebalance=rebalance_config, 
    constraint=constraint_config, 
    cost=cost_config, 
    min_cross_section_size=5,
    missing_return_policy='default_to_zero_and_warning'
)
```

当 `harvest_profile='ts_inv_vol_gross_one'`时：

```python
H = 10
vol_R = 15

rebalance_config = RebalanceConfig(
    interval=H, anchor=pd.Timestamp('2001-06-01'))
constraint_config = ConstraintConfig()
cost_config = CostConfig(rate=3e-5)
risk_config = RiskConfig(
    volatility_lookback=vol_R, 
    volatility_min_periods=int(vol_R/2),
)
spec = DiagnosticPortfolioSpec(
    name='...', # 替换
    harvest_profile=ts_inv_vol_gross_one, 
    rebalance=rebalance_config, 
    constraint=constraint_config, 
    cost=cost_config, 
    risk=risk_config, 
    clip_bound=None,
    missing_return_policy='default_to_zero_and_warning'
)
```

notes:
- `DiagnosticPortfolioSpec.name` = {
    f'alpha101_{x:03d}_market_rank_ls', 
    f'alpha101_{x:03d}_ts_inv_vol_gross_one'
}

## 研究结果储存方面

利用`factors.diagnostic_portfolio.storage.DiagnosticPortfolioStore`储存结果。

以下为对应的实例化时参数设定：
- 实验结果保存根目录，`root_dir=r'research\experiments\alpha101\artifacts\diagnostic_portfolio'`

调用 `.save()` 时，字段填写说明：
- `note = None`
- `tags=['alpha101']`
- `data_provenance = {'main_panel': '2026-06-26'}`  ，即主面板的实际行情截止日期。


## 因子实现契约

请参考这两份文件：
- 基于 github 公开分享修改后的 alpha101 代码（股票版）：`research\experiments\alpha101\ref\review\alphas101_strict.py`
- 算子契约和实现细节：`research\experiments\alpha101\ref\review\operator_expression_implementation_notes.md`

股票版中，输入数据是时间戳为行索引，各股票为列，即 Index[timestamp], Column[stock]。而我们的 `ResearchDataset.main_panel` 是 MultiIndex[timestamp, product]，如果有需要，可能需要先做一次 `unstack`。

## 因子代码编写

- 所有的因子计算代码，请放在 `research\experiments\alpha101\alpha101_factor_definitions.py`。
- 每个因子，都要继承 `factors.library.base:: Factor`类。
    - `initialize_state`, `update` 为增量接口，用占位实现即可；其他方法必须实现。

### 变量和算子说明

#### 算子

参考代码中的收益率计算，是用收盘价的 `pct_change`:
```python
def returns(close: Frame) -> Frame:
    return close.pct_change(fill_method=None)
```
本项目数据引擎采用加法复权价格 + True_Return 的 dual-stream approach。收益率不能使用加法复权价格的 `pct_change`，改为直接使用 `True_Return` 列数值。

#### 变量说明

- open -> Adj_Open
- high -> Adj_High
- low -> Adj_Low
- close -> Adj_Close

alpha101 本用于股票，本项目目前是期货，故一些变量要寻找对应：
- volume -> Volume * multiplier
- vwap -> Adj_Settle， 即“复权结算价”
- amount -> Volume * multiplier * Settle，注意这里使用原始结算价，而不是复权结算价，因为成交额应真实表达当日名义成交金额
- cap -> OpenInterest * Raw_Close * multiplier

note: multiplier 可以走 `DataFacade.rule_query.get_multiplier(contract)` 获取。

行业分类变量，使用 DataFrame `ResearchDataset.auxiliary['classification']`：
- sector -> asset_class
- industry -> sector
- subindustry -> subsector

## 因子计算说明

- 若因子的计算范畴不仅限于自身（`Factor.computation_scope='cross_sectional'`)，直接将计算域设定为“当日可得的所有品种”，无需应用品种池。
    * Note：目前来看，此选择具备合理性。这使“因子定义”独立于会随参数变化的品种池。无论品种池条件如何变化，因子值是被固定下来的。否则同一个因子，计算时即应用品种池，那么换一个品种池参数，因子值就会变化，如此一来因子将不具备稳定和复用性。同时，更微妙的是，因子的内在性质也会不断变化。另外，期货本身品种就少，若计算时即应用品种池，会让原本就不大的样本空间变得更小，因子的统计稳定性会进一步下降。
- 滚动算子方面，参考代码显式使用 `min_periods=d`。遵循这一设定。由此出现的样本头部因子“无产出期”和某些低流动性品种（由于量价数据缺失导致的）因子值大量缺失，都是预期内现象。
- 行业中性化，同样定义计算域为“当日可得的所有品种”，无需应用品种池概念。若遇行业内单品种，不要将其因子计算结果设为 NaN。


## 其他实验设计说明

- 本次因子检测为纯样本内组合实验，不设训练集、测试集和验证集之分。
- 因子共同样本区间：由于存在（嵌套）滚动窗口计算，部分因子的历史依赖很长，在样本头部存在无产出期（或称暖机期）。最长的是 alpha48，长达1年。
因此，我们需要定义“共同样本区间”。具体如下：
    * 区间起点$T_0$，固定为前文`RebalanceConfig.anchor`取值（2001-06-01），终点为 `DatasetSpec.end_date`(2026-06-26)；
    * 2000-01-01 至 2001-05-31 的数据只用于计算 Alpha101 “因子暖机”，不用于组合实验，也不用于组合风险估计。
操作方面：构造一个评价用 `ResearchDataset`，将 `main_panel`、`universe.eligible`、`universe.exclude_reason` 及全部 `DiagnosticSignal.values` 同步截取至 [2001-06-01, 2026-06-26]。两种组合配置均显式设置 `anchor=pd.Timestamp('2001-06-01')`。注意，若该日不在全局时间轴上，则以其后首个可用全局时点为实际锚点。
- 时间对齐：使用当日收盘或结算价格计算 signal[t]，因子代码无需做 shift 操作，决策时间轴（信号、收益）由 `DiagnosticPortfolioExperimentRunner`保证。
- 实验运行报告方面，请生成一份文档和两份汇总表。文档命名为 'alpha101_implementation_report.md'，记录工程实现细节。汇总表命名为 'performance_summary_market_rank_ls.parquet', 'performance_summary_ts_inv_vol_gross_one.parquet'，记录 101 个因子的组合实验绩效。汇总表的行索引为 101 个因子，列为 'total_return', 'cagr', 'sharpe', 'n_periods'，数值取自 `DiagnosticPortfolioResult.performance` 中 `return_type='proxy_net'`的那一行。
- 本次无需生成因子分析报告——无需判断哪个因子有效，仅做工程实现。

## 实验运行工程工作流(建议)

1. 实现并单测公共算子。
2. 实现 101 个因子及稳定注册表。(跳过编写测试)
3. 构建一次 dataset，避免每个因子重复加载数据。
4. 每个因子只计算一次信号，复用于两种组合范式。
5. 先用少量因子和短日期进行 smoke test。
6. 再运行全部 101 × 2 实验。
7. 每成功一个实验立即保存；单个失败不阻塞其余实验。
8. 保存失败清单、异常堆栈、跳过原因。
    - 无需设置中断恢复工作流，仅全量运行即可。
    - 可选覆盖旧产物或跳过。
9. 最后生成实验运行报告和绩效汇总表。

建议增加以下产物：
```text
alpha101_factor_definitions.py   # 算子、因子类、注册表
run_alpha101_experiment.py       # 可恢复批量执行入口
summarize_alpha101_performance.py    # 汇总绩效
artifacts/failures.jsonl         # 失败信息
artifacts/performance_summary_mark_ranket_ls.parquet
artifacts/performance_summary_ts_inv_vol_gross_one.parquet
artifacts/alpha101_implementation_report.md
```
