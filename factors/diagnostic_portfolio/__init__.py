"""

组合实验层（Diagnostic Portfolio）——公共入口与可独立复用的纯函数。

本子包实现《20260924 组合实验层组件开发方案》的 V1 能力：把单列信号按
``ts_inv_vol_gross_one``、``ts_inv_vol_target_vol`` 或 ``market_rank_ls``
收获范式转换为连续研究权重，并在不引入订单、成交、保证金与账户状态的前提下
计算下一期研究收益、换手、成本、净值、暴露、风险和归因。

公共接口分为三类：

- 运行器：:class:`DiagnosticPortfolioExperimentRunner`
- 输入对齐与调仓日程纯函数：:func:`align_inputs`、
  :func:`build_global_timeline`、:func:`build_rebalance_schedule`
- 权重状态机：:class:`MarketRankLSWeightStateMachine`、
  :class:`TSInverseVolatilityWeightMachine`

领域对象（:class:`DiagnosticSignal`、:class:`DiagnosticPortfolioSpec` 等）
定义在 :mod:`factors.diagnostic_portfolio.models`，并由 ``factors`` 顶层
包统一导出。

"""
from factors.diagnostic_portfolio.models import (
    DiagnosticSignal,
    DiagnosticPortfolioSpec,
    RebalanceConfig,
    RiskConfig,
    ConstraintConfig,
    CostConfig
)

from factors.diagnostic_portfolio.experiment import (
    DiagnosticPortfolioExperimentRunner,
    align_inputs,
    build_global_timeline,
    build_rebalance_schedule,
)
from factors.diagnostic_portfolio.weights import (
    CurrentWeight,
    MarketRankLSWeightStateMachine,
    TSInverseVolatilityWeightMachine,
    WeightStateMachine,
)

__all__ = [
    'DiagnosticPortfolioExperimentRunner',
    'align_inputs',
    'build_global_timeline',
    'build_rebalance_schedule',
    'CurrentWeight',
    'WeightStateMachine',
    'MarketRankLSWeightStateMachine',
    'TSInverseVolatilityWeightMachine',
    'DiagnosticSignal',
    'DiagnosticPortfolioSpec'
]
