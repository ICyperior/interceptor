"""Cross-mode analytics: activity tracking, summaries, and emergency squawk detection."""

from __future__ import annotations

import contextlib
import time
from collections import deque
from typing import Any

import app as app_module


class ModeActivityTracker:
    """Track device counts per mode in time-bucketed ring buffer for sparklines."""

    def __init__(self, max_buckets: int = 60, bucket_interval: float = 5.0):
        self._max_buckets = max_buckets
        self._bucket_interval = bucket_interval
        self._history: dict[str, deque] = {}
        self._last_record_time = 0.0

    def record(self) -> None:
        """Snapshot current counts for all modes."""
        now = time.time()
        if now - self._last_record_time < self._bucket_interval:
            return
        self._last_record_time = now

        counts = _get_mode_counts()
        for mode, count in counts.items():
            if mode not in self._history:
                self._history[mode] = deque(maxlen=self._max_buckets)
            self._history[mode].append(count)

    def get_sparkline(self, mode: str) -> list[int]:
        """Return sparkline array for a mode."""
        self.record()
        return list(self._history.get(mode, []))

    def get_all_sparklines(self) -> dict[str, list[int]]:
        """Return sparkline arrays for all tracked modes."""
        self.record()
        return {mode: list(values) for mode, values in self._history.items()}


# Singleton
_tracker: ModeActivityTracker | None = None


def get_activity_tracker() -> ModeActivityTracker:
    global _tracker
    if _tracker is None:
        _tracker = ModeActivityTracker()
    return _tracker


def _safe_len(attr_name: str) -> int:
    """Safely get len() of an app_module attribute."""
    try:
        return len(getattr(app_module, attr_name))
    except Exception:
        return 0


def _safe_route_attr(module_path: str, attr_name: str, default: int = 0) -> int:
    """Safely read a module-level counter from a route file."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return int(getattr(mod, attr_name, default))
    except Exception:
        return default


def _get_mode_counts() -> dict[str, int]:
    """Read current entity counts from all available data sources."""
    counts: dict[str, int] = {}

    # ADS-B aircraft (DataStore)
    counts['adsb'] = _safe_len('adsb_aircraft')

    # AIS vessels (DataStore)
    counts['ais'] = _safe_len('ais_vessels')

    # WiFi: prefer v2 scanner, fall back to legacy DataStore
    wifi_count = 0
    try:
        from utils.wifi.scanner import _scanner_instance as wifi_scanner
        if wifi_scanner is not None:
            wifi_count = len(wifi_scanner.access_points)
    except Exception:
        pass
    if wifi_count == 0:
        wifi_count = _safe_len('wifi_networks')
    counts['wifi'] = wifi_count

    # Bluetooth: prefer v2 scanner, fall back to legacy DataStore
    bt_count = 0
    try:
        from utils.bluetooth.scanner import _scanner_instance as bt_scanner
        if bt_scanner is not None:
            bt_count = len(bt_scanner.get_devices())
    except Exception:
        pass
    if bt_count == 0:
        bt_count = _safe_len('bt_devices')
    counts['bluetooth'] = bt_count

    # DSC messages (DataStore)
    counts['dsc'] = _safe_len('dsc_messages')

    # ACARS message count (route-level counter)
    counts['acars'] = _safe_route_attr('routes.acars', 'acars_message_count')

    # VDL2 message count (route-level counter)
    counts['vdl2'] = _safe_route_attr('routes.vdl2', 'vdl2_message_count')

    # APRS stations (route-level dict)
    try:
        import routes.aprs as aprs_mod
        counts['aprs'] = len(getattr(aprs_mod, 'aprs_stations', {}))
    except Exception:
        counts['aprs'] = 0

    # Meshtastic recent messages (route-level list)
    try:
        import routes.meshtastic as mesh_route
        counts['meshtastic'] = len(getattr(mesh_route, '_recent_messages', []))
    except Exception:
        counts['meshtastic'] = 0

    return counts


def get_cross_mode_summary() -> dict[str, Any]:
    """Return counts dict for all available data sources."""
    counts = _get_mode_counts()
    wifi_clients_count = 0
    try:
        from utils.wifi.scanner import _scanner_instance as wifi_scanner
        if wifi_scanner is not None:
            wifi_clients_count = len(wifi_scanner.clients)
    except Exception:
        pass
    if wifi_clients_count == 0:
        wifi_clients_count = _safe_len('wifi_clients')
    counts['wifi_clients'] = wifi_clients_count
    return counts


def get_mode_health() -> dict[str, dict]:
    """Check process refs and SDR status for each mode."""
    health: dict[str, dict] = {}

    process_map = {
        'pager': 'current_process',
        'sensor': 'sensor_process',
        'adsb': 'adsb_process',
        'ais': 'ais_process',
        'acars': 'acars_process',
        'vdl2': 'vdl2_process',
        'aprs': 'aprs_process',
        'wifi': 'wifi_process',
        'bluetooth': 'bt_process',
        'dsc': 'dsc_process',
        'rtlamr': 'rtlamr_process',
    }

    for mode, attr in process_map.items():
        proc = getattr(app_module, attr, None)
        running = proc is not None and (proc.poll() is None if proc else False)
        health[mode] = {'running': running}

    # Override WiFi/BT health with v2 scanner status if available
    try:
        from utils.wifi.scanner import _scanner_instance as wifi_scanner
        if wifi_scanner is not None and wifi_scanner.is_scanning:
            health['wifi'] = {'running': True}
    except Exception:
        pass
    try:
        from utils.bluetooth.scanner import _scanner_instance as bt_scanner
        if bt_scanner is not None and bt_scanner.is_scanning:
            health['bluetooth'] = {'running': True}
    except Exception:
        pass

    # Meshtastic: check client connection status
    try:
        from utils.meshtastic import get_meshtastic_client
        client = get_meshtastic_client()
        health['meshtastic'] = {'running': client._interface is not None}
    except Exception:
        health['meshtastic'] = {'running': False}

    try:
        sdr_status = app_module.get_sdr_device_status()
        health['sdr_devices'] = {str(k): v for k, v in sdr_status.items()}
    except Exception:
        health['sdr_devices'] = {}

    return health


EMERGENCY_SQUAWKS = {
    '7700': 'General Emergency',
    '7600': 'Comms Failure',
    '7500': 'Hijack',
}


def get_emergency_squawks() -> list[dict]:
    """Iterate adsb_aircraft DataStore for emergency squawk codes."""
    emergencies: list[dict] = []
    try:
        for icao, aircraft in app_module.adsb_aircraft.items():
            sq = str(aircraft.get('squawk', '')).strip()
            if sq in EMERGENCY_SQUAWKS:
                emergencies.append({
                    'icao': icao,
                    'callsign': aircraft.get('callsign', ''),
                    'squawk': sq,
                    'meaning': EMERGENCY_SQUAWKS[sq],
                    'altitude': aircraft.get('altitude'),
                    'lat': aircraft.get('lat'),
                    'lon': aircraft.get('lon'),
                })
    except Exception:
        pass
    return emergencies
