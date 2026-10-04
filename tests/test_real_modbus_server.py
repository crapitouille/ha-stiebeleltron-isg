"""Tests bout en bout contre un vrai serveur Modbus TCP pymodbus (127.0.0.1).

Contrairement aux autres tests (faux client), ceux-ci valident le comportement
réel de pymodbus : trames, `device_id`, codes d'exception, fonctions 0x03/0x04/0x06.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import ModbusTcpServer

from custom_components.stiebel_isg_modbus.hub import (
    IsgConnectionError,
    IsgModbusHub,
    IsgRegisterError,
)

from .common import get_entity_id, make_entry



@pytest.fixture(autouse=True)
def _allow_sockets(socket_enabled: None) -> None:
    """Autorise les vraies sockets (127.0.0.1) pour ce module uniquement."""


SLAVE = 1

# Le datastore de pymodbus décale les adresses de +1 (le « zero mode » est
# désactivé) : on déclare donc les valeurs à adresse + 1 pour qu'elles soient
# servies à l'adresse filaire attendue.
_DATASTORE_OFFSET = 1


@dataclass
class RealIsg:
    """Faux ISG réel : serveur Modbus TCP local et ses registres."""

    port: int
    input_block: ModbusSequentialDataBlock
    holding_block: ModbusSequentialDataBlock

    def set_input(self, address: int, value: int) -> None:
        self.input_block.setValues(address + _DATASTORE_OFFSET, [value])

    def set_holding(self, address: int, value: int) -> None:
        self.holding_block.setValues(address + _DATASTORE_OFFSET, [value])

    def get_holding(self, address: int) -> int:
        return self.holding_block.getValues(address + _DATASTORE_OFFSET, 1)[0]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
async def real_isg() -> AsyncGenerator[RealIsg]:
    input_block = ModbusSequentialDataBlock(0, [0] * 2000)
    holding_block = ModbusSequentialDataBlock(0, [0] * 2000)
    context = ModbusServerContext(
        devices={SLAVE: ModbusDeviceContext(ir=input_block, hr=holding_block)},
        single=False,
    )
    port = _free_port()
    server = ModbusTcpServer(context, address=("127.0.0.1", port))
    task = asyncio.create_task(server.serve_forever())
    await asyncio.sleep(0.2)  # laisse le serveur écouter

    isg = RealIsg(port, input_block, holding_block)
    for address, value in {506: 85, 507: 352, 521: 487}.items():
        isg.set_input(address, value)
    for address, value in {1500: 2, 1507: 355, 1509: 500, 1510: 400}.items():
        isg.set_holding(address, value)

    yield isg

    await server.shutdown()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_hub_lectures_et_ecriture_reelles(real_isg: RealIsg) -> None:
    hub = IsgModbusHub("127.0.0.1", real_isg.port, SLAVE, 2)
    try:
        assert await hub.async_read_input_register(506) == 85
        assert await hub.async_read_input_register(521) == 487
        assert await hub.async_read_holding_register(1500) == 2

        await hub.async_write_register(1507, 375)

        assert real_isg.get_holding(1507) == 375
        assert await hub.async_read_holding_register(1507) == 375
    finally:
        await hub.async_close()


async def test_hub_valeur_negative_reelle(real_isg: RealIsg) -> None:
    real_isg.set_input(506, 0xFFF6)
    hub = IsgModbusHub("127.0.0.1", real_isg.port, SLAVE, 2)
    try:
        assert await hub.async_read_input_register(506) == 0xFFF6
    finally:
        await hub.async_close()


async def test_hub_mauvais_identifiant_esclave(real_isg: RealIsg) -> None:
    """Un esclave inexistant provoque une exception Modbus, pas une coupure."""
    hub = IsgModbusHub("127.0.0.1", real_isg.port, 9, 2)
    try:
        with pytest.raises(IsgRegisterError):
            await hub.async_read_input_register(506)
    finally:
        await hub.async_close()


async def test_hub_serveur_arrete() -> None:
    hub = IsgModbusHub("127.0.0.1", _free_port(), SLAVE, 1)
    try:
        with pytest.raises(IsgConnectionError):
            await hub.async_read_input_register(506)
    finally:
        await hub.async_close()


async def test_integration_complete(hass: HomeAssistant, real_isg: RealIsg) -> None:
    """Installation de l'intégration face à un serveur réel : lecture + écriture."""
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={"host": "127.0.0.1", "port": real_isg.port, "slave": SLAVE}
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    outdoor = get_entity_id(hass, "sensor", entry, "outdoor_temperature")
    assert hass.states.get(outdoor).state == "8.5"
    assert (
        hass.states.get(get_entity_id(hass, "sensor", entry, "dhw_temperature")).state
        == "48.7"
    )

    cc1 = get_entity_id(hass, "climate", entry, "cc1_heating")
    state = hass.states.get(cc1)
    assert state.state == "auto"
    assert state.attributes["temperature"] == 35.5

    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": cc1, "temperature": 38.5}, blocking=True
    )
    assert real_isg.get_holding(1507) == 385

    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": cc1, "hvac_mode": "off"}, blocking=True
    )
    # CC1 « off » = valeur 36864 dans la consigne ; le mode global n'est pas touché.
    assert real_isg.get_holding(1507) == 36864
    assert real_isg.get_holding(1500) == 2
    assert hass.states.get(cc1).state == "off"

    # Retour en « auto » : la dernière consigne (38,5 °C) est réécrite.
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": cc1, "hvac_mode": "auto"}, blocking=True
    )
    assert real_isg.get_holding(1507) == 385
    assert hass.states.get(cc1).state == "auto"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
