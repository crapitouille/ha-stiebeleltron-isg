"""Tests du (dé)codage et de la table de registres (sans Home Assistant)."""

from __future__ import annotations

import pytest

from custom_components.stiebel_isg_modbus.registers import (
    CLIMATES,
    GLOBAL_MODE,
    INVALID_INT16,
    SENSORS,
    ModeMapping,
    SETPOINT_OFF_VALUE,
    BUFFER_SETPOINT_ADDRESS,
    decode_setpoint,
    is_heating_season,
    decode_temperature,
    encode_temperature,
    holding_addresses,
    input_addresses,
    to_signed16,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, 0), (1, 1), (32767, 32767), (32768, -32768), (65535, -1), (65526, -10)],
)
def test_to_signed16(raw: int, expected: int) -> None:
    assert to_signed16(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, 0.0),
        (85, 8.5),
        (352, 35.2),
        (123, 12.3),
        (65526, -1.0),  # -10 en int16 -> -1,0 °C
        (65535, -0.1),
        (32767, 3276.7),
    ],
)
def test_decode_temperature(raw: int, expected: float) -> None:
    assert decode_temperature(raw) == expected


def test_decode_temperature_sentinelle_et_absence() -> None:
    """0x8000 (sonde absente) et une lecture manquante donnent None."""
    assert decode_temperature(INVALID_INT16 & 0xFFFF) is None
    assert decode_temperature(None) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(35.5, 355), (20.1, 201), (35.3, 353), (41.7, 417), (44.9, 449), (0, 0)],
)
def test_encode_temperature(value: float, expected: int) -> None:
    assert encode_temperature(value) == expected


def test_encode_temperature_negative_en_complement_a_deux() -> None:
    assert encode_temperature(-1.0) == 65526


@pytest.mark.parametrize("value", [4000.0, -4000.0])
def test_encode_temperature_hors_plage(value: float) -> None:
    with pytest.raises(ValueError):
        encode_temperature(value)


@pytest.mark.parametrize("value", [20.0, 22.5, 35.3, 44.9, -5.5, 60.0])
def test_encode_decode_aller_retour(value: float) -> None:
    assert decode_temperature(encode_temperature(value)) == value


def test_mode_mapping_lecture() -> None:
    assert GLOBAL_MODE.mode_from_raw(1) == "off"
    assert GLOBAL_MODE.mode_from_raw(2) == "auto"
    assert [GLOBAL_MODE.mode_from_raw(raw) for raw in (3, 4, 5)] == ["heat"] * 3
    assert GLOBAL_MODE.mode_from_raw(99) is None
    assert GLOBAL_MODE.mode_from_raw(None) is None


def test_mode_mapping_ecriture_utilise_la_premiere_valeur() -> None:
    assert GLOBAL_MODE.raw_from_mode("off") == 1
    assert GLOBAL_MODE.raw_from_mode("auto") == 2
    assert GLOBAL_MODE.raw_from_mode("heat") == 3


def test_mode_mapping_mode_inconnu() -> None:
    mapping = ModeMapping(address=1, values={"off": (1,)})
    with pytest.raises(ValueError):
        mapping.raw_from_mode("heat")
    assert mapping.modes == ["off"]


# --- Non-régression : la table doit rester fidèle au configuration.yaml -------


def test_capteurs_conformes_au_yaml_d_origine() -> None:
    assert {s.key: s.address for s in SENSORS} == {
        "outdoor_temperature": 506,
        "cc1_temperature": 507,
        "dhw_temperature": 521,
        "buffer_setpoint": 518,
    }


def test_thermostats_conformes_au_yaml_d_origine() -> None:
    by_key = {c.key: c for c in CLIMATES}
    assert set(by_key) == {"cc1_heating", "dhw_comfort", "dhw_eco"}

    cc1 = by_key["cc1_heating"]
    assert (cc1.current_address, cc1.target_address) == (507, 1507)
    assert (cc1.min_temp, cc1.max_temp, cc1.step) == (20, 45, 0.5)

    comfort = by_key["dhw_comfort"]
    assert (comfort.current_address, comfort.target_address) == (521, 1509)
    assert (comfort.min_temp, comfort.max_temp, comfort.step) == (40, 60, 0.5)

    eco = by_key["dhw_eco"]
    assert (eco.current_address, eco.target_address) == (521, 1510)
    assert (eco.min_temp, eco.max_temp, eco.step) == (40, 60, 0.5)


def test_mode_global_conforme_au_yaml_d_origine() -> None:
    assert GLOBAL_MODE.address == 1500
    assert dict(GLOBAL_MODE.values) == {"off": (1,), "auto": (2,), "heat": (3, 4, 5)}


def test_cles_uniques() -> None:
    keys = [s.key for s in SENSORS] + [c.key for c in CLIMATES]
    assert len(keys) == len(set(keys))


def test_plages_de_consigne_coherentes() -> None:
    for climate in CLIMATES:
        assert climate.min_temp < climate.max_temp
        assert climate.step > 0


def test_adresses_a_lire_sans_doublon() -> None:
    assert input_addresses() == [506, 507, 518, 521]
    assert holding_addresses() == [1500, 1507, 1509, 1510]


# --- Valeur d'arrêt de la consigne CC1 (36864) -------------------------------


def test_valeur_d_arret_conforme_au_yaml_d_origine() -> None:
    by_key = {c.key: c for c in CLIMATES}
    assert SETPOINT_OFF_VALUE == 36864
    assert by_key["cc1_heating"].off_value == 36864
    assert by_key["dhw_comfort"].off_value is None
    assert by_key["dhw_eco"].off_value is None


def test_decode_setpoint() -> None:
    assert decode_setpoint(36864, 36864) is None
    assert decode_setpoint(355, 36864) == 35.5
    assert decode_setpoint(None, 36864) is None
    # Sans valeur d'arrêt, le décodage reste celui d'une température.
    assert decode_setpoint(355) == 35.5
    assert decode_setpoint(36864) == -2867.2


# --- Période de chauffage : consigne tampon (registre 518) --------------------


def test_adresse_de_la_consigne_tampon() -> None:
    """Doc 519 (PUFFERSOLLTEMPERATUR) -> adresse 518, confirmée sur l'ISG réel."""
    assert BUFFER_SETPOINT_ADDRESS == 518
    sensor = next(s for s in SENSORS if s.key == "buffer_setpoint")
    assert sensor.address == 518
    assert sensor.diagnostic is True
    assert not any(s.diagnostic for s in SENSORS if s.key != "buffer_setpoint")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (50, False),  # 5,0 °C : hors période de chauffage
        (51, True),  # 5,1 °C : chauffage
        (49, False),
        (0, False),
        (350, True),
        (450, True),
        (0xFFF6, False),  # négatif (-1,0 °C)
        (0x7FFF, True),
    ],
)
def test_is_heating_season_seuil_par_defaut(raw: int, expected: bool) -> None:
    assert is_heating_season(raw, 5.0) is expected


@pytest.mark.parametrize("raw", [None, 0x8000])
def test_is_heating_season_valeur_inconnue(raw: int | None) -> None:
    """Pas de conclusion « hors saison » sur une valeur absente ou invalide."""
    assert is_heating_season(raw, 5.0) is None


@pytest.mark.parametrize(
    ("raw", "threshold", "expected"),
    [(50, 4.9, True), (49, 4.9, False), (100, 10.0, False), (101, 10.0, True), (60, 5.5, True), (55, 5.5, False)],
)
def test_is_heating_season_seuil_configurable(
    raw: int, threshold: float, expected: bool
) -> None:
    assert is_heating_season(raw, threshold) is expected


def test_is_heating_season_pas_d_erreur_d_arrondi_flottant() -> None:
    """0,1 * 51 n'est pas exactement 5,1 en flottant : la comparaison reste exacte."""
    for tenths in range(0, 400):
        assert is_heating_season(tenths, 5.0) is (tenths > 50)


def test_is_heating_season_exact_pour_tous_les_seuils_au_dixieme() -> None:
    """Pour tout seuil au dixième de °C, la valeur égale au seuil n'est pas « chauffage »."""
    for tenths in range(0, 301):
        threshold = tenths / 10
        for raw in (tenths - 1, tenths, tenths + 1):
            assert is_heating_season(raw, threshold) is (raw > tenths), (raw, threshold)
