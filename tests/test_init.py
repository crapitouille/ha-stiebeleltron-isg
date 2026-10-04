"""Tests du cycle de vie de l'intégration et du coordinateur."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pymodbus.exceptions import ModbusIOException

from custom_components.stiebel_isg_modbus.const import DOMAIN

from .common import FakeModbusClient, async_poll, get_entity_id, make_entry


async def test_installation_et_entites(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    entry = init_integration
    assert entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(entities) == 6
    assert {e.domain for e in entities} == {"sensor", "climate"}

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.manufacturer == "Stiebel Eltron"
    assert device.configuration_url == f"http://{entry.data['host']}"


async def test_noms_et_entity_id_par_defaut(
    hass: HomeAssistant, init_integration
) -> None:
    """Non-régression : les entity_id et noms affichés ne doivent pas changer."""
    expected = {
        "sensor.stiebel_eltron_isg_outdoor_temperature": "Stiebel Eltron ISG Outdoor temperature",
        "sensor.stiebel_eltron_isg_heating_circuit_1_temperature": "Stiebel Eltron ISG Heating circuit 1 temperature",
        "sensor.stiebel_eltron_isg_hot_water_temperature": "Stiebel Eltron ISG Hot water temperature",
        "climate.stiebel_eltron_isg_heating_circuit_1": "Stiebel Eltron ISG Heating circuit 1",
        "climate.stiebel_eltron_isg_hot_water_comfort": "Stiebel Eltron ISG Hot water comfort",
        "climate.stiebel_eltron_isg_hot_water_reduced": "Stiebel Eltron ISG Hot water reduced",
    }
    for entity_id, name in expected.items():
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.attributes["friendly_name"] == name


async def test_registres_lus_une_seule_fois_par_cycle(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    """Chaque registre n'est lu qu'une fois par cycle (pas de doublon 507/521/1500)."""
    assert sorted(fake_client.reads) == [
        ("holding", 1500),
        ("holding", 1507),
        ("holding", 1509),
        ("holding", 1510),
        ("input", 506),
        ("input", 507),
        ("input", 521),
    ]
    assert fake_client.connect_calls == 1
    assert set(fake_client.unit_ids) == {1}


async def test_cadence_d_interrogation_par_defaut(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    fake_client.reads.clear()

    await async_poll(hass, freezer, seconds=20)
    assert fake_client.reads == []

    await async_poll(hass, freezer, seconds=11)
    assert len(fake_client.reads) == 7


async def test_intervalle_issu_des_options(
    hass: HomeAssistant, freezer, fake_client: FakeModbusClient
) -> None:
    entry = make_entry(options={CONF_SCAN_INTERVAL: 120})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    fake_client.reads.clear()

    await async_poll(hass, freezer, seconds=60)
    assert fake_client.reads == []

    await async_poll(hass, freezer, seconds=61)
    assert len(fake_client.reads) == 7


async def test_dechargement(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    entry = init_integration
    entity_id = get_entity_id(hass, "sensor", entry, "outdoor_temperature")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert fake_client.close_calls >= 1
    assert fake_client.connected is False
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


async def test_rechargement(hass: HomeAssistant, init_integration) -> None:
    entry = init_integration
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.stiebel_eltron_isg_outdoor_temperature").state == "8.5"


async def test_isg_injoignable_au_demarrage(
    hass: HomeAssistant, fake_client: FakeModbusClient
) -> None:
    fake_client.connect_result = False
    entry = make_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert fake_client.close_calls >= 1


async def test_perte_puis_retour_de_la_communication(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    entry = init_integration
    sensor = get_entity_id(hass, "sensor", entry, "outdoor_temperature")
    climate = get_entity_id(hass, "climate", entry, "cc1_heating")
    assert hass.states.get(sensor).state == "8.5"

    fake_client.request_exception = ModbusIOException("timeout")
    await async_poll(hass, freezer)
    assert hass.states.get(sensor).state == STATE_UNAVAILABLE
    assert hass.states.get(climate).state == STATE_UNAVAILABLE

    fake_client.request_exception = None
    fake_client.input_registers[506] = 90
    await async_poll(hass, freezer)
    assert hass.states.get(sensor).state == "9.0"
    assert hass.states.get(climate).state != STATE_UNAVAILABLE
    assert fake_client.connect_calls == 2  # reconnexion automatique


async def test_une_perte_de_connexion_n_epuise_pas_les_timeouts(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    """Le cycle s'arrête à la première erreur de connexion (un seul timeout)."""
    fake_client.reads.clear()
    fake_client.request_exception = ModbusIOException("timeout")

    await async_poll(hass, freezer)

    # Aucune lecture n'a abouti et le cycle n'a pas insisté sur chaque registre.
    assert fake_client.reads == []
    assert len(fake_client.unit_ids) <= 2 + 7  # init + au plus une tentative


async def test_registre_illisible_n_affecte_pas_les_autres(
    hass: HomeAssistant,
    freezer,
    fake_client: FakeModbusClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Le registre de mode (1500) en erreur n'invalide pas les températures."""
    fake_client.error_addresses = {1500}
    entry = make_entry()
    entry.add_to_hass(hass)
    with caplog.at_level(logging.WARNING):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await async_poll(hass, freezer)
        await async_poll(hass, freezer)

    assert hass.states.get(get_entity_id(hass, "sensor", entry, "outdoor_temperature")).state == "8.5"
    climate = hass.states.get(get_entity_id(hass, "climate", entry, "cc1_heating"))
    assert climate.state == STATE_UNKNOWN  # mode inconnu
    assert climate.attributes["temperature"] == 35.5
    assert climate.attributes["current_temperature"] == 35.2
    # L'avertissement n'est émis qu'une seule fois.
    warnings = [r for r in caplog.records if "illisible" in r.getMessage()]
    assert len(warnings) == 1

    # Le registre redevient lisible : le mode réapparaît.
    fake_client.error_addresses = set()
    await async_poll(hass, freezer)
    assert hass.states.get(get_entity_id(hass, "climate", entry, "cc1_heating")).state == "auto"


async def test_unique_ids_stables(hass: HomeAssistant, init_integration) -> None:
    """Non-régression : les unique_id ne doivent pas changer (historique HA)."""
    entry = init_integration
    registry = er.async_get(hass)
    unique_ids = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert unique_ids == {
        f"{entry.entry_id}_{key}"
        for key in (
            "outdoor_temperature",
            "cc1_temperature",
            "dhw_temperature",
            "cc1_heating",
            "dhw_comfort",
            "dhw_eco",
        )
    }


async def test_ecriture_avant_premiere_lecture_sans_donnees(
    hass: HomeAssistant, fake_client: FakeModbusClient
) -> None:
    """Sans donnée locale, l'écriture part quand même et n'échoue pas."""
    from custom_components.stiebel_isg_modbus.coordinator import IsgCoordinator
    from custom_components.stiebel_isg_modbus.hub import IsgModbusHub

    entry = make_entry()
    entry.add_to_hass(hass)
    coordinator = IsgCoordinator(
        hass, entry, IsgModbusHub("192.168.0.199", 502, 1, 5)
    )
    assert coordinator.data is None

    await coordinator.async_write_holding(1507, 375)

    assert fake_client.writes == [(1507, 375)]
    assert coordinator.data is None
    await coordinator.hub.async_close()
