"""主力连续合约拼接：换月表和换月日历构建，跳空价差；连续合约管家。"""
from __future__ import annotations

from typing import TYPE_CHECKING
import pandas as pd
from datetime import date
from pathlib import Path
import numpy as np

from data.stitching.models import (
    ActiveContractSelection, MultipleRolloverError, RollState,
)

if TYPE_CHECKING:
    from data.calendar.product_calendar import ProductCalendar
    from data.rules.instrument_registry_manager import InstrumentRegistryManager
    from data.config import RolloverConfig

class CurrentDomNotPresentError(Exception):
    """在当前Series的索引中找不到当前主力错误。"""
    pass
class PositiveOIAfterOrEqualLastTradeDate(Exception):
    """合约在最后交易日或之后仍有正持仓的数据错误。"""
    pass

FAIL_REASONS = {
    -1: 'success',
    1: 'OI_all_missing',
    3: 'no_positive_OI',
    4: 'next_trading_date_to_ltd_less_than_transition_days',
    5: 'hard_rule_but_no_other_contract',
    6: 'current_dom_not_present_thus_inpossible_to_comparable'
}

class RolloverDetector:
    """
    根据每日收盘持仓量生成主力合约换月日历。

    优先按最后交易日硬规则换月；未触发时，按持仓量阈值及连续天数软规则换月。
    当日收盘作出的决定从下一交易日生效。

    Parameters
    ----------
    registry_manager : InstrumentRegistryManager
        合约注册信息管理器，用于查询各合约的最后交易日。
    calendar : ProductCalendar
        品种交易日历，用于查询后续交易日并判断换月期限。
    buffer_days : int, default 3
        软规则要求同一候选合约连续达到持仓量阈值的交易日数。
    ratio_threshold : float, default 1.1
        软规则的持仓量倍率；候选合约当日持仓量须至少为当前主力的此倍率。
    transition_days : int, default 1
        新主力生效日之后、至其最后交易日（含）须保留的交易日数。
    """
    
    def __init__(self, 
                 registry_manager: InstrumentRegistryManager,
                 calendar: ProductCalendar,
                 buffer_days: int = 3, 
                 ratio_threshold: float = 1.1,
                 transition_days: int = 1):
        self.registry_manager = registry_manager
        self.calendar = calendar
        self.buffer_days = buffer_days
        self.ratio_threshold = ratio_threshold
        self.transition_days = transition_days


    def _dom_to_be_hard_rule(
            self, curr_dom: str, oi_series: pd.Series,
            curr_date: pd.Timestamp) -> tuple[str | None, int | None]:
        """硬规则触发后，选择持仓量最大的合格后继合约。"""
        other_contracts = oi_series.drop(labels=curr_dom, errors='ignore')
        if other_contracts.empty:
            return None, 5

        has_positive_oi = False
        for contract, oi in other_contracts.sort_values(ascending=False).items():
            assert isinstance(contract, str)
            if oi <= 0:
                continue
            has_positive_oi = True
            legitimate, _ = self._ligitimate_dom_to_be(oi, contract, curr_date)
            if legitimate:
                return contract, None
        return None, 4 if has_positive_oi else 3

    def _soft_rule(self,
                   curr_dom: str, oi_series: pd.Series,
                   new_dom_candidate: str | None,
                   days_candidate_led: int,
                   curr_date: pd.Timestamp,
    ) -> tuple[str | None, str | None, int, int | None]:
        """
        软换月规则：新主力candidate 的持仓量 连续 buffer_days 以 ratio_threshold 倍
        超越旧主力。

        Returns
        ------
        tuple[str | None, str | None, int, int | None]
            四元数组：
            分别对应（dom_to_be, new_dom_candidate,
            days_candidate_led, fail_code）。
        """
        if curr_dom not in oi_series.index:
            return None, None, 0, 6

        # 失效合约即使持仓量最大，也不能阻挡其他合格候选合约竞争。
        max_oi_contract = None
        max_oi = None
        for contract, oi in oi_series.sort_values(ascending=False).items():
            legitimate, _ = self._ligitimate_dom_to_be(oi, contract, curr_date) # pyright: ignore[reportArgumentType]
            if legitimate:
                max_oi_contract = contract
                max_oi = oi
                break

        if max_oi_contract is None or max_oi_contract == curr_dom:
            return curr_dom, None, 0, None
        assert max_oi is not None
        curr_dom_oi = oi_series[curr_dom]
        if curr_dom_oi > 0 and max_oi / curr_dom_oi < self.ratio_threshold:
            return curr_dom, None, 0, None

        days_candidate_led = (
            days_candidate_led + 1 if max_oi_contract == new_dom_candidate else 1
        )
        if days_candidate_led >= self.buffer_days:
            return max_oi_contract, None, 0, None # pyright: ignore[reportReturnType]
        return curr_dom, max_oi_contract, days_candidate_led, None # pyright: ignore[reportReturnType]

    def _ligitimate_dom_to_be(
            self, oi: int | float,
            dom_to_be: str,
            curr_date: pd.Timestamp,
    ) -> tuple[bool, int | None]:
        """合约是否有资格作为下个交易日的主力合约。
        当同时满足以下两个条件时，为有资格：
        （1）当日持仓量大于0；
        （2）下个交易日到该合约的最后交易日之间（左开右闭），至少仍有
        transition_days 个交易日。
        否则为无资格。
        """
        # condition 1
        if oi <= 0:
            return False, 3
        # condition 2
        ltd_dom_to_be = self.registry_manager\
            .get_instrument_registry(dom_to_be).last_trade_date
        try:
            date_to_examine = self._shifted_trading_date(
                trading_date=curr_date, n=self.transition_days + 1
            )
        except ValueError as exc:
            raise ValueError(f'无法找到日期->无法判断条件2是否成立\
            ->无法判断合约 {dom_to_be}是否有资格作为下个交易日的主力合约。')
        if date_to_examine <= pd.Timestamp(ltd_dom_to_be):
            return True, None
        return False, 4
    
    def _dom_to_be_no_current_dominant(
            self, oi_series: pd.Series, curr_date: pd.Timestamp
    ) -> tuple[str | None, int | None]:
        """当前主力合约缺失时，选择持仓量最大的合格合约。"""
        has_positive_oi = False
        for contract, oi in oi_series.sort_values(ascending=False).items():
            if oi <= 0:
                continue
            has_positive_oi = True
            legitimate, _ = self._ligitimate_dom_to_be(oi, contract, curr_date) # pyright: ignore[reportArgumentType]
            if legitimate:
                return contract, None # pyright: ignore[reportReturnType]
        return None, 4 if has_positive_oi else 3

    def _shifted_trading_date(
            self, trading_date: pd.Timestamp,
            n: int) -> pd.Timestamp:
        """利用交易日历，查询当前交易日所对应的后面第 n 个交易日。
        交易日历查询超出范围时抛出 ValueError 。"""
        dt = trading_date.date()
        for _ in range(n):
            try:
                dt = self.calendar.next_trading_date(dt)
            except ValueError as exc:
                raise ValueError(f'利用交易日历查询 {dt} 的\
                下个交易日时超出最大范围。') from exc
        return pd.Timestamp(dt)

    def _hard_rule_hit(
            self,
            contract: str,
            trading_date: pd.Timestamp
    ) -> bool:
        """
        判断合约是否触发 hard rule 换月：
        trading_date 后的第 transition_days 个交易日大于等于
        该合约的最后交易日。
        """
        ltd = self.registry_manager.get_instrument_registry(contract)\
            .last_trade_date
        dt = trading_date.date()
        for _ in range(self.transition_days):
            try:
                dt = self.calendar.next_trading_date(dt)
            except ValueError as exc:
                raise ValueError('由于交易日历查询失败，无法判断合约是否触发\
                                 hard rule 换月。') from exc
        return True if dt >= ltd else False

    def generate_rollover_calendar(self, oi_matrix: pd.DataFrame) -> pd.DataFrame:
        """
        按交易日逐日判定主力，生成换月日历。

        Parameters
        ----------
        oi_matrix : pd.DataFrame
            每行对应一个交易日的收盘持仓量，每列对应一个合约，索引可转换为日期。
            应覆盖相邻交易日；整日无数据时应保留全 NaN 行，以免跨过该日。
            函数会复制、按日期排序，并拒绝重复日期。

        Returns
        -------
        pd.DataFrame
            索引与排序后的输入交易日一致，包含：

            - Dominant_Contract：该交易日使用的主力，由前一交易日收盘决定；
              首日没有此前的判定，因此为空。
            - Max_OI_Contract_Today：当日非缺失持仓量中的最大者，不要求持仓量
              为正，也不要求符合最后交易日约束；全缺失时为空。
            - Fail_Reason：当日收盘为下一交易日选主力的结果；成功为 ``success``，
              失败为具体原因。最后一日不作下一交易日的判定，除持仓量全缺失外为空。

        Notes
        -----
        设当日为 T，T+n 为其后第 n 个交易日。逐日按以下顺序处理：

        1. 去掉当日 OI 缺失的合约。若全部缺失，记录失败、清除软规则候选状态，
           不为 T+1 指定主力。否则记录当日原始最大持仓量合约。
        2. 所有可能成为 T+1 主力的合约均须满足：T 日 OI > 0，且
           T+(transition_days+1) 不晚于该合约的最后交易日。选择候选合约时
           按 OI 从高到低跳过不合格者；最终确定的合约还会再次接受此检查。
        3. 若 T 日没有已确定的主力，从合格合约中直接选 OI 最大者，并清除
           软规则候选状态。若存在当前主力，且 T+transition_days 不早于其
           最后交易日，则优先触发硬规则：排除当前主力，选持仓量最大的合格
           后继合约，清除软规则候选状态；无合格后继时不沿用即将到期的主力。
        4. 未触发硬规则时，若当前主力当日 OI 缺失，则判定失败；否则软规则
           从合格合约中取 OI 最大者。若它不是当前主力，且其 OI 至少为当前
           主力的 ratio_threshold 倍，则计入该候选的连续达标天数；当前
           主力 OI 非正时，合格候选也视为达标。候选改变时从第 1 天重新
           计数，未达标时清零。连续达到 buffer_days 天后切换，否则继续
           沿用当前主力。
        5. 只有选出的合约通过资格检查，才将其写为 T+1 的主力；否则 T+1
           主力为空，并清除候选状态。因此 T 日收盘 OI 不会改变 T 日主力。
           最后一行仅记录当日最大持仓量，不向输入范围以外生成新交易日。
        """
        oi_matrix = oi_matrix.copy()
        oi_matrix.index = pd.to_datetime(oi_matrix.index)
        oi_matrix = oi_matrix.sort_index()
        if not oi_matrix.index.is_unique:
            raise ValueError('OI 矩阵的交易日期重复，无法生成换月日历。')

        new_dom_candidate = None
        days_candidate_led = 0

        dominant_contracts: dict[pd.Timestamp, str] = {}
        max_oi_contracts: dict[pd.Timestamp, str] = {}
        fail_codes: dict[pd.Timestamp, int] = {}

        if oi_matrix.empty:
            raise ValueError('OI 矩阵为空，无法生成换月日历。')

        # 最后一日的收盘 OI 尚无对应的行情交易日，不生成超出行情覆盖范围的主力。
        for trading_date, row in oi_matrix.iloc[:-1].iterrows():
            assert isinstance(trading_date, pd.Timestamp)
            curr_dom = dominant_contracts.get(trading_date)
            oi_series = row.dropna()

            if oi_series.empty:
                fail_codes[trading_date] = 1 
                new_dom_candidate = None
                days_candidate_led = 0
                continue

            # 先记录当日最大持仓量合约
            max_oi_c = oi_series.idxmax()
            assert isinstance(max_oi_c, str)
            max_oi_contracts[trading_date] = max_oi_c 

            # 开始寻找主力合约
            if curr_dom is None:
                dom_to_be, code = self._dom_to_be_no_current_dominant(
                    oi_series, trading_date
                )
                new_dom_candidate = None
                days_candidate_led = 0
            else:
                # 检查 hard rule 换月是否被触发
                hit = self._hard_rule_hit(
                    contract=curr_dom,
                    trading_date=trading_date,  
                )
                if hit: 
                    dom_to_be, code = self._dom_to_be_hard_rule(
                        curr_dom=curr_dom, oi_series=oi_series,
                        curr_date=trading_date,
                    )
                    new_dom_candidate = None
                    days_candidate_led = 0
                else: #  soft rule 换月检测
                    (dom_to_be, new_dom_candidate,
                    days_candidate_led, code ) = self._soft_rule(
                        curr_dom=curr_dom, oi_series=oi_series,
                        new_dom_candidate=new_dom_candidate,
                        days_candidate_led=days_candidate_led,
                        curr_date=trading_date,
                    )
            if dom_to_be is None:
                assert code is not None
                fail_codes[trading_date] = code  
                new_dom_candidate = None
                days_candidate_led = 0
                continue

            oi_dom_to_be = oi_series.loc[dom_to_be]
            ligitimate, code = self._ligitimate_dom_to_be(
                oi=oi_dom_to_be, dom_to_be=dom_to_be,
                curr_date=trading_date  
            )
            if ligitimate: # 可以设定下一交易日的主力合约
                next_trading_date = self._shifted_trading_date(
                    trading_date=trading_date, n=1  
                )
                dominant_contracts[next_trading_date] = dom_to_be
                fail_codes[trading_date] = -1  
            else:
                assert code is not None
                fail_codes[trading_date] = code 
                new_dom_candidate = None
                days_candidate_led = 0

        last_date = oi_matrix.index[-1]
        last_oi = oi_matrix.iloc[-1].dropna()
        if last_oi.empty:
            fail_codes[last_date] = 1
        else:
            max_oi_contracts[last_date] = last_oi.idxmax() # pyright: ignore[reportArgumentType]

        # 组装和拼接结果返回
        dom_s = pd.Series(
            dominant_contracts if dominant_contracts else [],   # type: ignore
            name='Dominant_Contract'
        )
        max_s = pd.Series(
            max_oi_contracts if max_oi_contracts else [], # type: ignore
            name='Max_OI_Contract_Today'
        )
        reason_s = pd.Series(fail_codes).map(FAIL_REASONS) # type: ignore
        reason_s.name = 'Fail_Reason'
        rollover_calendar =  pd.concat(
            [dom_s, max_s, reason_s], axis=1
        ).reindex(oi_matrix.index)
        rollover_calendar.index.name='trading_date'
        return rollover_calendar

class RollGapCalculator:
    """
    换月缺口计算器

    接收换月日历 (rollover_calendar) ，
    根据价格数据计算换月当天的 Price_Gap，拼装出换月总表 (master Table) 。

    Parameters
    ------
    无

    Methods
    ------
    build_master_table(rollover_calendar, daily_matrix, settle_matrix)
        生成换月总表，换月价差优先采用收盘价，并在必要时成对回退至结算价。

    """
    def __init__(self):
        pass
    
    def build_master_table(self, 
                           rollover_calendar: pd.DataFrame,
                           daily_matrix: pd.DataFrame,
                           print_calculation: bool = False,
                           settle_matrix: pd.DataFrame | None = None) -> pd.DataFrame:
        """
        生成换月总表。索引为交易日期。

        Parameters
        ------
        rollover_calendar: pd.DataFrame
            换月日历，至少包含 ``Dominant_Contract`` 列。
        daily_matrix: pd.DataFrame
            日频收盘价矩阵。每一行为一个交易日，各列为不同合约。
        print_calculation: bool
            是否打印计算过程，默认为 False。
        settle_matrix: pd.DataFrame or None, optional
            日频结算价矩阵。收盘价对不完整时，新旧合约必须同时从该矩阵取值；
            若结算价对仍不完整则严格报错。默认为 None，此时不提供 fallback。
        
        Notes
        -----
        记时间上相邻的两个交易日为 D（较早）与 D'（较晚）。由换月日历的生成规则可知，
        ``Dominant_Contract`` 是当日生效的主力；若 D 与 D' 的主力不同，则换月发生在 D 与
        D' 之间，D 即旧主力的最后一个交易日。实现时先把 master_table 按日期降序排列，
        从晚到早遍历，并在循环中维护 ``new_dominant`` 表示"时间上更晚那个交易日的主力"。
        逐日处理逻辑如下：

        1. 若 ``new_dominant`` 或当日主力 ``old_dominant`` 任一缺失（NaN），当日不计算
           缺口，``Price_Gap`` 保持 NaN，并把 ``new_dominant`` 更新为当日主力后跳过。
        2. 若两者相同，说明未发生换月，``Price_Gap`` 置 0。
        3. 若两者不同，说明以 D 为旧主力最后一日发生了换月。价差采用成对价格口径：

           - 新旧合约在 D 日的收盘价均有效时，使用
             ``Price_Gap = 新主力 Close - 旧主力 Close``；
           - 任意一方收盘价无效时，双方统一回退为结算价，使用
             ``Price_Gap = 新主力 Settle - 旧主力 Settle``；
           - 结算价对仍不完整时严格抛出 ValueError，不把未知价差当作 0。

           ``Price_Gap_Basis`` 在实际换月边界分别记录 ``close`` 或
           ``settle_fallback``，非换月日及主力缺失处为空。
        4. 每轮结束时执行 ``new_dominant = old_dominant``，使其在下一轮（更早的交易日）
           中充当"较晚交易日的主力"。

        由此，``Price_Gap`` 仅在换月日（旧主力所在的那个交易日）取到非零跳空，其余交易日
        为 0；最新一日因初值 ``new_dominant`` 为空、以及主力缺失造成的断链处均为 NaN。
        最终返回 ``Dominant_Contract``、``Price_Gap`` 与 ``Price_Gap_Basis``，
        并按日期升序排列。
        """
        
        #日历从最新到从前降序排列，方便后续做遍历
        master_table = rollover_calendar.copy().sort_index(ascending=False)

        master_table['Price_Gap'] = np.nan
        master_table['Price_Gap_Basis'] = pd.Series(
            pd.NA, index=master_table.index, dtype='string' # type: ignore
        ) 

        #记录最新主力合约
        new_dominant = None 

        for date, row in master_table.iterrows():
            old_dominant = row['Dominant_Contract'] #由于这里是倒序排列了df，所以old_dominant指的是日期上更早的主力合约

            # 若旧主力、新主力合约两者任意一者缺失
            if pd.isna(new_dominant) or pd.isna(old_dominant):
                new_dominant = old_dominant
                continue

            if old_dominant == new_dominant:
                master_table.at[date, 'Price_Gap'] = 0.0
            else: #（旧主力、新主力都存在前提下）出现换月
                p_old_close = self._get_numeric_price(
                    daily_matrix, date, old_dominant # pyright: ignore[reportArgumentType]
                )
                p_new_close = self._get_numeric_price(
                    daily_matrix, date, new_dominant # pyright: ignore[reportArgumentType]
                )

                if pd.notna(p_old_close) and pd.notna(p_new_close):
                    p_old = p_old_close
                    p_new = p_new_close
                    basis = 'close'
                else:
                    p_old_settle = self._get_numeric_price(
                        settle_matrix, date, old_dominant # pyright: ignore[reportArgumentType]
                    )
                    p_new_settle = self._get_numeric_price(
                        settle_matrix, date, new_dominant # pyright: ignore[reportArgumentType]
                    )
                    if pd.isna(p_old_settle) or pd.isna(p_new_settle):
                        raise ValueError(
                            '【RollGapCalculator】已知主力发生切换，但收盘价对不完整，'
                            '且无法取得完整的结算价对，不能计算 Price_Gap。'
                            f'交易日={date}, 旧主力={old_dominant}, '
                            f'新主力={new_dominant}, '
                            f'Close=({p_old_close}, {p_new_close}), '
                            f'Settle=({p_old_settle}, {p_new_settle})。'
                        )
                    p_old = p_old_settle
                    p_new = p_new_settle
                    basis = 'settle_fallback'

                gap = p_new - p_old
                master_table.at[date,'Price_Gap'] = gap
                master_table.at[date, 'Price_Gap_Basis'] = basis
                
                if print_calculation:
                    print(
                        f'【复权计算】{date}: 新主力 {new_dominant} ({p_new}) - '
                        f'旧主力 {old_dominant} ({p_old}) = {gap}；口径={basis}'
                    )
            
            new_dominant = old_dominant # 由于df是按日期倒序做loop，所以对于下一次循环提取的是更早的主力合约(old_dominant)，那么相对应的本次循环的主力合约（相对下一次的）应该是时间上更新的new_dominant。

        master_table = master_table[
            ['Dominant_Contract', 'Price_Gap', 'Price_Gap_Basis']
        ]
        return master_table.sort_index() #再按日期做顺序排序

    @staticmethod
    def _get_numeric_price(
            price_matrix: pd.DataFrame | None,
            trading_date: pd.Timestamp,
            contract: str,
    ) -> float:
        """从价格矩阵安全读取数值；矩阵、行列或数值缺失时返回 NaN。"""
        if price_matrix is None:
            return np.nan
        try:
            value = price_matrix.at[trading_date, contract]
        except KeyError:
            return np.nan
        numeric = pd.to_numeric(value, errors='coerce')
        return float(numeric) if pd.notna(numeric) else np.nan

class ContinuousContractManager:
    """
    连续合约大管家
    
    对下游 feed 提供品种到当日主力、旧主力及换月状态的解析。

    Parameters
    ------
    product: str
        品种代码，例如'RB'。
    rollover_config: RolloverConfig
        换月规则配置类实例，包含 buffer_days, ratio_threshold, transition_days。
    master_table_dir: str
        本地持久化的 master_table 文件目录路径。
        ContinuousContractManager 会根据 rollover_config 中的参数 signature 来寻找对应的 master_table 文件，例如 RB_b3_r110_t1.parquet。

    Methods
    ------
    get_active_physical_contracts(trading_date)
        根据查询的交易日返回结构化的合约选择与换月状态。
    get_continuous_info(trading_date)
        根据查询的交易日返回一个包含主力合约（名称）和换月价差的字典。

    """

    def __init__(self,
                 product: str,
                 master_table_dir: str,
                 rollover_config: RolloverConfig
                 ):

        self.product = product

        # 读取本地持久化的 master_table （换月日历）。文件名如 RB_b3_r110_t1.parquet
        file_name = f'{product}_{rollover_config.signature}.parquet'
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
                f"原因: 未能在元数据仓库中找到品种 {product} 对应当前参数指纹的换月总表文件。\n"
                f"预期路径: {file_path}\n"
                f"参数:\n"
                f"  - 平滑天数 (buffer_days): {rollover_config.buffer_days}\n"
                f"  - 持仓阈值 (ratio_threshold): {rollover_config.ratio_threshold}\n"
                f"  - 过渡天数 (transition_days): {rollover_config.transition_days}\n"
                f"  - 唯一签名 (signature): {rollover_config.signature}\n"
                f"【行动指南】: 请先运行离线 ETL 脚本生成该指纹的换月总表 (master_table) ，然后再启动回测：\n"
            )
                raise FileNotFoundError(error_msg)

        # 记录配置中的换月过渡期参数
        self.transition_days = rollover_config.transition_days

        # 提取所有交易日序列
        self.dates_list: list[pd.Timestamp] = self.master_table.index.tolist()
        assert isinstance(self.dates_list[0], pd.Timestamp)

    def get_active_physical_contracts(
            self, trading_date: date
    ) -> ActiveContractSelection:
        """
        解析某交易日需要订阅的合约及三态换月状态。

        Parameters
        ----------
        trading_date : datetime.date
            需要查询的交易日。

        Returns
        -------
        ActiveContractSelection
            包含当日主力、可确定的旧主力及 ``RollState``。其 ``contracts``
            属性按旧主力、新主力顺序列出合约；主力为空时为空元组。

        Notes
        -----
        查询窗口为截至当日的 ``transition_days + 1`` 个表内交易日。
        当日主力为空时，返回空合约与 ``UNKNOWN``，不把缺失值转成字符串。
        当日主力已知但窗口历史有缺失时，仍返回当日主力，换月状态为
        ``UNKNOWN``，不推断旧主力。窗口无缺失时，无变化返回 ``NOT_ROLL``；
        恰有一次变化返回 ``ROLL``，旧主力取变化前的合约。多次变化无法用
        单个旧主力表示，明确抛出 ``MultipleRolloverError``。
        换月表缺少整个查询日期属于数据错误，不等同于主力为空。
        """
        ts = pd.Timestamp(trading_date)
        if ts not in self.master_table.index:
            raise ValueError(f'【ContinuousContractManager】在已有数据中找不到所查询的日期 {trading_date} 。')

        current_idx = self.dates_list.index(ts)
        curr_dominant = self.master_table.at[ts, 'Dominant_Contract']
        if pd.isna(curr_dominant):
            return ActiveContractSelection(None, None, RollState.UNKNOWN)
        if not isinstance(curr_dominant, str):
            raise TypeError(f'{trading_date} 的主力合约不是字符串：{curr_dominant!r}')

        idx_start = max(0, current_idx - self.transition_days)
        window = self.master_table['Dominant_Contract'].iloc[idx_start:current_idx + 1]
        if window.isna().any():
            return ActiveContractSelection(curr_dominant, None, RollState.UNKNOWN)

        dominants = window.tolist()
        changes = [
            (old, new) for old, new in zip(dominants, dominants[1:])
            if old != new
        ]
        if len(changes) > 1:
            raise MultipleRolloverError(
                f'{self.product} 在 {trading_date} 的过渡窗口内发生多次换月：'
                f'{dominants}；单个旧主力无法表达。'
            )
        if changes:
            return ActiveContractSelection(curr_dominant, changes[0][0], RollState.ROLL)
        return ActiveContractSelection(curr_dominant, None, RollState.NOT_ROLL)
    
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
            raise KeyError(f'无法在 {self.product} 的 master table 中找到交易日 {trading_date}。')
        
        curr_dominant = self.master_table.at[trading_date, 'Dominant_Contract']
        price_gap = self.master_table.at[trading_date, 'Price_Gap']

        return {'dominant': curr_dominant, 'price_gap': price_gap}



    

    
    




