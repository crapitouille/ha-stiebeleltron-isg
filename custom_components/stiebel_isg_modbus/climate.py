"""Thermostats de l'ISG Stiebel Eltron (chauffage CC1 et ECS)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IsgConfigEntry, IsgCoordinator
from .hub import IsgModbusError
from .registers import (
    CLIMATES,
    ClimateDef,
    decode_temperature,
    decode_setpoint,
    encode_temperature,
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IsgConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crée les thermostats."""
    coordinator = entry.runtime_data
    async_add_entities(
        IsgClimate(coordinator, entry.entry_id, definition) for definition in CLIMATES
    )


@dataclass
class IsgClimateExtraData(ExtraStoredData):
    """Données conservées au redémarrage : dernière consigne connue."""

    last_target_temperature: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Représentation sérialisable."""
        return {"last_target_temperature": self.last_target_temperature}


class IsgClimate(CoordinatorEntity[IsgCoordinator], ClimateEntity, RestoreEntity):
    """Thermostat dont la consigne est un registre holding de l'ISG.

    Pour CC1, « arrêt » consiste à écrire 36864 dans le registre de consigne
    (1507) : la consigne fixe est alors désactivée. La dernière consigne valide
    est mémorisée pour pouvoir être réécrite en quittant l'arrêt.
    """

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(
        self, coordinator: IsgCoordinator, entry_id: str, definition: ClimateDef
    ) -> None:
        """Initialise le thermostat."""
        super().__init__(coordinator)
        self._definition = definition
        self._last_target: float | None = None
        self._attr_translation_key = definition.key
        self._attr_unique_id = f"{entry_id}_{definition.key}"
        self._attr_device_info = coordinator.device_info
        self._attr_min_temp = definition.min_temp
        self._attr_max_temp = definition.max_temp
        self._attr_target_temperature_step = definition.step
        self._attr_hvac_modes = [HVACMode(mode) for mode in definition.mode.modes]

    # --- Lecture -------------------------------------------------------------

    @property
    def _setpoint_raw(self) -> int | None:
        return self.coordinator.data.holding.get(self._definition.target_address)

    @property
    def _is_off_by_setpoint(self) -> bool:
        """Vrai si la consigne fixe est désactivée (valeur d'arrêt dans le registre)."""
        off_value = self._definition.off_value
        return off_value is not None and self._setpoint_raw == off_value

    @property
    def current_temperature(self) -> float | None:
        """Température actuelle."""
        return decode_temperature(
            self.coordinator.data.input.get(self._definition.current_address)
        )

    @property
    def target_temperature(self) -> float | None:
        """Consigne actuelle (None si la consigne est désactivée)."""
        return decode_setpoint(self._setpoint_raw, self._definition.off_value)

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Mode de fonctionnement."""
        if self._is_off_by_setpoint:
            return HVACMode.OFF
        mode = self._definition.mode.mode_from_raw(
            self.coordinator.data.holding.get(self._definition.mode.address)
        )
        return HVACMode(mode) if mode is not None else None

    # --- Mémorisation de la dernière consigne -------------------------------

    def _remember_target(self) -> None:
        """Retient la consigne courante si c'est une vraie température."""
        if (target := self.target_temperature) is not None:
            self._last_target = target

    @callback
    def _handle_coordinator_update(self) -> None:
        """Met à jour la dernière consigne connue à chaque rafraîchissement."""
        self._remember_target()
        super()._handle_coordinator_update()

    @property
    def extra_restore_state_data(self) -> IsgClimateExtraData:
        """Dernière consigne, conservée au redémarrage de Home Assistant."""
        return IsgClimateExtraData(self._last_target)

    async def async_added_to_hass(self) -> None:
        """Restaure la dernière consigne puis lit la valeur courante."""
        await super().async_added_to_hass()
        if (extra := await self.async_get_last_extra_data()) is not None:
            restored = extra.as_dict().get("last_target_temperature")
            if (
                isinstance(restored, int | float)
                and not isinstance(restored, bool)
                and self.min_temp <= restored <= self.max_temp
            ):
                self._last_target = float(restored)
        self._remember_target()

    # --- Écriture ------------------------------------------------------------

    async def _async_write(self, address: int, raw: int) -> None:
        """Écrit un registre en convertissant les erreurs pour Home Assistant."""
        try:
            await self.coordinator.async_write_holding(address, raw)
        except IsgModbusError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"address": str(address), "error": str(err)},
            ) from err

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Modifie la consigne (ce qui réactive la consigne si elle était à l'arrêt)."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await self._async_write(
            self._definition.target_address, encode_temperature(temperature)
        )

    async def _async_restore_setpoint(self) -> None:
        """Réécrit la dernière consigne connue pour quitter l'arrêt."""
        if self._last_target is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_previous_setpoint"
            )
        await self._async_write(
            self._definition.target_address, encode_temperature(self._last_target)
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Modifie le mode de fonctionnement."""
        definition = self._definition
        mode_map = definition.mode

        if definition.off_value is not None:
            if hvac_mode == HVACMode.OFF:
                # Arrêt : désactive la consigne fixe (valeur 36864).
                await self._async_write(definition.target_address, definition.off_value)
                return
            if self._is_off_by_setpoint:
                await self._async_restore_setpoint()
                current_mode = mode_map.mode_from_raw(
                    self.coordinator.data.holding.get(mode_map.address)
                )
                if current_mode == hvac_mode.value:
                    # Le mode global (partagé avec l'ECS) est déjà le bon.
                    return

        await self._async_write(mode_map.address, mode_map.raw_from_mode(hvac_mode.value))
