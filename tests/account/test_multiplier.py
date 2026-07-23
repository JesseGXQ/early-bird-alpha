from __future__ import annotations

from dataclasses import dataclass

from account.multiplier import RegistryMultiplierProvider


@dataclass(frozen=True)
class _Registry:
    multiplier: int


class _RegistryManager:
    def get_instrument_registry(self, contract: str) -> _Registry:
        assert contract == "RB2510"
        return _Registry(multiplier=10)


def test_registry_multiplier_provider_reads_existing_registry_interface():
    provider = RegistryMultiplierProvider(_RegistryManager())

    assert provider("rb2510") == 10

