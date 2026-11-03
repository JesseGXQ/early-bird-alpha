from __future__ import annotations

from math import sqrt
from typing import TYPE_CHECKING, Any, Mapping
import pandas as pd

from utils.datetime_utils import annualization_factor
from factors.preprocess.base import clean_multiindex

from factors.models import LabelSpec, LabelResult
if TYPE_CHECKING:

    from factors.models import ResearchDataset  

class LabelBuilder:

    def __init__(
        self, 
        specs: LabelSpec | tuple[LabelSpec, ...], 
        dataset: ResearchDataset
        ):
        # 统一 specs 的格式为 tuple of LabelSpec
        if isinstance(specs, LabelSpec): 
            self._specs = (specs, )
        else:
            self._specs = tuple(specs)
        self._dataset = dataset

    def compute(self) -> LabelResult:
        label_collection: list[pd.Series] = []
        for spec in self._specs:
            lab = self._compute_spec(spec=spec)
            label_collection.append(lab)

        # 拼接起来
        labels = pd.concat(label_collection, axis=1)
        assert isinstance(labels, pd.DataFrame)

        return LabelResult(
            labels = labels,
            specs = self._specs,
        )

    def _compute_spec(self, spec: LabelSpec) -> pd.Series:
        if spec.type == 'forward_return':
            return self._compute_forward_return(spec=spec)
        elif spec.type == 'direction':
            return self._compute_direction(spec=spec)
        elif spec.type == 'future_volatility':
            return self._compute_future_volatility(spec=spec)
        elif spec.type == 'event':
            return self._compute_event(spec=spec)
        raise ValueError(f'非法 method: {spec.type}')

    def _compute_forward_return(self, spec: LabelSpec) -> pd.Series:
        """计算未来收益率。label_h[t] = product(1 + True_Return[t+1:t+h]) - 1"""
        ret = self._dataset.main_panel['True_Return']
        h = spec.horizon
        # 对每个(product, timestamp)，将未来1至h期的收益率拼接过来形成宽表。
        # 即对(i,t)，拼接 r[i, t+1], r[i, t+2], ... r[i, t+h]
        multi_forward_ret_wide_df = pd.concat(
            [(1 + ret).groupby('product').shift(-i) for i in range(1, h + 1)],
            axis=1,
        )
        return multi_forward_ret_wide_df \
            .prod(axis=1, skipna=True, min_count=spec.min_valid_obs) - 1

    def _compute_direction(self, spec: LabelSpec) -> pd.Series:
        """
        计算未来收益率的方向：上涨(1)，持平(0)，下跌(-1)。
        
        可通过 spec.parameters 设置 upper_threshold 和 lower_threshold 。
        r > upper_threshold -> 1; r < lower_threshold -> -1; else 0

        upper_threshold 和 lower_threshold 默认值为 0.0 。
        """
        forward_ret = self._compute_forward_return(spec=spec)

        def convert_with_thres(
                x: float, 
                upper_thres: float = 0.0, 
                lower_thres: float = 0.0):
            """根据上下边界，将收益率三值化。"""
            if x > upper_thres: return 1
            elif x < lower_thres: return -1
            return 0

        if spec.parameters is not None:
            upper_thres = spec.parameters['upper_threshold']
            lower_thres = spec.parameters['lower_threshold']
            direction = forward_ret.apply(
                convert_with_thres, 
                upper_thres=upper_thres, lower_thres=lower_thres)
        else:
            direction = forward_ret.apply(convert_with_thres)
        return direction

    def _compute_future_volatility(self, spec: LabelSpec) -> pd.Series:
        """计算未来实现波动率，默认转换为年化。"""
        freq = self._dataset.spec.freq 
        ret = self._dataset.main_panel['True_Return']
        h = spec.horizon
        assert h >=2, f'计算未来波动率，horizon 必须大于等于2，现在为 {h}。'
        # 未来1至h期的收益率拼接宽表。
        multi_forward_ret_wide_df = pd.concat(
            [(1 + ret).groupby('product').shift(-i) for i in range(1, h + 1)],
            axis=1,
        )    
        future_vol = multi_forward_ret_wide_df.std(axis=1)
        # 若标签已配置无需年化
        if spec.parameters is not None and spec.parameters['annualize'] == False:
            return future_vol
        # 否则默认转换为年化 
        N = annualization_factor(freq) # 默认配置：一年252个交易日，每个交易日360分钟（简化） 
        return future_vol * sqrt(N) 

    def _compute_event(self, spec: LabelSpec) -> pd.Series:
        """计算事件标签。待实现。"""
        # 需要判定规则，窗口参数。
        # 可能需要额外数据集、计算参数
        ...

# ====================================
# inline test
# ====================================
if __name__ == '__main__':
    pass




        



    