from pandas.tseries.frequencies import to_offset

def annualization_factor(
        freq: str, 
        trading_days: int = 252, 
        minutes_per_day: int = 360) -> float:
    """由 pandas Offset Alias 计算一年内的交易期数 N。"""
    offset = to_offset(freq)
    n = offset.n
    rc = offset.rule_code
    if rc == 'T':            # 分钟级
        return trading_days * minutes_per_day / n
    if rc == 'H':            # 小时级
        return trading_days * minutes_per_day / (n * 60)
    if rc.startswith('D'):   # 日级（含 2D 等）
        return trading_days / n
    if rc.startswith('W'):   # 周级
        return trading_days / (n * 7)
    raise ValueError(f'暂不支持的年化频率: {freq!r} ({rc=})')