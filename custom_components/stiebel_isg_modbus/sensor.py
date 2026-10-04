"""Capteurs de température de l'ISG Stiebel Eltron."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import IsgConfigEntry, IsgCoordinator
from .registers import SENSORS, SensorDef, decode_temperature

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IsgConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crée les capteurs de température."""
    coordinator = entry.runtime_data
    async_add_entities(
        IsgTemperatureSensor(coordinator, entry.entry_id, definition)
        for definition in SENSORS
    )


class IsgTemperatureSensor(CoordinatorEntity[IsgCoordinator], SensorEntity):
    """Température lue dans un registre d'entrée de l'ISG."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(
        self, coordinator: IsgCoordinator, entry_id: str, definition: SensorDef
    ) -> None:
        """Initialise le capteur."""
        super().__init__(coordinator)
        self._definition = definition
        self._attr_translation_key = definition.key
        self._attr_unique_id = f"{entry_id}_{definition.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> float | None:
        """Température en °C (None si la sonde est indisponible)."""
        return decode_temperature(self.coordinator.data.input.get(self._definition.address))

    @property
    def available(self) -> bool:
        """Indisponible si la communication échoue ou si la sonde est absente."""
        return super().available and self.native_value is not None
