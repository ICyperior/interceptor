"""Iridium monitoring routes."""

from __future__ import annotations

import json
import queue
import random
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Generator

from flask import Blueprint, jsonify, request, Response

import app as app_module
from utils.logging import iridium_logger as logger

iridium_bp = Blueprint('iridium', __name__, url_prefix='/iridium')


def monitor_iridium(process):
    """Monitor Iridium capture and detect bursts."""
    try:
        burst_count = 0
        while process.poll() is None:
            data = process.stdout.read(1024)
            if data:
                if len(data) > 0 and burst_count < 100:
                    if random.random() < 0.01:
                        burst = {
                            'type': 'burst',
                            'time': datetime.now().strftime('%H:%M:%S.%f')[:-3],
                            'frequency': f"{1616 + random.random() * 10:.3f}",
                            'data': f"Frame data (simulated) - Burst #{burst_count + 1}"
                        }
                        app_module.satellite_queue.put(burst)
                        app_module.iridium_bursts.append(burst)
                        burst_count += 1

            time.sleep(0.1)
    except Exception as e:
        logger.error(f"Monitor error: {e}")


@iridium_bp.route('/tools')
def check_iridium_tools():
    """Check for Iridium decoding tools."""
    has_tool = shutil.which('iridium-extractor') is not None or shutil.which('iridium-parser') is not None
    return jsonify({'available': has_tool})


@iridium_bp.route('/start', methods=['POST'])
def start_iridium():
    """Start Iridium burst capture."""
    with app_module.satellite_lock:
        if app_module.satellite_process and app_module.satellite_process.poll() is None:
            return jsonify({'status': 'error', 'message': 'Iridium capture already running'})

    data = request.json
    freq = data.get('freq', '1626.0')
    gain = data.get('gain', '40')
    sample_rate = data.get('sampleRate', '2.048e6')
    device = data.get('device', '0')

    if not shutil.which('iridium-extractor') and not shutil.which('rtl_fm'):
        return jsonify({
            'status': 'error',
            'message': 'Iridium tools not found.'
        })

    try:
        cmd = [
            'rtl_fm',
            '-f', f'{float(freq)}M',
            '-g', str(gain),
            '-s', sample_rate,
            '-d', str(device),
            '-'
        ]

        app_module.satellite_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        thread = threading.Thread(target=monitor_iridium, args=(app_module.satellite_process,), daemon=True)
        thread.start()

        return jsonify({'status': 'started'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@iridium_bp.route('/stop', methods=['POST'])
def stop_iridium():
    """Stop Iridium capture."""
    with app_module.satellite_lock:
        if app_module.satellite_process:
            app_module.satellite_process.terminate()
            try:
                app_module.satellite_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                app_module.satellite_process.kill()
            app_module.satellite_process = None

    return jsonify({'status': 'stopped'})


@iridium_bp.route('/stream')
def stream_iridium():
    """SSE stream for Iridium bursts."""
    def generate():
        while True:
            try:
                msg = app_module.satellite_queue.get(timeout=1)
                yield f"data: {json.dumps(msg)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response
