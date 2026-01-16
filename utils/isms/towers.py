"""
Cell tower integration via OpenCelliD API.

Provides functions to query nearby towers and generate link-outs
to CellMapper and Ofcom resources.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from math import acos, cos, radians, sin

import requests

logger = logging.getLogger('intercept.isms.towers')

# OpenCelliD API endpoint
OPENCELLID_API_URL = 'https://opencellid.org/cell/getInArea'

# Request timeout
REQUEST_TIMEOUT = 10.0


@dataclass
class CellTower:
    """Cell tower information from OpenCelliD."""
    tower_id: int
    mcc: int
    mnc: int
    lac: int
    cellid: int
    lat: float
    lon: float
    range_m: int
    radio: str  # GSM, UMTS, LTE, NR
    samples: int = 0
    changeable: bool = True
    created: int = 0
    updated: int = 0

    @property
    def plmn(self) -> str:
        """Get PLMN code (MCC-MNC)."""
        return f'{self.mcc}-{self.mnc}'

    @property
    def distance_km(self) -> float | None:
        """Distance from query point (set after query)."""
        return getattr(self, '_distance_km', None)

    @distance_km.setter
    def distance_km(self, value: float) -> None:
        self._distance_km = value

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'tower_id': self.tower_id,
            'mcc': self.mcc,
            'mnc': self.mnc,
            'lac': self.lac,
            'cellid': self.cellid,
            'lat': self.lat,
            'lon': self.lon,
            'range_m': self.range_m,
            'radio': self.radio,
            'plmn': self.plmn,
            'samples': self.samples,
            'distance_km': self.distance_km,
            'cellmapper_url': build_cellmapper_url(self.mcc, self.mnc, self.lac, self.cellid),
        }


def get_opencellid_token() -> str | None:
    """Get OpenCelliD API token from environment."""
    return os.environ.get('OPENCELLID_TOKEN')


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in km using Haversine formula."""
    R = 6371  # Earth radius in km

    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)
    lon1_rad = radians(lon1)
    lon2_rad = radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    # Haversine formula
    a = sin(dlat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2) ** 2
    c = 2 * acos(min(1, (1 - a) ** 0.5 * (1 + a) ** 0.5 + a ** 0.5 * (1 - a) ** 0.5))

    # Simplified: c = 2 * atan2(sqrt(a), sqrt(1-a))
    # Using identity: acos(1 - 2a) for small angles

    return R * c


def query_nearby_towers(
    lat: float,
    lon: float,
    radius_km: float = 5.0,
    token: str | None = None,
    radio: str | None = None,
    mcc: int | None = None,
    mnc: int | None = None,
) -> list[CellTower]:
    """
    Query OpenCelliD for towers within radius.

    Args:
        lat: Latitude of center point
        lon: Longitude of center point
        radius_km: Search radius in kilometers
        token: OpenCelliD API token (uses env var if not provided)
        radio: Filter by radio type (GSM, UMTS, LTE, NR)
        mcc: Filter by MCC (country code)
        mnc: Filter by MNC (network code)

    Returns:
        List of CellTower objects sorted by distance
    """
    if token is None:
        token = get_opencellid_token()

    if not token:
        logger.warning("OpenCelliD token not configured")
        return []

    # Convert radius to bounding box
    # Approximate: 1 degree latitude ~ 111 km
    lat_delta = radius_km / 111.0
    # Longitude varies with latitude
    lon_delta = radius_km / (111.0 * cos(radians(lat)))

    params = {
        'key': token,
        'BBOX': f'{lon - lon_delta},{lat - lat_delta},{lon + lon_delta},{lat + lat_delta}',
        'format': 'json',
    }

    if radio:
        params['radio'] = radio
    if mcc is not None:
        params['mcc'] = mcc
    if mnc is not None:
        params['mnc'] = mnc

    try:
        response = requests.get(
            OPENCELLID_API_URL,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        data = response.json()

        if 'cells' not in data:
            logger.debug(f"No cells in OpenCelliD response: {data}")
            return []

        towers = []
        for cell in data['cells']:
            try:
                tower = CellTower(
                    tower_id=cell.get('cellid', 0),
                    mcc=cell.get('mcc', 0),
                    mnc=cell.get('mnc', 0),
                    lac=cell.get('lac', 0),
                    cellid=cell.get('cellid', 0),
                    lat=cell.get('lat', 0),
                    lon=cell.get('lon', 0),
                    range_m=cell.get('range', 0),
                    radio=cell.get('radio', 'UNKNOWN'),
                    samples=cell.get('samples', 0),
                    changeable=cell.get('changeable', True),
                    created=cell.get('created', 0),
                    updated=cell.get('updated', 0),
                )

                # Calculate distance
                distance = _haversine_distance(lat, lon, tower.lat, tower.lon)
                tower.distance_km = round(distance, 2)

                # Only include towers within actual radius (bounding box is larger)
                if distance <= radius_km:
                    towers.append(tower)

            except (KeyError, TypeError) as e:
                logger.debug(f"Failed to parse cell: {e}")
                continue

        # Sort by distance
        towers.sort(key=lambda t: t.distance_km or 0)

        logger.info(f"Found {len(towers)} towers within {radius_km}km of ({lat}, {lon})")
        return towers

    except requests.RequestException as e:
        logger.error(f"OpenCelliD API error: {e}")
        return []
    except Exception as e:
        logger.error(f"Error querying towers: {e}")
        return []


def build_cellmapper_url(mcc: int, mnc: int, lac: int, cid: int) -> str:
    """
    Build link-out URL to CellMapper (no scraping).

    Args:
        mcc: Mobile Country Code
        mnc: Mobile Network Code
        lac: Location Area Code
        cid: Cell ID

    Returns:
        URL to CellMapper map view for this cell
    """
    return f'https://www.cellmapper.net/map?MCC={mcc}&MNC={mnc}&LAC={lac}&CID={cid}'


def build_cellmapper_tower_url(mcc: int, mnc: int, lat: float, lon: float) -> str:
    """
    Build link-out URL to CellMapper map centered on location.

    Args:
        mcc: Mobile Country Code
        mnc: Mobile Network Code
        lat: Latitude
        lon: Longitude

    Returns:
        URL to CellMapper map view centered on location
    """
    return f'https://www.cellmapper.net/map?MCC={mcc}&MNC={mnc}&latitude={lat}&longitude={lon}&zoom=15'


def build_ofcom_coverage_url(lat: float | None = None, lon: float | None = None) -> str:
    """
    Build link to Ofcom mobile coverage checker.

    Args:
        lat: Optional latitude for location
        lon: Optional longitude for location

    Returns:
        URL to Ofcom coverage checker
    """
    base_url = 'https://www.ofcom.org.uk/phones-and-broadband/coverage-and-quality/mobile-coverage-checker'

    # Note: Ofcom coverage checker uses postcode entry, not lat/lon parameters
    # So we just return the base URL
    return base_url


def build_ofcom_emf_url() -> str:
    """
    Build link to Ofcom EMF/base station audits info.

    Returns:
        URL to Ofcom EMF information page
    """
    return 'https://www.ofcom.org.uk/phones-telecoms-and-internet/information-for-industry/radiocomms-and-spectrum/radio-spectrum/spectrum-for-mobile-services/electromagnetic-fields-emf'


def build_ofcom_sitefinder_url() -> str:
    """
    Build link to Ofcom Sitefinder (base station database).

    Note: Sitefinder was retired in 2017. This returns the info page.

    Returns:
        URL to Ofcom mobile sites information
    """
    return 'https://www.ofcom.org.uk/phones-telecoms-and-internet/advice-for-consumers/mobile-services'


def get_uk_operator_name(mcc: int, mnc: int) -> str | None:
    """
    Get UK operator name from MCC/MNC.

    Args:
        mcc: Mobile Country Code
        mnc: Mobile Network Code

    Returns:
        Operator name or None if not found
    """
    # UK MCC is 234
    if mcc != 234:
        return None

    operators = {
        10: 'O2 UK',
        15: 'Vodafone UK',
        20: 'Three UK',
        30: 'EE',
        33: 'EE',
        34: 'EE',
        50: 'JT (Jersey)',
        55: 'Sure (Guernsey)',
    }

    return operators.get(mnc)


def format_tower_info(tower: CellTower) -> dict:
    """
    Format tower information for display.

    Args:
        tower: CellTower object

    Returns:
        Formatted dictionary with display-friendly values
    """
    operator = get_uk_operator_name(tower.mcc, tower.mnc)

    return {
        'id': tower.tower_id,
        'plmn': tower.plmn,
        'operator': operator or f'MCC {tower.mcc} / MNC {tower.mnc}',
        'radio': tower.radio,
        'lac': tower.lac,
        'cellid': tower.cellid,
        'lat': round(tower.lat, 6),
        'lon': round(tower.lon, 6),
        'range_km': round(tower.range_m / 1000, 2) if tower.range_m else None,
        'distance_km': tower.distance_km,
        'samples': tower.samples,
        'cellmapper_url': build_cellmapper_url(tower.mcc, tower.mnc, tower.lac, tower.cellid),
        'ofcom_coverage_url': build_ofcom_coverage_url(),
        'ofcom_emf_url': build_ofcom_emf_url(),
    }
