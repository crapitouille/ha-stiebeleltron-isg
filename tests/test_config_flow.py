"""Tests de l'assistant de configuration et des options."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE,
    CONF_TIMEOUT,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pymodbus.exceptions import ModbusIOException

from custom_components.stiebel_isg_modbus.const import DOMAIN

from .common import HOST, FakeModbusClient

USER_INPUT = {CONF_HOST: HOST, CONF_PORT: 502, CONF_SLAVE: 1}


async def _start_flow(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_flux_nominal(hass: HomeAssistant, fake_client: FakeModbusClient) -> None:
    result = await _start_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Stiebel Eltron ISG ({HOST})"
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == f"{HOST}:502:1"
    # La validation lit la température extérieure.
    assert ("input", 506) in fake_client.reads
    # L'entrée créée est chargée.
    assert result["result"].state is config_entries.ConfigEntryState.LOADED


async def test_valeurs_par_defaut_du_formulaire(
    hass: HomeAssistant, fake_client: FakeModbusClient
) -> None:
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: 502, CONF_SLAVE: 1}


async def test_hote_nettoye(hass: HomeAssistant, fake_client: FakeModbusClient) -> None:
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_HOST: f"  {HOST} "}
    )
    await hass.async_block_till_done()

    assert result["data"][CONF_HOST] == HOST


async def test_connexion_impossible_puis_reprise(
    hass: HomeAssistant, fake_client: FakeModbusClient
) -> None:
    fake_client.connect_result = False

    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    # La cause réelle est affichée dans le message d'erreur.
    assert HOST in result["description_placeholders"]["error"]

    fake_client.connect_result = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_timeout_modbus(
    hass: HomeAssistant, fake_client: FakeModbusClient
) -> None:
    fake_client.request_exception = ModbusIOException("timeout")

    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["errors"] == {"base": "cannot_connect"}


async def test_reponse_d_erreur_du_peripherique(
    hass: HomeAssistant, fake_client: FakeModbusClient
) -> None:
    fake_client.error_addresses = {506}

    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_response"}
    assert "506" in result["description_placeholders"]["error"]


async def test_erreur_inattendue(
    hass: HomeAssistant, fake_client: FakeModbusClient
) -> None:
    with patch(
        "custom_components.stiebel_isg_modbus.config_flow._async_validate_connection",
        side_effect=RuntimeError("boom"),
    ):
        result = await _start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["errors"] == {"base": "unknown"}


async def test_connexion_fermee_apres_validation(
    hass: HomeAssistant, fake_client: FakeModbusClient
) -> None:
    fake_client.connect_result = True
    result = await _start_flow(hass)
    with patch(
        "custom_components.stiebel_isg_modbus.async_setup_entry", return_value=True
    ):
        await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        await hass.async_block_till_done()

    assert fake_client.close_calls >= 1
    assert fake_client.connected is False


async def test_deja_configure(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_deux_isg_differents(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_HOST: "192.168.0.200"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


@pytest.mark.parametrize(
    "bad_input",
    [
        {CONF_HOST: HOST, CONF_PORT: 0, CONF_SLAVE: 1},
        {CONF_HOST: HOST, CONF_PORT: 70000, CONF_SLAVE: 1},
        {CONF_HOST: HOST, CONF_PORT: 502, CONF_SLAVE: 300},
        {CONF_HOST: HOST, CONF_PORT: 502, CONF_SLAVE: -1},
    ],
)
async def test_valeurs_hors_plage_refusees(
    hass: HomeAssistant, fake_client: FakeModbusClient, bad_input: dict
) -> None:
    result = await _start_flow(hass)
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(result["flow_id"], bad_input)


async def test_options(
    hass: HomeAssistant, init_integration, fake_client: FakeModbusClient
) -> None:
    entry = init_integration
    assert entry.runtime_data.update_interval == timedelta(seconds=30)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 60, CONF_TIMEOUT: 10}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 60, CONF_TIMEOUT: 10}
    # L'entrée est rechargée avec les nouvelles options.
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert entry.runtime_data.update_interval == timedelta(seconds=60)
    assert fake_client.factory.call_args.kwargs["timeout"] == 10


@pytest.mark.parametrize(
    "bad_options",
    [
        {CONF_SCAN_INTERVAL: 1, CONF_TIMEOUT: 5},
        {CONF_SCAN_INTERVAL: 7200, CONF_TIMEOUT: 5},
        {CONF_SCAN_INTERVAL: 30, CONF_TIMEOUT: 0},
        {CONF_SCAN_INTERVAL: 30, CONF_TIMEOUT: 120},
    ],
)
async def test_options_hors_plage_refusees(
    hass: HomeAssistant, init_integration, bad_options: dict
) -> None:
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], bad_options
        )


async def test_cause_de_l_echec_journalisee(
    hass: HomeAssistant, fake_client: FakeModbusClient, caplog: pytest.LogCaptureFixture
) -> None:
    fake_client.request_exception = ModbusIOException("pas de réponse de l'ISG")

    result = await _start_flow(hass)
    with caplog.at_level("WARNING"):
        await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert any(
        "pas de réponse de l'ISG" in record.getMessage() for record in caplog.records
    )


async def test_champs_numeriques_en_saisie_libre(hass: HomeAssistant) -> None:
    """Non-régression : port / slave ne doivent pas être affichés en curseur."""
    from homeassistant.helpers.selector import NumberSelector, NumberSelectorMode

    result = await _start_flow(hass)
    schema = {str(key): value for key, value in result["data_schema"].schema.items()}

    for field in (CONF_PORT, CONF_SLAVE):
        assert isinstance(schema[field], NumberSelector)
        assert schema[field].config["mode"] == NumberSelectorMode.BOX
    assert schema[CONF_SLAVE].config["min"] == 0
    assert schema[CONF_SLAVE].config["max"] == 247


async def test_valeurs_numeriques_converties_en_entiers(
    hass: HomeAssistant, fake_client: FakeModbusClient
) -> None:
    """Le NumberSelector renvoie des flottants : les données doivent rester entières."""
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: 502.0, CONF_SLAVE: 3.0}
    )
    await hass.async_block_till_done()

    data = result["data"]
    assert data == {CONF_HOST: HOST, CONF_PORT: 502, CONF_SLAVE: 3}
    assert type(data[CONF_PORT]) is int
    assert type(data[CONF_SLAVE]) is int
    assert result["result"].unique_id == f"{HOST}:502:3"
