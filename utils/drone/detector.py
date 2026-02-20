"""Heuristics for identifying drone-related emissions across WiFi/BLE/RF feeds."""

from __future__ import annotations

import re
from typing import Any

from utils.drone.remote_id import decode_remote_id_payload

SSID_PATTERNS = [
    re.compile(r'(^|[-_\s])(dji|mavic|phantom|inspire|matrice|mini)([-_\s]|$)', re.IGNORECASE),
    re.compile(r'(^|[-_\s])(parrot|anafi|bebop)([-_\s]|$)', re.IGNORECASE),
    re.compile(r'(^|[-_\s])(autel|evo)([-_\s]|$)', re.IGNORECASE),
    re.compile(r'(^|[-_\s])(skydio|yuneec)([-_\s]|$)', re.IGNORECASE),
    re.compile(r'(^|[-_\s])(uas|uav|drone|rid|opendroneid)([-_\s]|$)', re.IGNORECASE),
]

DRONE_OUI_PREFIXES = {
    '60:60:1F': 'DJI',
    '90:3A:E6': 'DJI',
    '34:D2:62': 'DJI',
    '90:3A:AF': 'DJI',
    '00:12:1C': 'Parrot',
    '90:03:B7': 'Parrot',
    '48:1C:B9': 'Autel',
    'AC:89:95': 'Skydio',
}

BT_NAME_PATTERNS = [
    re.compile(r'(dji|mavic|phantom|inspire|matrice|mini)', re.IGNORECASE),
    re.compile(r'(parrot|anafi|bebop)', re.IGNORECASE),
    re.compile(r'(autel|evo)', re.IGNORECASE),
    re.compile(r'(skydio|yuneec)', re.IGNORECASE),
    re.compile(r'(remote\s?id|opendroneid|uas|uav|drone)', re.IGNORECASE),
]

REMOTE_ID_UUID_HINTS = {'fffa', 'faff', 'fffb'}
RF_FREQ_HINTS_MHZ = (315.0, 433.92, 868.0, 915.0, 1200.0, 2400.0, 5800.0)


def _normalize_mac(value: Any) -> str:
    text = str(value or '').strip().upper().replace('-', ':')
    if len(text) >= 8:
        return text
    return ''


def _extract_wifi_event(event: dict) -> dict | None:
    if not isinstance(event, dict):
        return None
    if isinstance(event.get('network'), dict):
        return event['network']
    if event.get('type') == 'network_update' and isinstance(event.get('network'), dict):
        return event['network']
    if any(k in event for k in ('bssid', 'essid', 'ssid')):
        return event
    return None


def _extract_bt_event(event: dict) -> dict | None:
    if not isinstance(event, dict):
        return None
    if isinstance(event.get('device'), dict):
        return event['device']
    if any(k in event for k in ('device_id', 'address', 'name', 'manufacturer_name', 'service_uuids')):
        return event
    return None


def _extract_frequency_mhz(event: dict) -> float | None:
    if not isinstance(event, dict):
        return None

    candidates = [
        event.get('frequency_mhz'),
        event.get('frequency'),
    ]

    if 'frequency_hz' in event:
        try:
            candidates.append(float(event['frequency_hz']) / 1_000_000.0)
        except (TypeError, ValueError):
            pass

    for value in candidates:
        try:
            if value is None:
                continue
            f = float(value)
            if f > 100000:  # likely in Hz
                f = f / 1_000_000.0
            if 1.0 <= f <= 7000.0:
                return round(f, 6)
        except (TypeError, ValueError):
            continue

    text = str(event.get('text') or event.get('message') or '')
    match = re.search(r'([0-9]{2,4}(?:\.[0-9]+)?)\s*MHz', text, flags=re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None

    return None


def _closest_freq_delta(freq_mhz: float) -> float:
    return min(abs(freq_mhz - hint) for hint in RF_FREQ_HINTS_MHZ)


def _maybe_track_from_remote_id(remote_id: dict, source: str) -> dict | None:
    if not remote_id.get('detected'):
        return None
    lat = remote_id.get('lat')
    lon = remote_id.get('lon')
    if lat is None or lon is None:
        return None
    return {
        'lat': lat,
        'lon': lon,
        'altitude_m': remote_id.get('altitude_m'),
        'speed_mps': remote_id.get('speed_mps'),
        'heading_deg': remote_id.get('heading_deg'),
        'quality': remote_id.get('confidence', 0.0),
        'source': source,
    }


def _detect_wifi(event: dict) -> list[dict]:
    network = _extract_wifi_event(event)
    if not network:
        return []

    bssid = _normalize_mac(network.get('bssid') or network.get('mac') or network.get('id'))
    ssid = str(network.get('essid') or network.get('ssid') or network.get('display_name') or '').strip()
    identifier = bssid or ssid
    if not identifier:
        return []

    score = 0.0
    reasons: list[str] = []

    if ssid:
        for pattern in SSID_PATTERNS:
            if pattern.search(ssid):
                score += 0.45
                reasons.append('ssid_pattern')
                break

    if bssid and len(bssid) >= 8:
        prefix = bssid[:8]
        if prefix in DRONE_OUI_PREFIXES:
            score += 0.45
            reasons.append(f'known_oui:{DRONE_OUI_PREFIXES[prefix]}')

    remote_id = decode_remote_id_payload(network)
    if remote_id.get('detected'):
        score = max(score, 0.75)
        reasons.append('remote_id_payload')

    if score < 0.5:
        return []

    confidence = min(1.0, round(score, 3))
    classification = 'wifi_drone_remote_id' if remote_id.get('detected') else 'wifi_drone_signature'

    return [{
        'source': 'wifi',
        'identifier': identifier,
        'classification': classification,
        'confidence': confidence,
        'payload': {
            'network': network,
            'reasons': reasons,
            'brand_hint': DRONE_OUI_PREFIXES.get(bssid[:8]) if bssid else None,
        },
        'remote_id': remote_id if remote_id.get('detected') else None,
        'track': _maybe_track_from_remote_id(remote_id, 'wifi'),
    }]


def _detect_bluetooth(event: dict) -> list[dict]:
    device = _extract_bt_event(event)
    if not device:
        return []

    address = _normalize_mac(device.get('address') or device.get('mac'))
    device_id = str(device.get('device_id') or '').strip()
    name = str(device.get('name') or '').strip()
    manufacturer = str(device.get('manufacturer_name') or '').strip()
    identifier = address or device_id or name
    if not identifier:
        return []

    score = 0.0
    reasons: list[str] = []

    haystack = f'{name} {manufacturer}'.strip()
    if haystack:
        for pattern in BT_NAME_PATTERNS:
            if pattern.search(haystack):
                score += 0.55
                reasons.append('name_or_vendor_pattern')
                break

    uuids = device.get('service_uuids') or []
    for uuid in uuids:
        if str(uuid).replace('-', '').lower()[-4:] in REMOTE_ID_UUID_HINTS:
            score = max(score, 0.7)
            reasons.append('remote_id_service_uuid')
            break

    tracker = device.get('tracker') if isinstance(device.get('tracker'), dict) else {}
    if tracker.get('is_tracker') and 'drone' in str(tracker.get('type') or '').lower():
        score = max(score, 0.7)
        reasons.append('tracker_engine_drone_label')

    remote_id = decode_remote_id_payload(device)
    if remote_id.get('detected'):
        score = max(score, 0.75)
        reasons.append('remote_id_payload')

    if score < 0.55:
        return []

    confidence = min(1.0, round(score, 3))
    classification = 'bluetooth_drone_remote_id' if remote_id.get('detected') else 'bluetooth_drone_signature'

    return [{
        'source': 'bluetooth',
        'identifier': identifier,
        'classification': classification,
        'confidence': confidence,
        'payload': {
            'device': device,
            'reasons': reasons,
        },
        'remote_id': remote_id if remote_id.get('detected') else None,
        'track': _maybe_track_from_remote_id(remote_id, 'bluetooth'),
    }]


def _detect_rf(event: dict) -> list[dict]:
    if not isinstance(event, dict):
        return []

    freq_mhz = _extract_frequency_mhz(event)
    if freq_mhz is None:
        return []

    delta = _closest_freq_delta(freq_mhz)
    if delta > 35.0:
        return []

    score = max(0.5, 0.85 - (delta / 100.0))
    confidence = min(1.0, round(score, 3))

    event_id = str(event.get('capture_id') or event.get('id') or f'{freq_mhz:.3f}MHz')
    identifier = f'rf:{event_id}'

    payload = {
        'event': event,
        'frequency_mhz': freq_mhz,
        'delta_from_known_band_mhz': round(delta, 3),
        'known_bands_mhz': list(RF_FREQ_HINTS_MHZ),
    }

    return [{
        'source': 'rf',
        'identifier': identifier,
        'classification': 'rf_drone_link_activity',
        'confidence': confidence,
        'payload': payload,
        'remote_id': None,
        'track': None,
    }]


def detect_from_event(mode: str, event: dict, event_type: str | None = None) -> list[dict]:
    """Detect drone-relevant signals from a normalized mode event."""
    mode_lower = str(mode or '').lower()

    if mode_lower.startswith('wifi'):
        return _detect_wifi(event)
    if mode_lower.startswith('bluetooth') or mode_lower.startswith('bt'):
        return _detect_bluetooth(event)
    if mode_lower in {'subghz', 'listening_scanner', 'waterfall', 'listening'}:
        return _detect_rf(event)

    # Opportunistic decode from any feed that carries explicit remote ID payloads.
    remote_id = decode_remote_id_payload(event)
    if remote_id.get('detected'):
        identifier = str(remote_id.get('uas_id') or remote_id.get('operator_id') or 'remote_id')
        return [{
            'source': mode_lower or 'unknown',
            'identifier': identifier,
            'classification': 'remote_id_detected',
            'confidence': float(remote_id.get('confidence') or 0.6),
            'payload': {'event': event, 'event_type': event_type},
            'remote_id': remote_id,
            'track': _maybe_track_from_remote_id(remote_id, mode_lower or 'unknown'),
        }]

    return []
