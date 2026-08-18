"""预处理组件 —— 中性化类。"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Iterable, Literal, Mapping

import numpy as np
import pandas as pd
import statsmodels.api as sm

from factors.models import INDEX_NAMES, ResearchDataset
from factors.preprocess.base import (
        FactorPreprocessor, PreprocessingResult, 
        PreprocessingContext, clean_multiindex,
        PreprocessContextConstructor)

from utils.validation_utils import check_df_columns_exist

def _get_residual(df: pd.DataFrame, 
                 y_var: str, X_vars : list[str],
                 nocons: bool = False) -> tuple[pd.Series, int]:
    """
    获取 y_var 对 X_vars 回归后的残差。返回（残差序列，exit_code）。

    当出现以下两种情况时，返回全nan的残差序列。
    1. 按行剔除nan和inf后，变为空 DataFrame， exit_code = 1；
    2. 按行剔除nan和inf后，某列和截距完全共线， exit_code = 2；

    若以上未出现，则返回正常的回归残差序列，exit_code = 0
    """
    # 先剔除缺失值和 inf
    mask_nan_inf = df.isna().any(axis=1) | df.isin([np.inf, -np.inf]).any(axis=1)
    df_valid = df[~mask_nan_inf].copy()

    y = df_valid[y_var]
    X = df_valid[X_vars]
    index_valid = df_valid.index

    all_nans = pd.Series(np.full(len(df), np.nan), index=df.index) 
    if df_valid.empty: # 若为空，则返回全nan序列
        return all_nans, 1
    if not nocons:
        try:
            X = sm.add_constant(X, has_constant='raise')
        except ValueError as e:  #若出现某个 x 和截距项完全共线，不静默剔除该变量后回归，而是返回全空序列
            # print(e.args)
            return all_nans, 2
    
    # 使用 numpy 向量化方法计算 OLS 残差: resid = y - X @ beta
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    resid = y - X @ beta
    res = pd.Series(resid, index=index_valid, name=y_var)
    res = res.reindex(df.index) # 将被丢弃的那些行补回去，使用 默认的 nan 填充。

    return res, 0

def _assert_no_duplicated_cols(cols: list[str]) -> None:
    """断言：所输入的列名中无重复项。"""
    assert len(set(cols)) == len(cols)

def _get_residuals_and_warnings(reg_data: pd.DataFrame, 
                               y_var: str, X_vars: list[str],
                               nocons: bool = False) -> tuple[pd.Series, list[str]]:
    """返回（回归残差序列，警告列表）。"""
    cols = reg_data.columns.tolist()
    _assert_no_duplicated_cols(cols)

    grouped = reg_data.groupby(level='timestamp')
    res_list: list[pd.Series] = []
    code1_ts: list[pd.Timestamp] = []
    code2_ts: list[pd.Timestamp] = []
    for ts, group in grouped:
        res, exit_code = _get_residual(df=group, y_var=y_var, \
                                        X_vars=X_vars, nocons=nocons)
        res_list.append(res)
        if exit_code == 1:
            code1_ts.append(ts)
        elif exit_code == 2:
            code2_ts.append(ts)
    # 将残差序列拼接起来
    resid = pd.concat(res_list, axis=0)
    assert isinstance(resid, pd.Series)
    assert resid.index.names == INDEX_NAMES
    # 处理可能存在的警告信息
    warnings = []
    if code1_ts:
        w = f'以下截面出现按行剔除nan和inf后，变为空 DataFrame: {code1_ts}'
        warnings.append(w)
    if code2_ts:
        w = f'以下截面出现按行剔除nan和inf后，某列和截距完全共线: {code2_ts}'
        warnings.append(w)
    return resid, warnings


# --------------------------------------------
# 品种组中性化
# --------------------------------------------
class SectorDataConstructor(PreprocessContextConstructor):
    """品种分类数据构建器类。"""

    def __init__(self, dataset: ResearchDataset):
        super().__init__(dataset=dataset)

    def build(self) -> Mapping[str, pd.Series | pd.DataFrame]:
        """构建品种分类数据。返回一个字典，包含 classification 键，
        值为分类 DataFrame。
        """
        assert self._dataset.auxiliary is not None
        assert 'classification' in self._dataset.auxiliary

        class_df: pd.DataFrame = self._dataset.auxiliary['classification']
        check_df_columns_exist(
            class_df, 
            ['product', 'asset_class', 'sector', 'subsector'])
        return {'classification': class_df}

class SectorExposureNeutralizer(FactorPreprocessor):
    """
    品种组中性化类。
    """

    def __init__(
        self, 
        constructor: SectorDataConstructor,
        level: Literal['asset_class','sector', 'subsector'] = 'sector'
    ):
        self._constructor = constructor
        self._class_df = self._constructor.build()['classification']
        self._level = level
        self._warnings: list[str] = []
        self._metadata = {}

    @property
    def name(self) -> str:
        return f'sector_exposure_neutralizer_{self._level}'

    @property
    def parameters(self) -> dict[str, Any]:
        return {'level': self._level}

    def transform(self, values: pd.Series) -> PreprocessingResult:
        """对每个截面，将因子对品种组虚拟变量矩阵做回归，取残差。"""

        # 1. 首先检查输入values的所有品种是否都在分类表中
        products = values.index.get_level_values('product').unique()
        class_df_products = self._class_df['product']
        miss = self._miss_in_classification(products, class_df_products)
        if miss: # 这是数据问题，拒绝执行；必须先解决数据问题
            raise ValueError(f'品种 {miss} 不在分类表中，先补齐品种分类表。')

        # 2. 准备数据
        values = values.copy()

        dummies = pd.get_dummies(self._class_df.set_index('product')[self._level])
        reg_data = pd.merge(
            values.reset_index(), dummies,
            left_on='product', right_index=True,
            how='left'
        )
        reg_data.set_index(['timestamp', 'product'], inplace=True)
        X_vars = dummies.columns.tolist()
        y_var = values.name

        # 计算残差，并收集警告
        resid, warnings = _get_residuals_and_warnings(
            reg_data=reg_data, y_var=y_var, X_vars=X_vars, nocons=True
        )
        self._warnings.clear()
        self._warnings.extend(warnings)

        return PreprocessingResult(
            values = resid,
            warnings = self._warnings,
            metadata = self._metadata
        )
    
    def _miss_in_classification(self, products: Iterable[str], 
            class_df_products: Iterable[str]) -> list[str]:
        """返回products中那些不包含在class_df_products中的品种。"""
        miss = set(products) - set(class_df_products)
        return list(miss)

    
# --------------------------------------------
# 波动率中性化
# --------------------------------------------

class VolatilityDataConstructor(PreprocessContextConstructor):
    """波动率数据构建类。"""
    def __init__(self, dataset: ResearchDataset):
        super().__init__(dataset=dataset)

    def build(self, n: int) -> Mapping[str, pd.Series | pd.DataFrame]:
        """构建收益率的波动率， n 为历史窗口大小。"""
        ret = self._dataset.main_panel['True_Return']
        ret_shifted = ret.groupby(level='product').shift(1)
        prev_ret_vol = clean_multiindex(
            (ret_shifted
             .groupby(level='product')
             .rolling(window=n, min_periods=n)
             .agg('std')
             )
        )
        prev_ret_vol.name = 'prev_ret_vol'
        return {'prev_ret_vol': prev_ret_vol}

class VolatilityExposureNeutralizer(FactorPreprocessor):
    '''波动率中性化类。'''

    def __init__(
        self, 
        constructor: VolatilityDataConstructor,
        method: Literal['regression', 'devision'] = 'devision',
        n: int = 20 # 过去n个观测值窗口内计算波动率
    ):
        assert method in ('regression', 'devision'), \
            f'method 只能为 regression/devision，现在为 {method}。'
        assert n >= 2
        self._method = method
        self._constructor = constructor
        self._n = n
        self._vol= constructor.build(n=self._n)['prev_ret_vol']
        assert isinstance(self._vol, pd.Series)

        self._warnings: list[str] = []
        self._metadata = {}
        
    @property
    def name(self) -> str:
        return f'volatility_exposure_neutralizer_{self._method}_{self._n}'

    @property
    def parameters(self) -> dict[str, Any]:
        return {'method': self._method, 'n': self._n}

    def transform(self, values: pd.Series) -> PreprocessingResult:
        values = values.copy()

        df = pd.merge(values, self._vol, left_index=True, right_index=True, how='left')
        
        if self._method == 'regression':
            res, warnings = _get_residuals_and_warnings(
                reg_data=df, y_var=values.name, X_vars=['prev_ret_vol']
            )
            self._warnings.clear()
            self._warnings.extend(warnings)
        elif self._method == 'devision':
            _assert_no_duplicated_cols(df.columns.tolist())
            res = df[values.name] / df['prev_ret_vol']
        else:
            raise ValueError(f'非法 method: {self._method}。')
        
        return PreprocessingResult(
            values = res, 
            warnings=self._warnings,
            metadata=self._metadata
        )
# --------------------------------------------
# 流动性中性化
# --------------------------------------------
class LiquidityDataConstructor(PreprocessContextConstructor):
    """流动性数据构建器类。"""
    def __init__(self, dataset: ResearchDataset):
        super().__init__(dataset=dataset)

    def build(
            self, 
            vars: list[str] = ['Volume'] 
    ) -> Mapping[str, pd.Series | pd.DataFrame]:
        liquidity_df = self._dataset.main_panel[vars]
        return {'liquidity': liquidity_df}


class LiquidityExposureNeutralizer(FactorPreprocessor):
    """流动性中性化类。"""
    def __init__(
            self, 
            constructor: LiquidityDataConstructor,
            liquidity_vars: list[str] = ['Volume'],
    ):
        self._vars = liquidity_vars
        self._constructor = constructor
        self._liquidity_df = self._constructor.build(vars=self._vars)['liquidity']

        self._warnings: list[str] = []
        self._metadata = {}

    @property
    def name(self) -> str:
        return f'liquidity_exposure_neutralizer_{self._vars}'

    @property
    def parameters(self) -> dict[str, Any]:
        return {'liquidity_vars': self._vars}

    def transform(self, values: pd.Series) -> PreprocessingResult:
        values = values.copy()

        df = pd.merge(values, self._liquidity_df, left_index=True, right_index=True,
                      how='left')

        # 将流动性变量都取对数，遇到0则设为缺失值
        df[self._vars] = df[self._vars].replace({0: np.nan}).apply(np.log)

        res, warnings = _get_residuals_and_warnings(
            reg_data=df, y_var=values.name, X_vars=self._vars
        )
        self._warnings.clear()
        self._warnings.extend(warnings)

        return PreprocessingResult(
            values = res,
            warnings = self._warnings,
            metadata = self._metadata
        )


# ======================================
# inline test
# ======================================
if __name__ == '__main__':
    # ============ 公共 fixtures ============
    rng = np.random.default_rng(0)
    ts = pd.date_range('2026-07-01', periods=15, freq='D')
    products = ['RB', 'CU', 'AP', 'A']
    idx = pd.MultiIndex.from_product([ts, products], names=['timestamp', 'product'])
    n = len(idx)

    main_panel = pd.DataFrame({
        'True_Return': rng.normal(0.0005, 0.01, n),
        'Volume': rng.integers(1000, 5000, n).astype(float),
    }, index=idx)
    class_df = pd.DataFrame({
        'product': products,
        'asset_class': ['black', 'nonferrous', 'agri', 'agri'],
        'sector': ['black', 'nonferrous', 'agri', 'agri'],
        'subsector': ['steel', 'copper', 'grain', 'oilseed'],
    })
    gmap = {'RB': 'black', 'CU': 'nonferrous', 'AP': 'agri', 'A': 'agri'}

    def make_fake(main_panel=main_panel, class_df=class_df):
        return type('FakeDataset', (), {
            'auxiliary': {'classification': class_df},
            'main_panel': main_panel})()

    fake = make_fake()
    v = pd.Series(rng.normal(0, 1, n), index=idx, name='f')

    # ============ 1. _get_residual / _get_residuals_and_warnings ============
    print('========== _get_residual / _get_residuals_and_warnings ==========')
    tsU = pd.date_range('2026-07-01', periods=1, freq='D')
    idxU = pd.MultiIndex.from_product([tsU, products], names=['timestamp', 'product'])
    dfU = pd.DataFrame({'f': [1., 2., 3., np.nan],
                        'vol': [1., 2., 3., np.nan]}, index=idxU)

    res, code = _get_residual(dfU, 'f', ['vol'], nocons=False)
    assert code == 0 and res.index.equals(dfU.index), 'nocons=False 索引/exit_code 异常'
    assert np.isnan(res.loc[('2026-07-01', 'A')]), 'NaN 行应补回 NaN'
    assert np.allclose(res.dropna(), 0, atol=1e-9), 'f=vol 完全拟合残差应为 0'

    res, code = _get_residual(dfU, 'f', ['vol'], nocons=True)
    assert code == 0
    valid = dfU['vol'].notna()
    beta = (dfU.loc[valid, 'vol'] @ dfU.loc[valid, 'f']) / \
        (dfU.loc[valid, 'vol'] @ dfU.loc[valid, 'vol'])
    exp = dfU.loc[valid, 'f'] - beta * dfU.loc[valid, 'vol']
    assert np.allclose(res.loc[valid], exp, atol=1e-9), 'nocons=True 应等于无截距 lstsq 残差'
    assert np.isnan(res.loc[('2026-07-01', 'A')])
    print('  PASS: _get_residual nocons=False(截距) / nocons=True(无截距) / NaN 补回')

    dfE = pd.DataFrame({'f': [np.nan, np.nan], 'vol': [np.nan, np.nan]}, index=idxU[:2])
    res, code = _get_residual(dfE, 'f', ['vol'])
    assert code == 1 and res.isna().all(), '空截面应 exit_code=1 且全 NaN'
    print('  PASS: 空截面 exit_code=1')

    dfC = pd.DataFrame({'f': [1., 2., 3.], 'x': [5., 5., 5.]}, index=idxU[:3])
    res, code = _get_residual(dfC, 'f', ['x'], nocons=False)
    assert code == 2 and res.isna().all(), '常数 X 与截距共线应 exit_code=2'
    print('  PASS: 常数 X 共线 exit_code=2')

    tsW = pd.date_range('2026-07-01', periods=2, freq='D')
    idxW = pd.MultiIndex.from_product([tsW, ['RB', 'CU']], names=['timestamp', 'product'])
    dfW = pd.DataFrame({'f': [1., 2., 3., np.nan],
                        'vol': [1., 2., np.nan, np.nan]}, index=idxW)
    rW, warnsW = _get_residuals_and_warnings(dfW, 'f', ['vol'], nocons=False)
    assert rW.index.equals(dfW.index), '_get_residuals_and_warnings 索引不一致'
    assert np.allclose(rW.loc[tsW[0]].values, 0, atol=1e-9), '07-01 残差应为 0'
    assert rW.loc[tsW[1]].isna().all(), '07-02 全 NaN 截面应为 NaN'
    assert len(warnsW) == 1 and '空 DataFrame' in warnsW[0] and str(tsW[1]) in warnsW[0], warnsW
    print('  PASS: _get_residuals_and_warnings 索引一致 + code1 警告条件生成')

    # ============ 2. SectorExposureNeutralizer ============
    print('========== SectorExposureNeutralizer ==========')
    nz = SectorExposureNeutralizer(SectorDataConstructor(fake), level='sector')
    r = nz.transform(v)
    assert r.values.index.equals(idx), 'Sector 输出索引不一致'
    for t in ts:
        cross = v.xs(t, level='timestamp')
        grp = cross.groupby([gmap[p] for p in cross.index]).transform('mean')
        assert np.allclose(r.values.xs(t, level='timestamp').sort_index().values,
                           (cross - grp).sort_index().values, atol=1e-9), \
            f'Sector 残差错误 @ {t}'
    assert nz.name == 'sector_exposure_neutralizer_sector'
    assert nz.parameters == {'level': 'sector'}
    assert r.warnings == []
    print('  PASS: 残差 == x - sector 均值，索引/name/parameters/warnings 正确')

    v2 = pd.concat([v, pd.Series([1.0], index=pd.MultiIndex.from_product(
        [[ts[-1]], ['ZZ']], names=['timestamp', 'product']), name='f')])
    try:
        nz.transform(v2)
        raise AssertionError('缺品种未报错')
    except ValueError as e:
        assert 'ZZ' in str(e), str(e)
        print('  PASS: 缺品种抛出 ValueError')

    v3 = v.copy()
    v3.loc[(ts[9],)] = np.nan
    r3 = SectorExposureNeutralizer(SectorDataConstructor(fake), level='sector').transform(v3)
    assert r3.values.index.equals(idx), 'Sector(全NaN截面) 索引不一致'
    assert any('空 DataFrame' in w for w in r3.warnings), r3.warnings
    print('  PASS: 全 NaN 截面生成 code1 警告')

    # ============ 3. VolatilityExposureNeutralizer ============
    print('========== VolatilityExposureNeutralizer ==========')
    warmup = pd.date_range('2026-07-01', periods=5, freq='D')
    # devision
    nz = VolatilityExposureNeutralizer(VolatilityDataConstructor(fake), method='devision', n=5)
    r = nz.transform(v)
    assert r.values.index.equals(idx), 'Vol devision 索引不一致'
    assert r.values.loc[warmup].isna().all(), 'Vol devision 暖机行应为 NaN'
    assert r.warnings == [], r.warnings
    vol = nz._vol
    valid = vol.notna() & (vol != 0)
    got_dev = r.values[valid].sort_index()
    exp_dev = (v[valid] / vol[valid]).sort_index()
    assert np.allclose(got_dev, exp_dev, atol=1e-9), 'devision 数值错误'
    print('  PASS: devision values/vol 正确，暖机 NaN，warnings 空')

    # regression
    nz = VolatilityExposureNeutralizer(VolatilityDataConstructor(fake), method='regression', n=5)
    r = nz.transform(v)
    assert r.values.index.equals(idx), 'Vol regression 索引不一致'
    assert r.values.loc[warmup].isna().all(), 'Vol regression 暖机行应为 NaN'
    for t in ts[5:]:
        cross = pd.DataFrame({'f': v.xs(t, level='timestamp'),
                              'vol': nz._vol.xs(t, level='timestamp')}).dropna()
        exp = sm.OLS(cross['f'], sm.add_constant(cross['vol'])).fit().resid
        got = r.values.xs(t, level='timestamp').dropna().sort_index()
        assert np.allclose(got.values, exp.sort_index().values, atol=1e-9), \
            f'Vol regression 残差错误 @ {t}'
    assert any('空 DataFrame' in w for w in r.warnings), r.warnings
    print('  PASS: regression 残差与 sm.OLS+截距 一致，暖机 code1 警告生成')

    # ============ 4. LiquidityExposureNeutralizer ============
    print('========== LiquidityExposureNeutralizer ==========')
    nz = LiquidityExposureNeutralizer(LiquidityDataConstructor(fake))
    r = nz.transform(v)
    assert r.values.index.equals(idx), 'Liquidity 索引不一致'
    logvol = np.log(main_panel['Volume'])
    for t in ts:
        cross = pd.DataFrame({'f': v.xs(t, level='timestamp'),
                              'lvol': logvol.xs(t, level='timestamp')}).dropna()
        exp = sm.OLS(cross['f'], sm.add_constant(cross['lvol'])).fit().resid
        got = r.values.xs(t, level='timestamp').dropna().sort_index()
        assert np.allclose(got.values, exp.sort_index().values, atol=1e-9), \
            f'Liquidity 残差错误 @ {t}'
    assert r.warnings == [], r.warnings
    print('  PASS: 残差与 sm.OLS+log(Volume)+截距 一致，warnings 空')

    mp0 = main_panel.copy()
    mp0.loc[(ts[7], 'RB'), 'Volume'] = 0.0
    r = LiquidityExposureNeutralizer(LiquidityDataConstructor(make_fake(main_panel=mp0))).transform(v)
    assert r.values.index.equals(idx), 'Liquidity(Volume=0) 索引不一致'
    assert np.isnan(r.values.loc[(ts[7], 'RB')]), 'Volume=0 行应补 NaN'
    print('  PASS: Volume=0 行补 NaN')

    mp_nan = main_panel.copy()
    mp_nan.loc[(ts[9], 'CU'), 'Volume'] = np.nan
    r = LiquidityExposureNeutralizer(LiquidityDataConstructor(make_fake(main_panel=mp_nan))).transform(v)
    assert r.values.index.equals(idx), 'Liquidity(Volume=NaN) 索引不一致'
    assert np.isnan(r.values.loc[(ts[9], 'CU')]), 'Volume=NaN 行应补 NaN'
    print('  PASS: Volume=NaN 行补 NaN')

    mp_collin = main_panel.copy()
    for p in products:
        mp_collin.loc[(ts[8], p), 'Volume'] = 777.0
    r = LiquidityExposureNeutralizer(LiquidityDataConstructor(make_fake(main_panel=mp_collin))).transform(v)
    assert r.values.index.equals(idx), 'Liquidity(共线) 索引不一致'
    assert any('共线' in w for w in r.warnings), r.warnings
    print('  PASS: 常数 log(Volume) 共线生成 code2 警告')

    print('全部 inline test 通过。')

    

    


