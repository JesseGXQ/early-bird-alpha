# 计划：编写 factors/preprocess/winsorize.py 缩尾类

## 目标
参照 `factors/preprocess/clean.py` 风格，完成 `factors/preprocess/winsorize.py` 中两个缩尾类及 inline test。

## 改动文件
- `factors/preprocess/winsorize.py`（唯一改动文件）

## 实现细节

### 共同设计
- `__init__`：`assert 0 < lower_quantile < upper_quantile < 1`
- `name` / `parameters` 属性编码参数
  - `ts_winsorizer_{lower}_{upper}_{window}`
  - `cs_winsorizer_{lower}_{upper}`
- `transform`：`assert context is None`，复制 `values`，返回 `PreprocessingResult(values, warnings=[], metadata=...)`
- metadata：报告下缩/上缩数量（跨期暖机区域不缩尾的计数）

### TimeseriesWinsorizer
```python
lower = values.rolling(window=self._window, min_periods=self._window).quantile(self._lower_quantile)
upper = values.rolling(window=self._window, min_periods=self._window).quantile(self._upper_quantile)
clipped = values.clip(lower=lower, upper=upper)
clipped = clipped.where(lower.notna(), values)   # 暖机区（前 window-1 个）保持原值
```
暖机区滚动分位数为 NaN，clip 会传播 NaN，故用 `where` 还原原值。

### CrossSectionalWinsorizer
```python
grouped = values.groupby(level='timestamp', group_keys=False)
lower = grouped.transform(lambda s: s.quantile(self._lower_quantile))
upper = grouped.transform(lambda s: s.quantile(self._upper_quantile))
clipped = values.clip(lower=lower, upper=upper)
```

### Inline test（文件底部，仿 clean.py）
1. 时序：30 个交易日、单品种 RB，第 25 个值设为 `10.0`，window=10 →
   断言该值缩到 `values[15:25]` 的 90% 分位，且前 9 个暖机点保持原值。
2. 截面：10 个交易日 × 5 品种，`2026-07-05` 的 `RB` 设为 `10.0` →
   断言缩到该日截面 90% 分位。
3. 打印原始/缩尾后值及 metadata。

## 验证
- 运行 `python -m pytest factors/preprocess -q`（确认模块可导入、语法正确）
- 直接运行 `python factors/preprocess/winsorize.py` 查看 inline test 输出

## 注意
- 不新增依赖，不引入 min_periods 等额外参数
- 无 lint/typecheck，靠 pytest + inline test 验证
