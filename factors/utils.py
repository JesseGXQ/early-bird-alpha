import pandas as pd
from data.factory import _create_trading_rule_components
from data.config import EnvironmentConfig
from data.calendar.product_calendar import ProductCalendar

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