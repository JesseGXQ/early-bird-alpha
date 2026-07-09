import datetime

def test_get_trading_sessions_scenarios(rule_service):
    # 测试 get_trading_session 方法
    
    # 1. 测试一个最普通的合约（无需应用任何 session_override_rules)
    contract = 'rb2605'
    trading_date = datetime.date(2025, 5, 19)
    view = 'scheduled'
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date, view=view)
    print(f'\n[Test 1] 合约 {contract} 在交易日 {trading_date} 的交易时段 view={view} 为：\n{trading_sessions}')

    # 2. 测试一个普通合约，但是无夜盘（同时也无需应用任何 session_override_rules)
    contract = 'rb1101'
    trading_date = datetime.date(2010, 1, 20)
    view = 'realized'
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date, view=view
    )
    print(f'\n[Test 2] 合约 {contract} 在交易日 {trading_date} 的交易时段 view={view} 为：\n{trading_sessions}')

    # 3. 测试一个国债期货合约，最后交易日（应该无下午盘，要在2013年9月6日之后）
    contract = 'T2212' # 2022-12-09 为其 last_trade_date
    trading_date = datetime.date(2022, 12, 9)
    view = 'scheduled'
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date, view=view
    )
    print(f'\n[Test 3] 合约 {contract} 在交易日 {trading_date} 的交易时段 view={view} 为：\n{trading_sessions}')

    # 4. 测试一个股指期货（历史上）最后交易日提前至15:00收盘（需要在2015年12月31日之前）
    print('\n[Test 4] 测试股指期货最后交易日（历史上）提前收盘：')
    print('最后交易日：2011-02-18')
    contract = 'IF1102' # last_trade_date = 2011-02-18
    trading_date = datetime.date(2011, 2, 18)
    view = 'scheduled'
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date, view=view
    )
    print(f'合约 {contract} 在交易日 {trading_date} 的交易时段 view={view} 为：\n{trading_sessions}')
    
    print('正常交易日：2011-01-17')
    trading_date_normal = datetime.date(2011, 1, 17)
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date_normal, view=view
    )
    print(f'合约 {contract} 在交易日 {trading_date_normal} 的交易时段 view={view} 为：\n{trading_sessions}')

    print('测试IF1801最后交易日提前收盘规则已被取消：')
    contract = 'IF1801' # last_trade_date = 2018-01-19
    trading_date = datetime.date(2018, 1, 19)
    view = 'realized'
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date, view=view
    )
    print(f'合约 {contract} 在交易日 {trading_date} 的交易时段 view={view} 为：\n{trading_sessions}')

    # 5. 测试法定节假日后首个交易日无夜盘(选择 AG1912, trading_date=2019-09-16(中秋节后))
    print('\n[Test 5] 测试法定节假日后首个交易日无夜盘：')
    contract = 'AG1912' 
    trading_date = datetime.date(2019, 9, 16)
    view = 'scheduled'
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date, view=view
    )
    print(f'合约 {contract} 在交易日 {trading_date} 的交易时段 view={view} 为：\n{trading_sessions}')
    
    print('测试正常日期')
    trading_date_normal = datetime.date(2019, 9, 17)
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date_normal, view=view
    )
    print(f'合约 {contract} 在交易日 {trading_date_normal} 的交易时段 view={view} 为：\n{trading_sessions}')

    # 6. 测试疫情期间商品期货暂停夜盘（需要在2020-02-04至2020-05-06之间）
    print('\n[Test 6] 测试疫情期间商品期货暂停夜盘：')
    contract = 'AG2007' 
    trading_date = datetime.date(2020, 3, 25)
    view = 'scheduled'
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date, view=view
    )
    print(f'合约 {contract} 在交易日 {trading_date} 的交易时段 view={view} 为：\n{trading_sessions}')

    print('测试正常日期')
    trading_date_normal = datetime.date(2020, 5, 7)
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date_normal, view=view
    )
    print(f'合约 {contract} 在交易日 {trading_date_normal} 的交易时段 view={view} 为：\n{trading_sessions}')

    # 7. 测试2019-12-26结算系统故障导致夜盘延迟开市
    print('\n[Test 7] 测试2019-12-26结算系统故障导致夜盘延迟开市：')
    contract = 'AG2003' 
    trading_date = datetime.date(2019, 12, 26)
    view = 'scheduled'
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date, view=view
    )
    print(f'合约 {contract} 在交易日 {trading_date} 的交易时段 view={view} 为：\n{trading_sessions}')
    
    # 8. 测试2022-12-06上午江泽民追悼会默哀3分钟
    print('\n[Test 8] 测试2022-12-06上午江泽民追悼会默哀3分钟：')
    contract = 'AG2303' 
    trading_date = datetime.date(2022, 12, 6)
    view = 'scheduled'
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date, view=view
    )
    print(f'合约 {contract} 在交易日 {trading_date} 的交易时段 view={view} 为：\n{trading_sessions}')
    
    # 9. 测试 2016-01-07 股指期货熔断触发(contract选IF1603)
    print('\n[Test 9] 测试 2016-01-07 股指期货熔断触发：')
    contract = 'IF1603' 
    trading_date = datetime.date(2016, 1, 7)
    view = 'realized'
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date, view=view
    )
    print(f'合约 {contract} 在交易日 {trading_date} 的交易时段 view={view} 为：\n{trading_sessions}')

    print('若选择view=scheduled：')
    view = 'scheduled'
    trading_sessions = rule_service.get_trading_sessions(
        contract=contract, trading_date=trading_date, view=view
    )
    print(f'合约 {contract} 在交易日 {trading_date} 的交易时段 view={view} 为：\n{trading_sessions}')
