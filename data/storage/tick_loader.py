import pandas as pd
import os
import pyarrow as pa
import pyarrow.parquet as pq
import time
from typing import Generator

from abc import ABC, abstractmethod

class DataLoaderBase(ABC):
    '''
    数据加载器抽象基类(The standard Socket)
    为所有的 Tick 数据加载器制定强制性的接口契约。
    '''

    @abstractmethod
    def get_tick_df_chunk_stream(self) -> Generator[pd.DataFrame, None, None]:
        """
        返回一个生成器，每次吐出一个原始 TICK 数据的 DataFrame。
        """
        pass

class CSVTickLoader(DataLoaderBase):
    """
    CSV 专属数据加载器
    专门负责对接本地 CSV 格式的 TICK 数据，提供防爆内存的流式读取能力。
    """

    def __init__(self, filepath: str, chunk_size: int=100000):
        """
        初始化加载器，预先检查文件是否健康。
        """
        self.filepath=filepath
        self.chunk_size=chunk_size

        if not os.path.exists(self.filepath):
            raise FileNotFoundError(f'【Data Engine】找不到数据文件: {self.filepath}')
        
    def _stream_data(self):
        """
        核心活塞函数
        按块读取 CSV 并 yield 吐出数据。
        """
        try:
            csv_iterator = pd.read_csv(self.filepath, chunksize=self.chunk_size)
            for chunk_df in csv_iterator:
                yield chunk_df
        except Exception as e:
            raise RuntimeError(f'读取 CSV 文件时发生物理损坏或错误： {e}')
    
    def get_tick_df_chunk_stream(self):

        return self._stream_data()
    
class TickDataConverter:
    """
    极速格式转换器 (The "Compressor")
    将低效的 CSV 文件流式转换为高效的 Parquet 列示存储文件。
    """
    @staticmethod
    def csv_to_parquet_stream(csv_filepath: str, parquet_filepath: str, chunk_size: int=100000):
        """
        核心装罐流水线。
        staticmethod是因为不需要保存类状态，就是一个纯粹的加工厂。
        """
        print('开始启动数据压缩流水线...')
        print(f'源文件：{csv_filepath}')
        print(f'目标文件：{parquet_filepath}')

        start_time = time.time()

        loader = CSVTickLoader(filepath=csv_filepath, chunk_size=chunk_size)
        tick_df_chunk_stream = loader.get_tick_df_chunk_stream()

        writer = None
        total_rows = 0 

        try:
            # 核心循环：抽一桶，压一桶
            for i, chunk_df in enumerate(tick_df_chunk_stream):
                # parquet：所有列名需要为字符串
                chunk_df.columns = chunk_df.columns.astype(str)

                #将 Pandas DataFrame 转换为 PyArrow 支持的 Table 格式
                table = pa.Table.from_pandas(chunk_df)

                #如果是第一批数据，需要基于其结构(Schema)来初始化装罐机
                if i == 0:
                    #创建 Parquet 写入器，以追加模式工作
                    writer = pq.ParquetWriter(parquet_filepath, table.schema)
                    print('成功初始化 Parquet Schema, 开始流式写入...')
                
                writer.write_table(table)

                rows_in_chunk = len(chunk_df)
                total_rows += rows_in_chunk

                #实时打印进度
                if (i+1) % 5 == 0:
                    print(f'...已处理{i+1}批，累计写入{total_rows}行数据')
        
        except Exception as e:
            raise RuntimeError(f'转换过程中出现断裂：{e}')
        
        finally:
            if writer is not None:
                writer.close()
        
        end_time = time.time()
        print(f'转换完成！ 总计写入{total_rows}行，耗时{end_time - start_time: .2f}秒。')

class ParquetTickLoader(DataLoaderBase):
    """
    Parquet专属极速数据加载器(The "High-Speed Pump")
    对接工业级的 Parquet 格式，利用列式存储优势进行流式读取
    """

    def __init__(self, filepath: str, chunk_size: int = 100000):
        self.filepath = filepath
        self.chunk_size = chunk_size
        
        if not os.path.exists(self.filepath):
            raise FileNotFoundError(f'【Data Engine】找不到数据文件: {self.filepath}')
        
    def _stream_data(self):
        """
        极速活塞函数
        用 PyArrow 底层的 iter_batches 机制，比 Pandas 自己的迭代器快得多，内存控制极其精准
        """
        try:
            parquet_file = pq.ParquetFile(self.filepath)
            
            for batch in parquet_file.iter_batches(batch_size=self.chunk_size):
                yield batch.to_pandas()

        except Exception as e:
            raise RuntimeError(f'读取 Parquet 文件时发生物理损坏或错误： {e}')
        
    def get_tick_df_chunk_stream(self):

        return self._stream_data()
    
class TickLoaderFactory:
    """
    数据加载器自动工厂(The "Dispatcher")
    根据文件名后缀，自动分配对应的 DataLoader。对外彻底屏蔽底层细节。
    """
    @staticmethod
    def create_loader(filepath: str, chunk_size: int = 100000) -> DataLoaderBase:
        """
        根据输入的文件类型，返回相应的Loader(ParquetTickLoader实例或CSVTickLoader实例)。
        """
        _, ext = os.path.splitext(filepath)
        ext = ext.lower()

        if ext == '.csv':
            return CSVTickLoader(filepath, chunk_size)
        elif ext in ['.parquet','.pq']:
            return ParquetTickLoader(filepath, chunk_size)
        else:
            raise ValueError(f'【Data Engine】不支持数据格式{ext}，当前仅支持 csv, parquet。')
        


#====================================
# 本地验证测试
#====================================

if __name__ == "__main__":

    filepath = r"D:\量化策略\CTA策略\RAW_DATA\TICK\2026\20260109\rr2605.csv"
    
    if os.path.exists(filepath):
        
        start_time = time.time()
        
        loader = TickLoaderFactory.create_loader(filepath)

        stream = loader.get_tick_df_chunk_stream()
      
        # 2. 抽第一桶水看看
        first_batch = next(stream)
        
        end_time = time.time()
        
        print(f"✅ 成功跑通！瞬间抽出 {len(first_batch)} 行数据。")
        print(f"⏱️ 耗时: {end_time - start_time:.4f} 秒。")
        print("数据前两行概览：")
        print(first_batch.head(2))
    else:
        print(f'No such file as {filepath}')

    



        

