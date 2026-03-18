"""Weather satellite pass prediction utility.

Shared prediction logic used by both the API endpoint and the auto-scheduler.
Delegates to utils.satellite_predict for core pass detection, then enriches
results with weather-satellite-specific metadata.
"""

from __future__ import annotations

import datetime
import time
from typing import Any

from utils.logging import get_logger
from utils.weather_sat import WEATHER_SATELLITES

logger = get_logger('intercept.weather_sat_predict')

# Cache skyfield timescale to avoid re-downloading/re-parsing per request
_cached_timescale = None


def _get_timescale():
    global _cached_timescale
    if _cached_timescale is None:
        from skyfield.api import load
        _cached_timescale = load.timescale()
    return _cached_timescale


def _get_tle_source() -> dict:
    """Return the best available TLE source (live cache preferred over static data)."""
    from data.satellites import TLE_SATELLITES
    if not hasattr(_get_tle_source, '_ref') or \
       (time.time() - getattr(_get_tle_source, '_ref_ts', 0)) > 3600:
        try:
            from routes.satellite import _tle_cache
            if _tle_cache:
                _get_tle_source._ref = _tle_cache
                _get_tle_source._ref_ts = time.time()
        except ImportError:
            pass
    return getattr(_get_tle_source, '_ref', None) or TLE_SATELLITES


def predict_passes(
    lat: float,
    lon: float,
    hours: int = 24,
    min_elevation: float = 15.0,
    include_trajectory: bool = False,
    include_ground_track: bool = False,
) -> list[dict[str, Any]]:
    """Predict upcoming weather satellite passes for an observer location.

    Args:
        lat: Observer latitude (-90 to 90)
        lon: Observer longitude (-180 to 180)
        hours: Hours ahead to predict (1-72)
        min_elevation: Minimum peak elevation in degrees (0-90)
        include_trajectory: Include az/el trajectory points for polar plot
        include_ground_track: Include lat/lon ground track points for map

    Returns:
        List of pass dicts sorted by start time, enriched with weather-satellite
        fields: id, satellite, name, frequency, mode, quality, riseAz, setAz,
        maxElAz, and all standard fields from utils.satellite_predict.
    """
    from skyfield.api import wgs84
    from utils.satellite_predict import predict_passes as _predict_passes

    tle_source = _get_tle_source()
    ts = _get_timescale()
    observer = wgs84.latlon(lat, lon)
    t0 = ts.now()
    t1 = ts.utc(t0.utc_datetime() + datetime.timedelta(hours=hours))

    all_passes: list[dict[str, Any]] = []

    for sat_key, sat_info in WEATHER_SATELLITES.items():
        if not sat_info['active']:
            continue

        tle_data = tle_source.get(sat_info['tle_key'])
        if not tle_data:
            continue

        sat_passes = _predict_passes(
            tle_data,
            observer,
            ts,
            t0,
            t1,
            min_el=min_elevation,
            include_trajectory=include_trajectory,
            include_ground_track=include_ground_track,
        )

        for p in sat_passes:
            aos_iso = p['startTimeISO']
            try:
                aos_dt = datetime.datetime.fromisoformat(aos_iso)
                pass_id = f"{sat_key}_{aos_dt.strftime('%Y%m%d%H%M%S')}"
            except Exception:
                pass_id = f"{sat_key}_{aos_iso}"

            # Enrich with weather-satellite-specific fields
            p['id'] = pass_id
            p['satellite'] = sat_key
            p['name'] = sat_info['name']
            p['frequency'] = sat_info['frequency']
            p['mode'] = sat_info['mode']
            # Backwards-compatible aliases
            p['riseAz'] = p['aosAz']
            p['setAz'] = p['losAz']
            p['maxElAz'] = p['tcaAz']
            p['quality'] = (
                'excellent' if p['maxEl'] >= 60
                else 'good' if p['maxEl'] >= 30
                else 'fair'
            )

        all_passes.extend(sat_passes)

    all_passes.sort(key=lambda p: p['startTimeISO'])
    return all_passes
