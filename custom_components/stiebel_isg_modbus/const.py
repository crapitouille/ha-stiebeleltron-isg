"""Constantes de l'intégration Stiebel Eltron ISG (Modbus TCP)."""

from __future__ import annotations

DOMAIN = "stiebel_isg_modbus"

MANUFACTURER = "Stiebel Eltron"
MODEL = "ISG (Modbus TCP)"

DEFAULT_PORT = 502
DEFAULT_SLAVE = 1
DEFAULT_TIMEOUT = 5
DEFAULT_SCAN_INTERVAL = 30

MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 3600
