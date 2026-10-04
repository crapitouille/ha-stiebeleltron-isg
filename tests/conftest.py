"""Fixtures communes aux tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .common import FakeModbusClient, make_entry

CLIENT_PATH = "custom_components.stiebel_isg_modbus.hub.AsyncModbusTcpClient"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Autorise le chargement de custom_components dans tous les tests."""


@pytest.fixture
def fake_client() -> Generator[FakeModbusClient]:
    """Remplace AsyncModbusTcpClient par un faux ISG."""
    client = FakeModbusClient()
    with patch(CLIENT_PATH, return_value=client) as factory:
        client.factory = factory
        yield client


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """Entrée de configuration par défaut."""
    return make_entry()


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    fake_client: FakeModbusClient,
    mock_entry: MockConfigEntry,
) -> MockConfigEntry:
    """Installe l'intégration avec le faux ISG par défaut.

    Le temps est figé *avant* l'installation, sinon le minuteur du coordinateur
    serait planifié sur l'horloge réelle puis comparé à l'horloge figée.
    """
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    return mock_entry
