"""

因子研究模块 — 主流组合收获范式注册表

这里把《20260825 时序与截面因子评价》一文的六个中低频期货量化
范式实例化为 HarvestProfile 命名常量。它们是研究层的声明式研究假设
（信号 → 权重 → 可收获收益），不是完整策略配置。

六个范式对应文档章节：
    1. market_rank_ls           —— §6  截面范式一：全市场排名多空
    2. sector_neutral_rv        —— §7  截面范式二：板块中性相对价值
    3. sector_neutral_inv_vol   —— §8  截面范式三：板块中性与逆波动率
    4. constrained_alpha_opt    —— §9  截面范式四：协方差与成本约束 Alpha
    5. ts_inv_vol_target_vol    —— §10 时序范式一：独立预测 + 目标波动率 CTA
    6. sector_risk_budget_cta   —— §11 时序范式二：板块风险预算 + 协方差感知 CTA

"""

from factors.models import HarvestProfile

# ------------------------------------------------------------
# §6 截面范式一：全市场排名多空
#   同点比较所有品种，做多高分、做空低分，净敞口为零，gross 归 1。
# ------------------------------------------------------------
market_rank_ls = HarvestProfile(
    name='market_rank_ls',
    strategy_family='cross_sectional',
    signal_mapping='rank',
    risk_scaling='none',
    neutrality='market',
    risk_allocation='equal_product',
    portfolio_target='gross_one',
    position_limit='none',
    cost_proxy='linear_turnover',
    uses_covariance=False,
    description='全市场排名多空：M1_r 市场去均值收益为有效标签，'
                '核心统计量为截面 IC、Rank IC、分组单调性；'
                '最小实验为净敞口为零的 rank 多空。',
)

# ------------------------------------------------------------
# §7 截面范式二：板块中性相对价值
#   只交易板块内部相对强弱，硬约束 B^T w = 0，gross 归 1。
# ------------------------------------------------------------
sector_neutral_rv = HarvestProfile(
    name='sector_neutral_rv',
    strategy_family='cross_sectional',
    signal_mapping='continuous_score',
    risk_scaling='none',
    neutrality='sector',
    risk_allocation='equal_sector',
    portfolio_target='gross_one',
    position_limit='none',
    cost_proxy='linear_turnover',
    uses_covariance=False,
    description='板块中性相对价值：M_B r 板块残差收益为有效标签，'
                '核心统计量为板块残差协方差、双边残差 IC、板块内 Rank IC；'
                '最小实验为板块暴露为零的因子组合。',
)

# ------------------------------------------------------------
# §8 截面范式三：板块中性与逆波动率
#   等权正交后逆波动率会破坏中性，需用 W 内积下的加权投影 M_{B,W}。
#   该实例字段值对应文档 §15.2 的示例配置。
# ------------------------------------------------------------
sector_neutral_inv_vol = HarvestProfile(
    name='sector_neutral_inv_vol',
    strategy_family='cross_sectional',
    signal_mapping='continuous_score',
    risk_scaling='inverse_volatility',
    neutrality='sector',
    risk_allocation='equal_sector',
    portfolio_target='gross_one',
    position_limit='none',
    cost_proxy='linear_turnover',
    uses_covariance=False,
    description='板块中性 + 逆波动率：有效标签为 W M_{B,W} r，'
                '需按"估计波动率→加权投影→信号缩放→gross 归一→组合收益"'
                '的顺序执行最小实验。',
)

# ------------------------------------------------------------
# §9 截面范式四：协方差与成本约束的 Alpha 组合
#   多因子合成 alpha，在风险、暴露、仓位与成本之间优化权衡。
# ------------------------------------------------------------
constrained_alpha_opt = HarvestProfile(
    name='constrained_alpha_opt',
    strategy_family='cross_sectional',
    signal_mapping='continuous_score',
    risk_scaling='covariance',
    neutrality='sector',
    risk_allocation='optimization',
    portfolio_target='utility',
    position_limit='per_instrument_and_gross',
    cost_proxy='linear_turnover',
    uses_covariance=True,
    description='多因子约束优化：无固定标签，核心证据来自基准与新增因子'
                '的约束组合增量对比实验。',
)

# ------------------------------------------------------------
# §10 时序范式一：独立预测、逆波动率与目标波动率 CTA
#   每品种独立决定方向，1/sigma 定仓，组合缩放至目标波动率。
# ------------------------------------------------------------
ts_inv_vol_target_vol = HarvestProfile(
    name='ts_inv_vol_target_vol',
    strategy_family='time_series',
    signal_mapping='clip',
    risk_scaling='inverse_volatility',
    neutrality='none',
    risk_allocation='equal_product',
    portfolio_target='target_volatility',
    position_limit='per_instrument_and_gross',
    cost_proxy='linear_turnover',
    uses_covariance=True,
    description='时序独立预测 CTA：r_t+1 / sigma_t 为重要研究标签，'
                '核心统计量为分品种回归、pooled 回归、方向命中率、信号分箱；'
                '最小实验为逆波动率 + 目标波动率组合。',
)

# ------------------------------------------------------------
# §11 时序范式二：板块风险预算与协方差感知 CTA
#   保留板块共同方向，但限制板块风险贡献，控制相关品重仓集中。
# ------------------------------------------------------------
sector_risk_budget_cta = HarvestProfile(
    name='sector_risk_budget_cta',
    strategy_family='time_series',
    signal_mapping='clip',
    risk_scaling='inverse_volatility',
    neutrality='none',
    risk_allocation='equal_sector',
    portfolio_target='target_volatility',
    position_limit='per_instrument_and_gross',
    cost_proxy='linear_turnover',
    uses_covariance=True,
    description='板块风险预算 CTA：允许 B^T w != 0，按 RC_g 控制板块风险；'
                '最小实验为等品种、等板块、协方差感知三种风险预算对比。',
)

# 六个主流中低频期货量化范式的注册元组（保持该顺序）
MAINSTREAM_HARVEST_PROFILES: tuple[HarvestProfile, ...] = (
    market_rank_ls,
    sector_neutral_rv,
    sector_neutral_inv_vol,
    constrained_alpha_opt,
    ts_inv_vol_target_vol,
    sector_risk_budget_cta,
)