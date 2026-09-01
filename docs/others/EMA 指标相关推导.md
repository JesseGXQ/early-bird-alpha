# EMA 指标相关推导 

推导
1. 递推定义
设序列 $\{y_1, y_2, \dots, y_t\}$，服从 i.i.d. 分布，$E[y_i] = \mu$。
EMA 递归：
$$s_0 = x \quad (\text{任意初始化常量})$$
$$s_t = \alpha \cdot y_t + (1-\alpha) \cdot s_{t-1}, \quad t \ge 1$$
2. 展开递推
把 $s_{t-1}$ 逐层代回：
$$
\begin{aligned}
s_t &= \alpha y_t + (1-\alpha) s_{t-1} \\
    &= \alpha y_t + (1-\alpha)\left[ \alpha y_{t-1} + (1-\alpha) s_{t-2} \right] \\
    &= \alpha y_t + \alpha(1-\alpha) y_{t-1} + \alpha(1-\alpha)^2 y_{t-2} + \cdots + \alpha(1-\alpha)^{t-1} y_1 + (1-\alpha)^t \cdot x
\end{aligned}
$$
前 t 项是观测值的加权和（系数 $\alpha (1-\alpha)^k$），末尾一项是初始化值的残留权重 $(1-\alpha)^t$。
3. 取期望
因 $E[y_i] = \mu$，$x$ 是常量：
$$
\begin{aligned}
E[s_t] &= \alpha\mu \sum_{k=0}^{t-1} (1-\alpha)^k \;+\; (1-\alpha)^t \cdot x
\end{aligned}
$$
等比级数求和：
$$
\sum_{k=0}^{t-1} (1-\alpha)^k = \frac{1 - (1-\alpha)^t}{1 - (1-\alpha)} = \frac{1 - (1-\alpha)^t}{\alpha}
$$
乘以 $\alpha\mu$：
$$
\alpha\mu \cdot \frac{1 - (1-\alpha)^t}{\alpha} = \mu \left[1 - (1-\alpha)^t\right]
$$
代入：
$$\boxed{E[s_t] = \mu - (1-\alpha)^t \cdot (\mu - x)}$$


4. 两种初始化的实际含义
初始化方式	$x$ 的取值	$E[s_t]$
任意常量 $x$	$x$ 固定	$\mu - (1-\alpha)^t (\mu - x)$ ← 有条件偏差
pandas ewm：$x = y_1$	$y_1$ 来自同一分布	无条件 $E[s_t] = \mu$，但条件于 $y_1$ 仍为上式
当 pandas 用 $y_1$ 初始化后，无条件期望是无偏的（因为随机初始化的正向和负向偏差互相抵消）。但问题在于条件方差——
5. 真正的问题：方差
条件于 $y_1$，$s_t$ 的方差为：
$$\text{Var}(s_t) \approx \sigma^2 \cdot \frac{\alpha}{2-\alpha}\left[1 - (1-\alpha)^{2t}\right]$$
- 当 $t \to \infty$（稳态）：$\text{Var}(s_\infty) = \sigma^2 \cdot \frac{\alpha}{2-\alpha}$
- 当 $t$ 小：方差远小于稳态值，因为 $s_t$ 被初始化值主导