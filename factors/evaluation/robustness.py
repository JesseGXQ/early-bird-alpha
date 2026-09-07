from __future__ import annotations

from typing import Literal
import pandas as pd
import numpy as np

from statsmodels.stats.multitest import multipletests


def adjust_pvalues(
    pvalues: pd.Series,
    alpha: float = 0.05,
    method: str = "fdr_bh",
) -> pd.DataFrame:
    """校正有效的原始 p-value，并保留全部检验元数据。"""

    result = pvalues.rename('raw_pvalue').to_frame()
    valid = result["raw_pvalue"].notna()
    n_tests = int(valid.sum())

    result["adjusted_pvalue"] = pd.NA
    result["rejected"] = False
    # result["correction_method"] = method
    # result["alpha"] = alpha
    result["n_tests"] = n_tests

    if n_tests == 0:
        return result

    rejected, adjusted, _, _ = multipletests(
        result.loc[valid, "raw_pvalue"].to_numpy(),
        alpha=alpha,
        method=method,
    )

    result.loc[valid, "adjusted_pvalue"] = adjusted
    result.loc[valid, "rejected"] = rejected
    return result
