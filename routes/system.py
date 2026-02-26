"""System Health monitoring blueprint.

Provides real-time system metrics (CPU, memory, disk, temperatures),
active process status, and SDR device enumeration via SSE streaming.
"""

from __future__ import annotations

import contextlib
import os
import platform
import queue
import socket
import threading
import time
from typing import Any

from flask import Blueprint, Response, jsonify

from utils.constants import SSE_KEEPALIVE_INTERVAL, SSE_QUEUE_TIMEOUT
from utils.logging import sensor_logger as logger
from utils.sse import sse_stream_fanout

try:
    import psutil

    _HAS_PSUTIL = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    _HAS_PSUTIL = False

system_bp = Blueprint('system', __name__, url_prefix='/system')

# ---------------------------------------------------------------------------
# Background metrics collector
# ---------------------------------------------------------------------------

_metrics_queue: queue.Queue = queue.Queue(maxsize=500)
_collector_started = False
_collector_lock = threading.Lock()
_app_start_time: float | None = None


def _get_app_start_time() -> float:
    """Return the application start timestamp from the main app module."""
    global _app_start_time
    if _app_start_time is None:
        try:
            import app as app_module

            _app_start_time = getattr(app_module, '_app_start_time', time.time())
        except Exception:
            _app_start_time = time.time()
    return _app_start_time


def _get_app_version() -> str:
    """Return the application version string."""
    try:
        from config import VERSION

        return VERSION
    except Exception:
        return 'unknown'


def _format_uptime(seconds: float) -> str:
    """Format seconds into a human-readable uptime string."""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    parts = []
    if days > 0:
        parts.append(f'{days}d')
    if hours > 0:
        parts.append(f'{hours}h')
    parts.append(f'{minutes}m')
    return ' '.join(parts)


def _collect_process_status() -> dict[str, bool]:
    """Return running/stopped status for each decoder process.

    Mirrors the logic in app.py health_check().
    """
    try:
        import app as app_module

        def _alive(attr: str) -> bool:
            proc = getattr(app_module, attr, None)
            if proc is None:
                return False
            try:
                return proc.poll() is None
            except Exception:
                return False

        processes: dict[str, bool] = {
            'pager': _alive('current_process'),
            'sensor': _alive('sensor_process'),
            'adsb': _alive('adsb_process'),
            'ais': _alive('ais_process'),
            'acars': _alive('acars_process'),
            'vdl2': _alive('vdl2_process'),
            'aprs': _alive('aprs_process'),
            'dsc': _alive('dsc_process'),
            'morse': _alive('morse_process'),
        }

        # WiFi
        try:
            from app import _get_wifi_health

            wifi_active, _, _ = _get_wifi_health()
            processes['wifi'] = wifi_active
        except Exception:
            processes['wifi'] = False

        # Bluetooth
        try:
            from app import _get_bluetooth_health

            bt_active, _ = _get_bluetooth_health()
            processes['bluetooth'] = bt_active
        except Exception:
            processes['bluetooth'] = False

        # SubGHz
        try:
            from app import _get_subghz_active

            processes['subghz'] = _get_subghz_active()
        except Exception:
            processes['subghz'] = False

        return processes
    except Exception:
        return {}


def _collect_metrics() -> dict[str, Any]:
    """Gather a snapshot of system metrics."""
    now = time.time()
    start = _get_app_start_time()
    uptime_seconds = round(now - start, 2)

    metrics: dict[str, Any] = {
        'type': 'system_metrics',
        'timestamp': now,
        'system': {
            'hostname': socket.gethostname(),
            'platform': platform.platform(),
            'python': platform.python_version(),
            'version': _get_app_version(),
            'uptime_seconds': uptime_seconds,
            'uptime_human': _format_uptime(uptime_seconds),
        },
        'processes': _collect_process_status(),
    }

    if _HAS_PSUTIL:
        # CPU
        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_count = psutil.cpu_count() or 1
        try:
            load_1, load_5, load_15 = os.getloadavg()
        except (OSError, AttributeError):
            load_1 = load_5 = load_15 = 0.0

        metrics['cpu'] = {
            'percent': cpu_percent,
            'count': cpu_count,
            'load_1': round(load_1, 2),
            'load_5': round(load_5, 2),
            'load_15': round(load_15, 2),
        }

        # Memory
        mem = psutil.virtual_memory()
        metrics['memory'] = {
            'total': mem.total,
            'used': mem.used,
            'available': mem.available,
            'percent': mem.percent,
        }

        swap = psutil.swap_memory()
        metrics['swap'] = {
            'total': swap.total,
            'used': swap.used,
            'percent': swap.percent,
        }

        # Disk
        try:
            disk = psutil.disk_usage('/')
            metrics['disk'] = {
                'total': disk.total,
                'used': disk.used,
                'free': disk.free,
                'percent': disk.percent,
                'path': '/',
            }
        except Exception:
            metrics['disk'] = None

        # Temperatures
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                temp_data: dict[str, list[dict[str, Any]]] = {}
                for chip, entries in temps.items():
                    temp_data[chip] = [
                        {
                            'label': e.label or chip,
                            'current': e.current,
                            'high': e.high,
                            'critical': e.critical,
                        }
                        for e in entries
                    ]
                metrics['temperatures'] = temp_data
            else:
                metrics['temperatures'] = None
        except (AttributeError, Exception):
            metrics['temperatures'] = None
    else:
        metrics['cpu'] = None
        metrics['memory'] = None
        metrics['swap'] = None
        metrics['disk'] = None
        metrics['temperatures'] = None

    return metrics


def _collector_loop() -> None:
    """Background thread that pushes metrics onto the queue every 3 seconds."""
    # Seed psutil's CPU measurement so the first real read isn't 0%.
    if _HAS_PSUTIL:
        with contextlib.suppress(Exception):
            psutil.cpu_percent(interval=None)

    while True:
        try:
            metrics = _collect_metrics()
            # Non-blocking put — drop oldest if full
            try:
                _metrics_queue.put_nowait(metrics)
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    _metrics_queue.get_nowait()
                _metrics_queue.put_nowait(metrics)
        except Exception as exc:
            logger.debug('system metrics collection error: %s', exc)
        time.sleep(3)


def _ensure_collector() -> None:
    """Start the background collector thread once."""
    global _collector_started
    if _collector_started:
        return
    with _collector_lock:
        if _collector_started:
            return
        t = threading.Thread(target=_collector_loop, daemon=True, name='system-metrics-collector')
        t.start()
        _collector_started = True
        logger.info('System metrics collector started')


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@system_bp.route('/metrics')
def get_metrics() -> Response:
    """REST snapshot of current system metrics."""
    _ensure_collector()
    return jsonify(_collect_metrics())


@system_bp.route('/stream')
def stream_system() -> Response:
    """SSE stream for real-time system metrics."""
    _ensure_collector()

    response = Response(
        sse_stream_fanout(
            source_queue=_metrics_queue,
            channel_key='system',
            timeout=SSE_QUEUE_TIMEOUT,
            keepalive_interval=SSE_KEEPALIVE_INTERVAL,
        ),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


@system_bp.route('/sdr_devices')
def get_sdr_devices() -> Response:
    """Enumerate all connected SDR devices (on-demand, not every tick)."""
    try:
        from utils.sdr.detection import detect_all_devices

        devices = detect_all_devices()
        result = []
        for d in devices:
            result.append({
                'type': d.sdr_type.value if hasattr(d.sdr_type, 'value') else str(d.sdr_type),
                'index': d.index,
                'name': d.name,
                'serial': d.serial or '',
                'driver': d.driver or '',
            })
        return jsonify({'devices': result})
    except Exception as exc:
        logger.warning('SDR device detection failed: %s', exc)
        return jsonify({'devices': [], 'error': str(exc)})
