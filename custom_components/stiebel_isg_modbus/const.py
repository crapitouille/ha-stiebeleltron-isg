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

# Option : au-dessus de ce seuil (°C) de consigne tampon, on est en période de chauffage.
CONF_HEATING_THRESHOLD = "heating_threshold"
DEFAULT_HEATING_THRESHOLD = 5.0
MIN_HEATING_THRESHOLD = 0.0
MAX_HEATING_THRESHOLD = 30.0
