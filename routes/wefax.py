"""WeFax (Weather Fax) decoder routes.

Provides endpoints for decoding HF weather fax transmissions from
maritime/aviation weather services worldwide.
"""

from __future__ import annotations

import queue

from flask import Blueprint, Response, jsonify, request, send_file

import app as app_module
from utils.logging import get_logger
from utils.sse import sse_stream_fanout
from utils.validation import validate_frequency
from utils.wefax import get_wefax_decoder
from utils.wefax_stations import get_current_broadcasts, get_station, load_stations

logger = get_logger('intercept.wefax')

wefax_bp = Blueprint('wefax', __name__, url_prefix='/wefax')

# SSE progress queue
_wefax_queue: queue.Queue = queue.Queue(maxsize=100)

# Track active SDR device
wefax_active_device: int | None = None


def _progress_callback(data: dict) -> None:
    """Callback to queue progress updates for SSE stream."""
    try:
        _wefax_queue.put_nowait(data)
    except queue.Full:
        try:
            _wefax_queue.get_nowait()
            _wefax_queue.put_nowait(data)
        except queue.Empty:
            pass


@wefax_bp.route('/status')
def get_status():
    """Get WeFax decoder status."""
    decoder = get_wefax_decoder()
    return jsonify({
        'available': True,
        'running': decoder.is_running,
        'image_count': len(decoder.get_images()),
    })


@wefax_bp.route('/start', methods=['POST'])
def start_decoder():
    """Start WeFax decoder.

    JSON body:
        {
            "frequency_khz": 4298,
            "station": "NOJ",
            "device": 0,
            "gain": 40,
            "ioc": 576,
            "lpm": 120,
            "direct_sampling": true
        }
    """
    decoder = get_wefax_decoder()

    if decoder.is_running:
        return jsonify({
            'status': 'already_running',
            'message': 'WeFax decoder is already running',
        })

    # Clear queue
    while not _wefax_queue.empty():
        try:
            _wefax_queue.get_nowait()
        except queue.Empty:
            break

    data = request.get_json(silent=True) or {}

    # Validate frequency (required)
    frequency_khz = data.get('frequency_khz')
    if frequency_khz is None:
        return jsonify({
            'status': 'error',
            'message': 'frequency_khz is required',
        }), 400

    try:
        frequency_khz = float(frequency_khz)
        # WeFax operates on HF: 2-30 MHz (2000-30000 kHz)
        freq_mhz = frequency_khz / 1000.0
        validate_frequency(freq_mhz, min_mhz=2.0, max_mhz=30.0)
    except (TypeError, ValueError) as e:
        return jsonify({
            'status': 'error',
            'message': f'Invalid frequency: {e}',
        }), 400

    station = str(data.get('station', '')).strip()
    device_index = data.get('device', 0)
    gain = float(data.get('gain', 40.0))
    ioc = int(data.get('ioc', 576))
    lpm = int(data.get('lpm', 120))
    direct_sampling = bool(data.get('direct_sampling', True))

    # Validate IOC and LPM
    if ioc not in (288, 576):
        return jsonify({
            'status': 'error',
            'message': 'IOC must be 288 or 576',
        }), 400

    if lpm not in (60, 120):
        return jsonify({
            'status': 'error',
            'message': 'LPM must be 60 or 120',
        }), 400

    # Claim SDR device
    global wefax_active_device
    device_int = int(device_index)
    error = app_module.claim_sdr_device(device_int, 'wefax')
    if error:
        return jsonify({
            'status': 'error',
            'error_type': 'DEVICE_BUSY',
            'message': error,
        }), 409

    # Set callback and start
    decoder.set_callback(_progress_callback)
    success = decoder.start(
        frequency_khz=frequency_khz,
        station=station,
        device_index=device_int,
        gain=gain,
        ioc=ioc,
        lpm=lpm,
        direct_sampling=direct_sampling,
    )

    if success:
        wefax_active_device = device_int
        return jsonify({
            'status': 'started',
            'frequency_khz': frequency_khz,
            'station': station,
            'ioc': ioc,
            'lpm': lpm,
            'device': device_int,
        })
    else:
        app_module.release_sdr_device(device_int)
        return jsonify({
            'status': 'error',
            'message': 'Failed to start decoder',
        }), 500


@wefax_bp.route('/stop', methods=['POST'])
def stop_decoder():
    """Stop WeFax decoder."""
    global wefax_active_device
    decoder = get_wefax_decoder()
    decoder.stop()

    if wefax_active_device is not None:
        app_module.release_sdr_device(wefax_active_device)
        wefax_active_device = None

    return jsonify({'status': 'stopped'})


@wefax_bp.route('/stream')
def stream_progress():
    """SSE stream of WeFax decode progress."""
    response = Response(
        sse_stream_fanout(
            source_queue=_wefax_queue,
            channel_key='wefax',
            timeout=1.0,
            keepalive_interval=30.0,
        ),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response


@wefax_bp.route('/images')
def list_images():
    """Get list of decoded WeFax images."""
    decoder = get_wefax_decoder()
    images = decoder.get_images()

    limit = request.args.get('limit', type=int)
    if limit and limit > 0:
        images = images[-limit:]

    return jsonify({
        'status': 'ok',
        'images': [img.to_dict() for img in images],
        'count': len(images),
    })


@wefax_bp.route('/images/<filename>')
def get_image(filename: str):
    """Get a decoded WeFax image file."""
    decoder = get_wefax_decoder()

    if not filename.replace('_', '').replace('-', '').replace('.', '').isalnum():
        return jsonify({'status': 'error', 'message': 'Invalid filename'}), 400

    if not filename.endswith('.png'):
        return jsonify({'status': 'error', 'message': 'Only PNG files supported'}), 400

    image_path = decoder._output_dir / filename
    if not image_path.exists():
        return jsonify({'status': 'error', 'message': 'Image not found'}), 404

    return send_file(image_path, mimetype='image/png')


@wefax_bp.route('/images/<filename>', methods=['DELETE'])
def delete_image(filename: str):
    """Delete a decoded WeFax image."""
    decoder = get_wefax_decoder()

    if not filename.replace('_', '').replace('-', '').replace('.', '').isalnum():
        return jsonify({'status': 'error', 'message': 'Invalid filename'}), 400

    if not filename.endswith('.png'):
        return jsonify({'status': 'error', 'message': 'Only PNG files supported'}), 400

    if decoder.delete_image(filename):
        return jsonify({'status': 'ok'})
    else:
        return jsonify({'status': 'error', 'message': 'Image not found'}), 404


@wefax_bp.route('/images', methods=['DELETE'])
def delete_all_images():
    """Delete all decoded WeFax images."""
    decoder = get_wefax_decoder()
    count = decoder.delete_all_images()
    return jsonify({'status': 'ok', 'deleted': count})


@wefax_bp.route('/stations')
def list_stations():
    """Get all WeFax stations from the database."""
    stations = load_stations()
    return jsonify({
        'status': 'ok',
        'stations': stations,
        'count': len(stations),
    })


@wefax_bp.route('/stations/<callsign>')
def station_detail(callsign: str):
    """Get station detail including current schedule info."""
    station = get_station(callsign)
    if not station:
        return jsonify({
            'status': 'error',
            'message': f'Station {callsign} not found',
        }), 404

    current = get_current_broadcasts(callsign)

    return jsonify({
        'status': 'ok',
        'station': station,
        'current_broadcasts': current,
    })
