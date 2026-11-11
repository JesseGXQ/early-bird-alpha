from typing import Literal

import pytest

from factors.evaluation.ic import fast_cs_ic
import numpy as np
import pandas as pd

def _build_factor_label_eligible(
        return_as: Literal['Series', 'DataFrame'] = 'Series'
) -> tuple[pd.Series, pd.Series, pd.Series] | tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    index = pd.MultiIndex.from_arrays(
                [
                    [pd.Timestamp('2025-05-10')] * 4, 
                    ['prod1', 'prod2', 'prod3', 'prod4']
                ],
                names=['timestamp', 'product']
    )
    factor = pd.Series(
        [0.02, 0.01, np.nan, 0.03],
        index=index
    )
    label = pd.Series(
        [-0.01, -0.02, 0.03, -0.05], index=index
    )
    elig_index = pd.MultiIndex.from_arrays(
        [
            [pd.Timestamp('2025-05-10')]*5,
            ['prod5', 'prod1', 'prod2', 'prod3', 'prod4']
        ],
        names=['timestamp', 'product']
    )
    eligible = pd.Series([False, True, True, True, True], index=elig_index)
    #print(factor, '\n' , label, '\n', eligible)
    if return_as == 'Series':
        return factor, label, eligible
    return factor.unstack(), label.unstack(), eligible.unstack()

def test_fast_cs_ic_example_input_as_Series():
    factor, label, eligible = _build_factor_label_eligible()
    ic = fast_cs_ic(factor, label, eligible, False)
    ic_correct = -0.720576692122892
    assert ic.iloc[0] == pytest.approx(ic_correct)

def test_fast_cs_ic_example_input_as_DataFrame():
    factor, label, eligible = _build_factor_label_eligible('DataFrame')
    #print(factor)
    ic = fast_cs_ic(factor, label, eligible, False)
    ic_correct = -0.720576692122892
    assert ic.iloc[0] == pytest.approx(ic_correct)    