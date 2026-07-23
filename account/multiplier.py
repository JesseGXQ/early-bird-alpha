from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.rules.instrument_registry_manager import InstrumentRegistryManager


class RegistryMultiplierProvider:
    """从现有合约注册表读取合约乘数。"""

    def __init__(self, registry_manager: InstrumentRegistryManager):
        self.registry_manager = registry_manager

    def __call__(self, contract: str) -> int:
        registry = self.registry_manager.get_instrument_registry(contract.upper())
        return registry.multiplier

