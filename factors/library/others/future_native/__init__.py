"""期货原生机制因子库。

按经济机制划分模块：
    - ``term_structure``：期限结构与展期机制；
    - ``open_interest``：持仓与资金流机制；
    - ``delivery``：实物交割与库存机制；
    - ``settlement``：结算机制；
    - ``price_limit``：价格限制机制；
    - ``margin``：保证金与杠杆约束机制；
    - ``session``：交易时段信息机制。

所有因子均继承 ``factors.library.base.Factor``，批量 ``compute`` 输出
MultiIndex[timestamp, product] 的命名 Series。
"""

from factors.library.others.future_native.delivery import (
    DeliverableCoverage,
    ReceiptChange,
    SeasonalReceiptScarcity,
)
from factors.library.others.future_native.margin import (
    MarginShockDeleveraging,
)
from factors.library.others.future_native.open_interest import (
    OpenInterestExhaustion,
    SignedOpenInterestChange,
)
from factors.library.others.future_native.price_limit import LimitPressure
from factors.library.others.future_native.session import OvernightDayReturn
from factors.library.others.future_native.settlement import (
    CloseSettleDeviation,
)
from factors.library.others.future_native.term_structure import (
    CurveRollDown,
    CurveSlopeChange,
)

__all__ = [
    'CurveSlopeChange',
    'CurveRollDown',
    'SignedOpenInterestChange',
    'OpenInterestExhaustion',
    'SeasonalReceiptScarcity',
    'DeliverableCoverage',
    'ReceiptChange',
    'CloseSettleDeviation',
    'LimitPressure',
    'MarginShockDeleveraging',
    'OvernightDayReturn',
]
