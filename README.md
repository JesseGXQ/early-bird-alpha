# Early Bird Alpha

面向中国期货市场的多因子策略量化研究框架，覆盖数据引擎、因子评价、组合实验与回测。

## 项目背景

### 为什么写这个项目？

*本人2025年毕业，2026年3月起转型学习量化，边写基础设施边做研究。*

### 为什么不用聚宽、米筐等已有平台，自己造轮子？

*理查德·费曼说过，"What I cannot create, I do not understand. Know how to solve every problem that has been solved." 我认为应该是这样。成熟平台隐藏了大量对研究很重要的细节，严肃的学习者至少应该尝试去理解它们。*

### 代码是自己写的，还是 AI 写的？

*绝大多数核心功能，包括数据引擎+因子模块，均由自己完成，或至少完成初稿。工作流为AI教学，个人落地，AI复核。学习研究框架+磨炼coding技能。保证研究全产业链持续自主可控（笑）。*

### 目前是否在求职？

*是的，偏好上海地区，不限资产类型，期望岗位是量化策略研究员。若您可提供工作机会，欢迎联系我😊。邮箱：jesse_gan@foxmail.com。*


## 快速开始

### 1. 克隆项目

```powershell
git clone --depth 1 https://github.com/JesseGXQ/early-bird-alpha.git
cd early-bird-alpha
```

后续命令请从仓库根目录执行。

`--depth 1` 只拉取最新一次提交，跳过历史，更快且占用更小；若需完整提交历史（查看变更、切回旧版本、`git blame` 等），再执行：

```powershell
git fetch --unshallow
```

### 2. 创建 Conda 环境

项目用 Python 3.12，依赖见 `environment.yml`：

```powershell
conda env create -f environment.yml
conda activate early-bird-alpha
```

若已创建过该环境，请按最新依赖更新：

```powershell
conda env update -n early-bird-alpha -f environment.yml --prune
```

### 3. 运行 Quickstart

Quickstart 用一份截取出的小型真实日频样本，完成因子计算 -> 组合实验的研究闭环：

```powershell
python examples/factor_quickstart.py
```

依次完成：

```text
读取 ResearchDataset 样本
→ 计算 10 日动量因子
→ 构造未来 5 日收益标签
→ 计算覆盖率、IC、Rank IC
→ 运行 market_rank_ls 诊断组合实验
→ 输出 Sharpe、CAGR、总收益并保存净值曲线
```

当前版本预期输出：

```text
Factor Quickstart
-----------------
Products: CU, AO, MA, LC, JD, EG, RM, B, PK, TF
Sample period: 2023-01-03 ~ 2024-12-31
Observations: 4598
Factor: momentum_10
Factor coverage: 100.00%
Joint coverage: 98.85%
Cross-Sectional Mean IC (5D forward-return label): 0.0193
Cross-Sectional Mean Rank IC (5D forward-return label): 0.0052
Portfolio: market_rank_ls, rebalance=5D, cost=3bp
Total return: 17.38%
CAGR: 8.72%
Sharpe: 1.022
NAV chart: <仓库路径>\examples\artifacts\factor_quickstart_nav.png
```

## 运行测试

默认测试只依赖仓库代码和自带小样本，不含全量真实行情：

```powershell
python -m pytest
```

## 数据库

克隆项目后自动获得（见 `db/`）：
- 交易日历、交易所节假日
- (derived) 换月日历、换月总表
- **日频k线**
- 合约注册信息
- 合约日频状态
- 品种分类表
- 交易所仓单

最新数据截止至2026年6月21日，目前不包含每日更新计划。1分钟k线和TICK级行情体积较大，未提交到 Git，
克隆不自动获得。


## 研究成果

使用本仓库基础设施进行的研究正在进行中，不定时更新。

### 单因子策略测试

- [动量, MomentumFactor](research/notebooks/202611/momentum_factor.ipynb)（`research/notebooks/202611/momentum_factor.ipynb`）。
- [展期收益率, RollYileFactor](research\notebooks\202611\roll_yield_factor.ipynb)
- [全曲线斜率, CurveSlop](research\notebooks\202611\curve_slope.ipynb)

### 多因子策略

## 许可证

本项目源代码采用 [MIT License](LICENSE)。`db/` 中的行情和衍生数据不自动适用 MIT License，请遵循原始数据提供方的条款。

