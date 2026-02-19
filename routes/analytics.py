"""Analytics dashboard: cross-mode summary, activity sparklines, export, geofence CRUD."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, Response, jsonify, request

import app as app_module
from utils.analytics import (
    get_activity_tracker,
    get_cross_mode_summary,
    get_emergency_squawks,
    get_mode_health,
)
from utils.alerts import get_alert_manager
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


@analytics_bp.route('/target')
def analytics_target():
    """Search entities across multiple modes for a target-centric view."""
    query = (request.args.get('q') or '').strip()
    requested_limit = request.args.get('limit', default=120, type=int) or 120
    limit = max(1, min(500, requested_limit))

    if not query:
        return jsonify({
            'status': 'success',
            'query': '',
            'results': [],
            'mode_counts': {},
        })

    needle = query.lower()
    results: list[dict[str, Any]] = []
    mode_counts: dict[str, int] = {}

    def push(mode: str, entity_id: str, title: str, subtitle: str, last_seen: str | None = None) -> None:
        if len(results) >= limit:
            return
        results.append({
            'mode': mode,
            'id': entity_id,
            'title': title,
            'subtitle': subtitle,
            'last_seen': last_seen,
        })
        mode_counts[mode] = mode_counts.get(mode, 0) + 1

    # ADS-B
    for icao, aircraft in app_module.adsb_aircraft.items():
        if not isinstance(aircraft, dict):
            continue
        fields = [
            icao,
            aircraft.get('icao'),
            aircraft.get('hex'),
            aircraft.get('callsign'),
            aircraft.get('registration'),
            aircraft.get('flight'),
        ]
        if not _matches_query(needle, fields):
            continue
        title = str(aircraft.get('callsign') or icao or 'Aircraft').strip()
        subtitle = f"ICAO {aircraft.get('icao') or icao} | Alt {aircraft.get('altitude', '--')} | Speed {aircraft.get('speed', '--')}"
        push('adsb', str(icao), title, subtitle, aircraft.get('lastSeen') or aircraft.get('last_seen'))
        if len(results) >= limit:
            break

    # AIS
    if len(results) < limit:
        for mmsi, vessel in app_module.ais_vessels.items():
            if not isinstance(vessel, dict):
                continue
            fields = [
                mmsi,
                vessel.get('mmsi'),
                vessel.get('name'),
                vessel.get('shipname'),
                vessel.get('callsign'),
                vessel.get('imo'),
            ]
            if not _matches_query(needle, fields):
                continue
            vessel_name = vessel.get('name') or vessel.get('shipname') or mmsi or 'Vessel'
            subtitle = f"MMSI {vessel.get('mmsi') or mmsi} | Type {vessel.get('ship_type') or vessel.get('type') or '--'}"
            push('ais', str(mmsi), str(vessel_name), subtitle, vessel.get('lastSeen') or vessel.get('last_seen'))
            if len(results) >= limit:
                break

    # WiFi networks and clients
    if len(results) < limit:
        for bssid, net in app_module.wifi_networks.items():
            if not isinstance(net, dict):
                continue
            fields = [bssid, net.get('bssid'), net.get('ssid'), net.get('vendor')]
            if not _matches_query(needle, fields):
                continue
            title = str(net.get('ssid') or net.get('bssid') or bssid or 'WiFi Network')
            subtitle = f"BSSID {net.get('bssid') or bssid} | CH {net.get('channel', '--')} | RSSI {net.get('signal', '--')}"
            push('wifi', str(bssid), title, subtitle, net.get('lastSeen') or net.get('last_seen'))
            if len(results) >= limit:
                break

    if len(results) < limit:
        for client_mac, client in app_module.wifi_clients.items():
            if not isinstance(client, dict):
                continue
            fields = [client_mac, client.get('mac'), client.get('bssid'), client.get('ssid'), client.get('vendor')]
            if not _matches_query(needle, fields):
                continue
            title = str(client.get('mac') or client_mac or 'WiFi Client')
            subtitle = f"BSSID {client.get('bssid') or '--'} | Probe {client.get('ssid') or '--'}"
            push('wifi', str(client_mac), title, subtitle, client.get('lastSeen') or client.get('last_seen'))
            if len(results) >= limit:
                break

    # Bluetooth
    if len(results) < limit:
        for address, dev in app_module.bt_devices.items():
            if not isinstance(dev, dict):
                continue
            fields = [
                address,
                dev.get('address'),
                dev.get('mac'),
                dev.get('name'),
                dev.get('manufacturer'),
                dev.get('vendor'),
            ]
            if not _matches_query(needle, fields):
                continue
            title = str(dev.get('name') or dev.get('address') or address or 'Bluetooth Device')
            subtitle = f"MAC {dev.get('address') or address} | RSSI {dev.get('rssi', '--')} | Vendor {dev.get('manufacturer') or dev.get('vendor') or '--'}"
            push('bluetooth', str(address), title, subtitle, dev.get('lastSeen') or dev.get('last_seen'))
            if len(results) >= limit:
                break

    # DSC recent messages
    if len(results) < limit:
        for msg_id, msg in app_module.dsc_messages.items():
            if not isinstance(msg, dict):
                continue
            fields = [
                msg_id,
                msg.get('mmsi'),
                msg.get('from_mmsi'),
                msg.get('to_mmsi'),
                msg.get('from_callsign'),
                msg.get('to_callsign'),
                msg.get('category'),
            ]
            if not _matches_query(needle, fields):
                continue
            title = str(msg.get('from_mmsi') or msg.get('mmsi') or msg_id or 'DSC Message')
            subtitle = f"To {msg.get('to_mmsi') or '--'} | Cat {msg.get('category') or '--'} | Freq {msg.get('frequency') or '--'}"
            push('dsc', str(msg_id), title, subtitle, msg.get('timestamp') or msg.get('lastSeen') or msg.get('last_seen'))
            if len(results) >= limit:
                break

    return jsonify({
        'status': 'success',
        'query': query,
        'results': results,
        'mode_counts': mode_counts,
    })


@analytics_bp.route('/insights')
def analytics_insights():
    """Return actionable insight cards and top changes."""
    counts = get_cross_mode_summary()
    tracker = get_activity_tracker()
    sparklines = tracker.get_all_sparklines()
    squawks = get_emergency_squawks()
    patterns = get_pattern_detector().get_all_patterns()
    alerts = get_alert_manager().list_events(limit=120)

    top_changes = _compute_mode_changes(sparklines)
    busiest_mode, busiest_count = _get_busiest_mode(counts)
    critical_1h = _count_recent_alerts(alerts, severities={'critical', 'high'}, max_age_seconds=3600)
    recurring_emitters = sum(1 for p in patterns if float(p.get('confidence') or 0.0) >= 0.7)

    cards = []
    if top_changes:
        lead = top_changes[0]
        direction = 'up' if lead['delta'] >= 0 else 'down'
        cards.append({
            'id': 'fastest_change',
            'title': 'Fastest Change',
            'value': f"{lead['mode_label']} ({lead['signed_delta']})",
            'label': 'last window vs prior',
            'severity': 'high' if lead['delta'] > 0 else 'low',
            'detail': f"Traffic is trending {direction} in {lead['mode_label']}.",
        })
    else:
        cards.append({
            'id': 'fastest_change',
            'title': 'Fastest Change',
            'value': 'Insufficient data',
            'label': 'wait for activity history',
            'severity': 'low',
            'detail': 'Sparklines need more samples to score momentum.',
        })

    cards.append({
        'id': 'busiest_mode',
        'title': 'Busiest Mode',
        'value': f"{busiest_mode} ({busiest_count})",
        'label': 'current observed entities',
        'severity': 'medium' if busiest_count > 0 else 'low',
        'detail': 'Highest live entity count across monitoring modes.',
    })
    cards.append({
        'id': 'critical_alerts',
        'title': 'Critical Alerts (1h)',
        'value': str(critical_1h),
        'label': 'critical/high severities',
        'severity': 'critical' if critical_1h > 0 else 'low',
        'detail': 'Prioritize triage if this count is non-zero.',
    })
    cards.append({
        'id': 'emergency_squawks',
        'title': 'Emergency Squawks',
        'value': str(len(squawks)),
        'label': 'active ADS-B emergency codes',
        'severity': 'critical' if squawks else 'low',
        'detail': 'Immediate aviation anomalies currently visible.',
    })
    cards.append({
        'id': 'recurring_emitters',
        'title': 'Recurring Emitters',
        'value': str(recurring_emitters),
        'label': 'pattern confidence >= 0.70',
        'severity': 'medium' if recurring_emitters > 0 else 'low',
        'detail': 'Potentially stationary or periodic emitters detected.',
    })

    return jsonify({
        'status': 'success',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'cards': cards,
        'top_changes': top_changes[:5],
    })


def _compute_mode_changes(sparklines: dict[str, list[int]]) -> list[dict]:
    mode_labels = {
        'adsb': 'ADS-B',
        'ais': 'AIS',
        'wifi': 'WiFi',
        'bluetooth': 'Bluetooth',
        'dsc': 'DSC',
        'acars': 'ACARS',
        'vdl2': 'VDL2',
        'aprs': 'APRS',
        'meshtastic': 'Meshtastic',
    }
    rows = []
    for mode, samples in (sparklines or {}).items():
        if not isinstance(samples, list) or len(samples) < 4:
            continue

        window = max(2, min(12, len(samples) // 2))
        recent = samples[-window:]
        previous = samples[-(window * 2):-window]
        if not previous:
            continue

        recent_avg = sum(recent) / len(recent)
        prev_avg = sum(previous) / len(previous)
        delta = round(recent_avg - prev_avg, 1)
        rows.append({
            'mode': mode,
            'mode_label': mode_labels.get(mode, mode.upper()),
            'delta': delta,
            'signed_delta': ('+' if delta >= 0 else '') + str(delta),
            'recent_avg': round(recent_avg, 1),
            'previous_avg': round(prev_avg, 1),
            'direction': 'up' if delta > 0 else ('down' if delta < 0 else 'flat'),
        })

    rows.sort(key=lambda r: abs(r['delta']), reverse=True)
    return rows


def _matches_query(needle: str, values: list[Any]) -> bool:
    for value in values:
        if value is None:
            continue
        if needle in str(value).lower():
            return True
    return False


def _count_recent_alerts(alerts: list[dict], severities: set[str], max_age_seconds: int) -> int:
    now = datetime.now(timezone.utc)
    count = 0
    for event in alerts:
        sev = str(event.get('severity') or '').lower()
        if sev not in severities:
            continue
        created_raw = event.get('created_at')
        if not created_raw:
            continue
        try:
            created = datetime.fromisoformat(str(created_raw).replace('Z', '+00:00'))
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (now - created).total_seconds()
        if 0 <= age <= max_age_seconds:
            count += 1
    return count


def _get_busiest_mode(counts: dict[str, int]) -> tuple[str, int]:
    mode_labels = {
        'adsb': 'ADS-B',
        'ais': 'AIS',
        'wifi': 'WiFi',
        'bluetooth': 'Bluetooth',
        'dsc': 'DSC',
        'acars': 'ACARS',
        'vdl2': 'VDL2',
        'aprs': 'APRS',
        'meshtastic': 'Meshtastic',
    }
    filtered = {k: int(v or 0) for k, v in (counts or {}).items() if k in mode_labels}
    if not filtered:
        return ('None', 0)
    mode = max(filtered, key=filtered.get)
    return (mode_labels.get(mode, mode.upper()), filtered[mode])


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

    # Try v2 scanners first for wifi/bluetooth
    if mode == 'wifi':
        try:
            from utils.wifi.scanner import _scanner_instance as wifi_scanner
            if wifi_scanner is not None:
                for ap in wifi_scanner.access_points:
                    all_items.append(ap.to_dict())
                for client in wifi_scanner.clients:
                    item = client.to_dict()
                    item['_store'] = 'wifi_clients'
                    all_items.append(item)
        except Exception:
            pass
    elif mode == 'bluetooth':
        try:
            from utils.bluetooth.scanner import _scanner_instance as bt_scanner
            if bt_scanner is not None:
                for dev in bt_scanner.get_devices():
                    all_items.append(dev.to_dict())
        except Exception:
            pass

    # Fall back to legacy DataStores if v2 scanners yielded nothing
    if not all_items:
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
