"""Tests du client Modbus (hub) avec un faux client pymodbus."""

from __future__ import annotations

import asyncio

import pytest
from pymodbus.exceptions import ConnectionException, ModbusException, ModbusIOException
from pymodbus.pdu import ExceptionResponse

from custom_components.stiebel_isg_modbus.hub import (
    IsgConnectionError,
    IsgModbusHub,
    IsgRegisterError,
)

from .common import (
    FakeModbusClient,
    FakeResponse,
    LegacyFakeModbusClient,
    SlowFakeModbusClient,
)


def make_hub(client: FakeModbusClient, slave: int = 1) -> IsgModbusHub:
    return IsgModbusHub("192.168.0.199", 502, slave, 5, client=client)


async def test_lectures_et_ecriture() -> None:
    client = FakeModbusClient()
    hub = make_hub(client)

    assert await hub.async_read_input_register(506) == 85
    assert await hub.async_read_holding_register(1507) == 355
    await hub.async_write_register(1507, 375)

    assert client.reads == [("input", 506), ("holding", 1507)]
    assert client.writes == [(1507, 375)]
    assert client.holding_registers[1507] == 375


async def test_connexion_ouverte_une_seule_fois() -> None:
    client = FakeModbusClient()
    hub = make_hub(client)

    await hub.async_read_input_register(506)
    await hub.async_read_input_register(507)

    assert client.connect_calls == 1


async def test_identifiant_esclave_transmis_avec_device_id() -> None:
    client = FakeModbusClient()
    hub = make_hub(client, slave=7)

    await hub.async_read_input_register(506)
    await hub.async_write_register(1507, 300)

    assert client.unit_ids == [7, 7]


async def test_compatibilite_ancienne_api_slave() -> None:
    """pymodbus < 3.10 : le paramètre s'appelle `slave`."""
    client = LegacyFakeModbusClient()
    hub = make_hub(client, slave=3)

    assert await hub.async_read_input_register(506) == 85
    assert await hub.async_read_holding_register(1500) == 2
    await hub.async_write_register(1509, 520)

    assert client.unit_ids == [3, 3, 3]
    assert client.writes == [(1509, 520)]


async def test_connexion_refusee() -> None:
    client = FakeModbusClient()
    client.connect_result = False
    hub = make_hub(client)

    with pytest.raises(IsgConnectionError):
        await hub.async_read_input_register(506)


@pytest.mark.parametrize(
    "error", [OSError("réseau"), TimeoutError(), ConnectionException("coupé")]
)
async def test_exception_a_la_connexion(error: Exception) -> None:
    client = FakeModbusClient()
    client.connect_exception = error
    hub = make_hub(client)

    with pytest.raises(IsgConnectionError):
        await hub.async_read_input_register(506)


@pytest.mark.parametrize("error", [ModbusIOException("timeout"), OSError("reset")])
async def test_exception_pendant_la_requete_force_la_reconnexion(
    error: Exception,
) -> None:
    client = FakeModbusClient()
    hub = make_hub(client)
    await hub.async_read_input_register(506)
    assert client.connect_calls == 1

    client.request_exception = error
    with pytest.raises(IsgConnectionError):
        await hub.async_read_input_register(506)
    assert client.close_calls == 1

    client.request_exception = None
    assert await hub.async_read_input_register(506) == 85
    assert client.connect_calls == 2


async def test_reponse_d_erreur_modbus_est_une_erreur_de_registre() -> None:
    """Une exception Modbus du périphérique ne coupe pas la connexion."""
    client = FakeModbusClient()
    client.error_addresses = {1500}
    hub = make_hub(client)

    with pytest.raises(IsgRegisterError):
        await hub.async_read_holding_register(1500)

    assert client.close_calls == 0
    assert await hub.async_read_input_register(506) == 85
    assert client.connect_calls == 1


async def test_vraie_exception_response_pymodbus() -> None:
    class ErrorClient(FakeModbusClient):
        async def read_holding_registers(self, address, **kwargs):  # type: ignore[no-untyped-def]
            return ExceptionResponse(0x83, 2)

    hub = make_hub(ErrorClient())
    with pytest.raises(IsgRegisterError):
        await hub.async_read_holding_register(1500)


async def test_reponse_ioexception_retournee_est_une_erreur_de_connexion() -> None:
    """Certaines versions renvoient l'exception au lieu de la lever."""

    class ReturningClient(FakeModbusClient):
        async def read_input_registers(self, address, **kwargs):  # type: ignore[no-untyped-def]
            return ModbusIOException("timeout")

    client = ReturningClient()
    hub = make_hub(client)
    with pytest.raises(IsgConnectionError):
        await hub.async_read_input_register(506)
    assert client.close_calls == 1
    assert isinstance(ModbusIOException("x"), ModbusException)


async def test_ecriture_refusee() -> None:
    client = FakeModbusClient()
    client.error_addresses = {1507}
    hub = make_hub(client)

    with pytest.raises(IsgRegisterError):
        await hub.async_write_register(1507, 375)
    assert client.writes == []


async def test_requetes_serialisees_par_le_verrou() -> None:
    client = SlowFakeModbusClient()
    hub = make_hub(client)

    results = await asyncio.gather(
        *(hub.async_read_input_register(a) for a in (506, 507, 521, 506, 507))
    )

    assert results == [85, 352, 487, 85, 352]
    assert client.max_in_flight == 1
    assert client.connect_calls == 1


async def test_fermeture() -> None:
    client = FakeModbusClient()
    hub = make_hub(client)
    await hub.async_read_input_register(506)

    await hub.async_close()

    assert client.close_calls == 1
    assert client.connected is False


async def test_fermeture_avec_close_asynchrone() -> None:
    closed = []

    class AsyncCloseClient(FakeModbusClient):
        async def close(self) -> None:  # type: ignore[override]
            closed.append(True)

    hub = make_hub(AsyncCloseClient())
    await hub.async_close()

    assert closed == [True]


def test_fake_response_repr() -> None:
    assert "error=False" in repr(FakeResponse([1]))


def test_propriete_host() -> None:
    assert make_hub(FakeModbusClient()).host == "192.168.0.199"


def test_detection_du_parametre_esclave_sans_signature() -> None:
    """Si la signature est introuvable, on retombe sur l'ancien nom `slave`."""
    from custom_components.stiebel_isg_modbus.hub import _detect_unit_kwarg

    client = FakeModbusClient()
    client.read_holding_registers = 42  # type: ignore[assignment]  # non appelable
    assert _detect_unit_kwarg(client) == "slave"
    assert _detect_unit_kwarg(FakeModbusClient()) == "device_id"
    assert _detect_unit_kwarg(LegacyFakeModbusClient()) == "slave"
