"""Tests des capteurs de température."""

from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from .common import FakeModbusClient, async_poll, get_entity_id


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("outdoor_temperature", "8.5"),
        ("cc1_temperature", "35.2"),
        ("dhw_temperature", "48.7"),
    ],
)
async def test_valeurs_initiales(
    hass: HomeAssistant, init_integration, key: str, expected: str
) -> None:
    state = hass.states.get(get_entity_id(hass, "sensor", init_integration, key))
    assert state.state == expected


async def test_attributs(hass: HomeAssistant, init_integration) -> None:
    state = hass.states.get(
        get_entity_id(hass, "sensor", init_integration, "outdoor_temperature")
    )
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"
    assert state.attributes["device_class"] == SensorDeviceClass.TEMPERATURE
    assert state.attributes["state_class"] == SensorStateClass.MEASUREMENT


async def test_temperature_negative(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    """Les températures négatives arrivent en complément à deux (int16)."""
    entity_id = get_entity_id(hass, "sensor", init_integration, "outdoor_temperature")
    fake_client.input_registers[506] = 0xFFF6  # -10 -> -1,0 °C

    await async_poll(hass, freezer)

    assert hass.states.get(entity_id).state == "-1.0"


async def test_sonde_absente_indisponible(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    """0x8000 (-32768) : l'ISG signale une sonde non disponible."""
    outdoor = get_entity_id(hass, "sensor", init_integration, "outdoor_temperature")
    cc1 = get_entity_id(hass, "sensor", init_integration, "cc1_temperature")
    fake_client.input_registers[506] = 0x8000

    await async_poll(hass, freezer)

    assert hass.states.get(outdoor).state == STATE_UNAVAILABLE
    assert hass.states.get(cc1).state == "35.2"

    fake_client.input_registers[506] = 100
    await async_poll(hass, freezer)
    assert hass.states.get(outdoor).state == "10.0"


async def test_mise_a_jour(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    entity_id = get_entity_id(hass, "sensor", init_integration, "dhw_temperature")
    fake_client.input_registers[521] = 551

    await async_poll(hass, freezer)

    assert hass.states.get(entity_id).state == "55.1"
