"""Registres Modbus de l'ISG Stiebel Eltron et (dé)codage des valeurs.

Ce module ne dépend pas de Home Assistant : il peut donc être testé seul.

Les adresses sont transmises telles quelles à pymodbus (adressage base 0),
exactement comme dans la configuration YAML d'origine du module `modbus:` de
Home Assistant (ex. : documentation 507 en base 1 -> adresse 506).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# Valeur sentinelle renvoyée par l'ISG quand une sonde n'est pas disponible.
INVALID_INT16 = -32768

# Valeur (0x9000) écrite dans le registre de consigne fixe pour la désactiver :
# c'est le « arrêt » du chauffage CC1 (state_off de l'ancien configuration.yaml).
SETPOINT_OFF_VALUE = 36864

TEMPERATURE_SCALE = 0.1
TEMPERATURE_PRECISION = 1

# Valeurs identiques aux HVACMode de Home Assistant (off / auto / heat).
MODE_OFF = "off"
MODE_AUTO = "auto"
MODE_HEAT = "heat"


def to_signed16(raw: int) -> int:
    """Interprète un registre 16 bits non signé comme un entier signé."""
    raw &= 0xFFFF
    return raw - 0x10000 if raw >= 0x8000 else raw


def decode_temperature(
    raw: int | None,
    scale: float = TEMPERATURE_SCALE,
    precision: int = TEMPERATURE_PRECISION,
) -> float | None:
    """Convertit un registre brut (int16, échelle 0,1) en degrés Celsius.

    Renvoie None si le registre n'a pas été lu ou si l'ISG signale une sonde
    indisponible.
    """
    if raw is None:
        return None
    value = to_signed16(raw)
    if value == INVALID_INT16:
        return None
    return round(value * scale, precision)


def decode_setpoint(
    raw: int | None,
    off_value: int | None = None,
    scale: float = TEMPERATURE_SCALE,
    precision: int = TEMPERATURE_PRECISION,
) -> float | None:
    """Décode un registre de consigne ; None si la consigne est désactivée.

    Quand `off_value` est défini et que le registre le contient, ce n'est pas une
    température (ex. 36864) : il n'y a alors pas de consigne.
    """
    if raw is not None and off_value is not None and raw == off_value:
        return None
    return decode_temperature(raw, scale, precision)


def encode_temperature(value: float, scale: float = TEMPERATURE_SCALE) -> int:
    """Convertit des degrés Celsius en valeur de registre (16 bits non signé)."""
    raw = round(value / scale)
    if not -0x8000 <= raw <= 0x7FFF:
        raise ValueError(f"Température hors plage du registre int16 : {value}")
    return raw & 0xFFFF


@dataclass(frozen=True)
class ModeMapping:
    """Correspondance entre un registre de mode et les modes HVAC.

    `values` associe chaque mode ("off", "auto", "heat") à la liste des valeurs
    du registre qui le représentent. En écriture, la première valeur est utilisée.
    """

    address: int
    values: Mapping[str, tuple[int, ...]]

    @property
    def modes(self) -> list[str]:
        """Modes supportés, dans l'ordre de déclaration."""
        return list(self.values)

    def mode_from_raw(self, raw: int | None) -> str | None:
        """Renvoie le mode correspondant à la valeur du registre (ou None)."""
        if raw is None:
            return None
        for mode, raws in self.values.items():
            if raw in raws:
                return mode
        return None

    def raw_from_mode(self, mode: str) -> int:
        """Renvoie la valeur à écrire pour activer le mode demandé."""
        try:
            return self.values[mode][0]
        except (KeyError, IndexError) as err:
            raise ValueError(f"Mode non supporté : {mode}") from err


@dataclass(frozen=True)
class SensorDef:
    """Capteur de température en lecture seule (registre d'entrée)."""

    key: str
    address: int


@dataclass(frozen=True)
class ClimateDef:
    """Thermostat : température actuelle, consigne et mode de fonctionnement."""

    key: str
    current_address: int  # registre d'entrée : température actuelle
    target_address: int  # registre holding : consigne (lecture + écriture)
    min_temp: float
    max_temp: float
    step: float
    mode: ModeMapping
    # Si défini, « arrêt » = écrire cette valeur dans le registre de consigne
    # (au lieu d'écrire le registre de mode global).
    off_value: int | None = None


# --- Configuration reprise de l'ancien configuration.yaml ---------------------

OUTDOOR_TEMPERATURE_ADDRESS = 506

# Mode de fonctionnement global de la pompe à chaleur (registre 1500) :
# 1 = veille, 2 = automatique, 3/4/5 = chauffage (jour / réduit / ECS).
GLOBAL_MODE = ModeMapping(
    address=1500,
    values={
        MODE_OFF: (1,),
        MODE_AUTO: (2,),
        MODE_HEAT: (3, 4, 5),
    },
)

SENSORS: tuple[SensorDef, ...] = (
    SensorDef(key="outdoor_temperature", address=OUTDOOR_TEMPERATURE_ADDRESS),
    SensorDef(key="cc1_temperature", address=507),
    SensorDef(key="dhw_temperature", address=521),
)

CLIMATES: tuple[ClimateDef, ...] = (
    ClimateDef(
        key="cc1_heating",
        current_address=507,
        target_address=1507,
        min_temp=20,
        max_temp=45,
        step=0.5,
        mode=GLOBAL_MODE,
        off_value=SETPOINT_OFF_VALUE,
    ),
    ClimateDef(
        key="dhw_comfort",
        current_address=521,
        target_address=1509,
        min_temp=40,
        max_temp=60,
        step=0.5,
        mode=GLOBAL_MODE,
    ),
    ClimateDef(
        key="dhw_eco",
        current_address=521,
        target_address=1510,
        min_temp=40,
        max_temp=60,
        step=0.5,
        mode=GLOBAL_MODE,
    ),
)


def input_addresses() -> list[int]:
    """Adresses des registres d'entrée à lire à chaque cycle (sans doublon)."""
    addresses = {sensor.address for sensor in SENSORS}
    addresses |= {climate.current_address for climate in CLIMATES}
    return sorted(addresses)


def holding_addresses() -> list[int]:
    """Adresses des registres holding à lire à chaque cycle (sans doublon)."""
    addresses = {climate.target_address for climate in CLIMATES}
    addresses |= {climate.mode.address for climate in CLIMATES}
    return sorted(addresses)
