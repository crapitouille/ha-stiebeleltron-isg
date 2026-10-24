"""Capteur binaire « Période de chauffage » de l'ISG Stiebel Eltron."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HEATING_THRESHOLD, DEFAULT_HEATING_THRESHOLD
from .coordinator import IsgConfigEntry, IsgCoordinator
from .registers import BUFFER_SETPOINT_ADDRESS, decode_temperature, is_heating_season

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IsgConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crée le capteur binaire de période de chauffage."""
    async_add_entities([IsgHeatingSeasonBinarySensor(entry.runtime_data, entry)])


class IsgHeatingSeasonBinarySensor(CoordinatorEntity[IsgCoordinator], BinarySensorEntity):
    """Actif quand la consigne tampon dépasse le seuil (5,0 °C par défaut).

    La consigne tampon vaut 5,0 °C hors période de chauffage ; toute valeur
    supérieure signifie que l'ISG est en période de chauffage (hiver).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "heating_season"

    def __init__(self, coordinator: IsgCoordinator, entry: IsgConfigEntry) -> None:
        """Initialise le capteur binaire."""
        super().__init__(coordinator)
        self._threshold = float(
            entry.options.get(CONF_HEATING_THRESHOLD, DEFAULT_HEATING_THRESHOLD)
        )
        self._attr_unique_id = f"{entry.entry_id}_heating_season"
        self._attr_device_info = coordinator.device_info

    @property
    def _raw(self) -> int | None:
        return self.coordinator.data.input.get(BUFFER_SETPOINT_ADDRESS)

    @property
    def is_on(self) -> bool | None:
        """Vrai en période de chauffage, faux hors période, None si inconnu."""
        return is_heating_season(self._raw, self._threshold)

    @property
    def available(self) -> bool:
        """Indisponible si la communication échoue ou si la valeur est invalide."""
        return super().available and self.is_on is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Seuil utilisé et consigne tampon lue."""
        return {
            "threshold": self._threshold,
            "buffer_setpoint": decode_temperature(self._raw),
        }
