"""Assistant de configuration de l'intégration Stiebel Eltron ISG."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE,
    CONF_TIMEOUT,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLAVE,
    DEFAULT_TIMEOUT,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import IsgConfigEntry
from .hub import IsgConnectionError, IsgModbusError, IsgModbusHub
from .registers import OUTDOOR_TEMPERATURE_ADDRESS

_LOGGER = logging.getLogger(__name__)


def _number_box(minimum: int, maximum: int, unit: str | None = None) -> NumberSelector:
    """Champ numérique en saisie libre (et non en curseur)."""
    config = NumberSelectorConfig(
        min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): _number_box(1, 65535),
        vol.Required(CONF_SLAVE, default=DEFAULT_SLAVE): _number_box(0, 247),
    }
)


async def _async_validate_connection(host: str, port: int, slave: int) -> None:
    """Vérifie que l'ISG répond en lisant la température extérieure."""
    hub = IsgModbusHub(host=host, port=port, slave=slave, timeout=DEFAULT_TIMEOUT)
    try:
        await hub.async_read_input_register(OUTDOOR_TEMPERATURE_ADDRESS)
    finally:
        await hub.async_close()


class IsgConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configuration via l'interface de Home Assistant."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Étape unique : adresse de l'ISG."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            slave = int(user_input[CONF_SLAVE])

            await self.async_set_unique_id(f"{host.lower()}:{port}:{slave}")
            self._abort_if_unique_id_configured()

            try:
                await _async_validate_connection(host, port, slave)
            except IsgConnectionError as err:
                _LOGGER.warning(
                    "Connexion à l'ISG %s:%s impossible : %s", host, port, err
                )
                errors["base"] = "cannot_connect"
                placeholders["error"] = str(err)
            except IsgModbusError as err:
                _LOGGER.warning(
                    "L'ISG %s:%s (esclave %s) a répondu par une erreur : %s",
                    host,
                    port,
                    slave,
                    err,
                )
                errors["base"] = "invalid_response"
                placeholders["error"] = str(err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Erreur inattendue lors de la validation")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"Stiebel Eltron ISG ({host})",
                    data={CONF_HOST: host, CONF_PORT: port, CONF_SLAVE: slave},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
            description_placeholders=placeholders or None,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: IsgConfigEntry) -> OptionsFlow:
        """Renvoie l'assistant d'options."""
        return IsgOptionsFlow()


class IsgOptionsFlow(OptionsFlow):
    """Options : intervalle d'interrogation et délai d'attente."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Gère les options."""
        if user_input is not None:
            return self.async_create_entry(
                data={key: int(value) for key, value in user_input.items()}
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): _number_box(MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL, "s"),
                vol.Required(
                    CONF_TIMEOUT,
                    default=options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                ): _number_box(1, 60, "s"),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
