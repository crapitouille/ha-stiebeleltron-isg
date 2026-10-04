"""Intégration Stiebel Eltron ISG (Modbus TCP)."""

from __future__ import annotations

from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_SLAVE,
    CONF_TIMEOUT,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DEFAULT_TIMEOUT
from .coordinator import IsgConfigEntry, IsgCoordinator
from .hub import IsgModbusHub

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.CLIMATE]


async def async_setup_entry(hass: HomeAssistant, entry: IsgConfigEntry) -> bool:
    """Initialise l'intégration à partir d'une entrée de configuration."""
    hub = IsgModbusHub(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        slave=entry.data[CONF_SLAVE],
        timeout=entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
    )
    coordinator = IsgCoordinator(hass, entry, hub)

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await hub.async_close()
        raise

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: IsgConfigEntry) -> bool:
    """Décharge l'entrée et ferme la connexion Modbus."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.hub.async_close()
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: IsgConfigEntry) -> None:
    """Recharge l'entrée quand les options changent."""
    await hass.config_entries.async_reload(entry.entry_id)
