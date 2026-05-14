# Data Engine架构设计

核心设计哲学：

* 用四个字概括为“单点事实”（Single Source of Truth）。这意味着，无论是特征工程模块、回测引擎，还是未来的实盘接口，都只能通过 Data Engine 来获取数据，且获取到的数据标准必须绝对统一，绝不能出现“研究切片里有这个字段，回测环境里没有”的情况。

* 避免循环依赖（Circular Dependency）或数据回流。底层的模块（例如storage）不能依赖或接收上层模块（cleaning/stitching）处理后的结果。

## 宏观架构

Data_Engine/
├── storage/            # 1. 底层存储与读写层 (Storage Layer)
├── metadata/           # 2. 基础信息中心 (Metadata Center)
├── cleaning/           # 3. 清洗与质检中心 (Cleaning & Validation)
├── stitching/          # 4. 连续合约构建器 (Continuous Contract Stitcher)
└── feed/               # 5. 数据分发 API (Data Feed / Dispatcher)

1. 底层存储与读写层 (storage/)
核心目标： 解决内存爆炸问题，屏蔽底层数据格式差异。

* DataLoaderBase (抽象基类)
功能： 定义数据加载的标准接口。
* ParquetTickLoader / CsvTickLoader (具体实现类)
功能： 实现针对特定文件格式的高效读取。
备注： 这里必须实现按天或按块读取的方法（如 load_chunk_by_date）。实盘中数据是一天一天追加的，回测也必须模拟这种流式特征。

2. 基础信息中心 (metadata/)
核心目标： 维护时空对齐的、带版本号的合约规则。

* InstrumentInfo (数据实体类/Data Class)
功能： 存储静态属性，如 symbol（RB）、multiplier（10）、price_tick（1）。
* TradingCalendarManager (交易日历管理器)
功能： 处理中国期货极其复杂的交易时间段。包含 is_trading_time(timestamp)、get_next_trading_day(date) 等方法。专门用于过滤非交易时段的垃圾 Tick。
* TemporalRuleManager (时效规则管理器)
功能： 管理手续费和保证金率。包含 get_commission_rate(symbol, date)，get_commission(symbol, date) ，get_margin_rate(symbol, date)等接口。
备注： 这里的入参必须带有 date，因为一年前的螺纹钢手续费和今天是不同的。

3. 清洗与质检中心 (cleaning/)
核心目标： 拦截脏数据，打上状态标签（如涨跌停板）。

* FilterBase (过滤器基类)
功能： 定义 process(tick) 接口。
* PriceLimitFilter (涨跌停过滤器)
功能： 检查 LastPrice 是否触及停板，并为 Tick 对象添加 is_limit_up 或 is_limit_down 的布尔属性。
OutlierFilter (异常值过滤器)
功能： 剔除价格负数、或者 AskPrice < BidPrice 这种明显违背撮合逻辑的脏数据。
* CleaningPipeline (清洗流水线)
功能： 将上述各种 Filter 串联起来，数据流经 Pipeline 后变成“干净且带有丰富状态标签”的 Tick。

4. 连续合约构建器 (stitching/)
核心目标： 发现换月时点，计算复权因子，不污染原始数据。

* RolloverDetector (换月探测器)
功能： 读取所有月份的日/分钟级别 OpenInterest 和 Volume，根据你设定的参数（如：持仓最大且维持 3 天），输出一张“换月日历表”。
* AdjustmentCalculator (复权计算器)
功能： 根据 RolloverDetector 给出的日期，去 storage 读取换月当天的跳空价差（Price Gap），并生成一张 AdjustmentFactorMap（复权因子映射表）。
* ContinuousContractManager (连续合约大管家)
功能： 对外提供查询接口。例如 get_active_contracts(symbol_root, date)，在正常日子返回 [RB2405]，在换月窗口期返回 [RB2405, RB2409]（支持 “双合约并行”的需求，以实现大规模资金换月窗口期内平滑换月）。
备注：如果两个合约的 Tick 不是同时来的（通常相差毫秒），需要做 “最新值对齐（Last-Value Cross-Section）。同时，Data Engine 需要支持同时计算两份合约的保证金占用。因为在换月过程中，你的总名义价值虽然没变，但在两个合约上都有持仓。

移仓换月的执行对Data Engine的要求：
在 Data Engine 的设计中引入 “虚拟容器（Virtual Container）” 概念：定义一个虚拟品种： 比如 RB_MAIN。映射逻辑： 在正常时期，RB_MAIN 映射到 RB2405。过渡时期： RB_MAIN 映射到一个 List [RB2405, RB2409]。对 Execution 模块透明： Execution 模块订阅 RB_MAIN。当它收到一个包含两个合约的数据包时，自动触发其内部的“平滑换仓逻辑”；如果只收到一个，则按常规趋势信号执行。

5. 数据分发 API (feed/)
核心目标： 对外隐藏内部复杂性，针对不同场景提供专用接口。

* DataFacade (数据门面类)
功能： 整个 Data Engine 的唯一对外总控。初始化时会将上述所有模块拼装起来。
* ResearchFeed (研究用数据分发器)
功能： 提供 get_historical_vector() 方法。自动将原始数据与复权因子相加，返回一张巨大、平滑的 Pandas/Polars DataFrame，供你做因子挖掘。
* SimulationFeed (回测用事件分发器)
功能： 提供 stream_tick_events() 方法。
备注： 这个方法在换月窗口期，会利用 ContinuousContractManager 的信息，将属于同一个逻辑品种的两个物理合约打包，吐出一个包含多合约快照的字典：{"RB_MAIN": {"RB2405": tick1, "RB2409": tick2}}，直接喂给 Execution 模块做平滑换仓。
* TickStreamGenerator (内部流式生成器/碾碎机)
功能： 它是 SimulationFeed 内部调用的底层引擎。专门负责接收前面经过清洗、复权后的 Chunk 数据块，将其转化为 Python 的 yield 生成器，按时间戳逐笔吐出 Event_Tick 数据。它解决了“高速矩阵批处理”与“低速逐笔撮合”之间的阻抗匹配问题。

架构图景总结
硬盘 (Parquet)
   │
   ▼
[storage] ── yield Chunks ──┐
                            │ (高速流水线：全部是批量矩阵运算)
[cleaning] ─ 洗净的 Chunks ─┤
                            │
[stitching] ─ 复权的 Chunks ─┘
   │
   ▼
进入 [feed] 模块，发生分化：
   ├─► SimulationFeed ── (调用 TickStreamGenerator 碾碎) ──► yield 逐笔 Tick 事件
   │
   └─► ResearchFeed ──── (拼接所有 Chunks) ────────────────► 返回庞大的 DataFrame
