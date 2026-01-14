"""
TSCM (Technical Surveillance Countermeasures) Routes

Provides endpoints for counter-surveillance sweeps, baseline management,
threat detection, and reporting.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime
from typing import Any

from flask import Blueprint, Response, jsonify, request

from data.tscm_frequencies import (
    SWEEP_PRESETS,
    get_all_sweep_presets,
    get_sweep_preset,
)
from utils.database import (
    add_tscm_threat,
    acknowledge_tscm_threat,
    create_tscm_sweep,
    delete_tscm_baseline,
    get_active_tscm_baseline,
    get_all_tscm_baselines,
    get_tscm_baseline,
    get_tscm_sweep,
    get_tscm_threat_summary,
    get_tscm_threats,
    set_active_tscm_baseline,
    update_tscm_sweep,
)
from utils.tscm.baseline import BaselineComparator, BaselineRecorder
from utils.tscm.detector import ThreatDetector

logger = logging.getLogger('intercept.tscm')

tscm_bp = Blueprint('tscm', __name__, url_prefix='/tscm')

# =============================================================================
# Global State (will be initialized from app.py)
# =============================================================================

# These will be set by app.py
tscm_queue: queue.Queue | None = None
tscm_lock: threading.Lock | None = None

# Local state
_sweep_thread: threading.Thread | None = None
_sweep_running = False
_current_sweep_id: int | None = None
_baseline_recorder = BaselineRecorder()


def init_tscm_state(tscm_q: queue.Queue, lock: threading.Lock) -> None:
    """Initialize TSCM state from app.py."""
    global tscm_queue, tscm_lock
    tscm_queue = tscm_q
    tscm_lock = lock


def _emit_event(event_type: str, data: dict) -> None:
    """Emit an event to the SSE queue."""
    if tscm_queue:
        try:
            tscm_queue.put_nowait({
                'type': event_type,
                'timestamp': datetime.now().isoformat(),
                **data
            })
        except queue.Full:
            logger.warning("TSCM queue full, dropping event")


# =============================================================================
# Sweep Endpoints
# =============================================================================

def _check_available_devices(wifi: bool, bt: bool, rf: bool) -> dict:
    """Check which scanning devices are available."""
    import shutil
    import subprocess

    available = {
        'wifi': False,
        'bluetooth': False,
        'rf': False,
        'wifi_reason': 'Not checked',
        'bt_reason': 'Not checked',
        'rf_reason': 'Not checked',
    }

    # Check WiFi
    if wifi:
        if shutil.which('airodump-ng') or shutil.which('iwlist'):
            # Check for wireless interfaces
            try:
                result = subprocess.run(
                    ['iwconfig'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if 'no wireless extensions' not in result.stderr.lower() and result.stdout.strip():
                    available['wifi'] = True
                    available['wifi_reason'] = 'Wireless interface detected'
                else:
                    available['wifi_reason'] = 'No wireless interfaces found'
            except (subprocess.TimeoutExpired, FileNotFoundError):
                available['wifi_reason'] = 'Cannot detect wireless interfaces'
        else:
            available['wifi_reason'] = 'WiFi tools not installed (aircrack-ng)'

    # Check Bluetooth
    if bt:
        if shutil.which('bluetoothctl') or shutil.which('hcitool'):
            try:
                result = subprocess.run(
                    ['hciconfig'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if 'hci' in result.stdout.lower():
                    available['bluetooth'] = True
                    available['bt_reason'] = 'Bluetooth adapter detected'
                else:
                    available['bt_reason'] = 'No Bluetooth adapters found'
            except (subprocess.TimeoutExpired, FileNotFoundError):
                # Try bluetoothctl as fallback
                try:
                    result = subprocess.run(
                        ['bluetoothctl', 'list'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.stdout.strip():
                        available['bluetooth'] = True
                        available['bt_reason'] = 'Bluetooth adapter detected'
                    else:
                        available['bt_reason'] = 'No Bluetooth adapters found'
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    available['bt_reason'] = 'Cannot detect Bluetooth adapters'
        else:
            available['bt_reason'] = 'Bluetooth tools not installed (bluez)'

    # Check RF/SDR
    if rf:
        try:
            from utils.sdr import SDRFactory
            devices = SDRFactory.detect_devices()
            if devices:
                available['rf'] = True
                available['rf_reason'] = f'{len(devices)} SDR device(s) detected'
            else:
                available['rf_reason'] = 'No SDR devices found'
        except ImportError:
            available['rf_reason'] = 'SDR detection unavailable'

    return available


@tscm_bp.route('/sweep/start', methods=['POST'])
def start_sweep():
    """Start a TSCM sweep."""
    global _sweep_running, _sweep_thread, _current_sweep_id

    if _sweep_running:
        return jsonify({'status': 'error', 'message': 'Sweep already running'})

    data = request.get_json() or {}
    sweep_type = data.get('sweep_type', 'standard')
    baseline_id = data.get('baseline_id')
    wifi_enabled = data.get('wifi', True)
    bt_enabled = data.get('bluetooth', True)
    rf_enabled = data.get('rf', True)

    # Check for available devices
    devices = _check_available_devices(wifi_enabled, bt_enabled, rf_enabled)

    warnings = []
    if wifi_enabled and not devices['wifi']:
        warnings.append(f"WiFi: {devices['wifi_reason']}")
    if bt_enabled and not devices['bluetooth']:
        warnings.append(f"Bluetooth: {devices['bt_reason']}")
    if rf_enabled and not devices['rf']:
        warnings.append(f"RF: {devices['rf_reason']}")

    # If no devices available at all, return error
    if not any([devices['wifi'], devices['bluetooth'], devices['rf']]):
        return jsonify({
            'status': 'error',
            'message': 'No scanning devices available',
            'details': warnings
        }), 400

    # Create sweep record
    _current_sweep_id = create_tscm_sweep(
        sweep_type=sweep_type,
        baseline_id=baseline_id,
        wifi_enabled=wifi_enabled,
        bt_enabled=bt_enabled,
        rf_enabled=rf_enabled
    )

    _sweep_running = True

    # Start sweep thread
    _sweep_thread = threading.Thread(
        target=_run_sweep,
        args=(sweep_type, baseline_id, wifi_enabled, bt_enabled, rf_enabled),
        daemon=True
    )
    _sweep_thread.start()

    logger.info(f"Started TSCM sweep: type={sweep_type}, id={_current_sweep_id}")

    return jsonify({
        'status': 'success',
        'message': 'Sweep started',
        'sweep_id': _current_sweep_id,
        'sweep_type': sweep_type,
        'warnings': warnings if warnings else None,
        'devices': {
            'wifi': devices['wifi'],
            'bluetooth': devices['bluetooth'],
            'rf': devices['rf']
        }
    })


@tscm_bp.route('/sweep/stop', methods=['POST'])
def stop_sweep():
    """Stop the current TSCM sweep."""
    global _sweep_running

    if not _sweep_running:
        return jsonify({'status': 'error', 'message': 'No sweep running'})

    _sweep_running = False

    if _current_sweep_id:
        update_tscm_sweep(_current_sweep_id, status='aborted', completed=True)

    _emit_event('sweep_stopped', {'reason': 'user_requested'})

    logger.info("TSCM sweep stopped by user")

    return jsonify({'status': 'success', 'message': 'Sweep stopped'})


@tscm_bp.route('/sweep/status')
def sweep_status():
    """Get current sweep status."""
    status = {
        'running': _sweep_running,
        'sweep_id': _current_sweep_id,
    }

    if _current_sweep_id:
        sweep = get_tscm_sweep(_current_sweep_id)
        if sweep:
            status['sweep'] = sweep

    return jsonify(status)


@tscm_bp.route('/sweep/stream')
def sweep_stream():
    """SSE stream for real-time sweep updates."""
    def generate():
        while True:
            try:
                if tscm_queue:
                    msg = tscm_queue.get(timeout=1)
                    yield f"data: {json.dumps(msg)}\n\n"
                else:
                    time.sleep(1)
                    yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


def _run_sweep(
    sweep_type: str,
    baseline_id: int | None,
    wifi_enabled: bool,
    bt_enabled: bool,
    rf_enabled: bool
) -> None:
    """
    Run the TSCM sweep in a background thread.

    This orchestrates data collection from WiFi, BT, and RF sources,
    then analyzes results for threats.
    """
    global _sweep_running, _current_sweep_id

    try:
        # Get baseline for comparison if specified
        baseline = None
        if baseline_id:
            baseline = get_tscm_baseline(baseline_id)

        # Get sweep preset
        preset = get_sweep_preset(sweep_type) or SWEEP_PRESETS.get('standard')
        duration = preset.get('duration_seconds', 300)

        _emit_event('sweep_started', {
            'sweep_id': _current_sweep_id,
            'sweep_type': sweep_type,
            'duration': duration,
            'wifi': wifi_enabled,
            'bluetooth': bt_enabled,
            'rf': rf_enabled,
        })

        # Initialize detector
        detector = ThreatDetector(baseline)

        # Collect and analyze data
        threats_found = 0
        all_wifi = []
        all_bt = []
        all_rf = []

        start_time = time.time()

        while _sweep_running and (time.time() - start_time) < duration:
            # Import app module to access shared data stores
            try:
                import app as app_module

                # Collect WiFi data
                if wifi_enabled and hasattr(app_module, 'wifi_networks'):
                    wifi_data = list(app_module.wifi_networks.data.values())
                    for device in wifi_data:
                        if device not in all_wifi:
                            all_wifi.append(device)
                            threat = detector.analyze_wifi_device(device)
                            if threat:
                                _handle_threat(threat)
                                threats_found += 1

                # Collect Bluetooth data
                if bt_enabled and hasattr(app_module, 'bt_devices'):
                    bt_data = list(app_module.bt_devices.data.values())
                    for device in bt_data:
                        if device not in all_bt:
                            all_bt.append(device)
                            threat = detector.analyze_bt_device(device)
                            if threat:
                                _handle_threat(threat)
                                threats_found += 1

            except ImportError:
                logger.warning("Could not import app module for data collection")

            # Update progress
            elapsed = time.time() - start_time
            progress = min(100, int((elapsed / duration) * 100))

            _emit_event('sweep_progress', {
                'progress': progress,
                'elapsed': int(elapsed),
                'duration': duration,
                'wifi_count': len(all_wifi),
                'bt_count': len(all_bt),
                'rf_count': len(all_rf),
                'threats_found': threats_found,
            })

            time.sleep(2)  # Update every 2 seconds

        # Complete sweep
        if _sweep_running and _current_sweep_id:
            update_tscm_sweep(
                _current_sweep_id,
                status='completed',
                results={
                    'wifi_devices': len(all_wifi),
                    'bt_devices': len(all_bt),
                    'rf_signals': len(all_rf),
                },
                threats_found=threats_found,
                completed=True
            )

            _emit_event('sweep_completed', {
                'sweep_id': _current_sweep_id,
                'threats_found': threats_found,
                'wifi_count': len(all_wifi),
                'bt_count': len(all_bt),
                'rf_count': len(all_rf),
            })

    except Exception as e:
        logger.error(f"Sweep error: {e}")
        _emit_event('sweep_error', {'error': str(e)})
        if _current_sweep_id:
            update_tscm_sweep(_current_sweep_id, status='error', completed=True)

    finally:
        _sweep_running = False


def _handle_threat(threat: dict) -> None:
    """Handle a detected threat."""
    if not _current_sweep_id:
        return

    # Add to database
    threat_id = add_tscm_threat(
        sweep_id=_current_sweep_id,
        threat_type=threat['threat_type'],
        severity=threat['severity'],
        source=threat['source'],
        identifier=threat['identifier'],
        name=threat.get('name'),
        signal_strength=threat.get('signal_strength'),
        frequency=threat.get('frequency'),
        details=threat.get('details')
    )

    # Emit event
    _emit_event('threat_detected', {
        'threat_id': threat_id,
        **threat
    })

    logger.warning(
        f"TSCM threat detected: {threat['threat_type']} - "
        f"{threat['identifier']} ({threat['severity']})"
    )


# =============================================================================
# Baseline Endpoints
# =============================================================================

@tscm_bp.route('/baseline/record', methods=['POST'])
def record_baseline():
    """Start recording a new baseline."""
    data = request.get_json() or {}
    name = data.get('name', f'Baseline {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    location = data.get('location')
    description = data.get('description')

    baseline_id = _baseline_recorder.start_recording(name, location, description)

    return jsonify({
        'status': 'success',
        'message': 'Baseline recording started',
        'baseline_id': baseline_id
    })


@tscm_bp.route('/baseline/stop', methods=['POST'])
def stop_baseline():
    """Stop baseline recording."""
    result = _baseline_recorder.stop_recording()

    if 'error' in result:
        return jsonify({'status': 'error', 'message': result['error']})

    return jsonify({
        'status': 'success',
        'message': 'Baseline recording complete',
        **result
    })


@tscm_bp.route('/baseline/status')
def baseline_status():
    """Get baseline recording status."""
    return jsonify(_baseline_recorder.get_recording_status())


@tscm_bp.route('/baselines')
def list_baselines():
    """List all baselines."""
    baselines = get_all_tscm_baselines()
    return jsonify({'status': 'success', 'baselines': baselines})


@tscm_bp.route('/baseline/<int:baseline_id>')
def get_baseline(baseline_id: int):
    """Get a specific baseline."""
    baseline = get_tscm_baseline(baseline_id)
    if not baseline:
        return jsonify({'status': 'error', 'message': 'Baseline not found'}), 404

    return jsonify({'status': 'success', 'baseline': baseline})


@tscm_bp.route('/baseline/<int:baseline_id>/activate', methods=['POST'])
def activate_baseline(baseline_id: int):
    """Set a baseline as active."""
    success = set_active_tscm_baseline(baseline_id)
    if not success:
        return jsonify({'status': 'error', 'message': 'Baseline not found'}), 404

    return jsonify({'status': 'success', 'message': 'Baseline activated'})


@tscm_bp.route('/baseline/<int:baseline_id>', methods=['DELETE'])
def remove_baseline(baseline_id: int):
    """Delete a baseline."""
    success = delete_tscm_baseline(baseline_id)
    if not success:
        return jsonify({'status': 'error', 'message': 'Baseline not found'}), 404

    return jsonify({'status': 'success', 'message': 'Baseline deleted'})


@tscm_bp.route('/baseline/active')
def get_active_baseline():
    """Get the currently active baseline."""
    baseline = get_active_tscm_baseline()
    if not baseline:
        return jsonify({'status': 'success', 'baseline': None})

    return jsonify({'status': 'success', 'baseline': baseline})


# =============================================================================
# Threat Endpoints
# =============================================================================

@tscm_bp.route('/threats')
def list_threats():
    """List threats with optional filters."""
    sweep_id = request.args.get('sweep_id', type=int)
    severity = request.args.get('severity')
    acknowledged = request.args.get('acknowledged')
    limit = request.args.get('limit', 100, type=int)

    ack_filter = None
    if acknowledged is not None:
        ack_filter = acknowledged.lower() in ('true', '1', 'yes')

    threats = get_tscm_threats(
        sweep_id=sweep_id,
        severity=severity,
        acknowledged=ack_filter,
        limit=limit
    )

    return jsonify({'status': 'success', 'threats': threats})


@tscm_bp.route('/threats/summary')
def threat_summary():
    """Get threat count summary by severity."""
    summary = get_tscm_threat_summary()
    return jsonify({'status': 'success', 'summary': summary})


@tscm_bp.route('/threats/<int:threat_id>', methods=['PUT'])
def update_threat(threat_id: int):
    """Update a threat (acknowledge, add notes)."""
    data = request.get_json() or {}

    if data.get('acknowledge'):
        notes = data.get('notes')
        success = acknowledge_tscm_threat(threat_id, notes)
        if not success:
            return jsonify({'status': 'error', 'message': 'Threat not found'}), 404

    return jsonify({'status': 'success', 'message': 'Threat updated'})


# =============================================================================
# Preset Endpoints
# =============================================================================

@tscm_bp.route('/presets')
def list_presets():
    """List available sweep presets."""
    presets = get_all_sweep_presets()
    return jsonify({'status': 'success', 'presets': presets})


@tscm_bp.route('/presets/<preset_name>')
def get_preset(preset_name: str):
    """Get details for a specific preset."""
    preset = get_sweep_preset(preset_name)
    if not preset:
        return jsonify({'status': 'error', 'message': 'Preset not found'}), 404

    return jsonify({'status': 'success', 'preset': preset})


# =============================================================================
# Data Feed Endpoints (for adding data during sweeps/baselines)
# =============================================================================

@tscm_bp.route('/feed/wifi', methods=['POST'])
def feed_wifi():
    """Feed WiFi device data for baseline recording."""
    data = request.get_json()
    if data:
        _baseline_recorder.add_wifi_device(data)
    return jsonify({'status': 'success'})


@tscm_bp.route('/feed/bluetooth', methods=['POST'])
def feed_bluetooth():
    """Feed Bluetooth device data for baseline recording."""
    data = request.get_json()
    if data:
        _baseline_recorder.add_bt_device(data)
    return jsonify({'status': 'success'})


@tscm_bp.route('/feed/rf', methods=['POST'])
def feed_rf():
    """Feed RF signal data for baseline recording."""
    data = request.get_json()
    if data:
        _baseline_recorder.add_rf_signal(data)
    return jsonify({'status': 'success'})
