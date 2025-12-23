"""
INTERCEPT - Signal Intelligence Platform

Flask application and shared state.
"""

from __future__ import annotations

import sys
import site

# Ensure user site-packages is available (may be disabled when running as root/sudo)
if not site.ENABLE_USER_SITE:
    user_site = site.getusersitepackages()
    if user_site and user_site not in sys.path:
        sys.path.insert(0, user_site)

import os
import queue
import threading
import platform
import subprocess

from typing import Any

from flask import Flask, render_template, jsonify, send_file, Response

from utils.dependencies import check_tool, check_all_dependencies, TOOL_DEPENDENCIES
from utils.process import detect_devices, cleanup_stale_processes


# Create Flask app
app = Flask(__name__)

# ============================================
# GLOBAL PROCESS MANAGEMENT
# ============================================

# Pager decoder
current_process = None
output_queue = queue.Queue()
process_lock = threading.Lock()

# RTL_433 sensor
sensor_process = None
sensor_queue = queue.Queue()
sensor_lock = threading.Lock()

# WiFi
wifi_process = None
wifi_queue = queue.Queue()
wifi_lock = threading.Lock()

# Bluetooth
bt_process = None
bt_queue = queue.Queue()
bt_lock = threading.Lock()

# ADS-B aircraft
adsb_process = None
adsb_queue = queue.Queue()
adsb_lock = threading.Lock()

# Satellite/Iridium
satellite_process = None
satellite_queue = queue.Queue()
satellite_lock = threading.Lock()

# ============================================
# GLOBAL STATE DICTIONARIES
# ============================================

# Logging settings
logging_enabled = False
log_file_path = 'pager_messages.log'

# WiFi state
wifi_monitor_interface = None
wifi_networks = {}   # BSSID -> network info
wifi_clients = {}    # Client MAC -> client info
wifi_handshakes = [] # Captured handshakes

# Bluetooth state
bt_interface = None
bt_devices = {}      # MAC -> device info
bt_beacons = {}      # MAC -> beacon info (AirTags, Tiles, iBeacons)
bt_services = {}     # MAC -> list of services

# Aircraft (ADS-B) state
adsb_aircraft = {}   # ICAO hex -> aircraft info

# Satellite state
iridium_bursts = []  # List of detected Iridium bursts
satellite_passes = [] # Predicted satellite passes


# ============================================
# MAIN ROUTES
# ============================================

@app.route('/')
def index() -> str:
    tools = {
        'rtl_fm': check_tool('rtl_fm'),
        'multimon': check_tool('multimon-ng'),
        'rtl_433': check_tool('rtl_433')
    }
    devices = detect_devices()
    return render_template('index.html', tools=tools, devices=devices)


@app.route('/favicon.svg')
def favicon() -> Response:
    return send_file('favicon.svg', mimetype='image/svg+xml')


@app.route('/devices')
def get_devices() -> Response:
    return jsonify(detect_devices())


@app.route('/dependencies')
def get_dependencies() -> Response:
    """Get status of all tool dependencies."""
    results = check_all_dependencies()

    # Determine OS for install instructions
    system = platform.system().lower()
    if system == 'darwin':
        install_method = 'brew'
    elif system == 'linux':
        install_method = 'apt'
    else:
        install_method = 'manual'

    return jsonify({
        'os': system,
        'install_method': install_method,
        'modes': results
    })


@app.route('/killall', methods=['POST'])
def kill_all() -> Response:
    """Kill all decoder and WiFi processes."""
    global current_process, sensor_process, wifi_process

    killed = []
    processes_to_kill = [
        'rtl_fm', 'multimon-ng', 'rtl_433',
        'airodump-ng', 'aireplay-ng', 'airmon-ng'
    ]

    for proc in processes_to_kill:
        try:
            result = subprocess.run(['pkill', '-f', proc], capture_output=True)
            if result.returncode == 0:
                killed.append(proc)
        except (subprocess.SubprocessError, OSError):
            pass

    with process_lock:
        current_process = None

    with sensor_lock:
        sensor_process = None

    with wifi_lock:
        wifi_process = None

    return jsonify({'status': 'killed', 'processes': killed})


def main() -> None:
    """Main entry point."""
    print("=" * 50)
    print("  INTERCEPT // Signal Intelligence")
    print("  Pager / 433MHz / Aircraft / Satellite / WiFi / BT")
    print("=" * 50)
    print()

    # Clean up any stale processes from previous runs
    cleanup_stale_processes()

    # Register blueprints
    from routes import register_blueprints
    register_blueprints(app)

    print("Open http://localhost:5050 in your browser")
    print()
    print("Press Ctrl+C to stop")
    print()

    app.run(host='0.0.0.0', port=5050, debug=False, threaded=True)
