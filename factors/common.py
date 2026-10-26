import pandas as pd
from data.factory import _create_trading_rule_components
from data.config import EnvironmentConfig
from data.calendar.product_calendar import ProductCalendar
from dataclasses import dataclass
from typing import Literal

def reindex_to_full_dates(
        data: pd.DataFrame | pd.Series,
        env_config: EnvironmentConfig
        ) -> pd.DataFrame | pd.Series:
    """将输入数据中每个品种的交易日补齐到开始日期至结束日期之间的
    全部交易日，暴露出可能存在的缺失日期。缺失日期行用全 NaN 填充。
    
    Parameters
    ------
    data: pd.DataFrame or pd.Series
        - MultiIndex[timestamp, product]
    
    Returns
    ------
    pd.DataFrame or pd.Series, depends on input.
        每个品种索引的 timestamp 层为完整无空缺交易日的数据集。
    
    """
    assert isinstance(data.index, pd.MultiIndex) and data.index.nlevels == 2
    data = data.sort_index(level=['product','timestamp'])
    data = data.reset_index(level='timestamp', drop=False)

    prod_first_ts = data.groupby(level='product')['timestamp']\
        .first().rename('start_date')
    prod_last_ts = data.groupby(level='product')['timestamp']\
        .last().rename('end_date')
    prod_date_span_df = pd.concat([prod_first_ts, prod_last_ts],
                                  axis=1)
    
    (meta_manager, _, _, exchange_calendar_manager, _, _, _
     ) = _create_trading_rule_components(env_config=env_config)

    prod_level_values: list[str] = []
    date_level_values: list[pd.Timestamp] = []
    for prod, row in prod_date_span_df.iterrows():
        start_date: pd.Timestamp = row['start_date']
        end_date: pd.Timestamp = row['end_date']
        calendar = ProductCalendar(
            prod, meta_manager, exchange_calendar_manager,  # pyright: ignore[reportArgumentType]
            trading_rule_service=None
        )
        dates = calendar.get_trading_dates_in_date_range(
            start_date.date(), end_date.date(), True
        )
        prod_level_values.extend([prod] * len(dates)) # pyright: ignore[reportArgumentType]
        date_level_values.extend(dates)

    new_idx = pd.MultiIndex.from_arrays(
        [date_level_values, prod_level_values],
        names=['timestamp', 'product']
    )
    assert new_idx.is_unique

    data = data.set_index('timestamp', append=True).reorder_levels(
    ['timestamp', 'product']
)
    return data.reindex(new_idx)

@dataclass(frozen=True)
class TemporalSplit:
    train: pd.DatetimeIndex
    validation: pd.DatetimeIndex 
    test: pd.DatetimeIndex

def walk_forward_split(
        timeline: pd.DatetimeIndex,
        *,
        train_size: int | pd.DateOffset, 
        validation_size: int | pd.DateOffset | None, 
        test_size: int | pd.DateOffset,
        mode: Literal['expanding', 'rolling'] = 'rolling',
        embargo: int
) -> list[TemporalSplit]:

    """
    推进分析（walk-forward）——时间轴分隔工具。

    Parameters
    ------
    timeline: pd.DatetimeIndex
        待切分时间轴，必须唯一、单调递增，且非空。
    train_size: int or pd.DateOffset
        训练集大小。
    validation_size: int or pd.DateOffset or None
        验证集大小。当不需要验证集时，将其设为 `None`。
    test_size: int or pd.DateOffset 
        测试集大小。
    mode: {'expanding', 'rolling'}
        滚动窗口推进（训练集、验证集和测试集保持大小，向前滚动）；
        或扩展窗口推进（训练集起点不变，窗口不断扩张；验证集和测试集保持大小，向前滚动）
    embargo: int
        隔离期长度（以 `timeline` 的元素个数计）。训练集和验证集的末尾
        相应收缩该长度，以避免数据泄漏。
    
    Returns
    -------
    list[TemporalSplit]
        按时间顺序的时间轴切分结果，每项都含 `train`、`validation`、`test` 三个
        `pd.DatetimeIndex`。每个 `test` 均非空。

    Raises
    ------
    ValueError
        时间轴长度不足以构造至少一个非空测试窗口，或 embargo 使 train/validation
        窗口为空。

    Notes
    -------
    `train_size`, `validation_size`, `test_size` 必须全部为 `int`（正整数），
    或全部为 `DateOffset`。例外条件为 `validation_size` 可为 `None`（当不需要验证集时）。
    请确保三个 offset 都为正，否则会出现无法预期的错误。

    - `DateOffset`: 允许不同的 freq。例如，如果想要 `train_size` 为 3 个月, 
    `validation_size` 为 3 个月，`test_size` 为 20 天，
    可令 `train_size=pd.DateOffset(months=3)`, 
    `validation_size=pd.DateOffset(months=3)`, `test_size=pd.DateOffset(days=20)`。
    目前只支持纯 `pd.DateOffset`，不支持其子类，即 'BDay/Week/MonthEnd' 等。
    
    """
    # 最后没有选择 size 可以为 str 的选项。记录理由如下：
    # 若允许 str，假设传 validation_size='1M'。此时函数 _bwd_pos()中有如下：
    # `start_ts = timeline[end_pos] - size`，若 timeline[end_pos] = Timestamp('2020-06-01')，
    # 此时  Timestamp('2020-06-01') - MonthEnd 会得到 Timestamp('2020-05-31')！
    # 这是一个显然不符合意图的窗口推导。这是因为 pd.tseries.frequencies.to_offset('1M') 得到的
    # MonthEnd 实际上是会做一个 normalize， Timestamp('2020-06-01') 减去1个月，然后再
    # normalize到月末，就会得到 Timestamp('2020-05-31')，此时实际做的便完全不是所谓的
    # “减去1个月”的操作。因此我们不应该允许例如 '1M', '3M' 的类似 str，然后内部转 DateOffset，
    # 这会导致很多奇怪的现象（例如_check_end_gapped_after_embargo()的报错）。
    
    assert timeline.is_monotonic_increasing and timeline.is_unique and \
    not timeline.hasnans and len(timeline) != 0 and isinstance(timeline, pd.DatetimeIndex)
    assert mode in {'expanding', 'rolling'}

    # Determine whether validation set is needed
    has_val = True 
    if validation_size == 0 or validation_size is None:
        has_val = False
    
    # Check homogenous input "size" type
    if isinstance(train_size, int):
        assert isinstance(validation_size, int) or validation_size is None
        assert isinstance(test_size, int)
    else:
        assert type(train_size) is pd.DateOffset
        assert type(validation_size) is pd.DateOffset or validation_size is None
        assert type(test_size) is pd.DateOffset

    assert embargo >= 0

    def _fwd_pos(start_pos: int, size: int | pd.DateOffset) -> int:
        """find position forward."""
        if start_pos >= len(timeline): 
            return len(timeline) # 无法向前推进了
        if isinstance(size, int): 
            pos = min(start_pos + size, len(timeline))
        else:
            end_ts = timeline[start_pos] + size
            pos: int = timeline.searchsorted(end_ts, side='left')
        return pos
    
    def _bwd_pos(end_pos: int, size: int | pd.DateOffset) -> int:
        """find position backward."""
        if isinstance(size, int):
            pos = max(end_pos - size, 0)
        else:
            start_ts = timeline[end_pos] - size
            pos: int = max(timeline.searchsorted(start_ts, side='right') - 1, 0)
        return pos

    def _check_end_gapped_after_embargo(end_gapped_pos, start_pos):
        """Ensure that end position with embargo is strictly larger than
        start position.
        """
        if end_gapped_pos <= start_pos:
            raise ValueError(f'执行 embargo={embargo} 之后, 出现 end_gapped_pos '
                             f'{end_gapped_pos} 小于等于 start_pos {start_pos}。')

    splits: list[TemporalSplit] = []

    # First, let us build the first fold
    train_start_pos = 0
    train_end_pos = _fwd_pos(start_pos=train_start_pos, size=train_size)
    train_end_gapped_pos = train_end_pos - embargo
    _check_end_gapped_after_embargo(train_end_gapped_pos, train_start_pos)
    if has_val:
        val_start_pos = train_end_pos
        assert validation_size is not None
        val_end_pos = _fwd_pos(start_pos=val_start_pos, size=validation_size)

        if val_end_pos == val_start_pos == len(timeline): # 表明对验证集，推进失败（验证集为空），那么理由就是 first fold 的训练集吃光了所有数据。我们不允许这种情况发生。
            raise ValueError(f'fold 1 中 train set 独占了所有数据？检查输入。')
        
        val_end_gapped_pos = val_end_pos - embargo
        _check_end_gapped_after_embargo(val_end_gapped_pos, val_start_pos)

    test_start_pos = val_end_pos if has_val else train_end_pos # pyright: ignore[reportPossiblyUnboundVariable]
    test_end_pos = _fwd_pos(start_pos=test_start_pos, size=test_size)
    if test_end_pos <= test_start_pos:
        raise ValueError('timeline 数据长度不足以构造至少一个非空测试窗口。')

    fold = TemporalSplit(
        train=timeline[train_start_pos:train_end_gapped_pos],
        validation=timeline[val_start_pos:val_end_gapped_pos] if has_val else timeline[0:0],  # pyright: ignore[reportArgumentType, reportPossiblyUnboundVariable] 
        test=timeline[test_start_pos:test_end_pos]
    )
    splits.append(fold)

    if test_end_pos >= len(timeline): # If first fold reaches the end
        return splits

    # From the second fold and so on, make sure the next test set is adjacent to 
    # the previous test set. i.e. test_start_pos(k+1) = test_end_pos(k)
    # this is one of invariants that defines the term "walk-foward".
    while True:
        # first let's lay the test set
        test_start_pos = test_end_pos
        test_end_pos = _fwd_pos(test_start_pos, test_size)

        if test_end_pos <= test_start_pos: 
            raise ValueError('test_size 未能推进 test 窗口，请检查窗口是否为严格正 offset。')
        
        if has_val:
            # then go backward to lay the val set
            val_end_pos = test_start_pos
            val_end_gapped_pos = val_end_pos - embargo
            val_start_pos = _bwd_pos(val_end_pos, validation_size)  # pyright: ignore[reportArgumentType]
            _check_end_gapped_after_embargo(val_end_gapped_pos, val_start_pos)

        # then again go backward to lay the train set
        train_end_pos = val_start_pos if has_val else test_start_pos # pyright: ignore[reportPossiblyUnboundVariable]
        train_end_gapped_pos = train_end_pos - embargo
        if mode == 'rolling':
            train_start_pos = _bwd_pos(train_end_pos, train_size) 
        else:
            train_start_pos = 0
        _check_end_gapped_after_embargo(train_end_gapped_pos, train_start_pos)
        
        fold = TemporalSplit(
            train=timeline[train_start_pos:train_end_gapped_pos],
            validation=timeline[val_start_pos:val_end_gapped_pos] if has_val else timeline[0:0], # pyright: ignore[reportArgumentType, reportPossiblyUnboundVariable] 
            test=timeline[test_start_pos:test_end_pos]
        )
        splits.append(fold)

        if test_end_pos >= len(timeline): 
            break

    # Finally do invariant(postcondition) tests before function exit:
    # for all test set, test_start_pos(k+1) = test_end_pos(k)
    # first fold 的 test 已在上方保证非空（否则抛 ValueError），
    # 因此这里直接取 [-1] 是安全的。
    prev_test_end_ts = splits[0].test[-1]
    prev_test_end_pos = timeline.searchsorted(prev_test_end_ts, side='right')
    for i, fold in enumerate(splits[1:]):
        if fold.test.empty:
            raise RuntimeError(f'第 {i + 1} 折的 test 为空，违反 walk-forward 不变量。')
        j = i + 1
        test_start_ts = fold.test[0]
        test_start_pos = timeline.searchsorted(test_start_ts, side='left')
        if test_start_pos != prev_test_end_pos:
            raise RuntimeError(
                f'第 {j} 折的 test_start_pos={test_start_pos} 与前折 '
                f'prev_test_end_pos={prev_test_end_pos} 不相邻：'
                f'test_start_ts={test_start_ts}, prev_test_end_ts={prev_test_end_ts}。'
                '违反 walk-forward test 折连续不变量。'
            )
        prev_test_end_ts = fold.test[-1]
        prev_test_end_pos = timeline.searchsorted(prev_test_end_ts, side='right')
    
    return splits
