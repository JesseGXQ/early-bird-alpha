# README 待补清单

本文档临时保存已经确认、但尚未整理进根目录 `README.md` 的使用说明。相关内容正式写入 README 后，应从本清单中删除。

## 原始行情目录约定

项目默认从仓库同级的 `RAW_DATA` 目录读取原始行情：

```text
工作目录/
├── early-bird-alpha/
│   └── db/
└── RAW_DATA/
    ├── 1min/
    └── TICK/
```

- `RAW_DATA/1min`：1 分钟 K 线数据；
- `RAW_DATA/TICK`：逐笔行情数据；
- `early-bird-alpha/db`：规则库、日频快照、交易日历、派生数据和基本面数据。

程序根据仓库的实际位置计算以上路径，不依赖启动 Python 时的当前工作目录。

### 自定义原始行情位置

如果无法把 `RAW_DATA` 放在仓库同级，可以在启动 Python 前设置环境变量：

```powershell
$env:EARLY_BIRD_HIGH_FREQ_RAW_DATA_ROOT = "E:\futures_data\RAW_DATA"
```

设置后，程序分别从以下目录读取数据：

```text
E:\futures_data\RAW_DATA\1min
E:\futures_data\RAW_DATA\TICK
```

单次运行也可以在代码中显式传入路径：

```python
from data.config import EnvironmentConfig

config = EnvironmentConfig(
    data_dir_1min="E:/special_data/1min",
    data_dir_tick="E:/special_data/TICK",
)
```

路径优先级为：

```text
EnvironmentConfig 显式参数
> EARLY_BIRD_HIGH_FREQ_RAW_DATA_ROOT 环境变量
> 仓库同级 RAW_DATA 默认目录
```

## 后续整合事项

- 将本页的数据目录约定整理进 README 的“数据准备”章节；
- 补充完整行情库和 Quickstart 样本的下载地址、版本与校验值；
- 补充数据缺失时的检查命令和错误排查方式；
- 内容迁移完成后，删除 README 中的临时提示和本文件。

## Factors Quickstart

在 Conda 环境中从仓库根目录执行：

```powershell
python examples/factor_quickstart.py
```

该示例使用10个品种在2023—2024年的小型真实日频样本，依次完成：

```text
读取 ResearchDataset 样本
→ 计算10日动量因子
→ 构造未来5日收益标签
→ 计算覆盖率、IC 与 Rank IC
→ 运行 market_rank_ls 诊断组合实验
→ 输出 Sharpe、CAGR、总收益并保存净值曲线
```

净值图默认保存到：

```text
examples/artifacts/factor_quickstart_nav.png
```

正式整理 README 时，需要补入实际终端输出、净值图以及样本数据来源与授权说明。

## 测试命令

所有测试命令都应从仓库根目录执行。

默认测试只依赖仓库自带的代码与小型样本；需要完整私有行情库的测试会自动跳过：

```powershell
python -m pytest
```

本机已经准备好完整行情库时，显式运行真实数据测试：

```powershell
python -m pytest --run-real-data
```

默认测试会跳过标记为慢速的测试；需要运行时使用：

```powershell
python -m pytest --run-slow
```

两类可选测试可以同时启用：

```powershell
python -m pytest --run-real-data --run-slow
```

正式整理 README 时，需要解释 `sample_data`、`real_data`、`integration` 和 `slow` 四种测试标记，以及缺少外部数据时的跳过提示。
