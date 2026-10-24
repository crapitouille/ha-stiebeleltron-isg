"""Tests du capteur binaire « Période de chauffage » (consigne tampon, registre 518)."""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pymodbus.exceptions import ModbusIOException

from custom_components.stiebel_isg_modbus.const import CONF_HEATING_THRESHOLD

from .common import FakeModbusClient, async_poll, get_entity_id, make_entry

BINARY_ID = "binary_sensor.stiebel_eltron_isg_heating_season"
BUFFER_ID = "sensor.stiebel_eltron_isg_buffer_setpoint_temperature"


async def _setup(hass: HomeAssistant, options: dict | None = None):
    entry = make_entry(options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_periode_de_chauffage_par_defaut(
    hass: HomeAssistant, init_integration
) -> None:
    """Consigne tampon à 35,0 °C : période de chauffage."""
    state = hass.states.get(BINARY_ID)
    assert state.state == STATE_ON
    assert state.attributes["threshold"] == 5.0
    assert state.attributes["buffer_setpoint"] == 35.0
    assert get_entity_id(hass, "binary_sensor", init_integration, "heating_season") == BINARY_ID


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (50, STATE_OFF),  # 5,0 °C : hors période de chauffage
        (51, STATE_ON),  # 5,1 °C : chauffage
        (0, STATE_OFF),
        (200, STATE_ON),
        (0xFFF6, STATE_OFF),  # -1,0 °C
    ],
)
async def test_seuil_5_degres(
    hass: HomeAssistant,
    init_integration,
    fake_client: FakeModbusClient,
    freezer,
    raw: int,
    expected: str,
) -> None:
    fake_client.input_registers[518] = raw

    await async_poll(hass, freezer)

    assert hass.states.get(BINARY_ID).state == expected


async def test_bascule_automne_printemps(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    """Fin de saison puis reprise du chauffage."""
    fake_client.input_registers[518] = 50
    await async_poll(hass, freezer)
    assert hass.states.get(BINARY_ID).state == STATE_OFF

    fake_client.input_registers[518] = 280
    await async_poll(hass, freezer)
    assert hass.states.get(BINARY_ID).state == STATE_ON
    assert hass.states.get(BINARY_ID).attributes["buffer_setpoint"] == 28.0


async def test_valeur_invalide_indisponible(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    """0x8000 (objet indisponible) : ni « chauffage » ni « hors saison »."""
    fake_client.input_registers[518] = 0x8000

    await async_poll(hass, freezer)

    assert hass.states.get(BINARY_ID).state == STATE_UNAVAILABLE
    assert hass.states.get(BUFFER_ID).state == STATE_UNAVAILABLE
    # Les autres entités ne sont pas affectées.
    assert hass.states.get("sensor.stiebel_eltron_isg_outdoor_temperature").state == "8.5"

    fake_client.input_registers[518] = 50
    await async_poll(hass, freezer)
    assert hass.states.get(BINARY_ID).state == STATE_OFF


async def test_registre_illisible_indisponible(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    fake_client.error_addresses = {518}

    await async_poll(hass, freezer)

    assert hass.states.get(BINARY_ID).state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.stiebel_eltron_isg_outdoor_temperature").state == "8.5"


async def test_perte_de_communication_ne_donne_pas_hors_saison(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    """Une coupure Modbus rend le capteur indisponible (et non « off »)."""
    fake_client.request_exception = ModbusIOException("timeout")
    await async_poll(hass, freezer)
    assert hass.states.get(BINARY_ID).state == STATE_UNAVAILABLE

    fake_client.request_exception = None
    await async_poll(hass, freezer)
    assert hass.states.get(BINARY_ID).state == STATE_ON


async def test_seuil_configurable(
    hass: HomeAssistant, freezer, fake_client: FakeModbusClient
) -> None:
    fake_client.input_registers[518] = 100  # 10,0 °C
    await _setup(hass, options={CONF_HEATING_THRESHOLD: 10.0})

    state = hass.states.get(BINARY_ID)
    assert state.state == STATE_OFF  # 10,0 n'est pas > 10,0
    assert state.attributes["threshold"] == 10.0

    fake_client.input_registers[518] = 101
    await async_poll(hass, freezer)
    assert hass.states.get(BINARY_ID).state == STATE_ON


async def test_changement_du_seuil_recharge_l_entree(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    fake_client.input_registers[518] = 80  # 8,0 °C
    assert hass.config_entries.async_update_entry(
        init_integration, options={CONF_HEATING_THRESHOLD: 9.0}
    )
    await hass.async_block_till_done()

    assert hass.states.get(BINARY_ID).state == STATE_OFF
    assert hass.states.get(BINARY_ID).attributes["threshold"] == 9.0


async def test_capteur_consigne_tampon(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    state = hass.states.get(BUFFER_ID)
    assert state.state == "35.0"
    assert state.attributes["unit_of_measurement"] == "°C"
    assert state.attributes["device_class"] == "temperature"

    fake_client.input_registers[518] = 50
    await async_poll(hass, freezer)
    assert hass.states.get(BUFFER_ID).state == "5.0"


async def test_consigne_tampon_en_diagnostic(
    hass: HomeAssistant, init_integration
) -> None:
    entry = er.async_get(hass).async_get(BUFFER_ID)
    assert entry.entity_category is er.EntityCategory.DIAGNOSTIC
    # Le capteur binaire, lui, est une entité principale.
    assert er.async_get(hass).async_get(BINARY_ID).entity_category is None


async def test_lecture_en_lecture_seule(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    """La détection ne doit jamais écrire dans l'ISG."""
    assert fake_client.writes == []
    assert ("input", 518) in fake_client.reads
    assert ("holding", 518) not in fake_client.reads
