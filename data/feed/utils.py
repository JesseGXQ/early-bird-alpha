from __future__ import annotations
from datetime import date

def filter_date_range(
        start_date: date | None,
        end_date: date | None,
        earliest: date,
        latest: date,
    ) -> tuple[date, date] | tuple[None, None]: 
    """
    根据开始日期，结束日期，最早可用和最晚可用日期，返回筛选后日期范围。

    """
    if start_date is None and end_date is None: # 两者都是 None
        return earliest, latest
    
    if start_date is not None and end_date is not None: # 两者都不是 None
        if start_date > end_date:
            raise ValueError(f'开始日期 {start_date} 晚于结束日期 {end_date} 。')
        if start_date > latest or end_date < earliest:
            return None, None
        # 走到这意味着有交集
        return max(start_date, earliest), min(end_date, latest)

    # 到这，意味着 start_date 和 end_date 仅有一者为 None
    if start_date is None and end_date is not None:
        if end_date < earliest:
            return None, None
        return earliest, min(end_date, latest)
    else:
        assert start_date is not None
        if start_date > latest:
            return None, None
        return max(start_date,earliest), latest
