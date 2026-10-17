"""data.feed.utils.filter_date_range 的日期区间筛选测试。

覆盖四种入参形态（双 None / 双显式 / 仅 start / 仅 end）在「区间内、部分重叠、
完全无交集、边界相等」下的裁剪行为，以及 start_date > end_date 的报错路径。
"""

from datetime import date, timedelta

import pytest

from data.feed.utils import filter_date_range


# ----------------------------------------------------------------
# 可用区间与参照日期
# ----------------------------------------------------------------
EARLIEST = date(2015, 5, 13)
LATEST = date(2025, 5, 16)

DAY_BEFORE_EARLIEST = EARLIEST - timedelta(days=1)
DAY_AFTER_LATEST = LATEST + timedelta(days=1)

BEFORE = date(2010, 1, 1)   # 明显早于最早可用日
AFTER = date(2030, 1, 1)    # 明显晚于最晚可用日
INNER_START = date(2018, 1, 1)
INNER_END = date(2019, 1, 1)

EMPTY = (None, None)        # 无交集时的约定返回值


@pytest.mark.parametrize(
    'start_date, end_date, expected',
    [
        # ---- 双 None：取全区间 ----
        (None, None, (EARLIEST, LATEST)),

        # ---- 双显式：落在可用区间内，原样返回 ----
        (INNER_START, INNER_END, (INNER_START, INNER_END)),
        (INNER_START, INNER_START, (INNER_START, INNER_START)),
        (EARLIEST, LATEST, (EARLIEST, LATEST)),
        (EARLIEST, INNER_END, (EARLIEST, INNER_END)),
        (INNER_START, LATEST, (INNER_START, LATEST)),

        # ---- 双显式：部分重叠，两端裁剪 ----
        (BEFORE, INNER_END, (EARLIEST, INNER_END)),
        (INNER_START, AFTER, (INNER_START, LATEST)),
        (BEFORE, AFTER, (EARLIEST, LATEST)),
        (DAY_BEFORE_EARLIEST, LATEST, (EARLIEST, LATEST)),

        # ---- 双显式：无交集 ----
        (BEFORE, DAY_BEFORE_EARLIEST, EMPTY),
        (BEFORE, BEFORE, EMPTY),
        (DAY_AFTER_LATEST, AFTER, EMPTY),
        (AFTER, AFTER, EMPTY),

        # ---- 仅 end 显式：起点取 earliest ----
        (None, INNER_END, (EARLIEST, INNER_END)),
        (None, EARLIEST, (EARLIEST, EARLIEST)),
        (None, LATEST, (EARLIEST, LATEST)),
        # 终点晚于 latest 时必须裁剪到 latest
        (None, AFTER, (EARLIEST, LATEST)),
        (None, DAY_AFTER_LATEST, (EARLIEST, LATEST)),
        # 终点早于 earliest（含最早日前一天）时无交集
        (None, DAY_BEFORE_EARLIEST, EMPTY),
        (None, BEFORE, EMPTY),

        # ---- 仅 start 显式：终点取 latest ----
        (INNER_START, None, (INNER_START, LATEST)),
        (EARLIEST, None, (EARLIEST, LATEST)),
        (LATEST, None, (LATEST, LATEST)),
        # 起点早于 earliest 时必须裁剪到 earliest
        (BEFORE, None, (EARLIEST, LATEST)),
        (DAY_BEFORE_EARLIEST, None, (EARLIEST, LATEST)),
        # 起点晚于 latest（含最晚日后一天）时无交集
        (DAY_AFTER_LATEST, None, EMPTY),
        (AFTER, None, EMPTY),
    ],
)
def test_filter_date_range(start_date, end_date, expected):
    assert filter_date_range(
        start_date, end_date, EARLIEST, LATEST,
    ) == expected


@pytest.mark.parametrize(
    'start_date, end_date',
    [
        # 区间内倒序
        (INNER_END, INNER_START),
        # 恰好一天倒序
        (date(2020, 1, 2), date(2020, 1, 1)),
        # 两端分别落在区间外，且顺序倒置
        (AFTER, BEFORE),
        # 两端都在最晚日之后，且顺序倒置
        (AFTER, DAY_AFTER_LATEST),
    ],
)
def test_reversed_explicit_range_raises(start_date, end_date):
    """用户显式传入的 start_date > end_date 属于输入错误，必须报错。"""
    with pytest.raises(ValueError, match='晚于结束日期'):
        filter_date_range(start_date, end_date, EARLIEST, LATEST)


def test_single_sided_range_never_raises_on_ordering():
    """回归测试：单侧为 None 时不得用填充值去判定顺序。

    ``start_date=None, end_date=2000-01-01`` 的真实语义是「请求区间整体早于
    最早可用日」，应返回空结果；若先做顺序检查，就会拿 earliest 当作用户传入的
    start_date 抛出误导性的 ValueError。
    """
    assert filter_date_range(
        None, BEFORE, EARLIEST, LATEST,
    ) == EMPTY
    assert filter_date_range(
        AFTER, None, EARLIEST, LATEST,
    ) == EMPTY


def test_returned_range_is_always_a_subset_of_available_range():
    """不变量：非空返回值必须落在 [earliest, latest] 内且首尾不倒置。"""
    candidates = [
        None, BEFORE, DAY_BEFORE_EARLIEST, EARLIEST,
        INNER_START, INNER_END, LATEST, DAY_AFTER_LATEST, AFTER,
    ]

    for start_date in candidates:
        for end_date in candidates:
            try:
                result = filter_date_range(
                    start_date, end_date, EARLIEST, LATEST,
                )
            except ValueError:
                # 仅双显式且顺序倒置允许报错，跳过
                assert start_date is not None and end_date is not None
                assert start_date > end_date
                continue

            if result == EMPTY:
                continue

            start, end = result
            assert EARLIEST <= start <= end <= LATEST, (
                f'start_date={start_date}, end_date={end_date} '
                f'返回了越界区间 {result}'
            )