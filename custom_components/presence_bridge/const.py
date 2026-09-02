"""Constants for Presence Bridge."""

from __future__ import annotations

DOMAIN = "presence_bridge"
PLATFORMS = ["binary_sensor", "device_tracker", "sensor"]

STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1

TOPIC_ROOT = "presence_bridge/v1/observers"
TOPIC_STATUS = f"{TOPIC_ROOT}/+/status"
TOPIC_OBSERVATIONS = f"{TOPIC_ROOT}/+/observations"
TOPIC_PAIRING_STATUS = f"{TOPIC_ROOT}/+/pairing/status"
TOPIC_PAIRING_RESULT = f"{TOPIC_ROOT}/+/pairing/result"

GATT_SERVICE_UUID = "61dd168c-4ec1-40de-a78c-ccdce5774bba"
GATT_SESSION_UUID = "ef70387a-ba9d-4e83-9171-fea99252b57a"
GATT_CLAIM_UUID = "700dbb64-64ed-4f51-adae-b106a00e908a"
GATT_RESULT_UUID = "fb5312b1-c24c-42b5-8d21-5263874c258f"

DEFAULT_AWAY_TIMEOUT = 180
DEFAULT_OBSERVER_TIMEOUT = 75
DEFAULT_PAIRING_TIMEOUT = 180
MIN_PAIRING_TIMEOUT = 60
MAX_PAIRING_TIMEOUT = 600
MIN_RSSI = -105

CONF_AWAY_TIMEOUT = "away_timeout"
CONF_OBSERVER_TIMEOUT = "observer_timeout"

SIGNAL_IDENTITIES_UPDATED = f"{DOMAIN}_identities_updated"
SIGNAL_STATE_UPDATED = f"{DOMAIN}_state_updated"

PANEL_URL = "presence-bridge"
PANEL_TITLE = "Presence Bridge"
PANEL_ICON = "mdi:account-key"
STATIC_URL = "/presence_bridge_static"
