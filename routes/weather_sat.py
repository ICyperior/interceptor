"""Weather Satellite decoder routes.

Provides endpoints for capturing and decoding weather satellite images
from NOAA (APT) and Meteor (LRPT) satellites using SatDump.
"""

from __future__ import annotations

import queue
import time
from typing import Generator

from flask import Blueprint, jsonify, request, Response, send_file

from utils.logging import get_logger
from utils.sse import format_sse
from utils.weather_sat import (
    get_weather_sat_decoder,
    is_weather_sat_available,
    CaptureProgress,
    WEATHER_SATELLITES,
)

logger = get_logger('intercept.weather_sat')

weather_sat_bp = Blueprint('weather_sat', __name__, url_prefix='/weather-sat')

# Queue for SSE progress streaming
_weather_sat_queue: queue.Queue = queue.Queue(maxsize=100)


def _progress_callback(progress: CaptureProgress) -> None:
    """Callback to queue progress updates for SSE stream."""
    try:
        _weather_sat_queue.put_nowait(progress.to_dict())
    except queue.Full:
        try:
            _weather_sat_queue.get_nowait()
            _weather_sat_queue.put_nowait(progress.to_dict())
        except queue.Empty:
            pass


@weather_sat_bp.route('/status')
def get_status():
    """Get weather satellite decoder status.

    Returns:
        JSON with decoder availability and current status.
    """
    decoder = get_weather_sat_decoder()
    return jsonify(decoder.get_status())


@weather_sat_bp.route('/satellites')
def list_satellites():
    """Get list of supported weather satellites with frequencies.

    Returns:
        JSON with satellite definitions.
    """
    satellites = []
    for key, info in WEATHER_SATELLITES.items():
        satellites.append({
            'key': key,
            'name': info['name'],
            'frequency': info['frequency'],
            'mode': info['mode'],
            'description': info['description'],
            'active': info['active'],
        })

    return jsonify({
        'status': 'ok',
        'satellites': satellites,
    })


@weather_sat_bp.route('/start', methods=['POST'])
def start_capture():
    """Start weather satellite capture and decode.

    JSON body:
        {
            "satellite": "NOAA-18",    // Required: satellite key
            "device": 0,               // RTL-SDR device index (default: 0)
            "gain": 40.0,              // SDR gain in dB (default: 40)
            "bias_t": false            // Enable bias-T for LNA (default: false)
        }

    Returns:
        JSON with start status.
    """
    if not is_weather_sat_available():
        return jsonify({
            'status': 'error',
            'message': 'SatDump not installed. Build from source: https://github.com/SatDump/SatDump'
        }), 400

    decoder = get_weather_sat_decoder()

    if decoder.is_running:
        return jsonify({
            'status': 'already_running',
            'satellite': decoder.current_satellite,
            'frequency': decoder.current_frequency,
        })

    data = request.get_json(silent=True) or {}

    # Validate satellite
    satellite = data.get('satellite')
    if not satellite or satellite not in WEATHER_SATELLITES:
        return jsonify({
            'status': 'error',
            'message': f'Invalid satellite. Must be one of: {", ".join(WEATHER_SATELLITES.keys())}'
        }), 400

    # Validate device index
    device_index = data.get('device', 0)
    try:
        device_index = int(device_index)
        if not (0 <= device_index <= 255):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({
            'status': 'error',
            'message': 'Invalid device index (0-255)'
        }), 400

    # Validate gain
    gain = data.get('gain', 40.0)
    try:
        gain = float(gain)
        if not (0 <= gain <= 50):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({
            'status': 'error',
            'message': 'Invalid gain (0-50 dB)'
        }), 400

    bias_t = bool(data.get('bias_t', False))

    # Claim SDR device
    try:
        import app as app_module
        error = app_module.claim_sdr_device(device_index, 'weather_sat')
        if error:
            return jsonify({
                'status': 'error',
                'error_type': 'DEVICE_BUSY',
                'message': error,
            }), 409
    except ImportError:
        pass

    # Clear queue
    while not _weather_sat_queue.empty():
        try:
            _weather_sat_queue.get_nowait()
        except queue.Empty:
            break

    # Set callback and start
    decoder.set_callback(_progress_callback)
    success = decoder.start(
        satellite=satellite,
        device_index=device_index,
        gain=gain,
        bias_t=bias_t,
    )

    if success:
        sat_info = WEATHER_SATELLITES[satellite]
        return jsonify({
            'status': 'started',
            'satellite': satellite,
            'frequency': sat_info['frequency'],
            'mode': sat_info['mode'],
            'device': device_index,
        })
    else:
        # Release device on failure
        try:
            import app as app_module
            app_module.release_sdr_device(device_index)
        except ImportError:
            pass
        return jsonify({
            'status': 'error',
            'message': 'Failed to start capture'
        }), 500


@weather_sat_bp.route('/stop', methods=['POST'])
def stop_capture():
    """Stop weather satellite capture.

    Returns:
        JSON confirmation.
    """
    decoder = get_weather_sat_decoder()
    device_index = decoder._device_index

    decoder.stop()

    # Release SDR device
    try:
        import app as app_module
        app_module.release_sdr_device(device_index)
    except ImportError:
        pass

    return jsonify({'status': 'stopped'})


@weather_sat_bp.route('/images')
def list_images():
    """Get list of decoded weather satellite images.

    Query parameters:
        limit: Maximum number of images (default: all)
        satellite: Filter by satellite key (optional)

    Returns:
        JSON with list of decoded images.
    """
    decoder = get_weather_sat_decoder()
    images = decoder.get_images()

    # Filter by satellite if specified
    satellite_filter = request.args.get('satellite')
    if satellite_filter:
        images = [img for img in images if img.satellite == satellite_filter]

    # Apply limit
    limit = request.args.get('limit', type=int)
    if limit and limit > 0:
        images = images[-limit:]

    return jsonify({
        'status': 'ok',
        'images': [img.to_dict() for img in images],
        'count': len(images),
    })


@weather_sat_bp.route('/images/<filename>')
def get_image(filename: str):
    """Serve a decoded weather satellite image file.

    Args:
        filename: Image filename

    Returns:
        Image file or 404.
    """
    decoder = get_weather_sat_decoder()

    # Security: only allow safe filenames
    if not filename.replace('_', '').replace('-', '').replace('.', '').isalnum():
        return jsonify({'status': 'error', 'message': 'Invalid filename'}), 400

    if not (filename.endswith('.png') or filename.endswith('.jpg') or filename.endswith('.jpeg')):
        return jsonify({'status': 'error', 'message': 'Only PNG/JPG files supported'}), 400

    image_path = decoder._output_dir / filename

    if not image_path.exists():
        return jsonify({'status': 'error', 'message': 'Image not found'}), 404

    mimetype = 'image/png' if filename.endswith('.png') else 'image/jpeg'
    return send_file(image_path, mimetype=mimetype)


@weather_sat_bp.route('/images/<filename>', methods=['DELETE'])
def delete_image(filename: str):
    """Delete a decoded image.

    Args:
        filename: Image filename

    Returns:
        JSON confirmation.
    """
    decoder = get_weather_sat_decoder()

    if not filename.replace('_', '').replace('-', '').replace('.', '').isalnum():
        return jsonify({'status': 'error', 'message': 'Invalid filename'}), 400

    if decoder.delete_image(filename):
        return jsonify({'status': 'deleted', 'filename': filename})
    else:
        return jsonify({'status': 'error', 'message': 'Image not found'}), 404


@weather_sat_bp.route('/stream')
def stream_progress():
    """SSE stream of capture/decode progress.

    Returns:
        SSE stream (text/event-stream)
    """
    def generate() -> Generator[str, None, None]:
        last_keepalive = time.time()
        keepalive_interval = 30.0

        while True:
            try:
                progress = _weather_sat_queue.get(timeout=1)
                last_keepalive = time.time()
                yield format_sse(progress)
            except queue.Empty:
                now = time.time()
                if now - last_keepalive >= keepalive_interval:
                    yield format_sse({'type': 'keepalive'})
                    last_keepalive = now

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response


@weather_sat_bp.route('/passes')
def get_passes():
    """Get upcoming weather satellite passes for observer location.

    Query parameters:
        latitude: Observer latitude (required)
        longitude: Observer longitude (required)
        hours: Hours to predict ahead (default: 24, max: 72)
        min_elevation: Minimum elevation in degrees (default: 15)

    Returns:
        JSON with upcoming passes for all weather satellites.
    """
    lat = request.args.get('latitude', type=float)
    lon = request.args.get('longitude', type=float)
    hours = request.args.get('hours', 24, type=int)
    min_elevation = request.args.get('min_elevation', 15, type=float)

    if lat is None or lon is None:
        return jsonify({
            'status': 'error',
            'message': 'latitude and longitude parameters required'
        }), 400

    if not (-90 <= lat <= 90):
        return jsonify({'status': 'error', 'message': 'Invalid latitude'}), 400
    if not (-180 <= lon <= 180):
        return jsonify({'status': 'error', 'message': 'Invalid longitude'}), 400

    hours = max(1, min(hours, 72))
    min_elevation = max(0, min(min_elevation, 90))

    try:
        from skyfield.api import load, wgs84, EarthSatellite
        from skyfield.almanac import find_discrete
        from data.satellites import TLE_SATELLITES

        ts = load.timescale()
        observer = wgs84.latlon(lat, lon)
        t0 = ts.now()
        t1 = ts.utc(t0.utc_datetime() + __import__('datetime').timedelta(hours=hours))

        all_passes = []

        for sat_key, sat_info in WEATHER_SATELLITES.items():
            if not sat_info['active']:
                continue

            tle_data = TLE_SATELLITES.get(sat_info['tle_key'])
            if not tle_data:
                continue

            satellite = EarthSatellite(tle_data[1], tle_data[2], tle_data[0], ts)

            def above_horizon(t, _sat=satellite):
                diff = _sat - observer
                topocentric = diff.at(t)
                alt, _, _ = topocentric.altaz()
                return alt.degrees > 0

            above_horizon.step_days = 1 / 720

            try:
                times, events = find_discrete(t0, t1, above_horizon)
            except Exception:
                continue

            i = 0
            while i < len(times):
                if i < len(events) and events[i]:  # Rising
                    rise_time = times[i]
                    set_time = None

                    for j in range(i + 1, len(times)):
                        if not events[j]:  # Setting
                            set_time = times[j]
                            i = j
                            break
                    else:
                        i += 1
                        continue

                    if set_time is None:
                        i += 1
                        continue

                    # Calculate max elevation
                    max_el = 0
                    max_el_az = 0
                    duration_seconds = (
                        set_time.utc_datetime() - rise_time.utc_datetime()
                    ).total_seconds()
                    duration_minutes = round(duration_seconds / 60, 1)

                    for k in range(30):
                        frac = k / 29
                        t_point = ts.utc(
                            rise_time.utc_datetime()
                            + __import__('datetime').timedelta(
                                seconds=duration_seconds * frac
                            )
                        )
                        diff = satellite - observer
                        topocentric = diff.at(t_point)
                        alt, az, _ = topocentric.altaz()
                        if alt.degrees > max_el:
                            max_el = alt.degrees
                            max_el_az = az.degrees

                    if max_el >= min_elevation:
                        # Calculate rise/set azimuth
                        rise_diff = satellite - observer
                        rise_topo = rise_diff.at(rise_time)
                        _, rise_az, _ = rise_topo.altaz()

                        set_diff = satellite - observer
                        set_topo = set_diff.at(set_time)
                        _, set_az, _ = set_topo.altaz()

                        pass_data = {
                            'satellite': sat_key,
                            'name': sat_info['name'],
                            'frequency': sat_info['frequency'],
                            'mode': sat_info['mode'],
                            'startTime': rise_time.utc_datetime().strftime(
                                '%Y-%m-%d %H:%M UTC'
                            ),
                            'startTimeISO': rise_time.utc_datetime().isoformat(),
                            'endTimeISO': set_time.utc_datetime().isoformat(),
                            'maxEl': round(max_el, 1),
                            'maxElAz': round(max_el_az, 1),
                            'riseAz': round(rise_az.degrees, 1),
                            'setAz': round(set_az.degrees, 1),
                            'duration': duration_minutes,
                            'quality': (
                                'excellent' if max_el >= 60
                                else 'good' if max_el >= 30
                                else 'fair'
                            ),
                        }
                        all_passes.append(pass_data)

                i += 1

        # Sort by start time
        all_passes.sort(key=lambda p: p['startTimeISO'])

        return jsonify({
            'status': 'ok',
            'passes': all_passes,
            'count': len(all_passes),
            'observer': {'latitude': lat, 'longitude': lon},
            'prediction_hours': hours,
            'min_elevation': min_elevation,
        })

    except ImportError:
        return jsonify({
            'status': 'error',
            'message': 'skyfield library not installed'
        }), 503

    except Exception as e:
        logger.error(f"Error predicting passes: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
