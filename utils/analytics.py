"""Cross-mode analytics: activity tracking, summaries, and emergency squawk detection."""

from __future__ import annotations

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


def _get_mode_counts() -> dict[str, int]:
    """Read current entity counts from app_module DataStores."""
    counts: dict[str, int] = {}
    try:
        counts['adsb'] = len(app_module.adsb_aircraft)
    except Exception:
        counts['adsb'] = 0
    try:
        counts['ais'] = len(app_module.ais_vessels)
    except Exception:
        counts['ais'] = 0
    try:
        counts['wifi'] = len(app_module.wifi_networks)
    except Exception:
        counts['wifi'] = 0
    try:
        counts['bluetooth'] = len(app_module.bt_devices)
    except Exception:
        counts['bluetooth'] = 0
    try:
        counts['dsc'] = len(app_module.dsc_messages)
    except Exception:
        counts['dsc'] = 0
    return counts


def get_cross_mode_summary() -> dict[str, Any]:
    """Return counts dict for all active DataStores."""
    counts = _get_mode_counts()
    try:
        counts['wifi_clients'] = len(app_module.wifi_clients)
    except Exception:
        counts['wifi_clients'] = 0
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
    }

    for mode, attr in process_map.items():
        proc = getattr(app_module, attr, None)
        running = proc is not None and (proc.poll() is None if proc else False)
        health[mode] = {'running': running}

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
