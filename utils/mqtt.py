"""
Optional MQTT data export.

Publishes decoded events from all modules to a configurable MQTT broker.
Disabled when INTERCEPT_MQTT_BROKER is not set — no broker, no connection,
no error.

Topics follow the pattern:
    <prefix>/<module>/<event_type>

Payload is JSON, matching the SSE event structure already used by the
frontend.  Configure via environment variables:

    INTERCEPT_MQTT_BROKER        broker hostname / IP  (required to enable)
    INTERCEPT_MQTT_PORT          port (default 1883)
    INTERCEPT_MQTT_USER          username (optional)
    INTERCEPT_MQTT_PASSWORD      password (optional)
    INTERCEPT_MQTT_TOPIC_PREFIX  topic prefix (default "intercept")
    INTERCEPT_MQTT_RETAIN        retain flag (default false)
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()
_enabled: bool | None = None  # None = not yet initialised


def _get_config():
    import config

    return config


def _is_enabled() -> bool:
    global _enabled
    if _enabled is None:
        cfg = _get_config()
        _enabled = bool(cfg.MQTT_BROKER)
    return _enabled


def _get_client():
    """Return a connected paho MQTT client, creating it on first call."""
    global _client
    if _client is not None:
        return _client

    with _lock:
        if _client is not None:
            return _client

        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.warning("paho-mqtt not installed; MQTT export disabled. Install with: pip install paho-mqtt")
            return None

        cfg = _get_config()

        client = mqtt.Client(client_id="intercept", clean_session=True)
        if cfg.MQTT_USER:
            client.username_pw_set(cfg.MQTT_USER, cfg.MQTT_PASSWORD or None)

        try:
            client.connect(cfg.MQTT_BROKER, cfg.MQTT_PORT, keepalive=60)
            client.loop_start()
            logger.info("MQTT connected to %s:%d (prefix=%s)", cfg.MQTT_BROKER, cfg.MQTT_PORT, cfg.MQTT_TOPIC_PREFIX)
            _client = client
        except Exception as exc:
            logger.warning("MQTT connect failed: %s — export disabled until restart", exc)
            return None

    return _client


def publish(mode: str, event: dict[str, Any], event_type: str | None = None) -> None:
    """Publish a decoded event to the MQTT broker.

    Args:
        mode:       Source module name (e.g. 'aprs', 'adsb', 'pager').
        event:      The decoded event dict.
        event_type: Optional sub-type string (e.g. 'packet', 'aircraft').
    """
    if not _is_enabled():
        return

    client = _get_client()
    if client is None:
        return

    cfg = _get_config()
    subtopic = event_type or mode
    topic = f"{cfg.MQTT_TOPIC_PREFIX}/{mode}/{subtopic}"

    try:
        payload = json.dumps(event, default=str)
        client.publish(topic, payload, retain=cfg.MQTT_RETAIN)
    except Exception as exc:
        logger.debug("MQTT publish error on %s: %s", topic, exc)
