"""Client Modbus TCP asynchrone pour l'ISG Stiebel Eltron."""

from __future__ import annotations

import inspect
import logging
from asyncio import Lock
from typing import Any

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

_LOGGER = logging.getLogger(__name__)


class IsgModbusError(Exception):
    """Erreur Modbus générique."""


class IsgConnectionError(IsgModbusError):
    """Connexion impossible, perdue ou délai dépassé."""


class IsgRegisterError(IsgModbusError):
    """L'ISG a répondu par une erreur Modbus (adresse invalide, etc.)."""


def _detect_unit_kwarg(client: Any) -> str:
    """Nom du paramètre d'identifiant esclave selon la version de pymodbus.

    pymodbus >= 3.10 utilise `device_id`, les versions précédentes `slave`.
    """
    try:
        parameters = inspect.signature(client.read_holding_registers).parameters
    except (TypeError, ValueError):
        return "slave"
    return "device_id" if "device_id" in parameters else "slave"


class IsgModbusHub:
    """Connexion partagée et sérialisée vers l'ISG.

    L'ISG n'accepte qu'un nombre très limité de connexions TCP simultanées : une
    seule connexion est donc conservée et toutes les requêtes passent par un
    verrou.
    """

    def __init__(
        self,
        host: str,
        port: int,
        slave: int,
        timeout: int,
        client: Any | None = None,
    ) -> None:
        """Initialise le client (injectable pour les tests)."""
        self._host = host
        self._port = port
        self._slave = slave
        self._client = client or AsyncModbusTcpClient(host, port=port, timeout=timeout)
        self._unit_kwarg = _detect_unit_kwarg(self._client)
        self._lock = Lock()

    @property
    def host(self) -> str:
        """Hôte de l'ISG."""
        return self._host

    async def _async_close_client(self) -> None:
        result = self._client.close()
        if inspect.isawaitable(result):
            await result

    async def _async_ensure_connected(self) -> None:
        """Ouvre la connexion si nécessaire (verrou déjà pris)."""
        if self._client.connected:
            return
        try:
            connected = await self._client.connect()
        except (ModbusException, OSError, TimeoutError) as err:
            raise IsgConnectionError(
                f"Connexion à {self._host}:{self._port} impossible : {err}"
            ) from err
        if not connected:
            raise IsgConnectionError(
                f"Connexion à {self._host}:{self._port} impossible"
            )

    async def _async_request(
        self, method_name: str, address: int, *args: int
    ) -> Any:
        """Exécute une requête Modbus et renvoie la réponse."""
        async with self._lock:
            await self._async_ensure_connected()
            method = getattr(self._client, method_name)
            unit = {self._unit_kwarg: self._slave}
            try:
                if args:
                    result = await method(address, *args, **unit)
                else:
                    result = await method(address, count=1, **unit)
            except (ModbusException, OSError, TimeoutError) as err:
                await self._async_close_client()
                raise IsgConnectionError(
                    f"Échec de la requête Modbus (registre {address}) : {err}"
                ) from err

            if result.isError():
                if isinstance(result, ModbusException):
                    # Pas de réponse exploitable : on force une reconnexion.
                    await self._async_close_client()
                    raise IsgConnectionError(
                        f"Pas de réponse Modbus (registre {address}) : {result}"
                    )
                raise IsgRegisterError(
                    f"Erreur Modbus sur le registre {address} : {result}"
                )
            return result

    async def async_read_input_register(self, address: int) -> int:
        """Lit un registre d'entrée (function code 0x04)."""
        result = await self._async_request("read_input_registers", address)
        return int(result.registers[0])

    async def async_read_holding_register(self, address: int) -> int:
        """Lit un registre holding (function code 0x03)."""
        result = await self._async_request("read_holding_registers", address)
        return int(result.registers[0])

    async def async_write_register(self, address: int, value: int) -> None:
        """Écrit un registre holding (function code 0x06)."""
        await self._async_request("write_register", address, value)
        _LOGGER.debug("Registre %s écrit avec la valeur %s", address, value)

    async def async_close(self) -> None:
        """Ferme la connexion."""
        async with self._lock:
            await self._async_close_client()
