"""
读取日频快照数据

数据格式提示

日频快照数据，按照每个品种一个 parquet 文件存储：
    data/
    └── storage/
        └── daily_snapshots/
            ├── RB_daily.parquet
            ├── CU_daily.parquet
            ├── IF_daily.parquet
            └── ...
"""
import pandas as pd

class DailySnapshotLoader:
    """
    日频快照数据加载器。

    Parameters
    ------
    daily_snapshot_dir: str
        存放日频快照数据的目录路径。

    Methods
    ------
    load(product, columns=None)
        加载特定品种的日频快照数据，返回 DataFrame。
    """

    def __init__(self, daily_snapshot_dir: str):
        self.daily_snapshot_dir:str = daily_snapshot_dir

    def load(self, product: str, columns: list[str] | None = None) -> pd.DataFrame:
        """
        加载日频快照数据。

        Parameters
        ------
        product: str
            品种代码，例如'RB'。
        
        columns: list[str] | None, optional
            要加载的列名列表，如果为 None ，则加载所有列。

        Returns
        ------
        pd.DataFrame
            日频快照数据 DataFrame。
        """
        file_path = f'{self.daily_snapshot_dir}/{product}_daily.parquet'
        try:
            df = pd.read_parquet(file_path, columns=columns)
            return df
        except FileNotFoundError:
            raise FileNotFoundError(f'未找到 {product} 的日频快照数据文件，请检查路径：{file_path}')