"""Tests des thermostats (chauffage CC1, ECS confort, ECS réduit)."""

from __future__ import annotations

import pytest
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pymodbus.exceptions import ModbusIOException

from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from .common import FakeModbusClient, async_poll, get_entity_id, make_entry

KEYS = ("cc1_heating", "dhw_comfort", "dhw_eco")


def climate_id(hass: HomeAssistant, entry, key: str) -> str:
    return get_entity_id(hass, "climate", entry, key)


async def _set_temperature(hass: HomeAssistant, entity_id: str, temperature: float) -> None:
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: temperature},
        blocking=True,
    )


async def _set_hvac_mode(hass: HomeAssistant, entity_id: str, mode: str) -> None:
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: mode},
        blocking=True,
    )


async def test_etat_initial(hass: HomeAssistant, init_integration) -> None:
    expected = {
        "cc1_heating": (35.2, 35.5, 20, 45),
        "dhw_comfort": (48.7, 50.0, 40, 60),
        "dhw_eco": (48.7, 40.0, 40, 60),
    }
    for key, (current, target, min_t, max_t) in expected.items():
        state = hass.states.get(climate_id(hass, init_integration, key))
        assert state.state == HVACMode.AUTO
        assert state.attributes["current_temperature"] == current
        assert state.attributes[ATTR_TEMPERATURE] == target
        assert state.attributes[ATTR_MIN_TEMP] == min_t
        assert state.attributes[ATTR_MAX_TEMP] == max_t
        assert state.attributes[ATTR_TARGET_TEMP_STEP] == 0.5
        assert state.attributes[ATTR_HVAC_MODES] == [
            HVACMode.OFF,
            HVACMode.AUTO,
            HVACMode.HEAT,
        ]
        features = state.attributes["supported_features"]
        assert features & ClimateEntityFeature.TARGET_TEMPERATURE
        assert features & ClimateEntityFeature.TURN_ON
        assert features & ClimateEntityFeature.TURN_OFF


@pytest.mark.parametrize(
    ("key", "temperature", "register", "raw"),
    [
        ("cc1_heating", 37.5, 1507, 375),
        ("cc1_heating", 20.0, 1507, 200),
        ("cc1_heating", 45.0, 1507, 450),
        ("dhw_comfort", 52.0, 1509, 520),
        ("dhw_eco", 42.5, 1510, 425),
    ],
)
async def test_modification_de_la_consigne(
    hass: HomeAssistant,
    init_integration,
    fake_client: FakeModbusClient,
    key: str,
    temperature: float,
    register: int,
    raw: int,
) -> None:
    entity_id = climate_id(hass, init_integration, key)

    await _set_temperature(hass, entity_id, temperature)

    assert fake_client.writes == [(register, raw)]
    # Répercuté immédiatement, sans attendre le cycle suivant.
    assert hass.states.get(entity_id).attributes[ATTR_TEMPERATURE] == temperature


async def test_la_consigne_d_un_thermostat_n_affecte_pas_les_autres(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    comfort = climate_id(hass, init_integration, "dhw_comfort")
    eco = climate_id(hass, init_integration, "dhw_eco")

    await _set_temperature(hass, comfort, 55.0)

    assert hass.states.get(comfort).attributes[ATTR_TEMPERATURE] == 55.0
    assert hass.states.get(eco).attributes[ATTR_TEMPERATURE] == 40.0


@pytest.mark.parametrize("temperature", [20.1, 35.3, 41.7, 44.9])
async def test_arrondi_des_consignes_decimales(
    hass: HomeAssistant,
    init_integration,
    fake_client: FakeModbusClient,
    temperature: float,
) -> None:
    """Pas de dérive de virgule flottante (35.3 / 0.1 = 352.99999...)."""
    entity_id = climate_id(hass, init_integration, "cc1_heating")

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: temperature},
        blocking=True,
    )

    assert fake_client.writes == [(1507, round(temperature * 10))]


@pytest.mark.parametrize(
    ("key", "temperature"),
    [("cc1_heating", 50.0), ("cc1_heating", 10.0), ("dhw_comfort", 61.0), ("dhw_eco", 39.0)],
)
async def test_consigne_hors_plage_refusee(
    hass: HomeAssistant,
    init_integration,
    fake_client: FakeModbusClient,
    key: str,
    temperature: float,
) -> None:
    entity_id = climate_id(hass, init_integration, key)

    with pytest.raises(ServiceValidationError):
        await _set_temperature(hass, entity_id, temperature)

    assert fake_client.writes == []


@pytest.mark.parametrize(
    ("mode", "raw"),
    [(HVACMode.OFF, 1), (HVACMode.AUTO, 2), (HVACMode.HEAT, 3)],
)
async def test_changement_de_mode(
    hass: HomeAssistant,
    init_integration,
    fake_client: FakeModbusClient,
    mode: HVACMode,
    raw: int,
) -> None:
    entity_id = climate_id(hass, init_integration, "dhw_comfort")

    await _set_hvac_mode(hass, entity_id, mode)

    assert fake_client.writes == [(1500, raw)]
    assert hass.states.get(entity_id).state == mode


async def test_le_mode_est_global_pour_les_trois_thermostats(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    """Les trois thermostats partagent le registre de mode 1500."""
    await _set_hvac_mode(hass, climate_id(hass, init_integration, "dhw_comfort"), HVACMode.OFF)

    for key in KEYS:
        assert hass.states.get(climate_id(hass, init_integration, key)).state == HVACMode.OFF
    assert fake_client.writes == [(1500, 1)]


@pytest.mark.parametrize("raw", [3, 4, 5])
async def test_modes_chauffage_multiples_lus_comme_heat(
    hass: HomeAssistant,
    init_integration,
    fake_client: FakeModbusClient,
    freezer,
    raw: int,
) -> None:
    fake_client.holding_registers[1500] = raw

    await async_poll(hass, freezer)

    assert (
        hass.states.get(climate_id(hass, init_integration, "dhw_eco")).state
        == HVACMode.HEAT
    )


async def test_mode_inconnu(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    fake_client.holding_registers[1500] = 99

    await async_poll(hass, freezer)

    assert (
        hass.states.get(climate_id(hass, init_integration, "dhw_eco")).state
        == STATE_UNKNOWN
    )


async def test_turn_off_et_turn_on_ecs(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    entity_id = climate_id(hass, init_integration, "dhw_comfort")

    await hass.services.async_call(
        "climate", "turn_off", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    assert fake_client.writes[-1] == (1500, 1)
    assert hass.states.get(entity_id).state == HVACMode.OFF

    await hass.services.async_call(
        "climate", "turn_on", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    assert fake_client.writes[-1] == (1500, 3)
    assert hass.states.get(entity_id).state == HVACMode.HEAT


async def test_echec_d_ecriture_connexion(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    entity_id = climate_id(hass, init_integration, "cc1_heating")
    fake_client.write_exception = ModbusIOException("timeout")

    with pytest.raises(HomeAssistantError, match="1507"):
        await _set_temperature(hass, entity_id, 40.0)

    # L'état affiché reste celui lu dans l'ISG.
    assert hass.states.get(entity_id).attributes[ATTR_TEMPERATURE] == 35.5


async def test_echec_d_ecriture_refus_du_peripherique(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    entity_id = climate_id(hass, init_integration, "dhw_comfort")
    fake_client.error_addresses = {1500}

    with pytest.raises(HomeAssistantError, match="1500"):
        await _set_hvac_mode(hass, entity_id, HVACMode.OFF)

    assert hass.states.get(entity_id).state == HVACMode.AUTO


async def test_la_valeur_ecrite_est_confirmee_par_le_cycle_suivant(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    entity_id = climate_id(hass, init_integration, "cc1_heating")
    await _set_temperature(hass, entity_id, 38.0)

    # L'ISG a corrigé la valeur écrite (ex. limite interne).
    fake_client.holding_registers[1507] = 370
    await async_poll(hass, freezer)

    assert hass.states.get(entity_id).attributes[ATTR_TEMPERATURE] == 37.0


async def test_set_temperature_sans_temperature_ne_fait_rien(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    entity = hass.data["climate"].get_entity(
        climate_id(hass, init_integration, "cc1_heating")
    )

    await entity.async_set_temperature()

    assert fake_client.writes == []


# --- CC1 : « arrêt » = valeur 36864 dans le registre de consigne (1507) --------

OFF_RAW = 36864
CC1_ENTITY_ID = "climate.stiebel_eltron_isg_heating_circuit_1"


async def _setup_entry(hass: HomeAssistant):
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_cc1_off_ecrit_36864_dans_la_consigne(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    cc1 = climate_id(hass, init_integration, "cc1_heating")

    await _set_hvac_mode(hass, cc1, HVACMode.OFF)

    assert fake_client.writes == [(1507, OFF_RAW)]
    state = hass.states.get(cc1)
    assert state.state == HVACMode.OFF
    assert state.attributes.get(ATTR_TEMPERATURE) is None
    # Le mode global (1500) n'est pas touché : l'ECS continue de fonctionner.
    assert fake_client.holding_registers[1500] == 2
    for key in ("dhw_comfort", "dhw_eco"):
        ecs = hass.states.get(climate_id(hass, init_integration, key))
        assert ecs.state == HVACMode.AUTO


async def test_cc1_off_via_turn_off(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    cc1 = climate_id(hass, init_integration, "cc1_heating")

    await hass.services.async_call(
        "climate", "turn_off", {ATTR_ENTITY_ID: cc1}, blocking=True
    )

    assert fake_client.writes == [(1507, OFF_RAW)]
    assert hass.states.get(cc1).state == HVACMode.OFF


async def test_cc1_valeur_d_arret_lue_comme_off(
    hass: HomeAssistant, freezer, fake_client: FakeModbusClient
) -> None:
    """36864 n'est pas une température : pas de consigne aberrante (-2867 °C)."""
    fake_client.holding_registers[1507] = OFF_RAW
    entry = await _setup_entry(hass)

    state = hass.states.get(climate_id(hass, entry, "cc1_heating"))
    assert state.state == HVACMode.OFF
    assert state.attributes.get(ATTR_TEMPERATURE) is None
    assert state.attributes["current_temperature"] == 35.2


async def test_cc1_passage_a_l_arret_detecte_au_cycle_suivant(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    cc1 = climate_id(hass, init_integration, "cc1_heating")
    fake_client.holding_registers[1507] = OFF_RAW
    await async_poll(hass, freezer)
    assert hass.states.get(cc1).state == HVACMode.OFF

    fake_client.holding_registers[1507] = 380
    await async_poll(hass, freezer)
    state = hass.states.get(cc1)
    assert state.state == HVACMode.AUTO
    assert state.attributes[ATTR_TEMPERATURE] == 38.0


async def test_cc1_sortie_de_l_arret_restaure_la_derniere_consigne(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    cc1 = climate_id(hass, init_integration, "cc1_heating")
    await _set_hvac_mode(hass, cc1, HVACMode.OFF)
    fake_client.writes.clear()

    await _set_hvac_mode(hass, cc1, HVACMode.AUTO)

    # La consigne 35,5 °C est réécrite ; le mode global (auto) est déjà le bon.
    assert fake_client.writes == [(1507, 355)]
    state = hass.states.get(cc1)
    assert state.state == HVACMode.AUTO
    assert state.attributes[ATTR_TEMPERATURE] == 35.5


async def test_cc1_sortie_de_l_arret_vers_heat_ecrit_aussi_le_mode_global(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    cc1 = climate_id(hass, init_integration, "cc1_heating")
    await _set_hvac_mode(hass, cc1, HVACMode.OFF)
    fake_client.writes.clear()

    await _set_hvac_mode(hass, cc1, HVACMode.HEAT)

    assert fake_client.writes == [(1507, 355), (1500, 3)]
    assert hass.states.get(cc1).state == HVACMode.HEAT


async def test_cc1_sortie_de_l_arret_ne_force_pas_un_mode_global_deja_chauffage(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    """Mode global déjà en chauffage (valeur 4) : on ne l'écrase pas par 3."""
    cc1 = climate_id(hass, init_integration, "cc1_heating")
    fake_client.holding_registers[1500] = 4
    await async_poll(hass, freezer)
    await _set_hvac_mode(hass, cc1, HVACMode.OFF)
    fake_client.writes.clear()

    await _set_hvac_mode(hass, cc1, HVACMode.HEAT)

    assert fake_client.writes == [(1507, 355)]
    assert fake_client.holding_registers[1500] == 4


async def test_cc1_turn_on_apres_turn_off(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    cc1 = climate_id(hass, init_integration, "cc1_heating")
    await hass.services.async_call("climate", "turn_off", {ATTR_ENTITY_ID: cc1}, blocking=True)
    fake_client.writes.clear()

    await hass.services.async_call("climate", "turn_on", {ATTR_ENTITY_ID: cc1}, blocking=True)

    assert fake_client.writes == [(1507, 355), (1500, 3)]
    state = hass.states.get(cc1)
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_TEMPERATURE] == 35.5


async def test_cc1_regler_une_temperature_reactive_la_consigne(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    """Comme dans l'ancien YAML : écrire une température sort de l'arrêt."""
    cc1 = climate_id(hass, init_integration, "cc1_heating")
    await _set_hvac_mode(hass, cc1, HVACMode.OFF)
    fake_client.writes.clear()

    await _set_temperature(hass, cc1, 38.0)

    assert fake_client.writes == [(1507, 380)]
    state = hass.states.get(cc1)
    assert state.state == HVACMode.AUTO
    assert state.attributes[ATTR_TEMPERATURE] == 38.0


async def test_cc1_la_derniere_consigne_suit_les_modifications(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    cc1 = climate_id(hass, init_integration, "cc1_heating")
    await _set_temperature(hass, cc1, 40.0)
    await _set_hvac_mode(hass, cc1, HVACMode.OFF)
    fake_client.writes.clear()

    await _set_hvac_mode(hass, cc1, HVACMode.AUTO)

    assert fake_client.writes == [(1507, 400)]


async def test_cc1_double_arret_conserve_la_derniere_consigne(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    cc1 = climate_id(hass, init_integration, "cc1_heating")
    await _set_hvac_mode(hass, cc1, HVACMode.OFF)
    await _set_hvac_mode(hass, cc1, HVACMode.OFF)
    fake_client.writes.clear()

    await _set_hvac_mode(hass, cc1, HVACMode.AUTO)

    assert fake_client.writes == [(1507, 355)]


async def test_cc1_standby_global_lu_comme_off_sans_perdre_la_consigne(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient, freezer
) -> None:
    """Mode global en veille (1) : CC1 apparaît à l'arrêt, consigne conservée."""
    cc1 = climate_id(hass, init_integration, "cc1_heating")
    fake_client.holding_registers[1500] = 1

    await async_poll(hass, freezer)

    state = hass.states.get(cc1)
    assert state.state == HVACMode.OFF
    assert state.attributes[ATTR_TEMPERATURE] == 35.5


async def test_cc1_sans_consigne_connue_refuse_de_quitter_l_arret(
    hass: HomeAssistant, freezer, fake_client: FakeModbusClient
) -> None:
    """Démarrage à l'arrêt et aucune consigne mémorisée : erreur explicite."""
    fake_client.holding_registers[1507] = OFF_RAW
    entry = await _setup_entry(hass)
    cc1 = climate_id(hass, entry, "cc1_heating")

    with pytest.raises(ServiceValidationError):
        await _set_hvac_mode(hass, cc1, HVACMode.AUTO)

    assert fake_client.writes == []
    assert hass.states.get(cc1).state == HVACMode.OFF

    # Régler une température fonctionne toujours.
    await _set_temperature(hass, cc1, 36.0)
    assert fake_client.writes == [(1507, 360)]
    assert hass.states.get(cc1).state == HVACMode.AUTO


async def test_cc1_derniere_consigne_restauree_au_redemarrage(
    hass: HomeAssistant, freezer, fake_client: FakeModbusClient
) -> None:
    fake_client.holding_registers[1507] = OFF_RAW
    mock_restore_cache_with_extra_data(
        hass,
        ((State(CC1_ENTITY_ID, "off"), {"last_target_temperature": 37.0}),),
    )
    entry = await _setup_entry(hass)
    cc1 = climate_id(hass, entry, "cc1_heating")
    assert cc1 == CC1_ENTITY_ID

    await _set_hvac_mode(hass, cc1, HVACMode.AUTO)

    assert fake_client.writes == [(1507, 370)]
    assert hass.states.get(cc1).attributes[ATTR_TEMPERATURE] == 37.0


@pytest.mark.parametrize("restored", [99.0, 5.0, None, "abc", True])
async def test_cc1_consigne_restauree_invalide_ignoree(
    hass: HomeAssistant, freezer, fake_client: FakeModbusClient, restored
) -> None:
    fake_client.holding_registers[1507] = OFF_RAW
    mock_restore_cache_with_extra_data(
        hass,
        ((State(CC1_ENTITY_ID, "off"), {"last_target_temperature": restored}),),
    )
    entry = await _setup_entry(hass)

    with pytest.raises(ServiceValidationError):
        await _set_hvac_mode(hass, climate_id(hass, entry, "cc1_heating"), HVACMode.AUTO)

    assert fake_client.writes == []


async def test_cc1_donnees_a_restaurer(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    cc1 = climate_id(hass, init_integration, "cc1_heating")
    entity = hass.data["climate"].get_entity(cc1)
    assert entity.extra_restore_state_data.as_dict() == {"last_target_temperature": 35.5}

    await _set_hvac_mode(hass, cc1, HVACMode.OFF)

    # La consigne mémorisée n'est pas écrasée par l'arrêt.
    assert entity.extra_restore_state_data.as_dict() == {"last_target_temperature": 35.5}


async def test_cc1_echec_d_ecriture_a_l_arret(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    cc1 = climate_id(hass, init_integration, "cc1_heating")
    fake_client.error_addresses = {1507}

    with pytest.raises(HomeAssistantError, match="1507"):
        await _set_hvac_mode(hass, cc1, HVACMode.OFF)

    assert hass.states.get(cc1).state == HVACMode.AUTO


async def test_les_thermostats_ecs_ne_sont_pas_concernes_par_la_valeur_d_arret(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    """Pour l'ECS, « arrêt » reste l'écriture du mode global (1500 = 1)."""
    for key in ("dhw_comfort", "dhw_eco"):
        fake_client.writes.clear()
        await _set_hvac_mode(hass, climate_id(hass, init_integration, key), HVACMode.OFF)
        assert fake_client.writes == [(1500, 1)]
