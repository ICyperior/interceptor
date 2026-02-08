"""GSM Spy route handlers for cellular tower and device tracking."""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import requests
from flask import Blueprint, Response, jsonify, render_template, request

import app as app_module
import config
from config import SHARED_OBSERVER_LOCATION_ENABLED
from utils.database import get_db
from utils.process import register_process, safe_terminate, unregister_process
from utils.sse import format_sse
from utils.validation import validate_device_index

from utils.logging import get_logger
logger = get_logger('intercept.gsm_spy')
logger.setLevel(logging.DEBUG)  # GSM Spy needs verbose logging for diagnostics

gsm_spy_bp = Blueprint('gsm_spy', __name__, url_prefix='/gsm_spy')

# Regional band configurations (G-01)
REGIONAL_BANDS = {
    'Americas': {
        'GSM850': {'start': 869e6, 'end': 894e6, 'arfcn_start': 128, 'arfcn_end': 251},
        'PCS1900': {'start': 1930e6, 'end': 1990e6, 'arfcn_start': 512, 'arfcn_end': 810}
    },
    'Europe': {
        'GSM800': {'start': 832e6, 'end': 862e6, 'arfcn_start': 438, 'arfcn_end': 511},  # E-GSM800 downlink
        'GSM850': {'start': 869e6, 'end': 894e6, 'arfcn_start': 128, 'arfcn_end': 251},  # Also used in some EU countries
        'EGSM900': {'start': 925e6, 'end': 960e6, 'arfcn_start': 0, 'arfcn_end': 124},
        'DCS1800': {'start': 1805e6, 'end': 1880e6, 'arfcn_start': 512, 'arfcn_end': 885}
    },
    'Asia': {
        'EGSM900': {'start': 925e6, 'end': 960e6, 'arfcn_start': 0, 'arfcn_end': 124},
        'DCS1800': {'start': 1805e6, 'end': 1880e6, 'arfcn_start': 512, 'arfcn_end': 885}
    }
}

# Module state tracking
gsm_using_service = False
gsm_connected = False
gsm_towers_found = 0
gsm_devices_tracked = 0

# Geocoding worker state
_geocoding_worker_thread = None


# ============================================
# API Usage Tracking Helper Functions
# ============================================

def get_api_usage_today():
    """Get OpenCellID API usage count for today."""
    from utils.database import get_setting
    today = datetime.now().date().isoformat()
    usage_date = get_setting('gsm.opencellid.usage_date', '')

    # Reset counter if new day
    if usage_date != today:
        from utils.database import set_setting
        set_setting('gsm.opencellid.usage_date', today)
        set_setting('gsm.opencellid.usage_count', 0)
        return 0

    return get_setting('gsm.opencellid.usage_count', 0)


def increment_api_usage():
    """Increment OpenCellID API usage counter."""
    from utils.database import set_setting
    current = get_api_usage_today()
    set_setting('gsm.opencellid.usage_count', current + 1)
    return current + 1


def can_use_api():
    """Check if we can make an API call within daily limit."""
    current_usage = get_api_usage_today()
    return current_usage < config.GSM_API_DAILY_LIMIT


# ============================================
# Background Geocoding Worker
# ============================================

def start_geocoding_worker():
    """Start background thread for async geocoding."""
    global _geocoding_worker_thread
    if _geocoding_worker_thread is None or not _geocoding_worker_thread.is_alive():
        _geocoding_worker_thread = threading.Thread(
            target=geocoding_worker,
            daemon=True,
            name='gsm-geocoding-worker'
        )
        _geocoding_worker_thread.start()
        logger.info("Started geocoding worker thread")


def geocoding_worker():
    """Worker thread processes pending geocoding requests."""
    from utils.gsm_geocoding import lookup_cell_from_api, get_geocoding_queue

    geocoding_queue = get_geocoding_queue()

    while True:
        try:
            # Wait for pending tower with timeout
            tower_data = geocoding_queue.get(timeout=5)

            # Check rate limit
            if not can_use_api():
                current_usage = get_api_usage_today()
                logger.warning(f"OpenCellID API rate limit reached ({current_usage}/{config.GSM_API_DAILY_LIMIT})")
                geocoding_queue.task_done()
                continue

            # Call API
            mcc = tower_data.get('mcc')
            mnc = tower_data.get('mnc')
            lac = tower_data.get('lac')
            cid = tower_data.get('cid')

            logger.debug(f"Geocoding tower via API: MCC={mcc} MNC={mnc} LAC={lac} CID={cid}")

            coords = lookup_cell_from_api(mcc, mnc, lac, cid)

            if coords:
                # Update tower data with coordinates
                tower_data['lat'] = coords['lat']
                tower_data['lon'] = coords['lon']
                tower_data['source'] = 'api'
                tower_data['status'] = 'resolved'
                tower_data['type'] = 'tower_update'

                # Add optional fields if available
                if coords.get('azimuth') is not None:
                    tower_data['azimuth'] = coords['azimuth']
                if coords.get('range_meters') is not None:
                    tower_data['range_meters'] = coords['range_meters']
                if coords.get('operator'):
                    tower_data['operator'] = coords['operator']
                if coords.get('radio'):
                    tower_data['radio'] = coords['radio']

                # Update DataStore
                key = f"{mcc}_{mnc}_{lac}_{cid}"
                app_module.gsm_spy_towers[key] = tower_data

                # Send update to SSE stream
                try:
                    app_module.gsm_spy_queue.put_nowait(tower_data)
                    logger.info(f"Resolved coordinates for tower: MCC={mcc} MNC={mnc} LAC={lac} CID={cid}")
                except queue.Full:
                    logger.warning("SSE queue full, dropping tower update")

                # Increment API usage counter
                usage_count = increment_api_usage()
                logger.info(f"OpenCellID API call #{usage_count} today")

            else:
                logger.warning(f"Could not resolve coordinates for tower: MCC={mcc} MNC={mnc} LAC={lac} CID={cid}")

            geocoding_queue.task_done()

            # Rate limiting between API calls (be nice to OpenCellID)
            time.sleep(1)

        except queue.Empty:
            # No pending towers, continue waiting
            continue
        except Exception as e:
            logger.error(f"Geocoding worker error: {e}", exc_info=True)
            time.sleep(1)


def arfcn_to_frequency(arfcn):
    """Convert ARFCN to downlink frequency in Hz.

    Uses REGIONAL_BANDS to determine the correct band and conversion formula.
    Returns frequency in Hz (e.g., 925800000 for 925.8 MHz).
    """
    arfcn = int(arfcn)

    # Search all bands to find which one this ARFCN belongs to
    for region_bands in REGIONAL_BANDS.values():
        for band_name, band_info in region_bands.items():
            arfcn_start = band_info['arfcn_start']
            arfcn_end = band_info['arfcn_end']

            if arfcn_start <= arfcn <= arfcn_end:
                # Found the right band, calculate frequency
                # Downlink frequency = band_start + (arfcn - arfcn_start) * 200kHz
                freq_hz = band_info['start'] + (arfcn - arfcn_start) * 200000
                return int(freq_hz)

    # If ARFCN not found in any band, raise error
    raise ValueError(f"ARFCN {arfcn} not found in any known GSM band")


def validate_band_names(bands: list[str], region: str) -> tuple[list[str], str | None]:
    """Validate band names against REGIONAL_BANDS whitelist.

    Args:
        bands: List of band names from user input
        region: Region name (Americas, Europe, Asia)

    Returns:
        Tuple of (validated_bands, error_message)
    """
    if not bands:
        return [], None

    region_bands = REGIONAL_BANDS.get(region)
    if not region_bands:
        return [], f"Invalid region: {region}"

    valid_band_names = set(region_bands.keys())
    invalid_bands = [b for b in bands if b not in valid_band_names]

    if invalid_bands:
        return [], (f"Invalid bands for {region}: {', '.join(invalid_bands)}. "
                   f"Valid bands: {', '.join(sorted(valid_band_names))}")

    return bands, None


# tshark field name discovery - field names vary between Wireshark versions
_tshark_fields_cache: dict[str, str] | None = None


def _discover_tshark_fields() -> dict[str, str]:
    """Discover correct tshark field names for GSM A protocol.

    Different Wireshark versions use different field names:
    - Wireshark 3.x: gsm_a.rr.timing_advance, gsm_a.tmsi, gsm_a.imsi, gsm_a.lac, gsm_a.cellid
    - Wireshark 4.x: gsm_a_rr.timing_adv, gsm_a_dtap.tmsi, e212.imsi, e212.lac, e212.ci

    Returns:
        Dict mapping logical names to actual tshark field names:
        {'ta': ..., 'tmsi': ..., 'imsi': ..., 'lac': ..., 'cid': ...}
    """
    global _tshark_fields_cache
    if _tshark_fields_cache is not None:
        return _tshark_fields_cache

    # Candidate field names for each logical field, in preference order
    candidates = {
        'ta': [
            'gsm_a.rr.timing_advance',
            'gsm_a_rr.timing_adv',
            'gsm_a_rr.timing_advance',
        ],
        'tmsi': [
            'gsm_a.tmsi',
            'gsm_a_dtap.tmsi',
            'gsm_a.dtap.tmsi',
        ],
        'imsi': [
            'gsm_a.imsi',
            'e212.imsi',
            'gsm_a_dtap.imsi',
        ],
        'lac': [
            'gsm_a.lac',
            'e212.lac',
            'gsm_a_bssmap.lac',
        ],
        'cid': [
            'gsm_a.cellid',
            'e212.ci',
            'gsm_a_bssmap.cell_ci',
            'gsm_a.cell_ci',
        ],
    }

    # Query tshark for all available GSM fields
    available_fields = set()
    try:
        result = subprocess.run(
            ['tshark', '-G', 'fields'],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            parts = line.split('\t')
            if len(parts) >= 3:
                available_fields.add(parts[2])  # Field name is 3rd column
    except Exception as e:
        logger.warning(f"Could not query tshark fields: {e}")

    # Match each logical field to the first available candidate
    resolved = {}
    for logical_name, field_candidates in candidates.items():
        resolved[logical_name] = field_candidates[0]  # default fallback
        if available_fields:
            for candidate in field_candidates:
                if candidate in available_fields:
                    resolved[logical_name] = candidate
                    break

    logger.info(f"Discovered tshark fields: {resolved}")
    _tshark_fields_cache = resolved
    return resolved


def _start_monitoring_processes(arfcn: int, device_index: int) -> tuple[subprocess.Popen, subprocess.Popen]:
    """Start grgsm_livemon and tshark processes for monitoring an ARFCN.

    Returns:
        Tuple of (grgsm_process, tshark_process)

    Raises:
        FileNotFoundError: If grgsm_livemon or tshark not found
        RuntimeError: If grgsm_livemon exits immediately
    """
    frequency_hz = arfcn_to_frequency(arfcn)
    frequency_mhz = frequency_hz / 1e6

    # Check prerequisites
    if not shutil.which('grgsm_livemon'):
        raise FileNotFoundError('grgsm_livemon not found. Please install gr-gsm.')

    # Start grgsm_livemon
    grgsm_cmd = [
        'grgsm_livemon',
        '--args', f'rtl={device_index}',
        '-f', f'{frequency_mhz}M'
    ]
    env = dict(os.environ,
               OSMO_FSM_DUP_CHECK_DISABLED='1',
               PYTHONUNBUFFERED='1',
               QT_QPA_PLATFORM='offscreen')
    logger.info(f"Starting grgsm_livemon: {' '.join(grgsm_cmd)}")
    grgsm_proc = subprocess.Popen(
        grgsm_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        env=env
    )
    register_process(grgsm_proc)
    logger.info(f"Started grgsm_livemon (PID: {grgsm_proc.pid})")

    # Wait and check it didn't die immediately
    time.sleep(2)

    if grgsm_proc.poll() is not None:
        # Process already exited - capture stderr for diagnostics
        stderr_output = ''
        try:
            stderr_output = grgsm_proc.stderr.read()
        except Exception:
            pass
        exit_code = grgsm_proc.returncode
        logger.error(
            f"grgsm_livemon exited immediately (code: {exit_code}). "
            f"stderr: {stderr_output[:500]}"
        )
        unregister_process(grgsm_proc)
        raise RuntimeError(
            f'grgsm_livemon failed (exit code {exit_code}): {stderr_output[:200]}'
        )

    # Start stderr reader thread for grgsm_livemon diagnostics
    def read_livemon_stderr():
        try:
            for line in iter(grgsm_proc.stderr.readline, ''):
                if line:
                    logger.debug(f"grgsm_livemon stderr: {line.strip()}")
        except Exception:
            pass
    threading.Thread(target=read_livemon_stderr, daemon=True).start()

    # Start tshark
    if not shutil.which('tshark'):
        safe_terminate(grgsm_proc)
        unregister_process(grgsm_proc)
        raise FileNotFoundError('tshark not found. Please install wireshark/tshark.')

    fields = _discover_tshark_fields()
    display_filter = f"{fields['ta']} || {fields['tmsi']} || {fields['imsi']}"
    tshark_cmd = [
        'tshark',
        '-i', 'lo',
        '-l',  # Line-buffered output for live capture
        '-f', 'udp port 4729',  # Capture filter: only GSMTAP packets
        '-Y', display_filter,
        '-T', 'fields',
        '-e', fields['ta'],
        '-e', fields['tmsi'],
        '-e', fields['imsi'],
        '-e', fields['lac'],
        '-e', fields['cid']
    ]
    logger.info(f"Starting tshark: {' '.join(tshark_cmd)}")
    tshark_proc = subprocess.Popen(
        tshark_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1
    )
    register_process(tshark_proc)
    logger.info(f"Started tshark (PID: {tshark_proc.pid})")

    # Check tshark didn't exit immediately
    time.sleep(1)
    if tshark_proc.poll() is not None:
        stderr_output = ''
        try:
            stderr_output = tshark_proc.stderr.read()
        except Exception:
            pass
        exit_code = tshark_proc.returncode
        logger.error(f"tshark exited immediately (code: {exit_code}). stderr: {stderr_output[:500]}")
        # Clean up grgsm_livemon since monitoring can't work without tshark
        safe_terminate(grgsm_proc)
        unregister_process(grgsm_proc)
        unregister_process(tshark_proc)
        raise RuntimeError(f'tshark failed (exit code {exit_code}): {stderr_output[:200]}')

    return grgsm_proc, tshark_proc


def _start_and_register_monitor(arfcn: int, device_index: int) -> None:
    """Start monitoring processes and register them in global state.

    This is shared logic between start_monitor() and auto_start_monitor().
    Must be called within gsm_spy_lock context.

    Args:
        arfcn: ARFCN to monitor
        device_index: SDR device index
    """
    # Start monitoring processes
    grgsm_proc, tshark_proc = _start_monitoring_processes(arfcn, device_index)
    app_module.gsm_spy_livemon_process = grgsm_proc
    app_module.gsm_spy_monitor_process = tshark_proc
    app_module.gsm_spy_selected_arfcn = arfcn

    # Start monitoring thread
    monitor_thread_obj = threading.Thread(
        target=monitor_thread,
        args=(tshark_proc,),
        daemon=True
    )
    monitor_thread_obj.start()


@gsm_spy_bp.route('/dashboard')
def dashboard():
    """Render GSM Spy dashboard."""
    return render_template(
        'gsm_spy_dashboard.html',
        shared_observer_location=SHARED_OBSERVER_LOCATION_ENABLED
    )


@gsm_spy_bp.route('/start', methods=['POST'])
def start_scanner():
    """Start GSM scanner (G-01 BTS Scanner)."""
    global gsm_towers_found, gsm_connected

    with app_module.gsm_spy_lock:
        if app_module.gsm_spy_scanner_running:
            return jsonify({'error': 'Scanner already running'}), 400

        data = request.get_json() or {}
        device_index = data.get('device', 0)
        region = data.get('region', 'Americas')
        selected_bands = data.get('bands', [])  # Get user-selected bands

        # Validate device index
        try:
            device_index = validate_device_index(device_index)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

        # Claim SDR device to prevent conflicts
        from app import claim_sdr_device
        claim_error = claim_sdr_device(device_index, 'GSM Spy')
        if claim_error:
            return jsonify({
                'error': claim_error,
                'error_type': 'DEVICE_BUSY'
            }), 409

        # If no bands selected, use all bands for the region (backwards compatibility)
        if selected_bands:
            validated_bands, error = validate_band_names(selected_bands, region)
            if error:
                from app import release_sdr_device
                release_sdr_device(device_index)
                return jsonify({'error': error}), 400
            selected_bands = validated_bands
        else:
            region_bands = REGIONAL_BANDS.get(region, REGIONAL_BANDS['Americas'])
            selected_bands = list(region_bands.keys())
            logger.warning(f"No bands specified, using all bands for {region}: {selected_bands}")

        # Build grgsm_scanner command
        # Example: grgsm_scanner --args="rtl=0" -b GSM900
        if not shutil.which('grgsm_scanner'):
            from app import release_sdr_device
            release_sdr_device(device_index)
            return jsonify({'error': 'grgsm_scanner not found. Please install gr-gsm.'}), 500

        try:
            cmd = ['grgsm_scanner']

            # Add device argument (--args for RTL-SDR device selection)
            cmd.extend(['--args', f'rtl={device_index}'])

            # Add selected band arguments
            # Map EGSM900 to GSM900 since that's what grgsm_scanner expects
            for band_name in selected_bands:
                # Normalize band name (EGSM900 -> GSM900, remove EGSM prefix)
                normalized_band = band_name.replace('EGSM', 'GSM')
                cmd.extend(['-b', normalized_band])

            logger.info(f"Starting GSM scanner: {' '.join(cmd)}")

            # Set a flag to indicate scanner should run
            app_module.gsm_spy_active_device = device_index
            app_module.gsm_spy_region = region
            app_module.gsm_spy_scanner_running = True  # Use as flag initially

            # Reset counters for new session
            gsm_towers_found = 0
            gsm_devices_tracked = 0

            # Start geocoding worker (if not already running)
            start_geocoding_worker()

            # Start scanning thread (will run grgsm_scanner in a loop)
            scanner_thread_obj = threading.Thread(
                target=scanner_thread,
                args=(cmd, device_index),
                daemon=True
            )
            scanner_thread_obj.start()

            gsm_connected = True

            return jsonify({
                'status': 'started',
                'device': device_index,
                'region': region
            })

        except FileNotFoundError:
            from app import release_sdr_device
            release_sdr_device(device_index)
            return jsonify({'error': 'grgsm_scanner not found. Please install gr-gsm.'}), 500
        except Exception as e:
            from app import release_sdr_device
            release_sdr_device(device_index)
            logger.error(f"Error starting GSM scanner: {e}")
            return jsonify({'error': str(e)}), 500


@gsm_spy_bp.route('/monitor', methods=['POST'])
def start_monitor():
    """Start monitoring specific tower (G-02 Decoding)."""
    with app_module.gsm_spy_lock:
        if app_module.gsm_spy_monitor_process:
            return jsonify({'error': 'Monitor already running'}), 400

        data = request.get_json() or {}
        arfcn = data.get('arfcn')
        device_index = data.get('device', app_module.gsm_spy_active_device or 0)

        if not arfcn:
            return jsonify({'error': 'ARFCN required'}), 400

        # Validate ARFCN is valid integer and in known GSM band ranges
        try:
            arfcn = int(arfcn)
            # This will raise ValueError if ARFCN is not in any known band
            arfcn_to_frequency(arfcn)
        except (ValueError, TypeError) as e:
            return jsonify({'error': f'Invalid ARFCN: {e}'}), 400

        # Validate device index
        try:
            device_index = validate_device_index(device_index)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

        try:
            # Start and register monitoring (shared logic)
            _start_and_register_monitor(arfcn, device_index)

            return jsonify({
                'status': 'monitoring',
                'arfcn': arfcn,
                'device': device_index
            })

        except FileNotFoundError as e:
            return jsonify({'error': f'Tool not found: {e}'}), 500
        except Exception as e:
            logger.error(f"Error starting monitor: {e}")
            return jsonify({'error': str(e)}), 500


@gsm_spy_bp.route('/stop', methods=['POST'])
def stop_scanner():
    """Stop GSM scanner and monitor."""
    global gsm_connected, gsm_towers_found, gsm_devices_tracked

    with app_module.gsm_spy_lock:
        killed = []

        # Stop scanner (now just a flag, thread will see it and exit)
        if app_module.gsm_spy_scanner_running:
            app_module.gsm_spy_scanner_running = False
            killed.append('scanner')

        # Terminate livemon process
        if app_module.gsm_spy_livemon_process:
            unregister_process(app_module.gsm_spy_livemon_process)
            if safe_terminate(app_module.gsm_spy_livemon_process, timeout=5):
                killed.append('livemon')
            app_module.gsm_spy_livemon_process = None

        # Terminate monitor process
        if app_module.gsm_spy_monitor_process:
            unregister_process(app_module.gsm_spy_monitor_process)
            if safe_terminate(app_module.gsm_spy_monitor_process, timeout=5):
                killed.append('monitor')
            app_module.gsm_spy_monitor_process = None

        # Release SDR device from registry
        if app_module.gsm_spy_active_device is not None:
            from app import release_sdr_device
            release_sdr_device(app_module.gsm_spy_active_device)
        app_module.gsm_spy_active_device = None
        app_module.gsm_spy_selected_arfcn = None
        gsm_connected = False
        gsm_towers_found = 0
        gsm_devices_tracked = 0

        return jsonify({'status': 'stopped', 'killed': killed})


@gsm_spy_bp.route('/stream')
def stream():
    """SSE stream for real-time GSM updates."""
    def generate():
        """Generate SSE events."""
        logger.info("SSE stream connected - client subscribed")

        # Send current state on connect (handles reconnects and late-joining clients)
        existing_towers = dict(app_module.gsm_spy_towers.items())
        logger.info(f"SSE sending {len(existing_towers)} existing towers on connect")
        for key, tower_data in existing_towers.items():
            yield format_sse(tower_data)

        last_keepalive = time.time()
        idle_count = 0  # Track consecutive idle checks to handle transitions

        while True:
            try:
                # Check if scanner/monitor are still running
                # Use idle counter to avoid disconnecting during scanner→monitor transition
                if not app_module.gsm_spy_scanner_running and not app_module.gsm_spy_monitor_process:
                    idle_count += 1
                    if idle_count >= 5:  # 5 seconds grace period for mode transitions
                        logger.info("SSE stream: no active scanner or monitor, disconnecting")
                        yield format_sse({'type': 'disconnected'})
                        break
                else:
                    idle_count = 0

                # Try to get data from queue
                try:
                    data = app_module.gsm_spy_queue.get(timeout=1)
                    logger.info(f"SSE sending: type={data.get('type', '?')} keys={list(data.keys())}")
                    yield format_sse(data)
                    last_keepalive = time.time()
                except queue.Empty:
                    # Send keepalive if needed
                    if time.time() - last_keepalive > 30:
                        yield format_sse({'type': 'keepalive'})
                        last_keepalive = time.time()

            except GeneratorExit:
                logger.info("SSE stream: client disconnected (GeneratorExit)")
                break
            except Exception as e:
                logger.error(f"Error in GSM stream: {e}")
                yield format_sse({'type': 'error', 'message': str(e)})
                break

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response


@gsm_spy_bp.route('/status')
def status():
    """Get current GSM Spy status."""
    api_usage = get_api_usage_today()
    return jsonify({
        'running': bool(app_module.gsm_spy_scanner_running),
        'monitoring': app_module.gsm_spy_monitor_process is not None,
        'towers_found': gsm_towers_found,
        'devices_tracked': gsm_devices_tracked,
        'device': app_module.gsm_spy_active_device,
        'region': app_module.gsm_spy_region,
        'selected_arfcn': app_module.gsm_spy_selected_arfcn,
        'api_usage_today': api_usage,
        'api_limit': config.GSM_API_DAILY_LIMIT,
        'api_remaining': config.GSM_API_DAILY_LIMIT - api_usage
    })


@gsm_spy_bp.route('/lookup_cell', methods=['POST'])
def lookup_cell():
    """Lookup cell tower via OpenCellID (G-05)."""
    data = request.get_json() or {}
    mcc = data.get('mcc')
    mnc = data.get('mnc')
    lac = data.get('lac')
    cid = data.get('cid')

    if not all([mcc, mnc, lac, cid]):
        return jsonify({'error': 'MCC, MNC, LAC, and CID required'}), 400

    try:
        # Check local cache first
        with get_db() as conn:
            result = conn.execute('''
                SELECT lat, lon, azimuth, range_meters, operator, radio
                FROM gsm_cells
                WHERE mcc = ? AND mnc = ? AND lac = ? AND cid = ?
            ''', (mcc, mnc, lac, cid)).fetchone()

            if result:
                return jsonify({
                    'source': 'cache',
                    'lat': result['lat'],
                    'lon': result['lon'],
                    'azimuth': result['azimuth'],
                    'range': result['range_meters'],
                    'operator': result['operator'],
                    'radio': result['radio']
                })

            # Check API usage limit
            if not can_use_api():
                current_usage = get_api_usage_today()
                return jsonify({
                    'error': 'OpenCellID API daily limit reached',
                    'usage_today': current_usage,
                    'limit': config.GSM_API_DAILY_LIMIT
                }), 429

            # Call OpenCellID API
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

                # Increment API usage counter
                usage_count = increment_api_usage()
                logger.info(f"OpenCellID API call #{usage_count} today")

                # Cache the result
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

                return jsonify({
                    'source': 'api',
                    'lat': cell_data.get('lat'),
                    'lon': cell_data.get('lon'),
                    'azimuth': cell_data.get('azimuth'),
                    'range': cell_data.get('range'),
                    'operator': cell_data.get('operator'),
                    'radio': cell_data.get('radio')
                })
            else:
                return jsonify({'error': 'Cell not found in OpenCellID'}), 404

    except Exception as e:
        logger.error(f"Error looking up cell: {e}")
        return jsonify({'error': str(e)}), 500


@gsm_spy_bp.route('/detect_rogue', methods=['POST'])
def detect_rogue():
    """Analyze and flag rogue towers (G-07)."""
    data = request.get_json() or {}
    tower_info = data.get('tower')

    if not tower_info:
        return jsonify({'error': 'Tower info required'}), 400

    try:
        is_rogue = False
        reasons = []

        # Check if tower exists in OpenCellID
        mcc = tower_info.get('mcc')
        mnc = tower_info.get('mnc')
        lac = tower_info.get('lac')
        cid = tower_info.get('cid')

        if all([mcc, mnc, lac, cid]):
            with get_db() as conn:
                result = conn.execute('''
                    SELECT id FROM gsm_cells
                    WHERE mcc = ? AND mnc = ? AND lac = ? AND cid = ?
                ''', (mcc, mnc, lac, cid)).fetchone()

                if not result:
                    is_rogue = True
                    reasons.append('Tower not found in OpenCellID database')

        # Check signal strength anomalies
        signal = tower_info.get('signal_strength', 0)
        if signal > -50:  # Suspiciously strong signal
            is_rogue = True
            reasons.append(f'Unusually strong signal: {signal} dBm')

        # If rogue, insert into database
        if is_rogue:
            with get_db() as conn:
                conn.execute('''
                    INSERT INTO gsm_rogues
                    (arfcn, mcc, mnc, lac, cid, signal_strength, reason, threat_level)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    tower_info.get('arfcn'),
                    mcc, mnc, lac, cid,
                    signal,
                    '; '.join(reasons),
                    'high' if len(reasons) > 1 else 'medium'
                ))
                conn.commit()

        return jsonify({
            'is_rogue': is_rogue,
            'reasons': reasons
        })

    except Exception as e:
        logger.error(f"Error detecting rogue: {e}")
        return jsonify({'error': str(e)}), 500


@gsm_spy_bp.route('/towers')
def get_towers():
    """Get all detected towers."""
    towers = []
    for key, tower_data in app_module.gsm_spy_towers.items():
        towers.append(tower_data)
    return jsonify(towers)


@gsm_spy_bp.route('/devices')
def get_devices():
    """Get all tracked devices (IMSI/TMSI)."""
    devices = []
    for key, device_data in app_module.gsm_spy_devices.items():
        devices.append(device_data)
    return jsonify(devices)


@gsm_spy_bp.route('/rogues')
def get_rogues():
    """Get all detected rogue towers."""
    try:
        with get_db() as conn:
            results = conn.execute('''
                SELECT * FROM gsm_rogues
                WHERE acknowledged = 0
                ORDER BY detected_at DESC
                LIMIT 50
            ''').fetchall()

            rogues = [dict(row) for row in results]
            return jsonify(rogues)
    except Exception as e:
        logger.error(f"Error fetching rogues: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================
# Advanced Features (G-08 through G-12)
# ============================================

@gsm_spy_bp.route('/velocity', methods=['GET'])
def get_velocity_data():
    """Get velocity vectoring data for tracked devices (G-08)."""
    try:
        device_id = request.args.get('device_id')
        minutes = int(request.args.get('minutes', 60))  # Last 60 minutes by default

        with get_db() as conn:
            # Get velocity log entries
            query = '''
                SELECT * FROM gsm_velocity_log
                WHERE timestamp >= datetime('now', '-' || ? || ' minutes')
            '''
            params = [minutes]

            if device_id:
                query += ' AND device_id = ?'
                params.append(device_id)

            query += ' ORDER BY timestamp DESC LIMIT 100'

            results = conn.execute(query, params).fetchall()
            velocity_data = [dict(row) for row in results]

            return jsonify(velocity_data)
    except Exception as e:
        logger.error(f"Error fetching velocity data: {e}")
        return jsonify({'error': str(e)}), 500


@gsm_spy_bp.route('/velocity/calculate', methods=['POST'])
def calculate_velocity():
    """Calculate velocity for a device based on TA transitions (G-08)."""
    data = request.get_json() or {}
    device_id = data.get('device_id')

    if not device_id:
        return jsonify({'error': 'device_id required'}), 400

    try:
        with get_db() as conn:
            # Get last two TA readings for this device
            results = conn.execute('''
                SELECT ta_value, cid, timestamp
                FROM gsm_signals
                WHERE (imsi = ? OR tmsi = ?)
                ORDER BY timestamp DESC
                LIMIT 2
            ''', (device_id, device_id)).fetchall()

            if len(results) < 2:
                return jsonify({'velocity': 0, 'message': 'Insufficient data'})

            curr = dict(results[0])
            prev = dict(results[1])

            # Calculate distance change (TA * 554 meters)
            curr_distance = curr['ta_value'] * config.GSM_TA_METERS_PER_UNIT
            prev_distance = prev['ta_value'] * config.GSM_TA_METERS_PER_UNIT
            distance_change = abs(curr_distance - prev_distance)

            # Calculate time difference
            curr_time = datetime.fromisoformat(curr['timestamp'])
            prev_time = datetime.fromisoformat(prev['timestamp'])
            time_diff_seconds = (curr_time - prev_time).total_seconds()

            # Calculate velocity (m/s)
            if time_diff_seconds > 0:
                velocity = distance_change / time_diff_seconds
            else:
                velocity = 0

            # Store in velocity log
            conn.execute('''
                INSERT INTO gsm_velocity_log
                (device_id, prev_ta, curr_ta, prev_cid, curr_cid, estimated_velocity)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (device_id, prev['ta_value'], curr['ta_value'],
                  prev['cid'], curr['cid'], velocity))
            conn.commit()

            return jsonify({
                'device_id': device_id,
                'velocity_mps': round(velocity, 2),
                'velocity_kmh': round(velocity * 3.6, 2),
                'distance_change_m': round(distance_change, 2),
                'time_diff_s': round(time_diff_seconds, 2)
            })

    except Exception as e:
        logger.error(f"Error calculating velocity: {e}")
        return jsonify({'error': str(e)}), 500


@gsm_spy_bp.route('/crowd_density', methods=['GET'])
def get_crowd_density():
    """Get crowd density data by sector (G-09)."""
    try:
        hours = int(request.args.get('hours', 1))  # Last 1 hour by default
        cid = request.args.get('cid')  # Optional: specific cell

        with get_db() as conn:
            # Count unique TMSI per cell in time window
            query = '''
                SELECT
                    cid,
                    lac,
                    COUNT(DISTINCT tmsi) as unique_devices,
                    COUNT(*) as total_pings,
                    MIN(timestamp) as first_seen,
                    MAX(timestamp) as last_seen
                FROM gsm_tmsi_log
                WHERE timestamp >= datetime('now', '-' || ? || ' hours')
            '''
            params = [hours]

            if cid:
                query += ' AND cid = ?'
                params.append(cid)

            query += ' GROUP BY cid, lac ORDER BY unique_devices DESC'

            results = conn.execute(query, params).fetchall()
            density_data = []

            for row in results:
                density_data.append({
                    'cid': row['cid'],
                    'lac': row['lac'],
                    'unique_devices': row['unique_devices'],
                    'total_pings': row['total_pings'],
                    'first_seen': row['first_seen'],
                    'last_seen': row['last_seen'],
                    'density_level': 'high' if row['unique_devices'] > 20 else
                                   'medium' if row['unique_devices'] > 10 else 'low'
                })

            return jsonify(density_data)

    except Exception as e:
        logger.error(f"Error fetching crowd density: {e}")
        return jsonify({'error': str(e)}), 500


@gsm_spy_bp.route('/life_patterns', methods=['GET'])
def get_life_patterns():
    """Get life pattern analysis for a device (G-10)."""
    try:
        device_id = request.args.get('device_id')
        if not device_id:
            # Return empty results gracefully when no device selected
            return jsonify({
                'device_id': None,
                'patterns': [],
                'message': 'No device selected'
            }), 200

        with get_db() as conn:
            # Get historical signal data
            results = conn.execute('''
                SELECT
                    strftime('%H', timestamp) as hour,
                    strftime('%w', timestamp) as day_of_week,
                    cid,
                    lac,
                    COUNT(*) as occurrences
                FROM gsm_signals
                WHERE (imsi = ? OR tmsi = ?)
                AND timestamp >= datetime('now', '-60 days')
                GROUP BY hour, day_of_week, cid, lac
                ORDER BY occurrences DESC
            ''', (device_id, device_id)).fetchall()

            patterns = []
            for row in results:
                patterns.append({
                    'hour': int(row['hour']),
                    'day_of_week': int(row['day_of_week']),
                    'cid': row['cid'],
                    'lac': row['lac'],
                    'occurrences': row['occurrences'],
                    'day_name': ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][int(row['day_of_week'])]
                })

            # Identify regular patterns
            regular_locations = []
            for pattern in patterns[:5]:  # Top 5 most frequent
                if pattern['occurrences'] >= 3:  # Seen at least 3 times
                    regular_locations.append({
                        'cid': pattern['cid'],
                        'typical_time': f"{pattern['day_name']} {pattern['hour']:02d}:00",
                        'frequency': pattern['occurrences']
                    })

            return jsonify({
                'device_id': device_id,
                'patterns': patterns,
                'regular_locations': regular_locations,
                'total_observations': sum(p['occurrences'] for p in patterns)
            })

    except Exception as e:
        logger.error(f"Error analyzing life patterns: {e}")
        return jsonify({'error': str(e)}), 500


@gsm_spy_bp.route('/neighbor_audit', methods=['GET'])
def neighbor_audit():
    """Audit neighbor cell lists for consistency (G-11)."""
    try:
        cid = request.args.get('cid')
        if not cid:
            # Return empty results gracefully when no tower selected
            return jsonify({
                'cid': None,
                'neighbors': [],
                'inconsistencies': [],
                'message': 'No tower selected'
            }), 200

        with get_db() as conn:
            # Get tower info with metadata (neighbor list stored in metadata JSON)
            result = conn.execute('''
                SELECT metadata FROM gsm_cells WHERE cid = ?
            ''', (cid,)).fetchone()

            if not result or not result['metadata']:
                return jsonify({
                    'cid': cid,
                    'status': 'no_data',
                    'message': 'No neighbor list data available'
                })

            # Parse metadata JSON
            metadata = json.loads(result['metadata'])
            neighbor_list = metadata.get('neighbors', [])

            # Audit consistency
            issues = []
            for neighbor_cid in neighbor_list:
                # Check if neighbor exists in database
                neighbor_exists = conn.execute('''
                    SELECT id FROM gsm_cells WHERE cid = ?
                ''', (neighbor_cid,)).fetchone()

                if not neighbor_exists:
                    issues.append({
                        'type': 'missing_neighbor',
                        'cid': neighbor_cid,
                        'message': f'Neighbor CID {neighbor_cid} not found in database'
                    })

            return jsonify({
                'cid': cid,
                'neighbor_count': len(neighbor_list),
                'neighbors': neighbor_list,
                'issues': issues,
                'status': 'suspicious' if issues else 'normal'
            })

    except Exception as e:
        logger.error(f"Error auditing neighbors: {e}")
        return jsonify({'error': str(e)}), 500


@gsm_spy_bp.route('/traffic_correlation', methods=['GET'])
def traffic_correlation():
    """Correlate uplink/downlink traffic for pairing analysis (G-12)."""
    try:
        cid = request.args.get('cid')
        minutes = int(request.args.get('minutes', 5))

        with get_db() as conn:
            # Get recent signal activity for this cell
            results = conn.execute('''
                SELECT
                    imsi,
                    tmsi,
                    ta_value,
                    timestamp,
                    metadata
                FROM gsm_signals
                WHERE cid = ?
                AND timestamp >= datetime('now', '-' || ? || ' minutes')
                ORDER BY timestamp DESC
            ''', (cid, minutes)).fetchall()

            correlations = []
            seen_devices = set()

            for row in results:
                device_id = row['imsi'] or row['tmsi']
                if device_id and device_id not in seen_devices:
                    seen_devices.add(device_id)

                    # Simple correlation: count bursts
                    burst_count = conn.execute('''
                        SELECT COUNT(*) as bursts
                        FROM gsm_signals
                        WHERE (imsi = ? OR tmsi = ?)
                        AND cid = ?
                        AND timestamp >= datetime('now', '-' || ? || ' minutes')
                    ''', (device_id, device_id, cid, minutes)).fetchone()

                    correlations.append({
                        'device_id': device_id,
                        'burst_count': burst_count['bursts'],
                        'last_seen': row['timestamp'],
                        'ta_value': row['ta_value'],
                        'activity_level': 'high' if burst_count['bursts'] > 10 else
                                        'medium' if burst_count['bursts'] > 5 else 'low'
                    })

            return jsonify({
                'cid': cid,
                'time_window_minutes': minutes,
                'active_devices': len(correlations),
                'correlations': correlations
            })

    except Exception as e:
        logger.error(f"Error correlating traffic: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================
# Helper Functions
# ============================================

def parse_grgsm_scanner_output(line: str) -> dict[str, Any] | None:
    """Parse grgsm_scanner output line.

    Actual output format (comma-separated key-value pairs):
      ARFCN: 975, Freq: 925.2M, CID: 13522, LAC: 38722, MCC: 262, MNC: 1, Pwr: -58
    """
    try:
        line = line.strip()

        # Skip non-data lines (progress, config, neighbour info, blank)
        if not line or 'ARFCN:' not in line:
            return None

        # Parse "ARFCN: 975, Freq: 925.2M, CID: 13522, LAC: 38722, MCC: 262, MNC: 1, Pwr: -58"
        fields = {}
        for part in line.split(','):
            part = part.strip()
            if ':' in part:
                key, _, value = part.partition(':')
                fields[key.strip()] = value.strip()

        if 'ARFCN' in fields and 'CID' in fields:
            cid = int(fields.get('CID', 0))
            mcc = int(fields.get('MCC', 0))
            mnc = int(fields.get('MNC', 0))

            # Only skip entries with no network identity at all (MCC=0 AND MNC=0)
            # CID=0 with valid MCC/MNC is a partially decoded cell - still useful
            if mcc == 0 and mnc == 0:
                logger.debug(f"Skipping unidentified ARFCN (MCC=0, MNC=0): {line}")
                return None

            # Freq may have 'M' suffix (e.g. "925.2M")
            freq_str = fields.get('Freq', '0').rstrip('Mm')

            data = {
                'type': 'tower',
                'arfcn': int(fields['ARFCN']),
                'frequency': float(freq_str),
                'cid': cid,
                'lac': int(fields.get('LAC', 0)),
                'mcc': mcc,
                'mnc': mnc,
                'signal_strength': float(fields.get('Pwr', -999)),
                'timestamp': datetime.now().isoformat()
            }
            return data

    except Exception as e:
        logger.debug(f"Failed to parse scanner line: {line} - {e}")

    return None


def parse_tshark_output(line: str) -> dict[str, Any] | None:
    """Parse tshark filtered GSM output."""
    try:
        # tshark output format: ta_value\ttmsi\timsi\tlac\tcid
        parts = line.strip().split('\t')

        if len(parts) >= 5:
            data = {
                'type': 'device',
                'ta_value': int(parts[0]) if parts[0] else None,
                'tmsi': parts[1] if parts[1] else None,
                'imsi': parts[2] if parts[2] else None,
                'lac': int(parts[3]) if parts[3] else None,
                'cid': int(parts[4]) if parts[4] else None,
                'timestamp': datetime.now().isoformat()
            }

            # Calculate distance from TA
            if data['ta_value'] is not None:
                data['distance_meters'] = data['ta_value'] * config.GSM_TA_METERS_PER_UNIT

            return data

    except Exception as e:
        logger.debug(f"Failed to parse tshark line: {line} - {e}")

    return None


def auto_start_monitor(tower_data):
    """Automatically start monitoring the strongest tower found."""
    try:
        arfcn = tower_data.get('arfcn')
        if not arfcn:
            logger.warning("Cannot auto-monitor: no ARFCN in tower data")
            return

        logger.info(f"Auto-monitoring strongest tower: ARFCN {arfcn}, Signal {tower_data.get('signal_strength')} dBm")

        # Brief delay to ensure scanner has stabilized
        time.sleep(2)

        with app_module.gsm_spy_lock:
            if app_module.gsm_spy_monitor_process:
                logger.info("Monitor already running, skipping auto-start")
                return

            device_index = app_module.gsm_spy_active_device or 0

            # Start and register monitoring (shared logic)
            _start_and_register_monitor(arfcn, device_index)

            # Send SSE notification
            try:
                app_module.gsm_spy_queue.put_nowait({
                    'type': 'auto_monitor_started',
                    'arfcn': arfcn,
                    'tower': tower_data
                })
            except queue.Full:
                pass

            logger.info(f"Auto-monitoring started for ARFCN {arfcn}")

    except Exception as e:
        logger.error(f"Error in auto-monitoring: {e}")


def scanner_thread(cmd, device_index):
    """Thread to continuously run grgsm_scanner in a loop with non-blocking I/O.

    grgsm_scanner scans once and exits, so we loop it to provide
    continuous updates to the dashboard.
    """
    global gsm_towers_found

    strongest_tower = None
    auto_monitor_triggered = False  # Moved outside loop - persists across scans
    scan_count = 0
    crash_count = 0
    process = None

    try:
        while app_module.gsm_spy_scanner_running:  # Flag check
            scan_count += 1
            logger.info(f"Starting GSM scan #{scan_count}")

            try:
                # Start scanner process
                # Set OSMO_FSM_DUP_CHECK_DISABLED to prevent libosmocore
                # abort on duplicate FSM registration (common with apt gr-gsm)
                env = dict(os.environ,
                           OSMO_FSM_DUP_CHECK_DISABLED='1',
                           PYTHONUNBUFFERED='1',
                           QT_QPA_PLATFORM='offscreen')
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    bufsize=1,
                    env=env
                )
                register_process(process)
                logger.info(f"Started grgsm_scanner (PID: {process.pid})")

                # Standard pattern: reader threads with queue
                output_queue_local = queue.Queue()

                def read_stdout():
                    try:
                        for line in iter(process.stdout.readline, ''):
                            if line:
                                output_queue_local.put(('stdout', line))
                    except Exception as e:
                        logger.error(f"stdout read error: {e}")
                    finally:
                        output_queue_local.put(('eof', None))

                def read_stderr():
                    try:
                        for line in iter(process.stderr.readline, ''):
                            if line:
                                logger.debug(f"grgsm_scanner stderr: {line.strip()}")
                                # grgsm_scanner outputs scan results to stderr
                                output_queue_local.put(('stderr', line))
                    except Exception as e:
                        logger.error(f"stderr read error: {e}")

                stdout_thread = threading.Thread(target=read_stdout, daemon=True)
                stderr_thread = threading.Thread(target=read_stderr, daemon=True)
                stdout_thread.start()
                stderr_thread.start()

                # Process output with timeout
                scan_start = time.time()
                last_output = scan_start
                scan_timeout = 300  # 5 minute maximum per scan (4 bands takes ~2-3 min)

                while app_module.gsm_spy_scanner_running:
                    # Check if process died
                    if process.poll() is not None:
                        logger.info(f"Scanner exited (code: {process.returncode})")
                        break

                    # Get output from queue with timeout
                    try:
                        msg_type, line = output_queue_local.get(timeout=1.0)

                        if msg_type == 'eof':
                            break  # EOF

                        last_output = time.time()
                        stripped = line.strip()
                        logger.info(f"Scanner [{msg_type}]: {stripped}")

                        # Forward progress and status info to frontend
                        progress_match = re.match(r'Scanning:\s+([\d.]+)%\s+done', stripped)
                        if progress_match:
                            try:
                                app_module.gsm_spy_queue.put_nowait({
                                    'type': 'progress',
                                    'percent': float(progress_match.group(1)),
                                    'scan': scan_count
                                })
                            except queue.Full:
                                pass
                            continue
                        if stripped.startswith('Try scan CCCH'):
                            try:
                                app_module.gsm_spy_queue.put_nowait({
                                    'type': 'status',
                                    'message': stripped,
                                    'scan': scan_count
                                })
                            except queue.Full:
                                pass

                        parsed = parse_grgsm_scanner_output(line)
                        if parsed:
                            # Enrich with coordinates
                            from utils.gsm_geocoding import enrich_tower_data
                            enriched = enrich_tower_data(parsed)

                            # Store in DataStore
                            key = f"{enriched['mcc']}_{enriched['mnc']}_{enriched['lac']}_{enriched['cid']}"
                            app_module.gsm_spy_towers[key] = enriched

                            # Track strongest tower
                            signal = enriched.get('signal_strength', -999)
                            if strongest_tower is None or signal > strongest_tower.get('signal_strength', -999):
                                strongest_tower = enriched

                            # Queue for SSE
                            try:
                                app_module.gsm_spy_queue.put_nowait(enriched)
                            except queue.Full:
                                logger.warning("Queue full, dropping tower update")

                            # Thread-safe counter update
                            with app_module.gsm_spy_lock:
                                gsm_towers_found += 1
                    except queue.Empty:
                        # No output, check timeout
                        if time.time() - last_output > scan_timeout:
                            logger.warning(f"Scan timeout after {scan_timeout}s")
                            break

                # Drain remaining queue items after process exits
                while not output_queue_local.empty():
                    try:
                        msg_type, line = output_queue_local.get_nowait()
                        if line:
                            logger.info(f"Scanner [{msg_type}] (drain): {line.strip()}")
                    except queue.Empty:
                        break

                # Clean up process with timeout
                if process.poll() is None:
                    logger.info("Terminating scanner process")
                    safe_terminate(process, timeout=5)
                else:
                    process.wait()  # Reap zombie

                exit_code = process.returncode
                scan_duration = time.time() - scan_start
                logger.info(f"Scan #{scan_count} complete (exit code: {exit_code}, duration: {scan_duration:.1f}s)")

                # Notify frontend scan completed
                try:
                    app_module.gsm_spy_queue.put_nowait({
                        'type': 'scan_complete',
                        'scan': scan_count,
                        'duration': round(scan_duration, 1),
                        'towers_found': gsm_towers_found
                    })
                except queue.Full:
                    pass

                # Detect crash pattern: process exits too quickly with no data
                if scan_duration < 5 and exit_code != 0:
                    crash_count += 1
                    logger.error(
                        f"grgsm_scanner crashed on startup (exit code: {exit_code}). "
                        f"Crash count: {crash_count}. Check gr-gsm/libosmocore compatibility."
                    )
                    try:
                        app_module.gsm_spy_queue.put_nowait({
                            'type': 'error',
                            'message': f'grgsm_scanner crashed (exit code: {exit_code}). '
                                       'This may be a gr-gsm/libosmocore compatibility issue. '
                                       'Try rebuilding gr-gsm from source.',
                            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S')
                        })
                    except queue.Full:
                        pass
                    if crash_count >= 3:
                        logger.error("grgsm_scanner crashed 3 times, stopping scanner")
                        break

            except FileNotFoundError:
                logger.error(
                    "grgsm_scanner not found. Please install gr-gsm: "
                    "https://github.com/bkerler/gr-gsm"
                )
                # Send error to SSE stream so the UI knows
                try:
                    app_module.gsm_spy_queue.put({
                        'type': 'error',
                        'message': 'grgsm_scanner not found. Please install gr-gsm.',
                        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S')
                    })
                except Exception:
                    pass
                break  # Don't retry - binary won't appear

            except Exception as e:
                logger.error(f"Scanner scan error: {e}", exc_info=True)
                if process and process.poll() is None:
                    safe_terminate(process)

            # Check if should continue
            if not app_module.gsm_spy_scanner_running:
                break

            # After first scan completes: auto-switch to monitoring if towers found
            # Scanner process has exited so SDR is free for grgsm_livemon
            if not auto_monitor_triggered and strongest_tower and scan_count >= 1:
                auto_monitor_triggered = True
                arfcn = strongest_tower.get('arfcn')
                signal = strongest_tower.get('signal_strength', -999)
                logger.info(
                    f"Scan complete with towers found. Auto-switching to monitor mode "
                    f"on ARFCN {arfcn} (signal: {signal} dBm)"
                )

                # Stop scanner loop - SDR needed for monitoring
                app_module.gsm_spy_scanner_running = False

                try:
                    app_module.gsm_spy_queue.put_nowait({
                        'type': 'status',
                        'message': f'Switching to monitor mode on ARFCN {arfcn}...'
                    })
                except queue.Full:
                    pass

                # Start monitoring (SDR is free since scanner process exited)
                try:
                    with app_module.gsm_spy_lock:
                        if not app_module.gsm_spy_monitor_process:
                            _start_and_register_monitor(arfcn, device_index)
                            logger.info(f"Auto-monitoring started for ARFCN {arfcn}")

                            try:
                                app_module.gsm_spy_queue.put_nowait({
                                    'type': 'auto_monitor_started',
                                    'arfcn': arfcn,
                                    'tower': strongest_tower
                                })
                            except queue.Full:
                                pass
                except Exception as e:
                    logger.error(f"Error starting auto-monitor: {e}", exc_info=True)
                    try:
                        app_module.gsm_spy_queue.put_nowait({
                            'type': 'error',
                            'message': f'Monitor failed: {e}'
                        })
                    except queue.Full:
                        pass
                    # Resume scanning if monitor failed
                    app_module.gsm_spy_scanner_running = True

                break  # Exit scanner loop (monitoring takes over)

            # Wait between scans with responsive flag checking
            logger.info("Waiting 5 seconds before next scan")
            for i in range(5):
                if not app_module.gsm_spy_scanner_running:
                    break
                time.sleep(1)

    except Exception as e:
        logger.error(f"Scanner thread fatal error: {e}", exc_info=True)

    finally:
        # Always cleanup
        if process and process.poll() is None:
            safe_terminate(process, timeout=5)

        logger.info("Scanner thread terminated")

        # Reset global state - but don't release SDR if monitoring took over
        with app_module.gsm_spy_lock:
            app_module.gsm_spy_scanner_running = False
            if app_module.gsm_spy_monitor_process is None:
                # No monitor running - release SDR device
                if app_module.gsm_spy_active_device is not None:
                    from app import release_sdr_device
                    release_sdr_device(app_module.gsm_spy_active_device)
                    app_module.gsm_spy_active_device = None
            else:
                logger.info("Monitor is running, keeping SDR device allocated")


def monitor_thread(process):
    """Thread to read tshark output using standard iter pattern."""
    global gsm_devices_tracked

    # Standard pattern: reader thread with queue
    output_queue_local = queue.Queue()

    def read_stdout():
        try:
            for line in iter(process.stdout.readline, ''):
                if line:
                    output_queue_local.put(('stdout', line))
        except Exception as e:
            logger.error(f"tshark read error: {e}")
        finally:
            output_queue_local.put(('eof', None))

    def read_stderr():
        try:
            for line in iter(process.stderr.readline, ''):
                if line:
                    logger.debug(f"tshark stderr: {line.strip()}")
        except Exception:
            pass

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stdout_thread.start()
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()

    monitor_start_time = time.time()
    packets_captured = 0
    last_heartbeat = time.time()

    try:
        while app_module.gsm_spy_monitor_process:
            # Check if process died
            if process.poll() is not None:
                logger.info(f"Monitor process exited (code: {process.returncode})")
                break

            # Send periodic heartbeat so frontend knows monitor is alive
            now = time.time()
            if now - last_heartbeat >= 5:
                last_heartbeat = now
                elapsed = int(now - monitor_start_time)
                try:
                    app_module.gsm_spy_queue.put_nowait({
                        'type': 'monitor_heartbeat',
                        'elapsed': elapsed,
                        'packets': packets_captured,
                        'devices': len(app_module.gsm_spy_devices)
                    })
                except queue.Full:
                    pass

            # Get output from queue with timeout
            try:
                msg_type, line = output_queue_local.get(timeout=1.0)
            except queue.Empty:
                continue  # Timeout, check flag again

            if msg_type == 'eof':
                break  # EOF

            parsed = parse_tshark_output(line)
            if parsed:
                packets_captured += 1

                # Store in DataStore
                key = parsed.get('tmsi') or parsed.get('imsi') or str(time.time())
                app_module.gsm_spy_devices[key] = parsed

                # Queue for SSE stream
                try:
                    app_module.gsm_spy_queue.put_nowait(parsed)
                except queue.Full:
                    pass

                # Store in database for historical analysis
                try:
                    with get_db() as conn:
                        # gsm_signals table
                        conn.execute('''
                            INSERT INTO gsm_signals
                            (imsi, tmsi, lac, cid, ta_value, arfcn)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (
                            parsed.get('imsi'),
                            parsed.get('tmsi'),
                            parsed.get('lac'),
                            parsed.get('cid'),
                            parsed.get('ta_value'),
                            app_module.gsm_spy_selected_arfcn
                        ))

                        # gsm_tmsi_log table for crowd density
                        if parsed.get('tmsi'):
                            conn.execute('''
                                INSERT INTO gsm_tmsi_log
                                (tmsi, lac, cid, ta_value)
                                VALUES (?, ?, ?, ?)
                            ''', (
                                parsed.get('tmsi'),
                                parsed.get('lac'),
                                parsed.get('cid'),
                                parsed.get('ta_value')
                            ))

                        # Velocity calculation (G-08)
                        device_id = parsed.get('imsi') or parsed.get('tmsi')
                        if device_id and parsed.get('ta_value') is not None:
                            # Get previous TA reading
                            prev_reading = conn.execute('''
                                SELECT ta_value, cid, timestamp
                                FROM gsm_signals
                                WHERE (imsi = ? OR tmsi = ?)
                                ORDER BY timestamp DESC
                                LIMIT 1 OFFSET 1
                            ''', (device_id, device_id)).fetchone()

                            if prev_reading:
                                # Calculate velocity
                                curr_ta = parsed.get('ta_value')
                                prev_ta = prev_reading['ta_value']
                                curr_distance = curr_ta * config.GSM_TA_METERS_PER_UNIT
                                prev_distance = prev_ta * config.GSM_TA_METERS_PER_UNIT
                                distance_change = abs(curr_distance - prev_distance)

                                # Time difference
                                prev_time = datetime.fromisoformat(prev_reading['timestamp'])
                                curr_time = datetime.now()
                                time_diff_seconds = (curr_time - prev_time).total_seconds()

                                if time_diff_seconds > 0:
                                    velocity = distance_change / time_diff_seconds

                                    # Store velocity
                                    conn.execute('''
                                        INSERT INTO gsm_velocity_log
                                        (device_id, prev_ta, curr_ta, prev_cid, curr_cid, estimated_velocity)
                                        VALUES (?, ?, ?, ?, ?, ?)
                                    ''', (
                                        device_id,
                                        prev_ta,
                                        curr_ta,
                                        prev_reading['cid'],
                                        parsed.get('cid'),
                                        velocity
                                    ))

                        conn.commit()
                except Exception as e:
                    logger.error(f"Error storing device data: {e}")

                # Thread-safe counter
                with app_module.gsm_spy_lock:
                    gsm_devices_tracked += 1

    except Exception as e:
        logger.error(f"Monitor thread error: {e}", exc_info=True)

    finally:
        # Reap process with timeout
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("Monitor process didn't terminate, killing")
                    process.kill()
                    process.wait()
            else:
                process.wait()
            logger.info(f"Monitor process exited with code {process.returncode}")
        except Exception as e:
            logger.error(f"Error reaping monitor process: {e}")

        logger.info("Monitor thread terminated")
