# Tushare 期货数据库介绍

## 期货合约信息表
接口：fut_basic
描述：获取期货合约列表数据
限量：单次最大10000
积分：用户需要至少2000积分才可以调取，具体请参阅积分获取办法

输入参数

名称	类型	必选	描述
exchange	str	Y	交易所代码 CFFEX-中金所 DCE-大商所 CZCE-郑商所 SHFE-上期所 INE-上海国际能源交易中心 GFEX-广州期货交易所
fut_type	str	N	合约类型 (1 普通合约 2主力与连续合约 默认取全部)
fut_code	str	N	标准合约代码，如白银AG、AP鲜苹果等
list_date	str	N	上市开始日期(格式YYYYMMDD，从某日开始以来所有合约）
输出参数

名称	类型	默认显示	描述
ts_code	str	Y	合约代码
symbol	str	Y	交易标识
exchange	str	Y	交易市场
name	str	Y	中文简称
fut_code	str	Y	合约产品代码
multiplier	float	Y	合约乘数(只适用于国债期货、指数期货)
trade_unit	str	Y	交易计量单位
per_unit	float	Y	交易单位(每手)
quote_unit	str	Y	报价单位
quote_unit_desc	str	Y	最小报价单位说明
d_mode_desc	str	Y	交割方式说明
list_date	str	Y	上市日期
delist_date	str	Y	最后交易日期
d_month	str	Y	交割月份
last_ddate	str	Y	最后交割日
trade_time_desc	str	N	交易时间说明
接口示例


pro = ts.pro_api('your token')

df = pro.fut_basic(exchange='DCE', fut_type='1', fields='ts_code,symbol,name,list_date,delist_date')
数据示例

        ts_code  symbol      name   list_date    delist_date
0      P0805.DCE   P0805   棕榈油0805  20071029    20080516
1      P0806.DCE   P0806   棕榈油0806  20071029    20080616
2      P0807.DCE   P0807   棕榈油0807  20071029    20080714
3      P0808.DCE   P0808   棕榈油0808  20071029    20080814
4      P0811.DCE   P0811   棕榈油0811  20071115    20081114
5      P0812.DCE   P0812   棕榈油0812  20071217    20081212


## 交易日历

接口：fut_trade_cal
描述：获取各大期货交易所交易日历数据
积分：需2000积分才可以提取数据

输入参数

名称	类型	必选	描述
exchange	str	N	交易所 SHFE 上期所 DCE 大商所 CFFEX中金所 CZCE郑商所 INE上海国际能源交易所
start_date	str	N	开始日期
end_date	str	N	结束日期
is_open	int	N	是否交易 0休市 1交易
输出参数

名称	类型	默认显示	描述
exchange	str	Y	交易所 同参数部分描述
cal_date	str	Y	日历日期
is_open	int	Y	是否交易 0休市 1交易
pretrade_date	str	N	上一个交易日
接口示例

pro = ts.pro_api('your token')


df = pro.fut_trade_cal(exchange='DCE', start_date='20180101', end_date='20181231')
或者

df = pro.query('fut_trade_cal', exchange='DCE', start_date='20180101', end_date='20181231')
数据样例

        exchange  cal_date  is_open
0        DCE  20180101        0
1        DCE  20180102        1
2        DCE  20180103        1
3        DCE  20180104        1
4        DCE  20180105        1
5        DCE  20180106        0

## 结算参数

接口：fut_settle
描述：获取每日结算参数数据，包括交易和交割费率等
限量：单次最大返回1600行数据，可根据日期循环，总量不限制
积分：用户需要至少2000积分才可以调取，具体请参阅积分获取办法

输入参数

名称	类型	必选	描述
trade_date	str	N	交易日期 （trade_date/ts_code至少需要输入一个参数）
ts_code	str	N	合约代码
start_date	str	N	开始日期(YYYYMMDD格式，下同)
end_date	str	N	结束日期
exchange	str	N	交易所代码
输出参数

名称	类型	默认显示	描述
ts_code	str	Y	合约代码
trade_date	str	Y	交易日期
settle	float	Y	结算价
trading_fee_rate	float	Y	交易手续费率
trading_fee	float	Y	交易手续费
delivery_fee	float	Y	交割手续费
b_hedging_margin_rate	float	Y	买套保交易保证金率
s_hedging_margin_rate	float	Y	卖套保交易保证金率
long_margin_rate	float	Y	买投机交易保证金率
short_margin_rate	float	Y	卖投机交易保证金率
offset_today_fee	float	N	平今仓手续率
exchange	str	N	交易所
接口示例


pro = ts.pro_api('your token')

df = pro.fut_settle(trade_date='20181114', exchange='SHFE')
数据示例

    ts_code     trade_date    settle  trading_fee_rate  trading_fee  \
0    CU1811.SHF   20181114  48840.00             0.050           0.0   
1    CU1812.SHF   20181114  48990.00             0.050           0.0   
2    CU1901.SHF   20181114  48980.00             0.050           0.0   
3    CU1902.SHF   20181114  48980.00             0.050           0.0   
4    CU1903.SHF   20181114  49020.00             0.050           0.0   
5    CU1904.SHF   20181114  49040.00             0.050           0.0   

## 期货合约涨跌停价格（盘前）

接口：ft_limit
描述：获取所有期货合约每天的涨跌停价格及最低保证金率，数据开始于2005年。
限量：单次最大获取4000行数据，可以通过日期、合约代码等参数循环获取所有历史
积分：用户积5000积分可调取，积分获取方法具体请参阅积分获取办法



输入参数

名称	类型	必选	描述
ts_code	str	N	合约代码
trade_date	str	N	交易日期（格式：YYYYMMDD）
start_date	str	N	开始日期
end_date	str	N	结束日期
cont	str	N	合约代码（例如：cont='CU')
exchange	str	N	交易所代码 （例如：exchange='DCE')


输出参数

名称	类型	默认显示	描述
trade_date	str	Y	交易日期
ts_code	str	Y	TS股票代码
name	str	Y	合约名称
up_limit	float	Y	涨停价
down_limit	float	Y	跌停价
m_ratio	float	Y	最低交易保证金率（%）
cont	str	Y	合约代码
exchange	str	Y	交易所代码


接口示例


pro = ts.pro_api()

#获取单日全部期货合约涨跌停价格
df = pro.ft_limit(trade_date='20250213')

#获取单个品种所有合约涨跌停价格
df = pro.ft_limit(cont='CU')



数据样例

    trade_date     ts_code     name      up_limit down_limit m_ratio cont exchange
0     20250213   A2503.DCE  连豆一2503   4229.000   3751.000   7.000    A      DCE
1     20250213   A2505.DCE  连豆一2505   4249.000   3769.000   7.000    A      DCE
2     20250213   A2507.DCE  连豆一2507   4258.000   3776.000   7.000    A      DCE
3     20250213   A2509.DCE  连豆一2509   4268.000   3786.000   7.000    A      DCE
4     20250213   A2511.DCE  连豆一2511   4234.000   3756.000   7.000    A      DCE
..         ...         ...      ...        ...        ...     ...  ...      ...
783   20250213  ZN2509.SHF   沪锌2509  24890.000  21635.000   9.000   ZN     SHFE
784   20250213  ZN2510.SHF   沪锌2510  24885.000  21630.000   9.000   ZN     SHFE
785   20250213  ZN2511.SHF   沪锌2511  24780.000  21535.000   9.000   ZN     SHFE
786   20250213  ZN2512.SHF   沪锌2512  24700.000  21465.000   9.000   ZN     SHFE
787   20250213  ZN2601.SHF   沪锌2601  24710.000  21475.000   9.000   ZN     SHFE
