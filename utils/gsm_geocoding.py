"""GSM Cell Tower Geocoding Service.

Provides hybrid cache-first geocoding with async API fallback for cell towers.
"""

from __future__ import annotations

import logging
import queue
from typing import Any

import requests

import config
from utils.database import get_db

logger = logging.getLogger('intercept.gsm_geocoding')

# Queue for pending geocoding requests
_geocoding_queue = queue.Queue(maxsize=100)


def lookup_cell_coordinates(mcc: int, mnc: int, lac: int, cid: int) -> dict[str, Any] | None:
    """
    Lookup cell tower coordinates with cache-first strategy.

    Strategy:
    1. Check gsm_cells table (cache) - fast synchronous lookup
    2. If not found, return None (caller decides whether to use API)

    Args:
        mcc: Mobile Country Code
        mnc: Mobile Network Code
        lac: Location Area Code
        cid: Cell ID

    Returns:
        dict with keys: lat, lon, source='cache', azimuth (optional),
        range_meters (optional), operator (optional), radio (optional)
        Returns None if not found in cache.
    """
    try:
        with get_db() as conn:
            result = conn.execute('''
                SELECT lat, lon, azimuth, range_meters, operator, radio
                FROM gsm_cells
                WHERE mcc = ? AND mnc = ? AND lac = ? AND cid = ?
            ''', (mcc, mnc, lac, cid)).fetchone()

            if result:
                return {
                    'lat': result['lat'],
                    'lon': result['lon'],
                    'source': 'cache',
                    'azimuth': result['azimuth'],
                    'range_meters': result['range_meters'],
                    'operator': result['operator'],
                    'radio': result['radio']
                }

        return None

    except Exception as e:
        logger.error(f"Error looking up coordinates from cache: {e}")
        return None


def lookup_cell_from_api(mcc: int, mnc: int, lac: int, cid: int) -> dict[str, Any] | None:
    """
    Lookup cell tower from OpenCellID API and cache result.

    Args:
        mcc: Mobile Country Code
        mnc: Mobile Network Code
        lac: Location Area Code
        cid: Cell ID

    Returns:
        dict with keys: lat, lon, source='api', azimuth (optional),
        range_meters (optional), operator (optional), radio (optional)
        Returns None if API call fails or cell not found.
    """
    try:
        api_url = config.GSM_OPENCELLID_API_URL
        params = {
            'key': config.GSM_OPENCELLID_API_KEY,
            'mcc': mcc,
            'mnc': mnc,
            'lac': lac,
            'cellid': cid,
            'format': 'json'
        }

        response = requests.get(api_url, params=params, timeout=10)

        if response.status_code == 200:
            cell_data = response.json()

            # Cache the result
            with get_db() as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO gsm_cells
                    (mcc, mnc, lac, cid, lat, lon, azimuth, range_meters, samples, radio, operator, last_verified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (
                    mcc, mnc, lac, cid,
                    cell_data.get('lat'),
                    cell_data.get('lon'),
                    cell_data.get('azimuth'),
                    cell_data.get('range'),
                    cell_data.get('samples'),
                    cell_data.get('radio'),
                    cell_data.get('operator')
                ))
                conn.commit()

            logger.info(f"Cached cell tower from API: MCC={mcc} MNC={mnc} LAC={lac} CID={cid}")

            return {
                'lat': cell_data.get('lat'),
                'lon': cell_data.get('lon'),
                'source': 'api',
                'azimuth': cell_data.get('azimuth'),
                'range_meters': cell_data.get('range'),
                'operator': cell_data.get('operator'),
                'radio': cell_data.get('radio')
            }
        else:
            logger.warning(f"OpenCellID API returned {response.status_code} for MCC={mcc} MNC={mnc} LAC={lac} CID={cid}")
            return None

    except Exception as e:
        logger.error(f"Error calling OpenCellID API: {e}")
        return None


def enrich_tower_data(tower_data: dict[str, Any]) -> dict[str, Any]:
    """
    Enrich tower data with coordinates using cache-first strategy.

    If coordinates found in cache, adds them immediately.
    If not found, marks as 'pending' and queues for background API lookup.

    Args:
        tower_data: Dictionary with keys mcc, mnc, lac, cid (and other tower data)

    Returns:
        Enriched tower_data dict with added fields:
        - lat, lon (if found in cache)
        - status='pending' (if needs API lookup)
        - source='cache' (if from cache)
    """
    mcc = tower_data.get('mcc')
    mnc = tower_data.get('mnc')
    lac = tower_data.get('lac')
    cid = tower_data.get('cid')

    # Validate required fields
    if not all([mcc is not None, mnc is not None, lac is not None, cid is not None]):
        logger.warning(f"Tower data missing required fields: {tower_data}")
        return tower_data

    # Try cache lookup
    coords = lookup_cell_coordinates(mcc, mnc, lac, cid)

    if coords:
        # Found in cache - add coordinates immediately
        tower_data['lat'] = coords['lat']
        tower_data['lon'] = coords['lon']
        tower_data['source'] = 'cache'

        # Add optional fields if available
        if coords.get('azimuth') is not None:
            tower_data['azimuth'] = coords['azimuth']
        if coords.get('range_meters') is not None:
            tower_data['range_meters'] = coords['range_meters']
        if coords.get('operator'):
            tower_data['operator'] = coords['operator']
        if coords.get('radio'):
            tower_data['radio'] = coords['radio']

        logger.debug(f"Cache hit for tower: MCC={mcc} MNC={mnc} LAC={lac} CID={cid}")
    else:
        # Not in cache - mark as pending and queue for API lookup
        tower_data['status'] = 'pending'
        tower_data['source'] = 'unknown'

        # Queue for background geocoding (non-blocking)
        try:
            _geocoding_queue.put_nowait(tower_data.copy())
            logger.debug(f"Queued tower for geocoding: MCC={mcc} MNC={mnc} LAC={lac} CID={cid}")
        except queue.Full:
            logger.warning("Geocoding queue full, dropping tower")

    return tower_data


def get_geocoding_queue() -> queue.Queue:
    """Get the geocoding queue for the background worker."""
    return _geocoding_queue
