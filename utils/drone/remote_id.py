"""Remote ID payload normalization and lightweight decoding helpers."""

from __future__ import annotations

import json
from typing import Any


DRONE_ID_KEYS = ('uas_id', 'drone_id', 'serial_number', 'serial', 'id', 'uasId')
OPERATOR_ID_KEYS = ('operator_id', 'pilot_id', 'operator', 'operatorId')
LAT_KEYS = ('lat', 'latitude')
LON_KEYS = ('lon', 'lng', 'longitude')
ALT_KEYS = ('alt', 'altitude', 'altitude_m', 'height')
SPEED_KEYS = ('speed', 'speed_mps', 'ground_speed')
HEADING_KEYS = ('heading', 'heading_deg', 'course')


def _get_nested(data: dict, *paths: str) -> Any:
    for path in paths:
        current: Any = data
        valid = True
        for part in path.split('.'):
            if not isinstance(current, dict) or part not in current:
                valid = False
                break
            current = current[part]
        if valid:
            return current
    return None


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == '':
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick(data: dict, keys: tuple[str, ...], nested_prefixes: tuple[str, ...] = ()) -> Any:
    for key in keys:
        if key in data:
            return data.get(key)
    for prefix in nested_prefixes:
        for key in keys:
            hit = _get_nested(data, f'{prefix}.{key}')
            if hit is not None:
                return hit
    return None


def _normalize_input(payload: Any) -> tuple[dict, str]:
    if isinstance(payload, dict):
        return payload, 'dict'

    if isinstance(payload, bytes):
        text = payload.decode('utf-8', errors='replace').strip()
    else:
        text = str(payload or '').strip()

    if not text:
        return {}, 'empty'

    # JSON-first parsing.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed, 'json'
    except json.JSONDecodeError:
        pass

    # Keep opaque string payload available to caller.
    return {'raw': text}, 'raw'


def decode_remote_id_payload(payload: Any) -> dict:
    """Decode/normalize Remote ID-like payload into a common shape."""
    data, fmt = _normalize_input(payload)

    drone_id = _pick(data, DRONE_ID_KEYS, ('remote_id', 'message', 'uas'))
    operator_id = _pick(data, OPERATOR_ID_KEYS, ('remote_id', 'message', 'operator'))

    lat = _coerce_float(_pick(data, LAT_KEYS, ('remote_id', 'message', 'position')))
    lon = _coerce_float(_pick(data, LON_KEYS, ('remote_id', 'message', 'position')))
    altitude_m = _coerce_float(_pick(data, ALT_KEYS, ('remote_id', 'message', 'position')))
    speed_mps = _coerce_float(_pick(data, SPEED_KEYS, ('remote_id', 'message', 'position')))
    heading_deg = _coerce_float(_pick(data, HEADING_KEYS, ('remote_id', 'message', 'position')))

    confidence = 0.0
    if drone_id:
        confidence += 0.35
    if lat is not None and lon is not None:
        confidence += 0.35
    if altitude_m is not None:
        confidence += 0.15
    if operator_id:
        confidence += 0.15
    confidence = min(1.0, round(confidence, 3))

    detected = bool(drone_id or (lat is not None and lon is not None and confidence >= 0.35))

    normalized = {
        'detected': detected,
        'source_format': fmt,
        'uas_id': str(drone_id).strip() if drone_id else None,
        'operator_id': str(operator_id).strip() if operator_id else None,
        'lat': lat,
        'lon': lon,
        'altitude_m': altitude_m,
        'speed_mps': speed_mps,
        'heading_deg': heading_deg,
        'confidence': confidence,
        'raw': data,
    }

    # Remove heavy raw payload if we successfully extracted structure.
    if detected and isinstance(data, dict) and len(data) > 0:
        normalized['raw'] = data

    return normalized
