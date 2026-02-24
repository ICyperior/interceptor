"""WeFax station database loader.

Loads and caches station data from data/wefax_stations.json. Provides
lookup by callsign and current-broadcast filtering based on UTC time.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_stations_cache: list[dict] | None = None
_stations_by_callsign: dict[str, dict] = {}

_STATIONS_PATH = Path(__file__).resolve().parent.parent / 'data' / 'wefax_stations.json'


def load_stations() -> list[dict]:
    """Load all WeFax stations from JSON, caching on first call."""
    global _stations_cache, _stations_by_callsign

    if _stations_cache is not None:
        return _stations_cache

    with open(_STATIONS_PATH) as f:
        data = json.load(f)

    _stations_cache = data.get('stations', [])
    _stations_by_callsign = {s['callsign']: s for s in _stations_cache}
    return _stations_cache


def get_station(callsign: str) -> dict | None:
    """Get a single station by callsign."""
    load_stations()
    return _stations_by_callsign.get(callsign.upper())


def get_current_broadcasts(callsign: str) -> list[dict]:
    """Return schedule entries closest to the current UTC time.

    Returns up to 3 entries: the most recent past broadcast and the
    next two upcoming ones, annotated with ``minutes_until`` or
    ``minutes_ago`` relative to now.
    """
    station = get_station(callsign)
    if not station:
        return []

    now = datetime.now(timezone.utc)
    current_minutes = now.hour * 60 + now.minute

    schedule = station.get('schedule', [])
    if not schedule:
        return []

    # Convert schedule times to minutes-since-midnight for comparison
    entries: list[tuple[int, dict]] = []
    for entry in schedule:
        parts = entry['utc'].split(':')
        mins = int(parts[0]) * 60 + int(parts[1])
        entries.append((mins, entry))
    entries.sort(key=lambda x: x[0])

    # Find closest entries relative to now
    results = []
    for mins, entry in entries:
        diff = mins - current_minutes
        # Wrap around midnight
        if diff < -720:
            diff += 1440
        elif diff > 720:
            diff -= 1440

        annotated = dict(entry)
        if diff >= 0:
            annotated['minutes_until'] = diff
        else:
            annotated['minutes_ago'] = abs(diff)
        annotated['_sort_key'] = abs(diff)
        results.append(annotated)

    results.sort(key=lambda x: x['_sort_key'])

    # Return 3 nearest entries, clean up sort key
    for r in results:
        r.pop('_sort_key', None)
    return results[:3]
