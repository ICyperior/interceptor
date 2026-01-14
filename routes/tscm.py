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

    # Get interface selections
    wifi_interface = data.get('wifi_interface', '')
    bt_interface = data.get('bt_interface', '')
    sdr_device = data.get('sdr_device')

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
        args=(sweep_type, baseline_id, wifi_enabled, bt_enabled, rf_enabled,
              wifi_interface, bt_interface, sdr_device),
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


@tscm_bp.route('/devices')
def get_tscm_devices():
    """Get available scanning devices for TSCM sweeps."""
    import platform
    import shutil
    import subprocess

    devices = {
        'wifi_interfaces': [],
        'bt_adapters': [],
        'sdr_devices': []
    }

    # Detect WiFi interfaces
    if platform.system() == 'Darwin':  # macOS
        try:
            result = subprocess.run(
                ['networksetup', '-listallhardwareports'],
                capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.split('\n')
            for i, line in enumerate(lines):
                if 'Wi-Fi' in line or 'AirPort' in line:
                    for j in range(i + 1, min(i + 3, len(lines))):
                        if 'Device:' in lines[j]:
                            device = lines[j].split('Device:')[1].strip()
                            devices['wifi_interfaces'].append({
                                'name': device,
                                'type': 'internal',
                                'monitor_capable': False
                            })
                            break
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass
    else:  # Linux
        try:
            result = subprocess.run(
                ['iw', 'dev'],
                capture_output=True, text=True, timeout=5
            )
            current_iface = None
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith('Interface'):
                    current_iface = line.split()[1]
                elif current_iface and 'type' in line:
                    devices['wifi_interfaces'].append({
                        'name': current_iface,
                        'type': line.split()[-1],
                        'monitor_capable': True
                    })
                    current_iface = None
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            # Fall back to iwconfig
            try:
                result = subprocess.run(
                    ['iwconfig'],
                    capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.split('\n'):
                    if 'IEEE 802.11' in line:
                        iface = line.split()[0]
                        devices['wifi_interfaces'].append({
                            'name': iface,
                            'type': 'managed',
                            'monitor_capable': True
                        })
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass

    # Detect Bluetooth adapters
    if platform.system() == 'Linux':
        try:
            result = subprocess.run(
                ['hciconfig'],
                capture_output=True, text=True, timeout=5
            )
            import re
            blocks = re.split(r'(?=^hci\d+:)', result.stdout, flags=re.MULTILINE)
            for block in blocks:
                if block.strip():
                    first_line = block.split('\n')[0]
                    match = re.match(r'(hci\d+):', first_line)
                    if match:
                        iface_name = match.group(1)
                        is_up = 'UP RUNNING' in block or '\tUP ' in block
                        devices['bt_adapters'].append({
                            'name': iface_name,
                            'type': 'hci',
                            'status': 'up' if is_up else 'down'
                        })
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            # Try bluetoothctl as fallback
            try:
                result = subprocess.run(
                    ['bluetoothctl', 'list'],
                    capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.split('\n'):
                    if 'Controller' in line:
                        # Format: Controller XX:XX:XX:XX:XX:XX Name
                        parts = line.split()
                        if len(parts) >= 2:
                            devices['bt_adapters'].append({
                                'name': parts[1],
                                'type': 'controller',
                                'status': 'available'
                            })
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass
    elif platform.system() == 'Darwin':
        # macOS has built-in Bluetooth
        devices['bt_adapters'].append({
            'name': 'default',
            'type': 'macos',
            'status': 'available'
        })

    # Detect SDR devices
    try:
        from utils.sdr import SDRFactory
        sdr_list = SDRFactory.detect_devices()
        for sdr in sdr_list:
            # SDRDevice is a dataclass with attributes, not a dict
            devices['sdr_devices'].append({
                'index': sdr.index,
                'name': sdr.name,
                'type': sdr.sdr_type.value if hasattr(sdr.sdr_type, 'value') else str(sdr.sdr_type),
                'serial': sdr.serial,
                'driver': sdr.driver
            })
    except ImportError:
        logger.debug("SDR module not available")
    except Exception as e:
        logger.warning(f"Error detecting SDR devices: {e}")

    return jsonify({'status': 'success', 'devices': devices})


def _scan_wifi_networks(interface: str) -> list[dict]:
    """Scan for WiFi networks using system tools."""
    import platform
    import re
    import subprocess

    networks = []

    if platform.system() == 'Darwin':
        # macOS: Use airport utility
        airport_path = '/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport'
        try:
            result = subprocess.run(
                [airport_path, '-s'],
                capture_output=True, text=True, timeout=15
            )
            # Parse airport output
            # Format: SSID BSSID RSSI CHANNEL HT CC SECURITY
            lines = result.stdout.strip().split('\n')
            for line in lines[1:]:  # Skip header
                if not line.strip():
                    continue
                # Parse the line - format is space-separated but SSID can have spaces
                parts = line.split()
                if len(parts) >= 7:
                    # BSSID is always XX:XX:XX:XX:XX:XX format
                    bssid_idx = None
                    for i, p in enumerate(parts):
                        if re.match(r'^[0-9a-fA-F:]{17}$', p):
                            bssid_idx = i
                            break
                    if bssid_idx is not None:
                        ssid = ' '.join(parts[:bssid_idx]) if bssid_idx > 0 else '[Hidden]'
                        bssid = parts[bssid_idx]
                        rssi = parts[bssid_idx + 1] if len(parts) > bssid_idx + 1 else '-100'
                        channel = parts[bssid_idx + 2] if len(parts) > bssid_idx + 2 else '0'
                        security = ' '.join(parts[bssid_idx + 5:]) if len(parts) > bssid_idx + 5 else ''
                        networks.append({
                            'bssid': bssid.upper(),
                            'essid': ssid or '[Hidden]',
                            'power': rssi,
                            'channel': channel,
                            'privacy': security
                        })
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            logger.warning(f"macOS WiFi scan failed: {e}")

    else:
        # Linux: Try iwlist scan
        iface = interface or 'wlan0'
        try:
            result = subprocess.run(
                ['iwlist', iface, 'scan'],
                capture_output=True, text=True, timeout=30
            )
            current_network = {}
            for line in result.stdout.split('\n'):
                line = line.strip()
                if 'Cell' in line and 'Address:' in line:
                    if current_network.get('bssid'):
                        networks.append(current_network)
                    bssid = line.split('Address:')[1].strip()
                    current_network = {'bssid': bssid.upper(), 'essid': '[Hidden]'}
                elif 'ESSID:' in line:
                    essid = line.split('ESSID:')[1].strip().strip('"')
                    current_network['essid'] = essid or '[Hidden]'
                elif 'Channel:' in line:
                    channel = line.split('Channel:')[1].strip()
                    current_network['channel'] = channel
                elif 'Signal level=' in line:
                    match = re.search(r'Signal level[=:]?\s*(-?\d+)', line)
                    if match:
                        current_network['power'] = match.group(1)
                elif 'Encryption key:' in line:
                    encrypted = 'on' in line.lower()
                    current_network['encrypted'] = encrypted
                elif 'WPA' in line or 'WPA2' in line:
                    current_network['privacy'] = 'WPA2' if 'WPA2' in line else 'WPA'
            if current_network.get('bssid'):
                networks.append(current_network)
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            logger.warning(f"Linux WiFi scan failed: {e}")

    return networks


def _scan_bluetooth_devices(interface: str, duration: int = 10) -> list[dict]:
    """Scan for Bluetooth devices using system tools."""
    import platform
    import os
    import pty
    import re
    import select
    import subprocess

    devices = []
    seen_macs = set()

    if platform.system() == 'Darwin':
        # macOS: Use system_profiler for basic Bluetooth info
        try:
            result = subprocess.run(
                ['system_profiler', 'SPBluetoothDataType', '-json'],
                capture_output=True, text=True, timeout=15
            )
            import json
            data = json.loads(result.stdout)
            bt_data = data.get('SPBluetoothDataType', [{}])[0]

            # Get connected/paired devices
            for section in ['device_connected', 'device_title']:
                section_data = bt_data.get(section, {})
                if isinstance(section_data, dict):
                    for name, info in section_data.items():
                        if isinstance(info, dict):
                            mac = info.get('device_address', '')
                            if mac and mac not in seen_macs:
                                seen_macs.add(mac)
                                devices.append({
                                    'mac': mac.upper(),
                                    'name': name,
                                    'type': info.get('device_minorType', 'unknown'),
                                    'connected': section == 'device_connected'
                                })
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError) as e:
            logger.warning(f"macOS Bluetooth scan failed: {e}")

    else:
        # Linux: Use bluetoothctl or hcitool
        iface = interface or 'hci0'

        # Try bluetoothctl first
        try:
            master_fd, slave_fd = pty.openpty()
            process = subprocess.Popen(
                ['bluetoothctl'],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True
            )
            os.close(slave_fd)

            # Start scanning
            time.sleep(0.3)
            os.write(master_fd, b'power on\n')
            time.sleep(0.3)
            os.write(master_fd, b'scan on\n')

            # Collect devices for specified duration
            scan_end = time.time() + duration
            buffer = ''

            while time.time() < scan_end and _sweep_running:
                readable, _, _ = select.select([master_fd], [], [], 1.0)
                if readable:
                    try:
                        data = os.read(master_fd, 4096)
                        if not data:
                            break
                        buffer += data.decode('utf-8', errors='replace')

                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            line = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()

                            if 'Device' in line:
                                match = re.search(
                                    r'([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:'
                                    r'[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})\s*(.*)',
                                    line
                                )
                                if match:
                                    mac = match.group(1).upper()
                                    name = match.group(2).strip()
                                    # Remove RSSI from name if present
                                    name = re.sub(r'\s*RSSI:\s*-?\d+\s*', '', name).strip()

                                    if mac not in seen_macs:
                                        seen_macs.add(mac)
                                        devices.append({
                                            'mac': mac,
                                            'name': name or '[Unknown]'
                                        })
                    except OSError:
                        break

            # Stop scanning and cleanup
            try:
                os.write(master_fd, b'scan off\n')
                time.sleep(0.2)
                os.write(master_fd, b'quit\n')
            except OSError:
                pass

            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

            try:
                os.close(master_fd)
            except OSError:
                pass

        except (FileNotFoundError, subprocess.SubprocessError) as e:
            logger.warning(f"bluetoothctl scan failed: {e}")

            # Fallback to hcitool
            try:
                result = subprocess.run(
                    ['hcitool', '-i', iface, 'scan'],
                    capture_output=True, text=True, timeout=duration + 5
                )
                for line in result.stdout.split('\n'):
                    parts = line.split()
                    if len(parts) >= 1 and ':' in parts[0]:
                        mac = parts[0].upper()
                        name = ' '.join(parts[1:]) if len(parts) > 1 else '[Unknown]'
                        if mac not in seen_macs:
                            seen_macs.add(mac)
                            devices.append({'mac': mac, 'name': name})
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
                logger.warning(f"hcitool scan failed: {e}")

    return devices


def _run_sweep(
    sweep_type: str,
    baseline_id: int | None,
    wifi_enabled: bool,
    bt_enabled: bool,
    rf_enabled: bool,
    wifi_interface: str = '',
    bt_interface: str = '',
    sdr_device: int | None = None
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
        severity_counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
        all_wifi = {}  # Use dict for deduplication by BSSID
        all_bt = {}    # Use dict for deduplication by MAC
        all_rf = []

        start_time = time.time()
        last_wifi_scan = 0
        last_bt_scan = 0
        wifi_scan_interval = 15  # Scan WiFi every 15 seconds
        bt_scan_interval = 20   # Scan Bluetooth every 20 seconds

        while _sweep_running and (time.time() - start_time) < duration:
            current_time = time.time()

            # Perform WiFi scan
            if wifi_enabled and (current_time - last_wifi_scan) >= wifi_scan_interval:
                try:
                    wifi_networks = _scan_wifi_networks(wifi_interface)
                    for network in wifi_networks:
                        bssid = network.get('bssid', '')
                        if bssid and bssid not in all_wifi:
                            all_wifi[bssid] = network
                            # Analyze for threats
                            threat = detector.analyze_wifi_device(network)
                            if threat:
                                _handle_threat(threat)
                                threats_found += 1
                                sev = threat.get('severity', 'low').lower()
                                if sev in severity_counts:
                                    severity_counts[sev] += 1
                    last_wifi_scan = current_time
                except Exception as e:
                    logger.error(f"WiFi scan error: {e}")

            # Perform Bluetooth scan
            if bt_enabled and (current_time - last_bt_scan) >= bt_scan_interval:
                try:
                    bt_devices = _scan_bluetooth_devices(bt_interface, duration=8)
                    for device in bt_devices:
                        mac = device.get('mac', '')
                        if mac and mac not in all_bt:
                            all_bt[mac] = device
                            # Analyze for threats
                            threat = detector.analyze_bt_device(device)
                            if threat:
                                _handle_threat(threat)
                                threats_found += 1
                                sev = threat.get('severity', 'low').lower()
                                if sev in severity_counts:
                                    severity_counts[sev] += 1
                    last_bt_scan = current_time
                except Exception as e:
                    logger.error(f"Bluetooth scan error: {e}")

            # RF scanning would go here if SDR is available
            # For now, RF scanning is not implemented

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
                'severity_counts': severity_counts,
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
                    'severity_counts': severity_counts,
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
                'severity_counts': severity_counts,
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
