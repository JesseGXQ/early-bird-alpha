# Early Bird Alpha

## 项目背景

### 为什么写这个项目？

本人2025年毕业，2026年3月起转型学习量化，边写基础设施边做研究。

### 项目主要涉及哪个方向？

目前主要为国内期货多因子策略。

### 为什么不用聚宽、米筐等已有平台，自己造轮子？

理查德·费曼说过，"What I cannot create, I do not understand. Know how to solve every problem that has been solved." 我认为应该是这样。成熟平台隐藏了大量对研究挺重要的细节，严肃的学习者至少应该尝试去理解它们。

### 代码是自己写的，还是 AI 写的？

绝大多数核心功能，包括数据引擎+因子模块，均由自己完成，或至少完成初稿。工作流为AI教学，个人落地，AI复核。学习研究框架+磨炼coding技能。保证研究全产业链持续自主可控（笑）。

### 目前是否在求职？

是的，偏好上海地区，不限资产类型，若您可提供工作机会，欢迎联系我。邮箱：jesse_gan@foxmail.com。


## 快速开始

### 1. 克隆项目

```powershell
git clone https://github.com/JesseGXQ/early-bird-alpha.git
cd early-bird-alpha
```

后续命令请从仓库根目录执行。

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

Quickstart 用仓库自带小型真实日频样本，完成因子计算 -> 组合实验的研究闭环：

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

默认测试只依赖仓库代码和自带小样本：

```powershell
python -m pytest
```

## 完整行情库

完整研究模式需要额外的数据文件，包括日频快照，1min 原始行情，TICK 行情，体积较大，未提交到 Git。后续可考虑用百度网盘提供，待更新。

## 研究成果

使用本仓库基础设施所进行的研究，正在进行中，不时更新。

### 单因子策略测试

- [动量, MomentumFactor](research/notebooks/202611/momentum_factor.ipynb)（`research/notebooks/202611/momentum_factor.ipynb`）。
- [展期收益率, RollYileFactor](research\notebooks\202611\roll_yield_factor.ipynb)
- [全曲线斜率, CurveSlop](research\notebooks\202611\curve_slope.ipynb)

### 多因子策略

