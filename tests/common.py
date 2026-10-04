"""Outils partagés par les tests : faux client pymodbus et aides diverses."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.stiebel_isg_modbus.const import DOMAIN

HOST = "192.168.0.199"

# Valeurs brutes par défaut renvoyées par le faux ISG.
DEFAULT_INPUT = {
    506: 85,  # extérieur : 8,5 °C
    507: 352,  # CC1 : 35,2 °C
    521: 487,  # ECS : 48,7 °C
}
DEFAULT_HOLDING = {
    1500: 2,  # mode global : automatique
    1507: 355,  # consigne CC1 : 35,5 °C
    1509: 500,  # consigne ECS confort : 50,0 °C
    1510: 400,  # consigne ECS réduit : 40,0 °C
}


class FakeResponse:
    """Réponse pymodbus minimale."""

    def __init__(self, registers: list[int] | None = None, error: bool = False) -> None:
        self.registers = registers or []
        self._error = error

    def isError(self) -> bool:  # noqa: N802 (nom imposé par pymodbus)
        return self._error

    def __repr__(self) -> str:
        return f"FakeResponse(error={self._error})"


class FakeModbusClient:
    """Faux AsyncModbusTcpClient (API pymodbus >= 3.10 : `device_id`)."""

    def __init__(self) -> None:
        self.connected = False
        self.connect_calls = 0
        self.close_calls = 0
        self.connect_result = True
        self.connect_exception: Exception | None = None
        self.request_exception: Exception | None = None
        self.write_exception: Exception | None = None
        self.error_addresses: set[int] = set()
        self.input_registers: dict[int, int] = dict(DEFAULT_INPUT)
        self.holding_registers: dict[int, int] = dict(DEFAULT_HOLDING)
        self.reads: list[tuple[str, int]] = []
        self.writes: list[tuple[int, int]] = []
        self.unit_ids: list[int] = []
        self.factory: Any = None  # mock de la classe patchée (voir conftest)

    async def connect(self) -> bool:
        self.connect_calls += 1
        if self.connect_exception is not None:
            raise self.connect_exception
        self.connected = self.connect_result
        return self.connect_result

    def close(self) -> None:
        self.close_calls += 1
        self.connected = False

    async def _read(self, kind: str, address: int, unit: int) -> FakeResponse:
        self.unit_ids.append(unit)
        if self.request_exception is not None:
            raise self.request_exception
        self.reads.append((kind, address))
        registers = self.input_registers if kind == "input" else self.holding_registers
        if address in self.error_addresses or address not in registers:
            return FakeResponse(error=True)
        return FakeResponse([registers[address]])

    async def _write(self, address: int, value: int, unit: int) -> FakeResponse:
        self.unit_ids.append(unit)
        if self.write_exception is not None:
            raise self.write_exception
        if self.request_exception is not None:
            raise self.request_exception
        if address in self.error_addresses:
            return FakeResponse(error=True)
        self.writes.append((address, value))
        self.holding_registers[address] = value
        return FakeResponse([value])

    async def read_input_registers(
        self,
        address: int,
        *,
        count: int = 1,
        device_id: int = 1,
        no_response_expected: bool = False,
    ) -> FakeResponse:
        return await self._read("input", address, device_id)

    async def read_holding_registers(
        self,
        address: int,
        *,
        count: int = 1,
        device_id: int = 1,
        no_response_expected: bool = False,
    ) -> FakeResponse:
        return await self._read("holding", address, device_id)

    async def write_register(
        self,
        address: int,
        value: int,
        *,
        device_id: int = 1,
        no_response_expected: bool = False,
    ) -> FakeResponse:
        return await self._write(address, value, device_id)


class LegacyFakeModbusClient(FakeModbusClient):
    """Faux client à l'ancienne API pymodbus (< 3.10 : paramètre `slave`)."""

    async def read_input_registers(  # type: ignore[override]
        self,
        address: int,
        count: int = 1,
        slave: int = 0,
        no_response_expected: bool = False,
    ) -> FakeResponse:
        return await self._read("input", address, slave)

    async def read_holding_registers(  # type: ignore[override]
        self,
        address: int,
        count: int = 1,
        slave: int = 0,
        no_response_expected: bool = False,
    ) -> FakeResponse:
        return await self._read("holding", address, slave)

    async def write_register(  # type: ignore[override]
        self,
        address: int,
        value: int,
        slave: int = 0,
        no_response_expected: bool = False,
    ) -> FakeResponse:
        return await self._write(address, value, slave)


class SlowFakeModbusClient(FakeModbusClient):
    """Client qui laisse la main à la boucle pendant chaque requête."""

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0
        self.max_in_flight = 0

    async def _read(self, kind: str, address: int, unit: int) -> FakeResponse:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return await super()._read(kind, address, unit)
        finally:
            self.in_flight -= 1


def make_entry(options: dict[str, Any] | None = None) -> MockConfigEntry:
    """Entrée de configuration de test."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Stiebel Eltron ISG ({HOST})",
        data={"host": HOST, "port": 502, "slave": 1},
        options=options or {},
        unique_id=f"{HOST}:502:1",
    )


def get_entity_id(
    hass: HomeAssistant, platform: str, entry: MockConfigEntry, key: str
) -> str:
    """Retrouve l'entity_id d'une entité à partir de sa clé."""
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None, f"entité {platform}/{key} introuvable"
    return entity_id


async def async_poll(hass: HomeAssistant, freezer: Any, seconds: int = 31) -> None:
    """Fait avancer le temps pour déclencher un cycle d'interrogation."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
