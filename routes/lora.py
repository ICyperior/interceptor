"""LoRa/ISM band monitoring routes."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from datetime import datetime
from typing import Generator

from flask import Blueprint, jsonify, request, Response

import app as app_module
from utils.logging import get_logger
from utils.validation import (
    validate_frequency, validate_device_index, validate_gain, validate_ppm,
    validate_rtl_tcp_host, validate_rtl_tcp_port
)
from utils.sse import format_sse
from utils.sdr import SDRFactory, SDRType

logger = get_logger('intercept.lora')

lora_bp = Blueprint('lora', __name__, url_prefix='/lora')

# LoRa frequency bands by region
LORA_BANDS = {
    'eu868': {
        'name': 'EU 868 MHz',
        'frequency': 868.0,
        'range': (863.0, 870.0),
        'channels': [868.1, 868.3, 868.5, 867.1, 867.3, 867.5, 867.7, 867.9]
    },
    'us915': {
        'name': 'US 915 MHz',
        'frequency': 915.0,
        'range': (902.0, 928.0),
        'channels': [902.3, 902.5, 902.7, 902.9, 903.1, 903.3, 903.5, 903.7]
    },
    'au915': {
        'name': 'AU 915 MHz',
        'frequency': 915.0,
        'range': (915.0, 928.0),
        'channels': [915.2, 915.4, 915.6, 915.8, 916.0, 916.2, 916.4, 916.6]
    },
    'as923': {
        'name': 'AS 923 MHz',
        'frequency': 923.0,
        'range': (920.0, 925.0),
        'channels': [923.2, 923.4, 923.6, 923.8, 924.0, 924.2, 924.4, 924.6]
    },
    'in865': {
        'name': 'IN 865 MHz',
        'frequency': 865.0,
        'range': (865.0, 867.0),
        'channels': [865.0625, 865.4025, 865.985]
    },
    'ism433': {
        'name': 'ISM 433 MHz',
        'frequency': 433.92,
        'range': (433.05, 434.79),
        'channels': [433.05, 433.42, 433.92, 434.42]
    }
}

# Device patterns that indicate LoRa/LPWAN devices
LORA_DEVICE_PATTERNS = [
    'LoRa', 'Dragino', 'RAK', 'Heltec', 'TTGO', 'LoPy', 'Pycom',
    'Semtech', 'SX127', 'RFM95', 'RFM96', 'Murata', 'Microchip',
    'The Things', 'TTN', 'Helium', 'Chirpstack', 'LoRaWAN',
    'Smart meter', 'Sensus', 'Itron', 'Landis', 'Water meter',
    'Gas meter', 'Electric meter', 'Utility meter'
]


def is_lora_device(model: str, protocol: str = '') -> bool:
    """Check if a device model/protocol indicates LoRa/LPWAN."""
    combined = f"{model} {protocol}".lower()
    return any(pattern.lower() in combined for pattern in LORA_DEVICE_PATTERNS)


def stream_lora_output(process: subprocess.Popen[bytes]) -> None:
    """Stream rtl_433 JSON output to LoRa queue."""
    try:
        app_module.lora_queue.put({'type': 'status', 'text': 'started'})

        for line in iter(process.stdout.readline, b''):
            line = line.decode('utf-8', errors='replace').strip()
            if not line:
                continue

            try:
                # rtl_433 outputs JSON objects
                data = json.loads(line)

                # Enhance with LoRa-specific info
                model = data.get('model', 'Unknown')
                protocol = data.get('protocol', '')
                data['type'] = 'lora_device'
                data['is_lora'] = is_lora_device(model, protocol)
                data['timestamp'] = datetime.now().isoformat()

                # Calculate signal quality if RSSI available
                rssi = data.get('rssi')
                if rssi is not None:
                    # Normalize RSSI to quality percentage
                    # Typical LoRa range: -120 dBm (weak) to -30 dBm (strong)
                    quality = max(0, min(100, (rssi + 120) * 100 / 90))
                    data['signal_quality'] = round(quality)

                app_module.lora_queue.put(data)

            except json.JSONDecodeError:
                # Not JSON, could be info message
                if line and not line.startswith('_'):
                    app_module.lora_queue.put({'type': 'info', 'text': line})

    except Exception as e:
        app_module.lora_queue.put({'type': 'error', 'text': str(e)})
    finally:
        process.wait()
        app_module.lora_queue.put({'type': 'status', 'text': 'stopped'})
        with app_module.lora_lock:
            app_module.lora_process = None


@lora_bp.route('/bands')
def get_bands() -> Response:
    """Get available LoRa frequency bands."""
    return jsonify({
        'status': 'success',
        'bands': LORA_BANDS
    })


@lora_bp.route('/start', methods=['POST'])
def start_lora() -> Response:
    """Start LoRa band monitoring."""
    with app_module.lora_lock:
        if app_module.lora_process:
            return jsonify({'status': 'error', 'message': 'LoRa monitor already running'}), 409

        data = request.json or {}

        # Get band or custom frequency
        band_id = data.get('band', 'eu868')
        band = LORA_BANDS.get(band_id, LORA_BANDS['eu868'])

        # Allow custom frequency override
        custom_freq = data.get('frequency')
        if custom_freq:
            try:
                freq = validate_frequency(custom_freq)
            except ValueError as e:
                return jsonify({'status': 'error', 'message': str(e)}), 400
        else:
            freq = band['frequency']

        # Validate other inputs
        try:
            gain = validate_gain(data.get('gain', '40'))  # Higher default gain for weak signals
            ppm = validate_ppm(data.get('ppm', '0'))
            device = validate_device_index(data.get('device', '0'))
        except ValueError as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400

        # Clear queue
        while not app_module.lora_queue.empty():
            try:
                app_module.lora_queue.get_nowait()
            except queue.Empty:
                break

        # Get SDR type
        sdr_type_str = data.get('sdr_type', 'rtlsdr')
        try:
            sdr_type = SDRType(sdr_type_str)
        except ValueError:
            sdr_type = SDRType.RTL_SDR

        # Check for rtl_tcp
        rtl_tcp_host = data.get('rtl_tcp_host')
        rtl_tcp_port = data.get('rtl_tcp_port', 1234)

        if rtl_tcp_host:
            try:
                rtl_tcp_host = validate_rtl_tcp_host(rtl_tcp_host)
                rtl_tcp_port = validate_rtl_tcp_port(rtl_tcp_port)
            except ValueError as e:
                return jsonify({'status': 'error', 'message': str(e)}), 400

            sdr_device = SDRFactory.create_network_device(rtl_tcp_host, rtl_tcp_port)
        else:
            sdr_device = SDRFactory.create_default_device(sdr_type, index=device)

        builder = SDRFactory.get_builder(sdr_device.sdr_type)

        # Build command for LoRa band monitoring
        bias_t = data.get('bias_t', False)

        # Use rtl_433 with settings optimized for LoRa bands
        # -f frequency, -g gain, -F json, -M time:utc, -Y autolevel
        cmd = builder.build_ism_command(
            device=sdr_device,
            frequency_mhz=freq,
            gain=float(gain) if gain else 40.0,
            ppm=int(ppm) if ppm else None,
            bias_t=bias_t
        )

        # Add hop frequencies for the band if enabled
        hop_enabled = data.get('hop_enabled', False)
        if hop_enabled and 'channels' in band:
            # Add additional frequencies to monitor
            for ch_freq in band['channels'][:4]:  # Limit to 4 hop frequencies
                if ch_freq != freq:
                    cmd.extend(['-f', f'{ch_freq}M'])

        full_cmd = ' '.join(cmd)
        logger.info(f"Running: {full_cmd}")

        try:
            app_module.lora_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # Start output thread
            thread = threading.Thread(target=stream_lora_output, args=(app_module.lora_process,))
            thread.daemon = True
            thread.start()

            # Monitor stderr
            def monitor_stderr():
                for line in app_module.lora_process.stderr:
                    err = line.decode('utf-8', errors='replace').strip()
                    if err:
                        logger.debug(f"[rtl_433] {err}")

            stderr_thread = threading.Thread(target=monitor_stderr)
            stderr_thread.daemon = True
            stderr_thread.start()

            app_module.lora_queue.put({
                'type': 'info',
                'text': f'Monitoring {band["name"]} at {freq} MHz'
            })

            return jsonify({
                'status': 'started',
                'band': band_id,
                'frequency': freq,
                'command': full_cmd
            })

        except FileNotFoundError:
            return jsonify({
                'status': 'error',
                'message': 'rtl_433 not found. Install with: sudo apt install rtl-433'
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})


@lora_bp.route('/stop', methods=['POST'])
def stop_lora() -> Response:
    """Stop LoRa monitoring."""
    with app_module.lora_lock:
        if app_module.lora_process:
            app_module.lora_process.terminate()
            try:
                app_module.lora_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                app_module.lora_process.kill()
            app_module.lora_process = None
            return jsonify({'status': 'stopped'})

        return jsonify({'status': 'not_running'})


@lora_bp.route('/stream')
def stream_lora() -> Response:
    """SSE stream for LoRa data."""
    def generate() -> Generator[str, None, None]:
        last_keepalive = time.time()
        keepalive_interval = 30.0

        while True:
            try:
                msg = app_module.lora_queue.get(timeout=1)
                last_keepalive = time.time()
                yield format_sse(msg)
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


@lora_bp.route('/status')
def lora_status() -> Response:
    """Get LoRa monitoring status."""
    with app_module.lora_lock:
        running = app_module.lora_process is not None
        return jsonify({
            'status': 'running' if running else 'stopped',
            'running': running
        })
