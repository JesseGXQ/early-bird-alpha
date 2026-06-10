import pandas as pd

def check_df_columns_exist(df: pd.DataFrame, required_columns: list[str]):
    """检查所需列是否存在于 DataFrame 中，若任一或多个列不存在，抛出 ValueError。"""
    cols = set(required_columns)
    missing_columns = cols - set(df.columns)
    
    if missing_columns:
        raise ValueError(f"Missing required columns: {missing_columns}")


