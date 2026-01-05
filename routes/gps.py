"""GPS dongle routes for USB GPS device support."""

from __future__ import annotations

import queue
import threading
import time
from typing import Generator

from flask import Blueprint, jsonify, request, Response

from utils.logging import get_logger
from utils.sse import format_sse
from utils.gps import (
    detect_gps_devices,
    is_serial_available,
    get_gps_reader,
    start_gps,
    start_gpsd,
    stop_gps,
    get_current_position,
    GPSPosition,
    GPSDClient,
)

logger = get_logger('intercept.gps')

gps_bp = Blueprint('gps', __name__, url_prefix='/gps')

# Queue for SSE position updates
_gps_queue: queue.Queue = queue.Queue(maxsize=100)


def _position_callback(position: GPSPosition) -> None:
    """Callback to queue position updates for SSE stream."""
    try:
        _gps_queue.put_nowait(position.to_dict())
    except queue.Full:
        # Discard oldest if queue is full
        try:
            _gps_queue.get_nowait()
            _gps_queue.put_nowait(position.to_dict())
        except queue.Empty:
            pass


@gps_bp.route('/available')
def check_gps_available():
    """Check if GPS dongle support is available."""
    return jsonify({
        'available': is_serial_available(),
        'message': None if is_serial_available() else 'pyserial not installed - run: pip install pyserial'
    })


@gps_bp.route('/gpsd/check')
def check_gpsd_available():
    """Check if gpsd is reachable."""
    import socket

    host = request.args.get('host', 'localhost')
    port = int(request.args.get('port', 2947))

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect((host, port))
        sock.close()
        return jsonify({
            'available': True,
            'host': host,
            'port': port,
            'message': f'gpsd reachable at {host}:{port}'
        })
    except Exception as e:
        return jsonify({
            'available': False,
            'host': host,
            'port': port,
            'message': f'Cannot connect to gpsd at {host}:{port}: {e}'
        })


@gps_bp.route('/devices')
def list_gps_devices():
    """List available GPS serial devices."""
    if not is_serial_available():
        return jsonify({
            'status': 'error',
            'message': 'pyserial not installed'
        }), 503

    devices = detect_gps_devices()
    return jsonify({
        'status': 'ok',
        'devices': devices
    })


@gps_bp.route('/start', methods=['POST'])
def start_gps_reader():
    """Start GPS reader on specified device."""
    if not is_serial_available():
        return jsonify({
            'status': 'error',
            'message': 'pyserial not installed'
        }), 503

    # Check if already running
    reader = get_gps_reader()
    if reader and reader.is_running:
        return jsonify({
            'status': 'error',
            'message': 'GPS reader already running'
        }), 409

    data = request.json or {}
    device_path = data.get('device')
    baudrate = data.get('baudrate', 9600)

    if not device_path:
        return jsonify({
            'status': 'error',
            'message': 'Device path required'
        }), 400

    # Validate baudrate
    valid_baudrates = [4800, 9600, 19200, 38400, 57600, 115200]
    if baudrate not in valid_baudrates:
        return jsonify({
            'status': 'error',
            'message': f'Invalid baudrate. Valid options: {valid_baudrates}'
        }), 400

    # Clear the queue
    while not _gps_queue.empty():
        try:
            _gps_queue.get_nowait()
        except queue.Empty:
            break

    # Start the GPS reader with callback pre-registered (avoids race condition)
    success = start_gps(device_path, baudrate, callback=_position_callback)

    if success:
        return jsonify({
            'status': 'started',
            'device': device_path,
            'baudrate': baudrate,
            'source': 'serial'
        })
    else:
        reader = get_gps_reader()
        error = reader.error if reader else 'Unknown error'
        return jsonify({
            'status': 'error',
            'message': f'Failed to start GPS reader: {error}'
        }), 500


@gps_bp.route('/gpsd/start', methods=['POST'])
def start_gpsd_client():
    """Start GPS client connected to gpsd."""
    # Check if already running
    reader = get_gps_reader()
    if reader and reader.is_running:
        return jsonify({
            'status': 'error',
            'message': 'GPS reader already running'
        }), 409

    data = request.json or {}
    host = data.get('host', 'localhost')
    port = data.get('port', 2947)

    # Validate port
    try:
        port = int(port)
        if not (1 <= port <= 65535):
            raise ValueError("Port out of range")
    except (ValueError, TypeError):
        return jsonify({
            'status': 'error',
            'message': 'Invalid port number'
        }), 400

    # Clear the queue
    while not _gps_queue.empty():
        try:
            _gps_queue.get_nowait()
        except queue.Empty:
            break

    # Start the gpsd client with callback pre-registered
    success = start_gpsd(host, port, callback=_position_callback)

    if success:
        return jsonify({
            'status': 'started',
            'host': host,
            'port': port,
            'source': 'gpsd'
        })
    else:
        reader = get_gps_reader()
        error = reader.error if reader else 'Unknown error'
        return jsonify({
            'status': 'error',
            'message': f'Failed to connect to gpsd: {error}'
        }), 500


@gps_bp.route('/stop', methods=['POST'])
def stop_gps_reader():
    """Stop GPS reader."""
    reader = get_gps_reader()
    if reader:
        reader.remove_callback(_position_callback)

    stop_gps()

    return jsonify({'status': 'stopped'})


@gps_bp.route('/status')
def get_gps_status():
    """Get current GPS reader status."""
    reader = get_gps_reader()

    if not reader:
        return jsonify({
            'running': False,
            'device': None,
            'position': None,
            'error': None,
            'message': 'GPS reader not started'
        })

    position = reader.position
    return jsonify({
        'running': reader.is_running,
        'device': reader.device_path,
        'position': position.to_dict() if position else None,
        'last_update': reader.last_update.isoformat() if reader.last_update else None,
        'error': reader.error,
        'message': 'Waiting for GPS fix - ensure GPS has clear view of sky' if reader.is_running and not position else None
    })


@gps_bp.route('/position')
def get_position():
    """Get current GPS position."""
    position = get_current_position()

    if position:
        return jsonify({
            'status': 'ok',
            'position': position.to_dict()
        })
    else:
        reader = get_gps_reader()
        if not reader or not reader.is_running:
            return jsonify({
                'status': 'error',
                'message': 'GPS reader not running'
            }), 400
        else:
            return jsonify({
                'status': 'waiting',
                'message': 'Waiting for GPS fix - ensure GPS has clear view of sky'
            })


@gps_bp.route('/debug')
def debug_gps():
    """Debug endpoint showing GPS reader state."""
    reader = get_gps_reader()

    if not reader:
        return jsonify({
            'reader': None,
            'message': 'No GPS reader initialized'
        })

    position = reader.position
    source = 'gpsd' if isinstance(reader, GPSDClient) else 'serial'
    return jsonify({
        'running': reader.is_running,
        'source': source,
        'device': reader.device_path,
        'baudrate': reader.baudrate,
        'has_position': position is not None,
        'position': position.to_dict() if position else None,
        'last_update': reader.last_update.isoformat() if reader.last_update else None,
        'error': reader.error,
        'callbacks_registered': len(reader._callbacks),
    })


@gps_bp.route('/stream')
def stream_gps():
    """SSE stream of GPS position updates."""
    def generate() -> Generator[str, None, None]:
        last_keepalive = time.time()
        keepalive_interval = 30.0

        while True:
            try:
                position = _gps_queue.get(timeout=1)
                last_keepalive = time.time()
                yield format_sse({'type': 'position', **position})
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
