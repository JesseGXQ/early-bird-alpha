"""主力合约解析结果及换月状态。"""

from dataclasses import dataclass
from enum import Enum


class RollState(Enum):
    """换月窗口的判定结果。"""

    ROLL = 'roll'
    NOT_ROLL = 'not_roll'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class ActiveContractSelection:
    """某交易日已知的主力、旧主力及换月状态。"""

    dominant: str | None
    outgoing: str | None
    roll_state: RollState

    @property
    def contracts(self) -> tuple[str, ...]:
        """按旧主力、新主力的顺序列出需要订阅的合约。"""
        if self.dominant is None:
            return ()
        if self.outgoing is None:
            return (self.dominant,)
        return (self.outgoing, self.dominant)


class MultipleRolloverError(ValueError):
    """过渡窗口内发生多次换月，无法用单个旧主力表示。"""
