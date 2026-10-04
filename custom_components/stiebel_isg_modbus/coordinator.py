"""Coordinateur de mise à jour des registres de l'ISG."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, MANUFACTURER, MODEL
from .hub import IsgConnectionError, IsgModbusHub, IsgRegisterError
from .registers import holding_addresses, input_addresses

_LOGGER = logging.getLogger(__name__)


@dataclass
class IsgData:
    """Dernières valeurs brutes lues (None = registre illisible)."""

    input: dict[int, int | None] = field(default_factory=dict)
    holding: dict[int, int | None] = field(default_factory=dict)


type IsgConfigEntry = ConfigEntry[IsgCoordinator]


class IsgCoordinator(DataUpdateCoordinator[IsgData]):
    """Lit périodiquement tous les registres nécessaires aux entités."""

    config_entry: IsgConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: IsgConfigEntry, hub: IsgModbusHub
    ) -> None:
        """Initialise le coordinateur."""
        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.hub = hub
        self._unreadable: set[tuple[str, int]] = set()
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Stiebel Eltron ISG",
            manufacturer=MANUFACTURER,
            model=MODEL,
            configuration_url=f"http://{entry.data[CONF_HOST]}",
        )

    async def _async_read(
        self,
        kind: str,
        reader: Callable[[int], Awaitable[int]],
        address: int,
    ) -> int | None:
        """Lit un registre ; une erreur propre au registre n'est pas fatale."""
        try:
            value = await reader(address)
        except IsgRegisterError as err:
            if (kind, address) not in self._unreadable:
                _LOGGER.warning(
                    "Registre %s %s illisible, entités concernées indisponibles : %s",
                    kind,
                    address,
                    err,
                )
                self._unreadable.add((kind, address))
            return None
        self._unreadable.discard((kind, address))
        return value

    async def _async_update_data(self) -> IsgData:
        """Lit tous les registres. Une perte de connexion interrompt le cycle."""
        data = IsgData()
        try:
            for address in input_addresses():
                data.input[address] = await self._async_read(
                    "input", self.hub.async_read_input_register, address
                )
            for address in holding_addresses():
                data.holding[address] = await self._async_read(
                    "holding", self.hub.async_read_holding_register, address
                )
        except IsgConnectionError as err:
            raise UpdateFailed(f"Communication avec l'ISG impossible : {err}") from err
        return data

    async def async_write_holding(self, address: int, raw: int) -> None:
        """Écrit un registre holding puis met à jour les données localement.

        La valeur est répercutée immédiatement (le cycle suivant la confirmera).
        Lève IsgModbusError en cas d'échec.
        """
        await self.hub.async_write_register(address, raw)
        if self.data is None:
            return
        self.async_set_updated_data(
            IsgData(
                input=dict(self.data.input),
                holding={**self.data.holding, address: raw},
            )
        )
