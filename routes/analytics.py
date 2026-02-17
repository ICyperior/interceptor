"""Analytics dashboard: cross-mode summary, activity sparklines, export, geofence CRUD."""

from __future__ import annotations

import csv
import io
import json

from flask import Blueprint, Response, jsonify, request

import app as app_module
from utils.analytics import (
    get_activity_tracker,
    get_cross_mode_summary,
    get_emergency_squawks,
    get_mode_health,
)
from utils.flight_correlator import get_flight_correlator
from utils.geofence import get_geofence_manager
from utils.temporal_patterns import get_pattern_detector

analytics_bp = Blueprint('analytics', __name__, url_prefix='/analytics')


# Map mode names to DataStore attribute(s)
MODE_STORES: dict[str, list[str]] = {
    'adsb': ['adsb_aircraft'],
    'ais': ['ais_vessels'],
    'wifi': ['wifi_networks', 'wifi_clients'],
    'bluetooth': ['bt_devices'],
    'dsc': ['dsc_messages'],
}


@analytics_bp.route('/summary')
def analytics_summary():
    """Return cross-mode counts, health, and emergency squawks."""
    return jsonify({
        'status': 'success',
        'counts': get_cross_mode_summary(),
        'health': get_mode_health(),
        'squawks': get_emergency_squawks(),
        'flight_messages': {
            'acars': get_flight_correlator().acars_count,
            'vdl2': get_flight_correlator().vdl2_count,
        },
    })


@analytics_bp.route('/activity')
def analytics_activity():
    """Return sparkline arrays for each mode."""
    tracker = get_activity_tracker()
    return jsonify({
        'status': 'success',
        'sparklines': tracker.get_all_sparklines(),
    })


@analytics_bp.route('/squawks')
def analytics_squawks():
    """Return current emergency squawk codes from ADS-B."""
    return jsonify({
        'status': 'success',
        'squawks': get_emergency_squawks(),
    })


@analytics_bp.route('/patterns')
def analytics_patterns():
    """Return detected temporal patterns."""
    return jsonify({
        'status': 'success',
        'patterns': get_pattern_detector().get_all_patterns(),
    })


@analytics_bp.route('/export/<mode>')
def analytics_export(mode: str):
    """Export current DataStore contents as JSON or CSV."""
    fmt = request.args.get('format', 'json').lower()

    if mode == 'sensor':
        # Sensor doesn't use DataStore; return recent queue-based data
        return jsonify({'status': 'success', 'data': [], 'message': 'Sensor data is stream-only'})

    store_names = MODE_STORES.get(mode)
    if not store_names:
        return jsonify({'status': 'error', 'message': f'Unknown mode: {mode}'}), 400

    all_items: list[dict] = []
    for store_name in store_names:
        store = getattr(app_module, store_name, None)
        if store is None:
            continue
        for key, value in store.items():
            item = dict(value) if isinstance(value, dict) else {'id': key, 'value': value}
            item.setdefault('_store', store_name)
            all_items.append(item)

    if fmt == 'csv':
        if not all_items:
            output = ''
        else:
            # Collect all keys across items
            fieldnames: list[str] = []
            seen: set[str] = set()
            for item in all_items:
                for k in item:
                    if k not in seen:
                        fieldnames.append(k)
                        seen.add(k)

            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            for item in all_items:
                # Serialize non-scalar values
                row = {}
                for k in fieldnames:
                    v = item.get(k)
                    if isinstance(v, (dict, list)):
                        row[k] = json.dumps(v)
                    else:
                        row[k] = v
                writer.writerow(row)
            output = buf.getvalue()

        response = Response(output, mimetype='text/csv')
        response.headers['Content-Disposition'] = f'attachment; filename={mode}_export.csv'
        return response

    # Default: JSON
    return jsonify({'status': 'success', 'mode': mode, 'count': len(all_items), 'data': all_items})


# =========================================================================
# Geofence CRUD
# =========================================================================

@analytics_bp.route('/geofences')
def list_geofences():
    return jsonify({
        'status': 'success',
        'zones': get_geofence_manager().list_zones(),
    })


@analytics_bp.route('/geofences', methods=['POST'])
def create_geofence():
    data = request.get_json() or {}
    name = data.get('name')
    lat = data.get('lat')
    lon = data.get('lon')
    radius_m = data.get('radius_m')

    if not all([name, lat is not None, lon is not None, radius_m is not None]):
        return jsonify({'status': 'error', 'message': 'name, lat, lon, radius_m are required'}), 400

    try:
        lat = float(lat)
        lon = float(lon)
        radius_m = float(radius_m)
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'lat, lon, radius_m must be numbers'}), 400

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return jsonify({'status': 'error', 'message': 'Invalid coordinates'}), 400
    if radius_m <= 0:
        return jsonify({'status': 'error', 'message': 'radius_m must be positive'}), 400

    alert_on = data.get('alert_on', 'enter_exit')
    zone_id = get_geofence_manager().add_zone(name, lat, lon, radius_m, alert_on)
    return jsonify({'status': 'success', 'zone_id': zone_id})


@analytics_bp.route('/geofences/<int:zone_id>', methods=['DELETE'])
def delete_geofence(zone_id: int):
    ok = get_geofence_manager().delete_zone(zone_id)
    if not ok:
        return jsonify({'status': 'error', 'message': 'Zone not found'}), 404
    return jsonify({'status': 'success'})
