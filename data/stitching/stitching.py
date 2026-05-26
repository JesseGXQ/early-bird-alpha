from __future__ import annotations

from typing import TYPE_CHECKING
import pandas as pd
from datetime import date
from pathlib import Path

from data.config import RolloverConfig

class RolloverDetector:
    """
    换月探测器

    计算出带有缓冲和阈值逻辑的主力合约换月日历。

    Parameters
    ------
    buffer_days: int, default = 3
        换月缓冲天数
    ratio_threshold: float, default = 1.1
        比例阈值。例如默认为1.1, 表示新合约 OI 必须大于旧合约 1.1 倍才记为有效超越

    Methods
    ------
    generate_rollover_calendar(oi_matrix)
        生成换月日历 (rollover_calendar)。
        
    """
    
    def __init__(self, 
                 buffer_days: int = 3, 
                 ratio_threshold: float = 1.1):
        self.buffer_days = buffer_days
        self.ratio_threshold = ratio_threshold
    
    def generate_rollover_calendar(self, oi_matrix: pd.DataFrame) -> pd.DataFrame:
        """
        带有比例阈值的状态机，生成日频级别的主力合约，以及最大持仓量合约。

        换月条件: 新合约的持仓量大于等于旧合约的 ratio_threshold 倍，且连续 buffer_days ，则将新合约定义为主力。

        Parameters
        ------
        oi_matrix: pd.DataFrame
            日频持仓量数据。每一行为一个交易日期，各列为各合约的持仓量 (OpenInterest)。

        Returns
        ------
        Pandas DataFrame
            换月日历表 (rollover_calendar) 。索引为交易日期，列为 Dominant_Contract, Max_OI_Contract_Today。
        """
        oi_matrix = oi_matrix.copy().sort_index() # 入口处做一次复制

        rollover_calendar = []
        current_dominant = None
        new_dominant_candidate = None
        days_candidate_led = 0

        for date, row in oi_matrix.iterrows():
            daily_max_contract = row.idxmax()
            max_oi = row[daily_max_contract]

            if current_dominant is None: #first row in iteration
                current_dominant = daily_max_contract
                curr_oi = row[current_dominant]
            else:#from the second row and so on, there's alreay a current dominant contract
                curr_oi = row[current_dominant]

            is_threshold_crossed =  (current_dominant != daily_max_contract) &  (max_oi >= self.ratio_threshold * curr_oi)

            if is_threshold_crossed:
                #如果是候选人新冒头
                if new_dominant_candidate != daily_max_contract:
                    new_dominant_candidate = daily_max_contract
                    days_candidate_led = 1
                #如果候选人蝉联
                else:
                    days_candidate_led += 1
                
                #评估换月条件是否达成
                #候选人连续buffer_days达成篡位条件（ratio_threshold倍超越当前主力），正式篡位
                if days_candidate_led >= self.buffer_days:
                    current_dominant = new_dominant_candidate
                    days_candidate_led = 0
                    new_dominant_candidate = None #重置
            #如果当天最大持仓没能跨越阈值，或者旧主力又夺回了第一，则篡位进度强制归零
            else:
                new_dominant_candidate = None
                days_candidate_led = 0

            rollover_calendar.append({
                'Date': date,
                'Dominant_Contract': current_dominant,
                'Max_OI_Contract_Today': daily_max_contract
            })
        
        rollover_calendar = pd.DataFrame(rollover_calendar).set_index('Date')
        return rollover_calendar 

class RollGapCalculator:
    """
    换月缺口计算器

    接收换月日历 (rollover_calendar) ，根据价格数据计算换月当天的 Price_Gap，拼装出换月总表 (master Table) 。

    Parameters
    ------
    无

    Methods
    ------
    build_master_table(rollover_calendar, daily_matrix)
        生成换月日历（包含Dominant_Contract 和 Price_Gap 列）。索引为交易日期。

    """
    def __init__(self):
        pass
    
    def build_master_table(self, 
                           rollover_calendar: pd.DataFrame,
                           daily_matrix: pd.DataFrame) -> pd.DataFrame:
        """
        生成换月日历（包含Dominant_Contract 和 Price_Gap 列）。索引为交易日期。

        Parameters
        ------
        rollover_caledar: pd.DataFrame
            换月日历
        daily_matrix: pd.DataFrame
            日频价格数据。每一行为一个交易日，各列为不同合约的价格（例如收盘价）。
        
        """
        
        #日历从最新到从前降序排列，方便后续做遍历
        master_table = rollover_calendar.copy().sort_index(ascending=False)

        master_table['Price_Gap'] = 0.0

        #记录最新主力合约
        new_dominant = None 

        for date, row in master_table.iterrows():
            old_dominant = row['Dominant_Contract'] #由于这里是倒序排列了df，所以old_dominant指的是日期上更早的主力合约

            #如果发生了主力切换
            if new_dominant is not None and old_dominant != new_dominant:
                try:
                    price_old_dominant = daily_matrix.at[date, old_dominant] #日期上更早的主力合约价格
                    price_new_dominant = daily_matrix.at[date, new_dominant] #日期上更新的主力合约价格

                    #跳空缺口 — 确保价格为数值型，兼容字符串等不可减类型
                    p_old = pd.to_numeric(price_old_dominant, errors='coerce')
                    p_new = pd.to_numeric(price_new_dominant, errors='coerce')

                    if pd.isna(p_old) or pd.isna(p_new):
                        raise ValueError(f'非数值类型价格: old={price_old_dominant}, new={price_new_dominant}')

                    gap = p_new - p_old

                    master_table.at[date,'Price_Gap'] = gap

                    print(f"【复权计算】 {date}: {new_dominant} ({p_new}) -> {old_dominant} ({p_old}) | 缺口: {gap}")
                except KeyError as e:
                    #如果找不到某天的收盘价
                    print(f'【AdjustmentCalculator】{date} 缺失价格数据，跳空计算跳过一次。Error: {e}')
            
            new_dominant = old_dominant # 由于df是按日期倒序做loop，所以对于下一次循环提取的是更早的主力合约(old_dominant)，那么相对应的本次循环的主力合约（相对下一次的）应该是时间上更新的new_domiant。

        master_table = master_table[['Dominant_Contract', 'Price_Gap']]
        return master_table.sort_index() #再按日期做顺序排序

class ContinuousContractManager:
    """
    连续合约大管家
    
    对下游feed子模块提供"品种->物理合约"的解析，支持换月窗口期的双合约输出。

    Parameters
    ------
    symbol_root: str
        品种代码前缀，例如'RB'。
    rollover_config: RolloverConfig, optional
        换月规则配置类实例，包含 buffer_days, ratio_threshold, transition_days。默认值为 RolloverConfig()，即 buffer_days=3, ratio_threshold=1.1, transition_days=1。
    master_table_dir: str
        本地持久化的 master_table 文件目录路径。
        ContinuousContractManager 会根据 rollover_config 中的参数 signature 来寻找对应的 master_table 文件，例如 RB_b3_r110_t1.parquet。

    Methods
    ------
    get_active_physical_contracts(trading_date)
        根据查询的交易日返回物理合约（名称）列表。   
    get_continuous_info(trading_date)
        根据查询的交易日返回一个包含主力合约（名称）和换月价差的字典。

    """

    def __init__(self,
                 symbol_root: str,
                 master_table_dir: str,
                 rollover_config: RolloverConfig = RolloverConfig()
                 ):
        # 读取本地持久化的 master_table （换月日历）。文件名如 RB_b3_r110_t1.parquet
        file_name = f'{symbol_root}_{rollover_config.signature}.parquet'
        file_path = Path(master_table_dir) / file_name

        if file_path.exists():
            print(f'【ContinuousContractManager】加载本地持久化的 master_table: {file_path}')
            try:
                self.master_table = pd.read_parquet(file_path)
            except Exception as e:
                raise RuntimeError(f'【ContinuousContractManager】读取 master_table 文件 {file_path} 失败: {e}')
        else: # 直接抛出错误，提示调用./scripts/run_stitching_etl.py 生成相应指纹的 master_table
                error_msg = (
                f"\n【Data Engine】无法初始化 ContinuousContractManager！\n"
                f"原因: 未能在元数据仓库中找到品种 {symbol_root} 对应当前参数指纹的换月总表文件。\n"
                f"预期路径: {file_path}\n"
                f"参数:\n"
                f"  - 平滑天数 (buffer_days): {rollover_config.buffer_days}\n"
                f"  - 持仓阈值 (ratio_threshold): {rollover_config.ratio_threshold}\n"
                f"  - 过渡天数 (transition_days): {rollover_config.transition_days}\n"
                f"  - 唯一签名 (signature): {rollover_config.signature}\n"
                f"【行动指南】: 请先运行离线 ETL 脚本生成该指纹的换月总表 (master_table) ，然后再启动回测：\n"
            )
                raise FileNotFoundError(error_msg)

        #记录配置中的换月过渡期参数
        self.transition_days = rollover_config.transition_days

        # 提取所有交易日序列
        self.dates_list = self.master_table.index.tolist()

    def get_active_physical_contracts(self, trading_date: date) -> list[str]:
        """
        核心路由接口A （供下游 feed 子模块的 SimulationFeed 实盘/回测执行使用）
        功能：传入一个交易日，返回当天应该订阅/交易的物理合约列表。如果处于换月期间，返回[旧主力, 新主力]。

        Parameters
        ------
        trading_date: datetime.date
            需要查询的交易日
        
        Returns
        ------
        list of str
            包含物理合约（名称）的列表。非换月期输出[主力]，换月期间输出[旧主力，新主力]。

        """
        if trading_date not in self.master_table.index:
            raise ValueError(f'【ContinuousContractManager】在已有数据中找不到所查询的日期 {trading_date} 。')
        
        current_idx = self.dates_list.index(trading_date)
        curr_dominant = self.master_table.at[trading_date, 'Dominant_Contract']
        # ensure string type (pandas may return bytes/Scalar)
        curr_dominant = str(curr_dominant)

        #如果是第一天，直接返回
        if current_idx == 0:
            return [curr_dominant]
        
        #检查是否属于“换月过渡期”，若是，则需要输出active_contracts = [旧主力, 新主力]
        active_contracts = [curr_dominant] #当然，必须包含新主力

        #开始寻找是否在过渡期窗口（transition_days)内，旧主力和新主力不同
        idx_start = max(0, current_idx - self.transition_days)
        for i in range(idx_start, current_idx):
            past_date = self.dates_list[i]
            past_dominant = self.master_table.at[past_date, 'Dominant_Contract']
            past_dominant = str(past_dominant)

            #若发现旧主力和新主力不同，且旧主力没有包含在需要返回的列表中
            if past_dominant != curr_dominant and past_dominant not in active_contracts:
                active_contracts.insert(0, past_dominant) #旧主力在列表第一位，表明我们需要从旧主力平仓，向新主力开仓
        
        return active_contracts
    
    def get_continuous_info(self, trading_date: date) -> dict:
        """
        功能：获取当天主力合约（名称）和换月价差（Price_Gap）。

        Parameters
        ------
        trading_date: datetime.date
            需要查询的交易日
        
        Returns
        ------
        dict
            包含如下键

            - ``dominant_contract``: str
                主力合约
            - ``price_gap``: float
                换月时的价格缺口
        """
        
        if trading_date not in self.dates_list:
            return {'dominant': None, 'price_gap': 0.0}
        
        curr_dominant = self.master_table.at[trading_date, 'Dominant_Contract']
        price_gap = self.master_table.at[trading_date, 'Price_Gap']

        return {'dominant': curr_dominant, 'price_gap': price_gap}



    

    
    




