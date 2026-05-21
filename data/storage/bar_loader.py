import pandas as pd
from collections import OrderedDict

class BarLoader:
    """
    K 线数据加载器

    将文件读取进内存，输出 DataFrame 。内部带 LRU (Least Recently Used) 缓存机制。

    Parameters
    ------
    max_cache_size: int, optional
        LRU 缓存的最大条目数，默认值为 100。超过此限制时，最久未使用的缓存条目将被自动移除。
    
    Methods
    ------
    load_1min_bar(file_path, normalize_col_names=True)
        读取 1min K 线数据文件，返回标准化的 DataFrame。包含 Open, High, Low, Close, Volume, OpenInterest 列，索引为 DatetimeIndex。

    """
    def __init__(self, max_cache_size: int = 100):
        self.max_cache_size = max_cache_size
        self._cache: OrderedDict[str, pd.DataFrame] = OrderedDict()
    
    def _normalize_col_names(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        标准化列名

        映射为：{'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume', 'open_interest': 'OpenInterest', 'datetime': 'Datetime'}
        """
        if df.empty:
            return df

        col_map = {
            'open': 'Open',
            'high': 'High',
            'low': 'Low',
            'close': 'Close',
            'volume': 'Volume',
            'open_interest': 'OpenInterest',
            'money': 'Money',
            'datetime': 'Datetime'
        }

        # 将列名转换为小写，然后映射到标准列名
        df.columns = [col_map.get(col.lower(), col) for col in df.columns]

        return df

    def load_1min_bar(self, 
                      file_path: str,
                      normalize_col_names: bool = True
                      ) -> pd.DataFrame:
        """
        读取 1min 原始数据

        Parameters
        -----
        file_path: str
            1min K 线数据路径
        normalize_col_names: bool, default True
            标准化列名，统一为 Open, High, Low, Close, Volume, OpenInterest。索引: Datetime 。
        
        Returns
        ------
        pd.DataFrame
            1min K 线数据，包含 Open, High, Low, Close, Volume, OpenInterest。索引为pd.DatatimeIndex。

        """
        if not file_path: # file_path 为空字符串
            return pd.DataFrame()
        
        # 1. 若命中缓存
        if file_path in self._cache:
            self._cache.move_to_end(file_path)
            return self._cache[file_path].copy() #返回副本，防止外部修改
        
        # 2. 若未命中缓存，执行物理I/O
        try:
            if file_path.endswith('.parquet'):
                df = pd.read_parquet(file_path)
            elif file_path.endswith('.csv'):
                df = pd.read_csv(file_path)
            else: #既非 parquet， 也非 csv
                raise ValueError(f'【BarLoader】不支持的文件格式 {file_path}，目前仅支持 Parquet 和 CSV。')
        except Exception as e:
            raise RuntimeError(f'【BarLoader】读取文件 {file_path} 失败: {e}')
        
        # 标准化列名
        if normalize_col_names:
            df = self._normalize_col_names(df)

        # 设置索引，并按时间排序
        if 'Datetime' in df.columns:
            df.index = pd.to_datetime(df['Datetime'])
            df = df.drop('Datetime', axis=1)
        elif 'datetime' in df.columns:
            df.index = pd.to_datetime(df['datetime'])
            df = df.drop('datetime', axis=1)
        else:
            raise ValueError(f'【BarLoader】未找到时间列 (datetime 或 Datetime)，无法设置索引。请检查文件 {file_path} 的列名。')
        
        df = df.sort_index() # 按时间排序
        
        # 将数据添加到缓存
        self._cache[file_path] = df 
        # 判断是否超出缓存数量
        if len(self._cache) > self.max_cache_size:
            self._cache.popitem(last=False) #踢掉最陈旧的一个未使用数据
        
        return df.copy() # 返回副本，防止外部修改
    



