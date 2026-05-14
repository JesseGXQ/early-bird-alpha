import pandas as pd
import os
import glob
from datetime import date


class RolloverDetector:
    """
    换月探测器(The "Scout")
    扫描分离的 1min 合约文件，提取日频持仓量(OI)，计算出带有缓冲和阈值逻辑的主力连续日历。

    Parameters
    ------
    data_dir: str
        1min 数据所在文件夹路径
    symbol_root: str
        品种代码前缀，例如'RB'
    buffer_days: int, default = 3
        换月缓冲天数
    ratio_threshold: float, default = 1.1
        比例阈值。例如默认为1.1, 表示新合约 OI 必须大于旧合约 1.1 倍才记为有效超越

    Methods
    ------
    generate_rollover_calendar()
        生成换月日历 (rollover_calendar)。
        
    """
    
    def __init__(self, data_dir: str, symbol_root:str, 
                 buffer_days: int = 3, ratio_threshold: float = 1.1):

        self.data_dir = data_dir
        self.symbol_root = symbol_root.upper()
        self.buffer_days = buffer_days
        self.ratio_threshold = ratio_threshold

    def _build_oi_matrix(self) -> pd.DataFrame:
        #TODO [Tech Debt]
        # 不应该允许这个类直接和硬盘打交道
        """从分离的 1min 文件中降维提取每日最终持仓量矩阵"""
        search_pattern = os.path.join(self.data_dir, f'{self.symbol_root}*.*')
        file_paths = glob.glob(search_pattern)

        if not file_paths:
            raise FileNotFoundError(f'【Stitching】在 {self.data_dir} 未找到 {self.symbol_root}的合约文件。')
        
        oi_series_list = []
        for file_path in file_paths:
            #从文件名中提取物理合约代码，例如从'RB2405.csv'中提取'RB2405'
            filename = os.path.basename(file_path)
            contract_code = filename.split(".")[0].upper()

            #读取数据
            # TODO: [Tech Debt] 
            #  当前直接调用了 pd.read_csv 破坏了解耦原则并绕过了 cleaning 模块。
            # 未来应通过传入 BarDataLoader 对象来获取干净的切片数据。
            print(f'读取数据文件 {filename}')
            df = pd.read_csv(file_path)
            df['date'] = pd.to_datetime(df['datetime']).dt.date

            #核心降维：按日期分组，取每天最后一条记录的持仓量
            # TODO:  [逻辑错误]
            # 此处last()方法会调用夜盘（属于下一个交易日）的最后一分钟的OI。正确的方法是调用日盘15:00收盘的OI（也是交易所官方公布的每日持仓量）。
            daily_oi = df.groupby('date')['open_interest'].last()
            daily_oi.name = contract_code

            oi_series_list.append(daily_oi)

        #把所有合约的 Series 按日期横向拼成一个 DataFrame
        #缺失值填写0（该合约当天还没上市或已退市）
        oi_matrix = pd.concat(oi_series_list, axis=1).fillna(0)
        oi_matrix.sort_index(inplace=True)

        return oi_matrix
    
    def generate_rollover_calendar(self) -> pd.DataFrame:
        """
        带有比例阈值的状态机，生成日频级别的主力合约，以及最大持仓量的那张合约。
        换月条件: 新合约的持仓量大于等于旧合约的ratio_threshold倍，且连续buffer_days，则将新合约定义为主力。

        Returns
        ------
        Pandas DataFrame
            换月日历表 (rollover_calendar) 。Index: Date. Columns: Dominant_Contract, Max_OI_Contract_Today
        """
        oi_matrix = self._build_oi_matrix()

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
    
class AdjustmentCalculator:
    """
    复权跳空计算器(The "Surgeon")。根据换月日历，计算每个交易日的累计加法复权因子。

    复权方法：前向调整法——锚定最新价格不变，调整历史合约价格。英文为Backward Adjustment。

    Parameters
    ------
    data_dir: str
        1min 数据所在路径
    symbol_root: str
        品种代码前缀，例如'RB'

    Methods
    ------
    calculate_adjustment_factors(rollover_calendar)
        内部计算加法复权因子，输出换月总表。

    Examples
    ------

    """

    def __init__(self, data_dir: str, symbol_root: str):
        self.data_dir = data_dir
        self.symbol_root = symbol_root

    def _build_daily_close_matrix(self) -> pd.DataFrame:
        """
        内部辅助函数，和Detector类似，提取所有合约的日线收盘价(Close)，拼成一张横表。Date as index, different Contracts as columns

        """
        search_pattern = os.path.join(self.data_dir, f"{self.symbol_root}*.*")
        file_paths = glob.glob(search_pattern)

        close_series_list = []
        for file_path in file_paths:
            filename = os.path.basename(file_path)
            contract_code = filename.split(".")[0].upper()

            #read file 
            # TODO: [Tech Debt] 
            # 当前直接调用了 pd.read_csv 破坏了解耦原则并绕过了 cleaning 模块。
            # 未来应通过传入 BarLoader 对象来获取干净的切片数据。
            df = pd.read_csv(file_path)
            df['Date'] = pd.to_datetime(df['datetime']).dt.date

            #取每天最后一分钟的收盘价作为日收盘价
            daily_close = df.groupby('Date')['close'].last()
            daily_close.name = contract_code
            close_series_list.append(daily_close)

        close_matrix = pd.concat(close_series_list, axis=1).sort_index()
        
        return close_matrix
    
    def calculate_adjustment_factors(self, rollover_calendar: pd.DataFrame) -> pd.DataFrame:
        """
        核心逻辑：计算加法复权因子(标准英文是Back Adjustment)
        公式： Adj_Factor(t-1)  = Adj_Factor(t) + (P_new_dominant - P_old_dominant)[仅在换月日触发]

        Parameters
        ------
        rollover_calendar: Pandas DataFrame
            换月日历。由RolloverDetector的generate_rollover_calendar方法生成。Index:Date. 含有Dominant_Contract列。

        Returns
        ------
        Pandas DataFrame
            换月总表(master_table)。 Index: Date. Columns: Dominant_Contract, Price_Gap, Adj_Factor
        """
        close_matrix = self._build_daily_close_matrix()

        #日历从最新到从前降序排列，方便后续做遍历
        master_table = rollover_calendar.copy().sort_index(ascending=False)

        master_table['Adj_Factor'] = 0.0
        master_table['Price_Gap'] = 0.0
        cumulative_factor = 0.0

        #记录最新主力合约
        new_dominant = None 

        for date, row in master_table.iterrows():
            old_dominant = row['Dominant_Contract'] #由于这里是倒序排列了df，所以old_dominant指的是日期上更早的主力合约

            #如果发生了主力切换
            if new_dominant is not None and old_dominant != new_dominant:
                try:
                    price_old_dominant = close_matrix.at[date, old_dominant] #日期上更早的主力合约价格
                    price_new_dominant = close_matrix.at[date, new_dominant] #日期上更新的主力合约价格

                    #跳空缺口
                    gap = price_new_dominant - price_old_dominant

                    #累加到cumulative_factor
                    cumulative_factor += gap

                    master_table.at[date,'Price_Gap'] = gap

                    print(f"【复权计算】 {date}: {new_dominant} ({price_new_dominant}) -> {old_dominant} ({price_old_dominant}) | 缺口: {gap}")
                except KeyError as e:
                    #如果找不到某天的收盘价
                    print(f'【AdjustmentCalculator】{date} 缺失价格数据，跳空计算跳过一次。Error: {e}')
            
            master_table.at[date, 'Adj_Factor'] = cumulative_factor
            
            new_dominant = old_dominant # 由于df是按日期倒序做loop，所以对于下一次循环提取的是更早的主力合约(old_dominant)，那么相对应的本次循环的主力合约（相对下一次的）应该是时间上更新的new_domiant。

        master_table = master_table[['Dominant_Contract', 'Price_Gap', 'Adj_Factor']]
        return master_table
    
class ContinuousContractManager:
    """
    连续合约大管家，输入换月总表，对下游feed子模块提供"品种->物理合约"的解析，支持换月窗口期的双合约输出。

    Parameters
    ------
    master_table: Pandas DataFrame.
        换月总表，包含 Dominant_Contract, Price_Gap, Adj_Factor  (索引为 Date)，日频。
    transition_days: int.
        换月过渡期窗口，默认为1。

    Methods
    ------
    get_active_physical_contracts(query_date)
        核心路由接口A，根据查询日期返回物理合约（名称）列表。   
    get_continuous_info(query_date)
        核心路由接口B，根据查询日期返回一个包含主力合约（名称）和复权因子的字典。

    """

    def __init__(self, master_table: pd.DataFrame, transition_days: int = 1):
        self.master_table = master_table.sort_index() #将master_table按日期顺序排列（最早在前），因为前面的AdjustmentCalculator的calculate_adjustment_factors方法返回的DataFrame是按日期逆序排列的（最新在前）
        self.transition_days = transition_days

        #提取所有交易日序列
        self.dates_list = self.master_table.index.tolist()

    def get_active_physical_contracts(self, query_date: date) -> list[str]:
        """
        核心路由接口A（供下游 feed 子模块的 SimulationFeed 实盘/回测执行使用）
        功能：传入一个日期，返回当天应该订阅/交易的物理合约列表。如果处于换月期间，返回一个包含两个元素的列表。

        Parameters
        ------
        query_date: datetime.date
            需要查询的日期
        
        Returns
        ------
        list of str
            包含物理合约（名称）的列表。非换月期输出[主力]，换月期间输出[旧主力，新主力]。

        """
        if query_date not in self.master_table.index:
            print(f'【Data Engine-Stitching-ContinousContractManager】在已有数据中找不到所查询的日期 {date} 。返回空列表。')
            return []
        
        current_idx = self.dates_list.index(query_date)
        curr_dominant = self.master_table.at[query_date, 'Dominant_Contract']

        #如果是第一天，直接返回
        if current_idx == 0:
            return [curr_dominant]
        
        #检查是否属于“换月过渡期”，若是，则需要输出active_contracts = [旧合约, 新合约]
        active_contracts = [curr_dominant] #当然，必须包含新主力

        #开始寻找是否在过渡期窗口（transition_days)内，旧主力和新主力不同
        idx_start = max(0, current_idx - self.transition_days)
        for i in range(idx_start, current_idx):
            past_date = self.dates_list[i]
            past_dominant = self.master_table.at[past_date, 'Dominant_Contract']

            #若发现旧主力和新主力不同，且旧主力没有包含在需要返回的列表中
            if past_dominant != curr_dominant and past_dominant not in active_contracts:
                active_contracts.insert(0, past_dominant) #旧主力在列表第一位，表明我们需要从旧主力平仓，向新主力开仓
        
        return active_contracts
    
    def get_continuous_info(self, query_date: date) -> dict:
        """
        核心路由接口 B （供下游 feed 子模块的 ResearchFeed 拼接历史切片使用）
        功能：获取当天主力合约（名称）和复权因子

        Parameters
        ------
        query_date: datetime.date
            需要查询的日期
        
        Returns
        ------
        dict
            包含如下键的字典：

            - ``dominant_contract``: str
                主力合约
            - ``adj_factor``: float
                （累计）复权因子
        """
        
        if query_date not in self.dates_list:
            return {'dominant': None, 'adj_factor': 0.0}
        
        curr_dominant = self.master_table.at[query_date, 'Dominant_Contract']
        adj_factor = self.master_table.at[query_date, 'Adj_Factor']

        return {'dominant': curr_dominant, 'adj_factor': adj_factor}



    

    
    




