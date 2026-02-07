"""RTL_433 sensor monitoring routes."""

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
from utils.logging import sensor_logger as logger
from utils.validation import (
    validate_frequency, validate_device_index, validate_gain, validate_ppm,
    validate_rtl_tcp_host, validate_rtl_tcp_port
)
from utils.sse import format_sse
from utils.event_pipeline import process_event
from utils.process import safe_terminate, register_process, unregister_process
from utils.sdr import SDRFactory, SDRType
from utils.dependencies import get_tool_path

sensor_bp = Blueprint('sensor', __name__)

# Track which device is being used
sensor_active_device: int | None = None
# IQ pipeline stop event
sensor_iq_stop_event: threading.Event | None = None
# Companion rtl_sdr process when using IQ pipeline
sensor_rtl_process: subprocess.Popen | None = None


def stream_sensor_output(process: subprocess.Popen[bytes]) -> None:
    """Stream rtl_433 JSON output to queue."""
    try:
        app_module.sensor_queue.put({'type': 'status', 'text': 'started'})

        for line in iter(process.stdout.readline, b''):
            line = line.decode('utf-8', errors='replace').strip()
            if not line:
                continue

            try:
                # rtl_433 outputs JSON objects, one per line
                data = json.loads(line)
                data['type'] = 'sensor'
                app_module.sensor_queue.put(data)

                # Log if enabled
                if app_module.logging_enabled:
                    try:
                        with open(app_module.log_file_path, 'a') as f:
                            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            f.write(f"{timestamp} | {data.get('model', 'Unknown')} | {json.dumps(data)}\n")
                    except Exception:
                        pass
            except json.JSONDecodeError:
                # Not JSON, send as raw
                app_module.sensor_queue.put({'type': 'raw', 'text': line})

    except Exception as e:
        app_module.sensor_queue.put({'type': 'error', 'text': str(e)})
    finally:
        global sensor_active_device, sensor_iq_stop_event, sensor_rtl_process
        # Stop IQ pipeline if running
        if sensor_iq_stop_event is not None:
            sensor_iq_stop_event.set()
            sensor_iq_stop_event = None
        if app_module.waterfall_source == 'sensor':
            app_module.waterfall_source = None
        # Terminate companion rtl_sdr process
        if sensor_rtl_process is not None:
            try:
                sensor_rtl_process.terminate()
                sensor_rtl_process.wait(timeout=2)
            except Exception:
                try:
                    sensor_rtl_process.kill()
                except Exception:
                    pass
            unregister_process(sensor_rtl_process)
            sensor_rtl_process = None
        # Ensure decoder process is terminated
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        unregister_process(process)
        app_module.sensor_queue.put({'type': 'status', 'text': 'stopped'})
        with app_module.sensor_lock:
            app_module.sensor_process = None
        # Release SDR device
        if sensor_active_device is not None:
            app_module.release_sdr_device(sensor_active_device)
            sensor_active_device = None


def _cleanup_sensor_failed_start(rtl_process: subprocess.Popen | None) -> None:
    """Clean up after a failed sensor start attempt."""
    global sensor_active_device, sensor_iq_stop_event, sensor_rtl_process
    if rtl_process:
        try:
            rtl_process.terminate()
            rtl_process.wait(timeout=2)
        except Exception:
            try:
                rtl_process.kill()
            except Exception:
                pass
    if sensor_iq_stop_event is not None:
        sensor_iq_stop_event.set()
        sensor_iq_stop_event = None
    if app_module.waterfall_source == 'sensor':
        app_module.waterfall_source = None
    sensor_rtl_process = None
    if sensor_active_device is not None:
        app_module.release_sdr_device(sensor_active_device)
        sensor_active_device = None


@sensor_bp.route('/start_sensor', methods=['POST'])
def start_sensor() -> Response:
    global sensor_active_device, sensor_iq_stop_event, sensor_rtl_process

    with app_module.sensor_lock:
        if app_module.sensor_process:
            return jsonify({'status': 'error', 'message': 'Sensor already running'}), 409

        data = request.json or {}

        # Validate inputs
        try:
            freq = validate_frequency(data.get('frequency', '433.92'))
            gain = validate_gain(data.get('gain', '0'))
            ppm = validate_ppm(data.get('ppm', '0'))
            device = validate_device_index(data.get('device', '0'))
        except ValueError as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400

        # Check for rtl_tcp (remote SDR) connection
        rtl_tcp_host = data.get('rtl_tcp_host')
        rtl_tcp_port = data.get('rtl_tcp_port', 1234)

        # Claim local device if not using remote rtl_tcp
        if not rtl_tcp_host:
            device_int = int(device)
            error = app_module.claim_sdr_device(device_int, 'sensor')
            if error:
                return jsonify({
                    'status': 'error',
                    'error_type': 'DEVICE_BUSY',
                    'message': error
                }), 409
            sensor_active_device = device_int

        # Clear queue
        while not app_module.sensor_queue.empty():
            try:
                app_module.sensor_queue.get_nowait()
            except queue.Empty:
                break

        # Get SDR type and build command via abstraction layer
        sdr_type_str = data.get('sdr_type', 'rtlsdr')
        try:
            sdr_type = SDRType(sdr_type_str)
        except ValueError:
            sdr_type = SDRType.RTL_SDR

        if rtl_tcp_host:
            # Validate and create network device
            try:
                rtl_tcp_host = validate_rtl_tcp_host(rtl_tcp_host)
                rtl_tcp_port = validate_rtl_tcp_port(rtl_tcp_port)
            except ValueError as e:
                return jsonify({'status': 'error', 'message': str(e)}), 400

            sdr_device = SDRFactory.create_network_device(rtl_tcp_host, rtl_tcp_port)
            logger.info(f"Using remote SDR: rtl_tcp://{rtl_tcp_host}:{rtl_tcp_port}")
        else:
            # Create local device object
            sdr_device = SDRFactory.create_default_device(sdr_type, index=device)

        builder = SDRFactory.get_builder(sdr_device.sdr_type)
        bias_t = data.get('bias_t', False)
        gain_val = float(gain) if gain and gain != 0 else None
        ppm_val = int(ppm) if ppm and ppm != 0 else None

        # Determine if we can use IQ pipeline for live waterfall
        use_iq_pipeline = (
            sdr_type == SDRType.RTL_SDR
            and not rtl_tcp_host
            and get_tool_path('rtl_sdr') is not None
        )

        if use_iq_pipeline:
            # IQ pipeline: rtl_sdr -> Python IQ tee -> rtl_433 -r -
            iq_sample_rate = 250000  # rtl_433 default

            rtl_cmd = builder.build_raw_capture_command(
                device=sdr_device,
                frequency_mhz=freq,
                sample_rate=iq_sample_rate,
                gain=gain_val,
                ppm=ppm_val,
                bias_t=bias_t,
            )

            rtl_433_path = get_tool_path('rtl_433') or 'rtl_433'
            decoder_cmd = [rtl_433_path, '-r', '-', '-s', str(iq_sample_rate), '-F', 'json']

            full_cmd = ' '.join(rtl_cmd) + ' | [iq_processor] | ' + ' '.join(decoder_cmd)
            logger.info(f"Running (IQ pipeline): {full_cmd}")

            try:
                rtl_process = subprocess.Popen(
                    rtl_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                register_process(rtl_process)
                sensor_rtl_process = rtl_process

                # Monitor rtl_sdr stderr
                def monitor_rtl_stderr():
                    for line in rtl_process.stderr:
                        err = line.decode('utf-8', errors='replace').strip()
                        if err:
                            logger.debug(f"[rtl_sdr] {err}")
                            app_module.sensor_queue.put({'type': 'info', 'text': f'[rtl_sdr] {err}'})

                threading.Thread(target=monitor_rtl_stderr, daemon=True).start()

                # Start rtl_433 reading from stdin
                decoder_process = subprocess.Popen(
                    decoder_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                register_process(decoder_process)

                # Start IQ processor thread
                from routes.listening_post import waterfall_queue
                from utils.iq_processor import run_passthrough_iq_pipeline

                stop_event = threading.Event()
                sensor_iq_stop_event = stop_event
                app_module.waterfall_source = 'sensor'

                iq_thread = threading.Thread(
                    target=run_passthrough_iq_pipeline,
                    args=(
                        rtl_process.stdout,
                        decoder_process.stdin,
                        waterfall_queue,
                        freq,
                        iq_sample_rate,
                        stop_event,
                    ),
                    daemon=True,
                )
                iq_thread.start()

                app_module.sensor_process = decoder_process

                # Monitor rtl_433 stderr
                def monitor_decoder_stderr():
                    for line in decoder_process.stderr:
                        err = line.decode('utf-8', errors='replace').strip()
                        if err:
                            logger.debug(f"[rtl_433] {err}")
                            app_module.sensor_queue.put({'type': 'info', 'text': f'[rtl_433] {err}'})

                threading.Thread(target=monitor_decoder_stderr, daemon=True).start()

                # Start output thread
                thread = threading.Thread(target=stream_sensor_output, args=(decoder_process,), daemon=True)
                thread.start()

                app_module.sensor_queue.put({'type': 'info', 'text': f'Command: {full_cmd}'})
                return jsonify({'status': 'started', 'command': full_cmd, 'waterfall_source': 'sensor'})

            except FileNotFoundError:
                _cleanup_sensor_failed_start(rtl_process)
                return jsonify({'status': 'error', 'message': 'rtl_sdr or rtl_433 not found'})
            except Exception as e:
                _cleanup_sensor_failed_start(rtl_process)
                return jsonify({'status': 'error', 'message': str(e)})

        else:
            # Legacy pipeline: rtl_433 directly
            cmd = builder.build_ism_command(
                device=sdr_device,
                frequency_mhz=freq,
                gain=gain_val,
                ppm=ppm_val,
                bias_t=bias_t,
            )

            full_cmd = ' '.join(cmd)
            logger.info(f"Running: {full_cmd}")

            try:
                app_module.sensor_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                register_process(app_module.sensor_process)

                # Start output thread
                thread = threading.Thread(target=stream_sensor_output, args=(app_module.sensor_process,), daemon=True)
                thread.start()

                # Monitor stderr
                def monitor_stderr():
                    for line in app_module.sensor_process.stderr:
                        err = line.decode('utf-8', errors='replace').strip()
                        if err:
                            logger.debug(f"[rtl_433] {err}")
                            app_module.sensor_queue.put({'type': 'info', 'text': f'[rtl_433] {err}'})

                threading.Thread(target=monitor_stderr, daemon=True).start()

                app_module.sensor_queue.put({'type': 'info', 'text': f'Command: {full_cmd}'})
                return jsonify({'status': 'started', 'command': full_cmd})

            except FileNotFoundError:
                if sensor_active_device is not None:
                    app_module.release_sdr_device(sensor_active_device)
                    sensor_active_device = None
                return jsonify({'status': 'error', 'message': 'rtl_433 not found. Install with: brew install rtl_433'})
            except Exception as e:
                if sensor_active_device is not None:
                    app_module.release_sdr_device(sensor_active_device)
                    sensor_active_device = None
                return jsonify({'status': 'error', 'message': str(e)})


@sensor_bp.route('/stop_sensor', methods=['POST'])
def stop_sensor() -> Response:
    global sensor_active_device, sensor_iq_stop_event, sensor_rtl_process

    with app_module.sensor_lock:
        if app_module.sensor_process:
            # Stop IQ pipeline if running
            if sensor_iq_stop_event is not None:
                sensor_iq_stop_event.set()
                sensor_iq_stop_event = None
            if app_module.waterfall_source == 'sensor':
                app_module.waterfall_source = None

            # Kill companion rtl_sdr process
            if sensor_rtl_process is not None:
                try:
                    sensor_rtl_process.terminate()
                    sensor_rtl_process.wait(timeout=2)
                except (subprocess.TimeoutExpired, OSError):
                    try:
                        sensor_rtl_process.kill()
                    except OSError:
                        pass
                sensor_rtl_process = None

            app_module.sensor_process.terminate()
            try:
                app_module.sensor_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                app_module.sensor_process.kill()
            app_module.sensor_process = None

            # Release device from registry
            if sensor_active_device is not None:
                app_module.release_sdr_device(sensor_active_device)
                sensor_active_device = None

            return jsonify({'status': 'stopped'})

        return jsonify({'status': 'not_running'})


@sensor_bp.route('/stream_sensor')
def stream_sensor() -> Response:
    def generate() -> Generator[str, None, None]:
        last_keepalive = time.time()
        keepalive_interval = 30.0

        while True:
            try:
                msg = app_module.sensor_queue.get(timeout=1)
                last_keepalive = time.time()
                try:
                    process_event('sensor', msg, msg.get('type'))
                except Exception:
                    pass
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
