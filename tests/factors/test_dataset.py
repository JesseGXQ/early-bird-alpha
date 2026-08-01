"""

tests/factors/test_dataset.py — 测试计划

本文件暂不包含实际测试代码。以下是待编写测试的完整规划。

============================================================================
测试数据约定
============================================================================

所有核心测试使用小型人工 DataFrame，不依赖本地行情文件。
需要 conftest.py 中提供 FakeDataFacade 夹具，或直接在测试函数中构造。

FakeDataFacade.get_historical_vector 返回如下格式的单品种 DataFrame：

    Index: calendar_time (DatetimeIndex)
    Columns: trading_date, Dominant_Contract, Adj_Open, Adj_High, Adj_Low,
             Adj_Close, Raw_Open, Raw_High, Raw_Low, Raw_Close,
             Volume, OpenInterest, True_Return, is_roll

============================================================================
待编写测试
============================================================================

=== 1. test_normalize_product_code ===
    测试 _normalize_product_code 的边界情况
    用例:
    - 输入 ' rb ' → 输出 'RB'
    - 输入 'm' → 输出 'M'
    - 输入 '  ' → 抛出 ValueError
    预期: 全部通过

=== 2. test_check_single_product_result ===
    测试单品种结果校验
    用例:
    - 空 DataFrame → ValueError("品种 X 返回空数据...")
    - 索引存在重复值 → ValueError("品种 X 的索引存在重复值")
    - 缺少 REQUIRED_COLUMNS 中的列（如缺少 Volume） → ValueError
    - 完整有效帧 → 不抛出异常
    预期: 全部通过

=== 3. test_validate_research_frame_success ===
    合法面板校验
    输入: 两个品种、交易日不完全重叠、MultiIndex[timestamp, product]
    预期: is_valid=True, total_rows > 0

=== 4. test_validate_research_frame_not_multiindex ===
    非 MultiIndex 输入
    输入: 单品种 DatetimeIndex
    预期: is_valid=False, errors 提示 "不是 MultiIndex"

=== 5. test_validate_research_frame_wrong_index_names ===
    MultiIndex 名称不正确
    输入: MultiIndex 名称为 ('date', 'symbol')
    预期: is_valid=False, errors 提示名称不合规

=== 6. test_validate_research_frame_duplicate_index ===
    索引重复
    输入: 同一 (timestamp, product) 出现两次
    预期: is_valid=False, errors 提示重复索引

=== 7. test_validate_research_frame_missing_columns ===
    缺少必需字段
    输入: 缺少 True_Return 列
    预期: is_valid=False, errors 提示缺失字段

=== 8. test_validate_research_frame_non_upper_product ===
    品种代码非全大写
    输入: product='rb'（小写）
    预期: is_valid=True, warnings 提示非全大写品种代码

=== 9. test_validate_research_frame_trading_date_non_monotonic ===
    trading_date 倒退
    输入: 同一品种内 trading_date 出现倒退（如 2020-01-03, 2020-01-02）
    预期: is_valid=False, errors 提示 trading_date 不递增

=== 10. test_validate_research_frame_negative_volume ===
    Volume 存在负值
    输入: Volume 列中含 -100
    预期: is_valid=True, warnings 提示负值行数 = 1

=== 11. test_validate_research_frame_negative_open_interest ===
    OpenInterest 存在负值
    输入: OpenInterest 列中含 -1
    预期: is_valid=True, warnings 提示负值行数 = 1

=== 12. test_validate_research_frame_missing_rate ===
    缺失率统计正确
    输入: True_Return 列 10 行中有 2 行为 NaN
    预期: missing_rate_by_column['True_Return'] == 0.2

=== 13. test_validate_research_frame_product_sample_counts ===
    各品种样本数统计
    输入: 品种 A 3 行，品种 B 2 行
    预期: product_sample_counts == {'A': 3, 'B': 2}

=== 14. test_builder_empty_products ===
    空品种列表
    输入: DatasetSpec(products=())
    预期: ValueError("DatasetSpec.products 不能为空")

=== 15. test_builder_none_dates ===
    日期为 None
    输入: DatasetSpec(products=('RB',), start_date=None)
    预期: ValueError("DatasetSpec.start_date 不能为 None")

=== 16. test_builder_two_products_partial_overlap ===
    两个品种交易日不完全重叠
    使用 FakeDataFacade 模拟:
    - 品种 A: 2020-01-02 ~ 2020-01-05
    - 品种 B: 2020-01-03 ~ 2020-01-06
    预期:
    - 合并后 DataFrame 有 8 行（A 4 行 + B 4 行）
    - 缺失日期自然保留为 NaN（不前向填充）
    - MultiIndex[timestamp, product] 名称正确
    - universe 为 all-True

=== 17. test_builder_single_product_empty_frame ===
    某品种返回空帧
    使用 FakeDataFacade 模拟某品种 get_historical_vector 返回空帧
    预期: ValueError(提示该品种返回空数据)

=== 18. test_builder_spec_preserved ===
    返回的 ResearchDataset.spec 与传入一致
    预期: result.spec is spec（或值相等）

============================================================================
实现依赖
============================================================================

conftest.py 需要提供:

    class FakeDataFacade:
        \"\"\"
        模拟 DataFacade，允许按品种注册不同的返回 DataFrame。
        \"\"\"
        def __init__(self):
            self._data: dict[str, pd.DataFrame] = {}

        def register(self, product: str, df: pd.DataFrame):
            self._data[product] = df.copy()

        def get_historical_vector(
            self, product, start_date, end_date, freq='1d', view='scheduled'
        ) -> pd.DataFrame:
            df = self._data.get(product)
            if df is None:
                raise ValueError(f"未注册品种: {product}")
            # 按日期范围过滤
            mask = (df['trading_date'] >= start_date) & (df['trading_date'] <= end_date)
            return df.loc[mask].copy()

    # conftest 中提供以下夹具:
    @pytest.fixture
    def fake_facade():
        return FakeDataFacade()

    @pytest.fixture
    def sample_two_product_frames():
        \"\"\"构建 A 和 B 两个品种的合法日频 DataFrame\"\"\"
        ...
        return {'A': df_a, 'B': df_b}

"""

# 未来实现:
#
# from factors.dataset import (
#     ResearchDatasetBuilder,
#     validate_research_frame,
#     _normalize_product_code,
#     _check_single_product_result,
# )
# from factors.models import DatasetSpec
# import pandas as pd
# import pytest
