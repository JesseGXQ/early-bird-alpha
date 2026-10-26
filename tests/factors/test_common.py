"""`walk_forward_split` 的切分不变量测试。

覆盖两类 size（int / pd.DateOffset）、两种推进模式、有无 validation、
缺口/重叠日历，以及数据不足时的行为。只断言主逻辑不变量，不追求覆盖所有参数组合。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.common import walk_forward_split


def _pos(timeline: pd.DatetimeIndex) -> dict[pd.Timestamp, int]:
    return {ts: i for i, ts in enumerate(timeline)}


def _assert_fold_geometry(
    timeline: pd.DatetimeIndex,
    fold,
    *,
    embargo: int,
    has_val: bool,
) -> None:
    """断言 train/validation/test 非空、有序，且 embargo 间隔正确。"""
    p = _pos(timeline)
    assert len(fold.train) > 0
    assert len(fold.test) > 0
    if has_val:
        assert len(fold.validation) > 0
        # 窗口末尾收缩 embargo 个元素，因此位置差是 embargo + 1
        assert p[fold.validation[0]] - p[fold.train[-1]] == embargo + 1
        assert p[fold.test[0]] - p[fold.validation[-1]] == embargo + 1
    else:
        assert len(fold.validation) == 0
        assert p[fold.test[0]] - p[fold.train[-1]] == embargo + 1


def _assert_test_tiling(
    timeline: pd.DatetimeIndex,
    splits,
) -> None:
    """断言所有 test 非空，且相邻 test 折在时间轴上严格连续。"""
    p = _pos(timeline)
    assert splits
    assert all(len(s.test) > 0 for s in splits)
    for prev, cur in zip(splits, splits[1:]):
        assert p[cur.test[0]] == p[prev.test[-1]] + 1


def test_int_rolling_geometry():
    tl = pd.date_range('2020-01-01', periods=200, freq='D')
    splits = walk_forward_split(
        tl,
        train_size=90,
        validation_size=10,
        test_size=30,
        mode='rolling',
        embargo=1,
    )

    assert len(splits) == 4
    assert [len(s.test) for s in splits] == [30, 30, 30, 10]
    assert all(len(s.train) == 89 and len(s.validation) == 9 for s in splits)
    for fold in splits:
        _assert_fold_geometry(tl, fold, embargo=1, has_val=True)
    _assert_test_tiling(tl, splits)


def test_int_expanding_geometry():
    tl = pd.date_range('2020-01-01', periods=200, freq='D')
    splits = walk_forward_split(
        tl,
        train_size=90,
        validation_size=10,
        test_size=30,
        mode='expanding',
        embargo=1,
    )

    assert len(splits) == 4
    assert [len(s.train) for s in splits] == [89, 119, 149, 179]
    assert [len(s.validation) for s in splits] == [9, 9, 9, 9]
    assert [len(s.test) for s in splits] == [30, 30, 30, 10]

    p = _pos(tl)
    assert all(p[s.train[0]] == 0 for s in splits)
    for fold in splits:
        _assert_fold_geometry(tl, fold, embargo=1, has_val=True)
    _assert_test_tiling(tl, splits)


def test_int_and_dateoffset_equivalent_on_dense_daily():
    """连续日频上，int 与 DateOffset 两种写法应给出完全相同的折。"""
    tl = pd.date_range('2020-01-01', periods=200, freq='D')
    common = dict(mode='rolling', embargo=1)

    int_splits = walk_forward_split(
        tl, train_size=90, validation_size=10, test_size=30, **common)
    offset_splits = walk_forward_split(
        tl,
        train_size=pd.DateOffset(days=90),
        validation_size=pd.DateOffset(days=10),
        test_size=pd.DateOffset(days=30),
        **common,
    )

    assert len(int_splits) == len(offset_splits) == 4
    for int_fold, offset_fold in zip(int_splits, offset_splits):
        assert int_fold.train.equals(offset_fold.train)
        assert int_fold.validation.equals(offset_fold.validation)
        assert int_fold.test.equals(offset_fold.test)

    _assert_test_tiling(tl, int_splits)


@pytest.mark.parametrize(
    'train_size, validation_size, test_size',
    [
        (90, None, 30),
        (90, 0, 30),
        (pd.DateOffset(days=90), None, pd.DateOffset(days=30)),
    ],
)
def test_no_validation(train_size, validation_size, test_size):
    tl = pd.date_range(
        '2020-01-01', periods=200, freq='D', tz='Asia/Shanghai')
    splits = walk_forward_split(
        tl,
        train_size=train_size,
        validation_size=validation_size,
        test_size=test_size,
        mode='rolling',
        embargo=1,
    )

    assert len(splits) == 4
    assert [len(s.test) for s in splits] == [30, 30, 30, 20]
    assert all(len(s.validation) == 0 for s in splits)
    # 空 validation 也必须保留 timeline 的时区
    assert all(s.validation.dtype == tl.dtype for s in splits)
    for fold in splits:
        _assert_fold_geometry(tl, fold, embargo=1, has_val=False)
    _assert_test_tiling(tl, splits)


def test_gap_and_overlap_minimal_cases():
    """5 点缺口/重叠日历上的最小算例，保护反向窗口的边界语义。"""
    def d(*dates: str) -> list[pd.Timestamp]:
        return [pd.Timestamp(x) for x in dates]

    gap_tl = pd.DatetimeIndex(
        ['2020-01-01', '2020-01-02', '2020-01-03',
         '2020-01-05', '2020-01-06'])
    gap_splits = walk_forward_split(
        gap_tl,
        train_size=pd.DateOffset(days=1),
        validation_size=pd.DateOffset(days=1),
        test_size=pd.DateOffset(days=2),
        mode='rolling',
        embargo=0,
    )

    assert len(gap_splits) == 2
    assert list(gap_splits[0].train) == d('2020-01-01')
    assert list(gap_splits[0].validation) == d('2020-01-02')
    assert list(gap_splits[0].test) == d('2020-01-03')
    assert list(gap_splits[1].train) == d('2020-01-02')
    assert list(gap_splits[1].validation) == d('2020-01-03')
    assert list(gap_splits[1].test) == d('2020-01-05', '2020-01-06')
    for fold in gap_splits:
        _assert_fold_geometry(gap_tl, fold, embargo=0, has_val=True)
    _assert_test_tiling(gap_tl, gap_splits)

    overlap_tl = pd.DatetimeIndex(
        ['2020-01-01', '2020-01-03', '2020-01-04',
         '2020-01-05', '2020-01-06'])
    overlap_splits = walk_forward_split(
        overlap_tl,
        train_size=pd.DateOffset(days=1),
        validation_size=pd.DateOffset(days=1),
        test_size=pd.DateOffset(days=2),
        mode='rolling',
        embargo=0,
    )

    assert len(overlap_splits) == 2
    assert list(overlap_splits[0].train) == d('2020-01-01')
    assert list(overlap_splits[0].validation) == d('2020-01-03')
    assert list(overlap_splits[0].test) == d('2020-01-04', '2020-01-05')
    assert list(overlap_splits[1].train) == d('2020-01-04')
    assert list(overlap_splits[1].validation) == d('2020-01-05')
    assert list(overlap_splits[1].test) == d('2020-01-06')
    for fold in overlap_splits:
        _assert_fold_geometry(overlap_tl, fold, embargo=0, has_val=True)
    _assert_test_tiling(overlap_tl, overlap_splits)


def test_calendar_windows():
    """DateOffset 日历窗口：只断言 test 连续/非空等不变量，不锁定点位长度。

    `pd.DateOffset(months=...)` 按“日”对齐（月末日期会随月份长度漂移），
    因此 train/validation 的点位数可能逐折变化；这里只验证窗口几何与 test 连续性。
    """
    month_end = pd.date_range('2020-01-31', periods=30, freq='M')
    month_end_splits = walk_forward_split(
        month_end,
        train_size=pd.DateOffset(months=3),
        validation_size=pd.DateOffset(months=2),
        test_size=pd.DateOffset(months=1),
        mode='rolling',
        embargo=0,
    )

    assert len(month_end_splits) == 25
    assert all(len(s.test) == 1 for s in month_end_splits)
    for fold in month_end_splits:
        _assert_fold_geometry(month_end, fold, embargo=0, has_val=True)
    _assert_test_tiling(month_end, month_end_splits)

    rng = np.random.default_rng(0)
    base = pd.date_range('2018-01-01', periods=1500, freq='D')
    keep = rng.random(len(base)) < 0.6
    sparse = pd.DatetimeIndex(
        sorted(set(base[keep]) | {base[0], base[-1]}))

    sparse_splits = walk_forward_split(
        sparse,
        train_size=pd.DateOffset(days=90),
        validation_size=pd.DateOffset(days=10),
        test_size=pd.DateOffset(days=7),
        mode='rolling',
        embargo=1,
    )

    assert sparse_splits
    for fold in sparse_splits:
        _assert_fold_geometry(sparse, fold, embargo=1, has_val=True)
    _assert_test_tiling(sparse, sparse_splits)


@pytest.mark.parametrize(
    'train_size, validation_size, test_size, embargo',
    [
        (90, 10, 30, 1),
        (90, 10, 30, 0),
        (100, None, 30, 1),
        (95, 5, 30, 0),
        (pd.DateOffset(days=90), pd.DateOffset(days=10),
         pd.DateOffset(days=30), 1),
    ],
)
def test_insufficient_data_raises(
    train_size, validation_size, test_size, embargo,
):
    """数据不足时抛 ValueError，而不是返回 test 为空的折。"""
    tl = pd.date_range('2020-01-01', periods=100, freq='D')
    with pytest.raises(ValueError):
        walk_forward_split(
            tl,
            train_size=train_size,
            validation_size=validation_size,
            test_size=test_size,
            mode='rolling',
            embargo=embargo,
        )


def test_partial_test_window_is_kept():
    """只够部分 test 窗口时，应保留非空的最后一折。"""
    tl = pd.date_range('2020-01-01', periods=100, freq='D')
    splits = walk_forward_split(
        tl,
        train_size=90,
        validation_size=9,
        test_size=30,
        mode='rolling',
        embargo=0,
    )

    assert len(splits) == 1
    fold = splits[0]
    assert (len(fold.train), len(fold.validation), len(fold.test)) == (
        90, 9, 1)
    _assert_fold_geometry(tl, fold, embargo=0, has_val=True)
    _assert_test_tiling(tl, splits)
