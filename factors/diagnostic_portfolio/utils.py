from __future__ import annotations
import pandas as pd

def inspect_signal_quality(
        signal: pd.Series,
        eligible: pd.Series[bool] | None = None,
) -> pd.DataFrame:
    """检查各品种的信号数据质量。可选限定在品种池内。
    
    Parameters
    ------
    signal: pd.Series
        信号数据。MultiIndex[timestamp, product]。
    eligible: pd.Series, optional
        品种池准入条件。默认为 None。

    Returns
    ------
    pd.DataFrame
        各品种的信号质量。Index[product], columns: n_valid, n_misisng, n_total,
        start_date, end_date,
        first_valid, last_valid, mean, p1, p5, p25, p50, p75, p95, p99, 
        min, max
    """
    def sig_quality_one_prod(values: pd.Series) -> pd.Series: # values: Index[timestamp]
        v = values
        return pd.Series(
            {
                'n_valid': v.notna().sum(),
                'n_missing': v.isna().sum(),
                'n_total': len(v),
                'start_date': v.index[0],
                'end_date': v.index[-1],
                'first_valid': v[v.notna()].index[0],
                'last_valid': v[v.notna()].index[-1],
                'p1': v.quantile(0.01),
                'p5': v.quantile(0.05),
                'p25': v.quantile(0.25),
                'p50': v.quantile(),
                'p75': v.quantile(0.75),
                'p99': v.quantile(0.99),
                'min': v.min(),
                'max': v.max()
            },
        )
    if eligible is not None:
        assert signal.index.isin(eligible.index).all(), '信号索引中存在不属于品种池索引的元素。'
        signal = signal.loc[eligible[eligible].index]
    quality_multi = signal.groupby(level='product').apply(sig_quality_one_prod) # this is a series with MultiIndex [('A', 'n_valid'),('A', 'n_missing'),...]
    return quality_multi.unstack(level=-1)