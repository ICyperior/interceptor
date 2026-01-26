#!/usr/bin/env python3
"""
INTERCEPT Agent - Remote node for distributed signal intelligence.

This agent runs on remote nodes and exposes Intercept's capabilities via REST API.
It can push data to a central controller or respond to pull requests.

Usage:
    python intercept_agent.py [--port 8020] [--config intercept_agent.cfg]
"""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import urlparse, parse_qs

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import dependency checking from Intercept utils
try:
    from utils.dependencies import check_all_dependencies, check_tool, TOOL_DEPENDENCIES
    HAS_DEPENDENCIES_MODULE = True
except ImportError:
    HAS_DEPENDENCIES_MODULE = False

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('intercept.agent')

# Version
AGENT_VERSION = '1.0.0'

# =============================================================================
# Configuration
# =============================================================================

class AgentConfig:
    """Agent configuration loaded from INI file or defaults."""

    def __init__(self):
        # Agent settings
        self.name: str = socket.gethostname()
        self.port: int = 8020
        self.allowed_ips: list[str] = []
        self.allow_cors: bool = False

        # Controller settings
        self.controller_url: str = ''
        self.controller_api_key: str = ''
        self.push_enabled: bool = False
        self.push_interval: int = 5

        # Mode settings (all enabled by default)
        self.modes_enabled: dict[str, bool] = {
            'pager': True,
            'sensor': True,
            'adsb': True,
            'ais': True,
            'acars': True,
            'aprs': True,
            'wifi': True,
            'bluetooth': True,
            'dsc': True,
            'rtlamr': True,
            'tscm': True,
            'satellite': True,
            'listening_post': True,
        }

    def load_from_file(self, filepath: str) -> bool:
        """Load configuration from INI file."""
        if not os.path.isfile(filepath):
            logger.warning(f"Config file not found: {filepath}")
            return False

        parser = configparser.ConfigParser()
        try:
            parser.read(filepath)

            # Agent section
            if parser.has_section('agent'):
                if parser.has_option('agent', 'name'):
                    self.name = parser.get('agent', 'name')
                if parser.has_option('agent', 'port'):
                    self.port = parser.getint('agent', 'port')
                if parser.has_option('agent', 'allowed_ips'):
                    ips = parser.get('agent', 'allowed_ips')
                    if ips.strip():
                        self.allowed_ips = [ip.strip() for ip in ips.split(',')]
                if parser.has_option('agent', 'allow_cors'):
                    self.allow_cors = parser.getboolean('agent', 'allow_cors')

            # Controller section
            if parser.has_section('controller'):
                if parser.has_option('controller', 'url'):
                    self.controller_url = parser.get('controller', 'url').rstrip('/')
                if parser.has_option('controller', 'api_key'):
                    self.controller_api_key = parser.get('controller', 'api_key')
                if parser.has_option('controller', 'push_enabled'):
                    self.push_enabled = parser.getboolean('controller', 'push_enabled')
                if parser.has_option('controller', 'push_interval'):
                    self.push_interval = parser.getint('controller', 'push_interval')

            # Modes section
            if parser.has_section('modes'):
                for mode in self.modes_enabled.keys():
                    if parser.has_option('modes', mode):
                        self.modes_enabled[mode] = parser.getboolean('modes', mode)

            logger.info(f"Loaded configuration from {filepath}")
            return True

        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return False

    def to_dict(self) -> dict:
        """Convert config to dictionary."""
        return {
            'name': self.name,
            'port': self.port,
            'allowed_ips': self.allowed_ips,
            'allow_cors': self.allow_cors,
            'controller_url': self.controller_url,
            'push_enabled': self.push_enabled,
            'push_interval': self.push_interval,
            'modes_enabled': self.modes_enabled,
        }


# Global config
config = AgentConfig()


# =============================================================================
# GPS Integration
# =============================================================================

class GPSManager:
    """Manages GPS position via gpsd."""

    def __init__(self):
        self._client = None
        self._position = None
        self._lock = threading.Lock()
        self._running = False

    @property
    def position(self) -> dict | None:
        """Get current GPS position."""
        with self._lock:
            if self._position:
                return {
                    'lat': self._position.latitude,
                    'lon': self._position.longitude,
                    'altitude': self._position.altitude,
                    'speed': self._position.speed,
                    'heading': self._position.heading,
                    'fix_quality': self._position.fix_quality,
                }
            return None

    def start(self, host: str = 'localhost', port: int = 2947) -> bool:
        """Start GPS client connection to gpsd."""
        try:
            from utils.gps import GPSDClient
            self._client = GPSDClient(host, port)
            self._client.add_callback(self._on_position_update)
            success = self._client.start()
            if success:
                self._running = True
                logger.info(f"GPS connected to gpsd at {host}:{port}")
            return success
        except ImportError:
            logger.warning("GPS module not available")
            return False
        except Exception as e:
            logger.error(f"Failed to start GPS: {e}")
            return False

    def stop(self):
        """Stop GPS client."""
        if self._client:
            self._client.stop()
            self._client = None
        self._running = False

    def _on_position_update(self, position):
        """Callback for GPS position updates."""
        with self._lock:
            self._position = position

    @property
    def is_running(self) -> bool:
        return self._running


# Global GPS manager
gps_manager = GPSManager()


# =============================================================================
# Controller Push Client
# =============================================================================

class ControllerPushClient(threading.Thread):
    """Daemon thread that pushes scan data to the controller."""

    def __init__(self, cfg: AgentConfig):
        super().__init__()
        self.daemon = True
        self.cfg = cfg
        self.queue: queue.Queue = queue.Queue(maxsize=200)
        self.running = False
        self.stop_event = threading.Event()

    def enqueue(self, scan_type: str, payload: dict, interface: str = None):
        """Add data to push queue."""
        if not self.cfg.push_enabled or not self.cfg.controller_url:
            return

        item = {
            'agent_name': self.cfg.name,
            'scan_type': scan_type,
            'interface': interface,
            'payload': payload,
            'received_at': datetime.now(timezone.utc).isoformat(),
            'attempts': 0,
        }

        try:
            self.queue.put_nowait(item)
        except queue.Full:
            logger.warning("Push queue full, dropping payload")

    def run(self):
        """Main push loop."""
        import requests

        self.running = True
        logger.info(f"Push client started, target: {self.cfg.controller_url}")

        while not self.stop_event.is_set():
            try:
                item = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if item is None:
                continue

            endpoint = f"{self.cfg.controller_url}/controller/api/ingest"
            headers = {'Content-Type': 'application/json'}
            if self.cfg.controller_api_key:
                headers['X-API-Key'] = self.cfg.controller_api_key

            body = {
                'agent_name': item['agent_name'],
                'scan_type': item['scan_type'],
                'interface': item['interface'],
                'payload': item['payload'],
                'received_at': item['received_at'],
            }

            try:
                response = requests.post(endpoint, json=body, headers=headers, timeout=5)
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP {response.status_code}")
                logger.debug(f"Pushed {item['scan_type']} data to controller")
            except Exception as e:
                item['attempts'] += 1
                if item['attempts'] < 3 and not self.stop_event.is_set():
                    try:
                        self.queue.put_nowait(item)
                    except queue.Full:
                        pass
                else:
                    logger.warning(f"Failed to push after {item['attempts']} attempts: {e}")
            finally:
                self.queue.task_done()

        self.running = False
        logger.info("Push client stopped")

    def stop(self):
        """Stop the push client."""
        self.stop_event.set()


# Global push client
push_client: ControllerPushClient | None = None


# =============================================================================
# Mode Manager - Uses Intercept's existing utilities and tools
# =============================================================================

class ModeManager:
    """
    Manages mode state using Intercept's existing infrastructure.

    This assumes Intercept (or its utilities) is installed on the agent host.
    The agent imports and uses the existing modules rather than reimplementing
    tool execution logic.
    """

    def __init__(self):
        self.running_modes: dict[str, dict] = {}
        self.data_snapshots: dict[str, list] = {}
        self.locks: dict[str, threading.Lock] = {}
        self._capabilities: dict | None = None
        # Process tracking per mode
        self.processes: dict[str, subprocess.Popen] = {}
        self.output_threads: dict[str, threading.Thread] = {}
        self.stop_events: dict[str, threading.Event] = {}
        # Data queues for each mode (for real-time collection)
        self.data_queues: dict[str, queue.Queue] = {}
        # WiFi-specific state
        self.wifi_networks: dict[str, dict] = {}
        self.wifi_clients: dict[str, dict] = {}
        # ADS-B specific state
        self.adsb_aircraft: dict[str, dict] = {}
        # Bluetooth specific state
        self.bluetooth_devices: dict[str, dict] = {}
        # Lazy-loaded Intercept utilities
        self._sdr_factory = None
        self._dependencies = None

    def _get_sdr_factory(self):
        """Lazy-load SDRFactory from Intercept's utils."""
        if self._sdr_factory is None:
            try:
                from utils.sdr import SDRFactory
                self._sdr_factory = SDRFactory
            except ImportError:
                logger.warning("SDRFactory not available - SDR features disabled")
        return self._sdr_factory

    def _get_dependencies(self):
        """Lazy-load dependencies module from Intercept's utils."""
        if self._dependencies is None:
            try:
                from utils import dependencies
                self._dependencies = dependencies
            except ImportError:
                logger.warning("Dependencies module not available")
        return self._dependencies

    def _check_tool(self, tool_name: str) -> bool:
        """Check if a tool is available using Intercept's dependency checker."""
        deps = self._get_dependencies()
        if deps and hasattr(deps, 'check_tool'):
            return deps.check_tool(tool_name)
        # Fallback to simple which check
        return shutil.which(tool_name) is not None

    def _get_tool_path(self, tool_name: str) -> str | None:
        """Get tool path using Intercept's dependency module."""
        deps = self._get_dependencies()
        if deps and hasattr(deps, 'get_tool_path'):
            return deps.get_tool_path(tool_name)
        return shutil.which(tool_name)

    def detect_capabilities(self) -> dict:
        """Detect available tools and hardware using Intercept's utilities."""
        if self._capabilities is not None:
            return self._capabilities

        capabilities = {
            'modes': {},
            'devices': [],
            'agent_version': AGENT_VERSION,
            'gps': gps_manager.is_running,
            'gps_position': gps_manager.position,
            'tool_details': {},  # Detailed tool status
        }

        # Use Intercept's comprehensive dependency checking if available
        if HAS_DEPENDENCIES_MODULE:
            try:
                dep_status = check_all_dependencies()
                # Map dependency status to mode availability
                mode_mapping = {
                    'pager': 'pager',
                    'sensor': 'sensor',
                    'aircraft': 'adsb',
                    'ais': 'ais',
                    'acars': 'acars',
                    'aprs': 'aprs',
                    'wifi': 'wifi',
                    'bluetooth': 'bluetooth',
                    'tscm': 'tscm',
                    'satellite': 'satellite',
                }
                for dep_mode, cap_mode in mode_mapping.items():
                    if dep_mode in dep_status:
                        mode_info = dep_status[dep_mode]
                        # Check if mode is enabled in config
                        if not config.modes_enabled.get(cap_mode, True):
                            capabilities['modes'][cap_mode] = False
                        else:
                            capabilities['modes'][cap_mode] = mode_info['ready']
                        # Store detailed tool info
                        capabilities['tool_details'][cap_mode] = {
                            'name': mode_info['name'],
                            'ready': mode_info['ready'],
                            'missing_required': mode_info['missing_required'],
                            'tools': mode_info['tools'],
                        }
                # Handle modes not in dependencies.py
                extra_modes = ['dsc', 'rtlamr', 'listening_post']
                extra_tools = {
                    'dsc': ['rtl_fm'],
                    'rtlamr': ['rtlamr'],
                    'listening_post': ['rtl_fm'],
                }
                for mode in extra_modes:
                    if not config.modes_enabled.get(mode, True):
                        capabilities['modes'][mode] = False
                    else:
                        tools = extra_tools.get(mode, [])
                        capabilities['modes'][mode] = all(
                            check_tool(tool) for tool in tools
                        ) if tools else True
            except Exception as e:
                logger.warning(f"Dependency check failed, using fallback: {e}")
                self._detect_capabilities_fallback(capabilities)
        else:
            self._detect_capabilities_fallback(capabilities)

        # Use Intercept's SDR detection
        sdr_factory = self._get_sdr_factory()
        if sdr_factory:
            try:
                devices = sdr_factory.detect_devices()
                capabilities['devices'] = [d.to_dict() for d in devices]
            except Exception as e:
                logger.warning(f"SDR device detection failed: {e}")

        self._capabilities = capabilities
        return capabilities

    def _detect_capabilities_fallback(self, capabilities: dict):
        """Fallback capability detection when dependencies module unavailable."""
        tool_checks = {
            'pager': ['rtl_fm', 'multimon-ng'],
            'sensor': ['rtl_433'],
            'adsb': ['dump1090'],
            'ais': ['AIS-catcher'],
            'acars': ['acarsdec'],
            'aprs': ['rtl_fm', 'direwolf'],
            'wifi': ['airmon-ng', 'airodump-ng'],
            'bluetooth': ['bluetoothctl'],
            'dsc': ['rtl_fm'],
            'rtlamr': ['rtlamr'],
            'satellite': [],
            'listening_post': ['rtl_fm'],
            'tscm': ['rtl_fm'],
        }

        for mode, tools in tool_checks.items():
            if not config.modes_enabled.get(mode, True):
                capabilities['modes'][mode] = False
                continue
            if not tools:
                capabilities['modes'][mode] = True
                continue
            if mode == 'adsb':
                capabilities['modes'][mode] = (
                    self._check_tool('dump1090') or
                    self._check_tool('dump1090-fa') or
                    self._check_tool('readsb')
                )
            else:
                capabilities['modes'][mode] = all(
                    self._check_tool(tool) for tool in tools
                )

    def get_status(self) -> dict:
        """Get overall agent status."""
        # Build running modes with device info for multi-SDR tracking
        running_modes_detail = {}
        for mode, info in self.running_modes.items():
            params = info.get('params', {})
            running_modes_detail[mode] = {
                'started_at': info.get('started_at'),
                'device': params.get('device', params.get('device_index', 0)),
            }

        status = {
            'running_modes': list(self.running_modes.keys()),
            'running_modes_detail': running_modes_detail,  # Include device info per mode
            'uptime': time.time() - _start_time,
            'push_enabled': config.push_enabled,
            'push_connected': push_client is not None and push_client.running,
            'gps': gps_manager.is_running,
        }
        # Include GPS position if available
        gps_pos = gps_manager.position
        if gps_pos:
            status['gps_position'] = gps_pos
        return status

    # Modes that use RTL-SDR devices
    SDR_MODES = {'adsb', 'sensor', 'pager', 'ais', 'acars', 'dsc', 'rtlamr', 'listening_post'}

    def get_sdr_in_use(self, device: int = 0) -> str | None:
        """Check if an SDR device is in use by another mode.

        Returns the mode name using the device, or None if available.
        """
        for mode, info in self.running_modes.items():
            if mode in self.SDR_MODES:
                mode_device = info.get('params', {}).get('device', 0)
                # Normalize to int for comparison
                try:
                    mode_device = int(mode_device)
                except (ValueError, TypeError):
                    mode_device = 0
                if mode_device == device:
                    return mode
        return None

    def start_mode(self, mode: str, params: dict) -> dict:
        """Start a mode with given parameters."""
        if mode in self.running_modes:
            return {'status': 'error', 'message': f'{mode} already running'}

        caps = self.detect_capabilities()
        if not caps['modes'].get(mode, False):
            return {'status': 'error', 'message': f'{mode} not available (missing tools)'}

        # Check SDR device conflicts for SDR-based modes
        if mode in self.SDR_MODES:
            device = params.get('device', 0)
            try:
                device = int(device)
            except (ValueError, TypeError):
                device = 0
            in_use_by = self.get_sdr_in_use(device)
            if in_use_by:
                return {
                    'status': 'error',
                    'message': f'SDR device {device} is in use by {in_use_by}. Stop {in_use_by} first or use a different device.'
                }

        # Initialize lock if needed
        if mode not in self.locks:
            self.locks[mode] = threading.Lock()

        with self.locks[mode]:
            try:
                # Mode-specific start logic
                result = self._start_mode_internal(mode, params)
                if result.get('status') == 'started':
                    self.running_modes[mode] = {
                        'started_at': datetime.now(timezone.utc).isoformat(),
                        'params': params,
                    }
                return result
            except Exception as e:
                logger.exception(f"Error starting {mode}")
                return {'status': 'error', 'message': str(e)}

    def stop_mode(self, mode: str) -> dict:
        """Stop a running mode."""
        if mode not in self.running_modes:
            return {'status': 'not_running'}

        if mode not in self.locks:
            self.locks[mode] = threading.Lock()

        with self.locks[mode]:
            try:
                result = self._stop_mode_internal(mode)
                if mode in self.running_modes:
                    del self.running_modes[mode]
                return result
            except Exception as e:
                logger.exception(f"Error stopping {mode}")
                return {'status': 'error', 'message': str(e)}

    def get_mode_status(self, mode: str) -> dict:
        """Get status of a specific mode."""
        if mode in self.running_modes:
            info = {
                'running': True,
                **self.running_modes[mode]
            }
            # Add mode-specific stats
            if mode == 'adsb':
                info['aircraft_count'] = len(self.adsb_aircraft)
            elif mode == 'wifi':
                info['network_count'] = len(self.wifi_networks)
                info['client_count'] = len(self.wifi_clients)
            elif mode == 'bluetooth':
                info['device_count'] = len(self.bluetooth_devices)
            elif mode == 'sensor':
                info['reading_count'] = len(self.data_snapshots.get(mode, []))
            elif mode == 'ais':
                info['vessel_count'] = len(getattr(self, 'ais_vessels', {}))
            elif mode == 'aprs':
                info['station_count'] = len(getattr(self, 'aprs_stations', {}))
            elif mode == 'pager':
                info['message_count'] = len(self.data_snapshots.get(mode, []))
            elif mode == 'acars':
                info['message_count'] = len(self.data_snapshots.get(mode, []))
            elif mode == 'rtlamr':
                info['reading_count'] = len(self.data_snapshots.get(mode, []))
            elif mode == 'tscm':
                info['anomaly_count'] = len(getattr(self, 'tscm_anomalies', []))
            elif mode == 'satellite':
                info['pass_count'] = len(self.data_snapshots.get(mode, []))
            elif mode == 'listening_post':
                info['signal_count'] = len(getattr(self, 'listening_post_activity', []))
                info['current_freq'] = getattr(self, 'listening_post_current_freq', 0)
            return info
        return {'running': False}

    def get_mode_data(self, mode: str) -> dict:
        """Get current data snapshot for a mode."""
        data = {
            'mode': mode,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }

        # Add GPS position
        gps_pos = gps_manager.position
        if gps_pos:
            data['agent_gps'] = gps_pos

        # Mode-specific data
        if mode == 'adsb':
            data['data'] = list(self.adsb_aircraft.values())
        elif mode == 'wifi':
            data['data'] = {
                'networks': list(self.wifi_networks.values()),
                'clients': list(self.wifi_clients.values()),
            }
        elif mode == 'bluetooth':
            data['data'] = list(self.bluetooth_devices.values())
        elif mode == 'ais':
            data['data'] = list(getattr(self, 'ais_vessels', {}).values())
        elif mode == 'aprs':
            data['data'] = list(getattr(self, 'aprs_stations', {}).values())
        elif mode == 'tscm':
            data['data'] = {
                'anomalies': getattr(self, 'tscm_anomalies', []),
                'baseline': getattr(self, 'tscm_baseline', {}),
            }
        elif mode == 'listening_post':
            data['data'] = {
                'activity': getattr(self, 'listening_post_activity', []),
                'current_freq': getattr(self, 'listening_post_current_freq', 0),
            }
        elif mode == 'pager':
            # Return recent pager messages
            messages = self.data_snapshots.get(mode, [])
            data['data'] = {
                'messages': messages[-50:] if len(messages) > 50 else messages,
                'total_count': len(messages),
            }
        else:
            data['data'] = self.data_snapshots.get(mode, [])

        return data

    # =========================================================================
    # Mode-specific implementations
    # =========================================================================

    def _start_mode_internal(self, mode: str, params: dict) -> dict:
        """Internal mode start - dispatches to mode-specific handlers."""
        logger.info(f"Starting mode {mode} with params: {params}")

        # Initialize data structures
        self.data_snapshots[mode] = []
        self.data_queues[mode] = queue.Queue(maxsize=500)
        self.stop_events[mode] = threading.Event()

        # Dispatch to mode-specific handler
        handlers = {
            'sensor': self._start_sensor,
            'adsb': self._start_adsb,
            'wifi': self._start_wifi,
            'bluetooth': self._start_bluetooth,
            'pager': self._start_pager,
            'ais': self._start_ais,
            'acars': self._start_acars,
            'aprs': self._start_aprs,
            'rtlamr': self._start_rtlamr,
            'dsc': self._start_dsc,
            'tscm': self._start_tscm,
            'satellite': self._start_satellite,
            'listening_post': self._start_listening_post,
        }

        handler = handlers.get(mode)
        if handler:
            return handler(params)

        # Unknown mode
        logger.warning(f"Unknown mode: {mode}")
        return {'status': 'error', 'message': f'Unknown mode: {mode}'}

    def _stop_mode_internal(self, mode: str) -> dict:
        """Internal mode stop - terminates processes and cleans up."""
        logger.info(f"Stopping mode {mode}")

        # Signal stop
        if mode in self.stop_events:
            self.stop_events[mode].set()

        # Terminate process if running
        if mode in self.processes:
            proc = self.processes[mode]
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            del self.processes[mode]

        # Wait for output thread
        if mode in self.output_threads:
            thread = self.output_threads[mode]
            if thread and thread.is_alive():
                thread.join(timeout=2)
            del self.output_threads[mode]

        # Clean up
        if mode in self.stop_events:
            del self.stop_events[mode]
        if mode in self.data_queues:
            del self.data_queues[mode]
        if mode in self.data_snapshots:
            del self.data_snapshots[mode]

        # Mode-specific cleanup
        if mode == 'adsb':
            self.adsb_aircraft.clear()
        elif mode == 'wifi':
            self.wifi_networks.clear()
            self.wifi_clients.clear()
        elif mode == 'bluetooth':
            self.bluetooth_devices.clear()

        return {'status': 'stopped', 'mode': mode}

    # -------------------------------------------------------------------------
    # SENSOR MODE (rtl_433) - Uses Intercept's SDR abstraction
    # -------------------------------------------------------------------------

    def _start_sensor(self, params: dict) -> dict:
        """Start rtl_433 sensor mode using Intercept's SDR utilities."""
        freq = params.get('frequency', '433.92')
        gain = params.get('gain')
        device = params.get('device', '0')
        ppm = params.get('ppm')
        bias_t = params.get('bias_t', False)
        sdr_type_str = params.get('sdr_type', 'rtlsdr')

        # Try to use Intercept's SDR abstraction layer
        sdr_factory = self._get_sdr_factory()
        if sdr_factory:
            try:
                from utils.sdr import SDRType
                sdr_type = SDRType(sdr_type_str)
                sdr_device = sdr_factory.create_default_device(sdr_type, index=int(device))
                builder = sdr_factory.get_builder(sdr_type)

                # Use the builder to construct the command properly
                cmd = builder.build_ism_command(
                    device=sdr_device,
                    frequency_mhz=float(freq),
                    gain=float(gain) if gain else None,
                    ppm=int(ppm) if ppm else None,
                    bias_t=bias_t
                )
                logger.info(f"Starting sensor (via SDR abstraction): {' '.join(cmd)}")

            except Exception as e:
                logger.warning(f"SDR abstraction failed, falling back to direct command: {e}")
                cmd = self._build_sensor_command_fallback(freq, gain, device, ppm)
        else:
            # Fallback: build command directly
            cmd = self._build_sensor_command_fallback(freq, gain, device, ppm)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.processes['sensor'] = proc

            # Wait briefly to verify process started successfully
            time.sleep(0.5)
            if proc.poll() is not None:
                stderr_output = proc.stderr.read().decode('utf-8', errors='replace')
                del self.processes['sensor']
                return {'status': 'error', 'message': f'rtl_433 failed to start: {stderr_output[:200]}'}

            # Start output reader thread
            thread = threading.Thread(
                target=self._sensor_output_reader,
                args=(proc,),
                daemon=True
            )
            thread.start()
            self.output_threads['sensor'] = thread

            return {
                'status': 'started',
                'mode': 'sensor',
                'command': ' '.join(cmd),
                'gps_enabled': gps_manager.is_running
            }

        except FileNotFoundError:
            return {'status': 'error', 'message': 'rtl_433 not found. Install via: apt install rtl-433'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _build_sensor_command_fallback(self, freq, gain, device, ppm) -> list:
        """Build rtl_433 command without SDR abstraction."""
        cmd = ['rtl_433', '-F', 'json']
        if freq:
            cmd.extend(['-f', f'{freq}M'])
        if gain and str(gain) != '0':
            cmd.extend(['-g', str(gain)])
        if device and str(device) != '0':
            cmd.extend(['-d', str(device)])
        if ppm and str(ppm) != '0':
            cmd.extend(['-p', str(ppm)])
        return cmd

    def _sensor_output_reader(self, proc: subprocess.Popen):
        """Read rtl_433 JSON output and collect data."""
        mode = 'sensor'
        stop_event = self.stop_events.get(mode)

        try:
            for line in iter(proc.stdout.readline, b''):
                if stop_event and stop_event.is_set():
                    break

                line = line.decode('utf-8', errors='replace').strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    data['type'] = 'sensor'
                    data['received_at'] = datetime.now(timezone.utc).isoformat()

                    # Add GPS if available
                    gps_pos = gps_manager.position
                    if gps_pos:
                        data['agent_gps'] = gps_pos

                    # Store in snapshot (keep last 100)
                    snapshots = self.data_snapshots.get(mode, [])
                    snapshots.append(data)
                    if len(snapshots) > 100:
                        snapshots = snapshots[-100:]
                    self.data_snapshots[mode] = snapshots

                    logger.debug(f"Sensor data: {data.get('model', 'Unknown')}")

                except json.JSONDecodeError:
                    pass  # Not JSON, ignore

        except Exception as e:
            logger.error(f"Sensor output reader error: {e}")
        finally:
            proc.wait()
            logger.info("Sensor output reader stopped")

    # -------------------------------------------------------------------------
    # ADS-B MODE (dump1090) - Uses Intercept's SDR abstraction
    # -------------------------------------------------------------------------

    def _start_adsb(self, params: dict) -> dict:
        """Start dump1090 ADS-B mode using Intercept's utilities."""
        gain = params.get('gain', '40')
        device = params.get('device', '0')
        bias_t = params.get('bias_t', False)
        sdr_type_str = params.get('sdr_type', 'rtlsdr')
        remote_sbs_host = params.get('remote_sbs_host')
        remote_sbs_port = params.get('remote_sbs_port', 30003)

        # If remote SBS host provided, just connect to it
        if remote_sbs_host:
            return self._start_adsb_sbs_connection(remote_sbs_host, remote_sbs_port)

        # Check if dump1090 already running on port 30003
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            result = sock.connect_ex(('localhost', 30003))
            sock.close()
            if result == 0:
                logger.info("dump1090 already running, connecting to SBS port")
                return self._start_adsb_sbs_connection('localhost', 30003)
        except Exception:
            pass

        # Try using Intercept's SDR abstraction for building the command
        sdr_factory = self._get_sdr_factory()
        cmd = None

        if sdr_factory:
            try:
                from utils.sdr import SDRType
                sdr_type = SDRType(sdr_type_str)
                sdr_device = sdr_factory.create_default_device(sdr_type, index=int(device))
                builder = sdr_factory.get_builder(sdr_type)

                # Use the builder to construct dump1090 command
                cmd = builder.build_adsb_command(
                    device=sdr_device,
                    gain=float(gain) if gain else None,
                    bias_t=bias_t
                )
                logger.info(f"Starting ADS-B (via SDR abstraction): {' '.join(cmd)}")

            except Exception as e:
                logger.warning(f"SDR abstraction failed for ADS-B: {e}")

        if not cmd:
            # Fallback: find dump1090 manually and build command
            dump1090_path = self._find_dump1090()
            if not dump1090_path:
                return {'status': 'error', 'message': 'dump1090 not found. Install via: apt install dump1090-fa'}

            cmd = [dump1090_path, '--net', '--quiet']
            if gain:
                cmd.extend(['--gain', str(gain)])
            if device and str(device) != '0':
                cmd.extend(['--device-index', str(device)])

        logger.info(f"Starting dump1090: {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True
            )
            self.processes['adsb'] = proc

            # Wait for dump1090 to start
            time.sleep(2)

            if proc.poll() is not None:
                stderr = proc.stderr.read().decode('utf-8', errors='ignore')
                return {'status': 'error', 'message': f'dump1090 failed to start: {stderr[:200]}'}

            # Connect to SBS port
            return self._start_adsb_sbs_connection('localhost', 30003)

        except FileNotFoundError:
            return {'status': 'error', 'message': 'dump1090 not found'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _find_dump1090(self) -> str | None:
        """Find dump1090 binary using Intercept's dependency module or fallback."""
        # Try Intercept's tool path finder first
        for name in ['dump1090', 'dump1090-fa', 'dump1090-mutability', 'readsb']:
            path = self._get_tool_path(name)
            if path:
                return path

        # Fallback: check common installation paths
        common_paths = [
            '/opt/homebrew/bin/dump1090',
            '/opt/homebrew/bin/dump1090-fa',
            '/usr/local/bin/dump1090',
            '/usr/local/bin/dump1090-fa',
            '/usr/bin/dump1090',
            '/usr/bin/dump1090-fa',
        ]
        for path in common_paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None

    def _start_adsb_sbs_connection(self, host: str, port: int) -> dict:
        """Connect to SBS port and start parsing."""
        thread = threading.Thread(
            target=self._adsb_sbs_reader,
            args=(host, port),
            daemon=True
        )
        thread.start()
        self.output_threads['adsb'] = thread

        return {
            'status': 'started',
            'mode': 'adsb',
            'sbs_source': f'{host}:{port}',
            'gps_enabled': gps_manager.is_running
        }

    def _adsb_sbs_reader(self, host: str, port: int):
        """Read and parse SBS data from dump1090."""
        mode = 'adsb'
        stop_event = self.stop_events.get(mode)
        retry_count = 0
        max_retries = 5

        while not (stop_event and stop_event.is_set()):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((host, port))
                logger.info(f"Connected to SBS at {host}:{port}")
                retry_count = 0

                buffer = ""
                sock.settimeout(1.0)

                while not (stop_event and stop_event.is_set()):
                    try:
                        data = sock.recv(4096).decode('utf-8', errors='ignore')
                        if not data:
                            break
                        buffer += data

                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            self._parse_sbs_line(line.strip())

                    except socket.timeout:
                        continue

                sock.close()

            except Exception as e:
                logger.warning(f"SBS connection error: {e}")
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error("Max SBS retries reached, stopping")
                    break
                time.sleep(2)

        logger.info("ADS-B SBS reader stopped")

    def _parse_sbs_line(self, line: str):
        """Parse SBS format line and update aircraft dict."""
        if not line:
            return

        parts = line.split(',')
        if len(parts) < 11 or parts[0] != 'MSG':
            return

        msg_type = parts[1]
        icao = parts[4].upper()
        if not icao:
            return

        aircraft = self.adsb_aircraft.get(icao) or {'icao': icao}
        aircraft['last_seen'] = datetime.now(timezone.utc).isoformat()

        # Add GPS
        gps_pos = gps_manager.position
        if gps_pos:
            aircraft['agent_gps'] = gps_pos

        try:
            if msg_type == '1' and len(parts) > 10:
                callsign = parts[10].strip()
                if callsign:
                    aircraft['callsign'] = callsign

            elif msg_type == '3' and len(parts) > 15:
                if parts[11]:
                    aircraft['altitude'] = int(float(parts[11]))
                if parts[14] and parts[15]:
                    aircraft['lat'] = float(parts[14])
                    aircraft['lon'] = float(parts[15])

            elif msg_type == '4' and len(parts) > 16:
                if parts[12]:
                    aircraft['speed'] = int(float(parts[12]))
                if parts[13]:
                    aircraft['heading'] = int(float(parts[13]))
                if parts[16]:
                    aircraft['vertical_rate'] = int(float(parts[16]))

            elif msg_type == '5' and len(parts) > 11:
                if parts[10]:
                    callsign = parts[10].strip()
                    if callsign:
                        aircraft['callsign'] = callsign
                if parts[11]:
                    aircraft['altitude'] = int(float(parts[11]))

            elif msg_type == '6' and len(parts) > 17:
                if parts[17]:
                    aircraft['squawk'] = parts[17]

        except (ValueError, IndexError):
            pass

        self.adsb_aircraft[icao] = aircraft

    # -------------------------------------------------------------------------
    # WIFI MODE (airodump-ng) - Uses Intercept's utilities
    # -------------------------------------------------------------------------

    def _start_wifi(self, params: dict) -> dict:
        """Start WiFi scanning using Intercept's existing infrastructure."""
        interface = params.get('interface')
        channel = params.get('channel')
        band = params.get('band', 'abg')
        scan_type = params.get('scan_type', 'deep')

        # Handle quick scan - returns results synchronously
        if scan_type == 'quick':
            return self._wifi_quick_scan(interface)

        # Deep scan requires interface
        if not interface:
            return {'status': 'error', 'message': 'WiFi interface required'}

        # Use Intercept's validation if available
        try:
            from utils.validation import validate_network_interface
            interface = validate_network_interface(interface)
        except ImportError:
            # Fallback: basic validation
            if not os.path.exists(f'/sys/class/net/{interface}'):
                return {'status': 'error', 'message': f'Interface {interface} not found'}
        except ValueError as e:
            return {'status': 'error', 'message': str(e)}

        # Clean up old output files
        csv_path = '/tmp/intercept_agent_wifi'
        for f in [f'{csv_path}-01.csv', f'{csv_path}-01.cap', f'{csv_path}-01.gps']:
            try:
                os.remove(f)
            except OSError:
                pass

        # Get airodump-ng path using Intercept's dependency module
        airodump_path = self._get_tool_path('airodump-ng')
        if not airodump_path:
            return {'status': 'error', 'message': 'airodump-ng not found. Install aircrack-ng suite.'}

        # Determine output formats - include gps if gpsd is running
        output_formats = 'csv'
        if gps_manager.is_running:
            output_formats = 'csv,gps'  # GPS file for accurate coordinates

        cmd = [
            airodump_path,
            '-w', csv_path,
            '--output-format', output_formats,
            '--band', band,
        ]

        # Add GPS support if gpsd is running
        # This writes GPS coordinates to a separate .gps file
        if gps_manager.is_running:
            cmd.append('--gpsd')
            logger.info("GPS enabled for airodump-ng captures (gps file output)")

        if channel:
            cmd.extend(['-c', str(channel)])

        # Interface must be last argument
        cmd.append(interface)

        logger.info(f"Starting airodump-ng: {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.processes['wifi'] = proc

            time.sleep(0.5)
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode('utf-8', errors='ignore')
                return {'status': 'error', 'message': f'airodump-ng failed: {stderr[:200]}'}

            # Start CSV parser thread
            thread = threading.Thread(
                target=self._wifi_csv_reader,
                args=(csv_path,),
                daemon=True
            )
            thread.start()
            self.output_threads['wifi'] = thread

            return {
                'status': 'started',
                'mode': 'wifi',
                'interface': interface,
                'gps_enabled': gps_manager.is_running
            }

        except FileNotFoundError:
            return {'status': 'error', 'message': 'airodump-ng not found'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _wifi_quick_scan(self, interface: str | None) -> dict:
        """
        Perform a quick one-shot WiFi scan using system tools.

        Uses nmcli, iw, or iwlist (no monitor mode required).
        Returns results synchronously.
        """
        try:
            from utils.wifi.scanner import get_wifi_scanner
            scanner = get_wifi_scanner()
            result = scanner.quick_scan(interface=interface, timeout=15.0)

            if result.error:
                return {
                    'status': 'error',
                    'message': result.error,
                    'warnings': result.warnings
                }

            # Convert access points to dict format
            networks = []
            gps_position = gps_manager.position
            for ap in result.access_points:
                net = ap.to_dict()
                # Add agent GPS if available
                if gps_position:
                    net['agent_gps'] = gps_position
                networks.append(net)

            return {
                'status': 'success',
                'scan_type': 'quick',
                'access_points': networks,
                'networks': networks,  # Alias for compatibility
                'network_count': len(networks),
                'warnings': result.warnings,
                'gps_enabled': gps_manager.is_running,
                'agent_gps': gps_position
            }

        except ImportError:
            # Fallback: simple nmcli scan
            return self._wifi_quick_scan_fallback(interface)
        except Exception as e:
            logger.exception("Quick WiFi scan failed")
            return {'status': 'error', 'message': str(e)}

    def _wifi_quick_scan_fallback(self, interface: str | None) -> dict:
        """Fallback quick scan using nmcli directly."""
        nmcli_path = shutil.which('nmcli')
        if not nmcli_path:
            return {'status': 'error', 'message': 'nmcli not found. Install NetworkManager.'}

        try:
            # Trigger rescan
            subprocess.run(
                [nmcli_path, 'device', 'wifi', 'rescan'],
                capture_output=True,
                timeout=5
            )

            # Get results
            cmd = [nmcli_path, '-t', '-f', 'BSSID,SSID,CHAN,SIGNAL,SECURITY', 'device', 'wifi', 'list']
            if interface:
                cmd.extend(['ifname', interface])

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

            if result.returncode != 0:
                return {'status': 'error', 'message': f'nmcli failed: {result.stderr}'}

            networks = []
            gps_position = gps_manager.position
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = line.split(':')
                if len(parts) >= 5:
                    net = {
                        'bssid': parts[0],
                        'essid': parts[1],
                        'channel': int(parts[2]) if parts[2].isdigit() else 0,
                        'signal': int(parts[3]) if parts[3].isdigit() else 0,
                        'rssi_current': int(parts[3]) - 100 if parts[3].isdigit() else -100,  # Convert % to dBm approx
                        'security': parts[4],
                    }
                    if gps_position:
                        net['agent_gps'] = gps_position
                    networks.append(net)

            return {
                'status': 'success',
                'scan_type': 'quick',
                'access_points': networks,
                'networks': networks,
                'network_count': len(networks),
                'warnings': ['Using fallback nmcli scanner'],
                'gps_enabled': gps_manager.is_running,
                'agent_gps': gps_position
            }

        except subprocess.TimeoutExpired:
            return {'status': 'error', 'message': 'nmcli scan timed out'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _wifi_csv_reader(self, csv_path: str):
        """Periodically parse airodump-ng CSV and GPS output."""
        mode = 'wifi'
        stop_event = self.stop_events.get(mode)
        csv_file = csv_path + '-01.csv'
        gps_file = csv_path + '-01.gps'

        while not (stop_event and stop_event.is_set()):
            if os.path.exists(csv_file):
                try:
                    # Parse GPS file for accurate coordinates (if available)
                    gps_data = self._parse_airodump_gps(gps_file) if os.path.exists(gps_file) else None

                    networks, clients = self._parse_airodump_csv(csv_file, gps_data)
                    self.wifi_networks = networks
                    self.wifi_clients = clients
                except Exception as e:
                    logger.error(f"CSV parse error: {e}")

            time.sleep(2)

        logger.info("WiFi CSV reader stopped")

    def _parse_airodump_gps(self, gps_path: str) -> dict | None:
        """
        Parse airodump-ng GPS file for accurate coordinates.

        Format:
        <?xml version="1.0" encoding="ISO-8859-1"?>
        <!DOCTYPE gps-run SYSTEM "...">
        <gps-run gps-version="1">
        <gps-point lat="LAT" lon="LON" alt="ALT" spd="SPD" time="TIME"/>
        ...
        </gps-run>

        Returns the most recent GPS point.
        """
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(gps_path)
            root = tree.getroot()

            # Get the last (most recent) GPS point
            gps_points = root.findall('.//gps-point')
            if gps_points:
                last_point = gps_points[-1]
                lat = last_point.get('lat')
                lon = last_point.get('lon')
                alt = last_point.get('alt')

                if lat and lon:
                    return {
                        'lat': float(lat),
                        'lon': float(lon),
                        'altitude': float(alt) if alt else None,
                        'source': 'airodump_gps'
                    }
        except Exception as e:
            logger.debug(f"GPS file parse error: {e}")

        return None

    def _parse_airodump_csv(self, csv_path: str, gps_data: dict | None = None) -> tuple[dict, dict]:
        """Parse airodump-ng CSV file using Intercept's existing parser."""
        networks = {}
        clients = {}

        try:
            # Use Intercept's robust airodump parser (handles edge cases, proper CSV parsing)
            from utils.wifi.parsers.airodump import parse_airodump_csv
            network_obs, client_list = parse_airodump_csv(csv_path)

            # Convert WiFiObservation objects to dicts for agent format
            for obs in network_obs:
                networks[obs.bssid] = {
                    'bssid': obs.bssid,
                    'essid': obs.essid or 'Hidden',
                    'channel': obs.channel,
                    'frequency_mhz': obs.frequency_mhz,
                    'signal': obs.rssi,
                    'security': obs.security,
                    'cipher': obs.cipher,
                    'auth': obs.auth,
                    'vendor': obs.vendor,
                    'beacon_count': obs.beacon_count,
                    'data_count': obs.data_count,
                    'band': obs.band,
                    'last_seen': datetime.now(timezone.utc).isoformat(),
                }

            # Convert client dicts (already in dict format from parser)
            for client in client_list:
                mac = client.get('mac')
                if mac:
                    clients[mac] = {
                        'mac': mac,
                        'signal': client.get('rssi'),
                        'bssid': client.get('bssid'),
                        'probes': ','.join(client.get('probed_essids', [])),
                        'packets': client.get('packets', 0),
                        'last_seen': datetime.now(timezone.utc).isoformat(),
                    }

            logger.debug(f"Parsed {len(networks)} networks, {len(clients)} clients")

        except ImportError:
            logger.warning("Intercept WiFi parser not available, using fallback")
            # Fallback: simple parsing if running standalone
            try:
                with open(csv_path, 'r', errors='replace') as f:
                    content = f.read()
                for section in content.split('\n\n'):
                    lines = section.strip().split('\n')
                    if not lines:
                        continue
                    header = lines[0]
                    if 'BSSID' in header and 'ESSID' in header:
                        for line in lines[1:]:
                            parts = [p.strip() for p in line.split(',')]
                            if len(parts) >= 14 and ':' in parts[0]:
                                networks[parts[0]] = {
                                    'bssid': parts[0],
                                    'channel': int(parts[3]) if parts[3].lstrip('-').isdigit() else None,
                                    'signal': int(parts[8]) if parts[8].lstrip('-').isdigit() else None,
                                    'security': parts[5],
                                    'essid': parts[13] or 'Hidden',
                                    'last_seen': datetime.now(timezone.utc).isoformat(),
                                }
                    elif 'Station MAC' in header:
                        for line in lines[1:]:
                            parts = [p.strip() for p in line.split(',')]
                            if len(parts) >= 6 and ':' in parts[0]:
                                clients[parts[0]] = {
                                    'mac': parts[0],
                                    'signal': int(parts[3]) if parts[3].lstrip('-').isdigit() else None,
                                    'bssid': parts[5] if ':' in parts[5] else None,
                                    'probes': parts[6] if len(parts) > 6 else '',
                                    'last_seen': datetime.now(timezone.utc).isoformat(),
                                }
            except Exception as e:
                logger.error(f"Fallback CSV parse error: {e}")

        except Exception as e:
            logger.error(f"Error parsing CSV: {e}")

        # Add GPS to all entries
        # Prefer GPS from airodump's .gps file (more accurate timestamp)
        # Fall back to GPSManager if no .gps file data
        if gps_data:
            # Use GPS coordinates from airodump's GPS file
            gps_pos = {
                'lat': gps_data['lat'],
                'lon': gps_data['lon'],
                'altitude': gps_data.get('altitude'),
                'source': 'airodump_gps',  # Mark as from airodump GPS file
            }
            logger.debug(f"Using airodump GPS: {gps_data['lat']:.6f}, {gps_data['lon']:.6f}")
        else:
            # Fall back to GPSManager position
            gps_pos = gps_manager.position

        if gps_pos:
            for net in networks.values():
                net['agent_gps'] = gps_pos
            for client in clients.values():
                client['agent_gps'] = gps_pos

        return networks, clients

    # -------------------------------------------------------------------------
    # BLUETOOTH MODE
    # -------------------------------------------------------------------------

    def _start_bluetooth(self, params: dict) -> dict:
        """Start Bluetooth scanning."""
        adapter = params.get('adapter', 'hci0')

        # Check for bluetoothctl
        if not shutil.which('bluetoothctl'):
            return {'status': 'error', 'message': 'bluetoothctl not found'}

        # Start scan thread
        thread = threading.Thread(
            target=self._bluetooth_scanner,
            args=(adapter,),
            daemon=True
        )
        thread.start()
        self.output_threads['bluetooth'] = thread

        return {
            'status': 'started',
            'mode': 'bluetooth',
            'adapter': adapter,
            'gps_enabled': gps_manager.is_running
        }

    def _bluetooth_scanner(self, adapter: str):
        """Scan for Bluetooth devices using bluetoothctl."""
        mode = 'bluetooth'
        stop_event = self.stop_events.get(mode)

        try:
            # Start bluetoothctl scan
            proc = subprocess.Popen(
                ['bluetoothctl'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.processes['bluetooth'] = proc

            # Enable scanning
            proc.stdin.write(b'scan on\n')
            proc.stdin.flush()

            while not (stop_event and stop_event.is_set()):
                line = proc.stdout.readline()
                if not line:
                    break

                line = line.decode('utf-8', errors='replace').strip()

                # Parse device discovery lines
                # Format: [NEW] Device XX:XX:XX:XX:XX:XX DeviceName
                # Format: [CHG] Device XX:XX:XX:XX:XX:XX RSSI: -XX
                if 'Device' in line:
                    self._parse_bluetooth_line(line)

                time.sleep(0.1)

            # Stop scanning
            proc.stdin.write(b'scan off\n')
            proc.stdin.write(b'exit\n')
            proc.stdin.flush()
            proc.wait(timeout=2)

        except Exception as e:
            logger.error(f"Bluetooth scanner error: {e}")
        finally:
            logger.info("Bluetooth scanner stopped")

    def _parse_bluetooth_line(self, line: str):
        """Parse bluetoothctl output line."""
        import re

        # Match device address (MAC)
        mac_match = re.search(r'([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})', line)
        if not mac_match:
            return

        mac = mac_match.group(1).upper()
        device = self.bluetooth_devices.get(mac) or {'mac': mac}
        device['last_seen'] = datetime.now(timezone.utc).isoformat()

        # Extract name
        if '[NEW]' in line or '[CHG]' in line and 'Name:' not in line:
            # Try to get name after MAC
            parts = line.split(mac)
            if len(parts) > 1:
                name = parts[1].strip()
                if name and not name.startswith('RSSI') and not name.startswith('ManufacturerData'):
                    device['name'] = name

        # Extract RSSI
        rssi_match = re.search(r'RSSI:\s*(-?\d+)', line)
        if rssi_match:
            device['rssi'] = int(rssi_match.group(1))

        # Add GPS
        gps_pos = gps_manager.position
        if gps_pos:
            device['agent_gps'] = gps_pos

        self.bluetooth_devices[mac] = device

    # -------------------------------------------------------------------------
    # PAGER MODE (rtl_fm | multimon-ng)
    # -------------------------------------------------------------------------

    def _start_pager(self, params: dict) -> dict:
        """Start POCSAG/FLEX pager decoding using rtl_fm | multimon-ng."""
        freq = params.get('frequency', '929.6125')
        gain = params.get('gain', '0')
        device = params.get('device', '0')
        ppm = params.get('ppm', '0')
        squelch = params.get('squelch', '0')
        protocols = params.get('protocols', ['POCSAG512', 'POCSAG1200', 'POCSAG2400', 'FLEX'])

        # Validate tools
        rtl_fm_path = self._get_tool_path('rtl_fm')
        multimon_path = self._get_tool_path('multimon-ng')
        if not rtl_fm_path:
            return {'status': 'error', 'message': 'rtl_fm not found. Install rtl-sdr.'}
        if not multimon_path:
            return {'status': 'error', 'message': 'multimon-ng not found. Install multimon-ng.'}

        # Build rtl_fm command for FM demodulation at 22050 Hz
        rtl_fm_cmd = [
            rtl_fm_path,
            '-f', f'{freq}M',
            '-s', '22050',
            '-g', str(gain),
            '-d', str(device),
        ]
        if ppm and str(ppm) != '0':
            rtl_fm_cmd.extend(['-p', str(ppm)])
        if squelch and str(squelch) != '0':
            rtl_fm_cmd.extend(['-l', str(squelch)])

        # Build multimon-ng command
        multimon_cmd = [multimon_path, '-t', 'raw', '-a']
        for proto in protocols:
            if proto in ['POCSAG512', 'POCSAG1200', 'POCSAG2400', 'FLEX']:
                multimon_cmd.extend(['-a', proto])
        multimon_cmd.append('-')

        logger.info(f"Starting pager: {' '.join(rtl_fm_cmd)} | {' '.join(multimon_cmd)}")

        try:
            # Start rtl_fm process
            rtl_fm_proc = subprocess.Popen(
                rtl_fm_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Pipe to multimon-ng
            multimon_proc = subprocess.Popen(
                multimon_cmd,
                stdin=rtl_fm_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            rtl_fm_proc.stdout.close()  # Allow SIGPIPE

            # Store both processes
            self.processes['pager'] = multimon_proc
            self.processes['pager_rtl'] = rtl_fm_proc

            # Wait briefly to verify processes started successfully
            time.sleep(0.5)
            if rtl_fm_proc.poll() is not None:
                stderr_output = rtl_fm_proc.stderr.read().decode('utf-8', errors='replace')
                multimon_proc.terminate()
                del self.processes['pager']
                del self.processes['pager_rtl']
                return {'status': 'error', 'message': f'rtl_fm failed to start: {stderr_output[:200]}'}

            # Start output reader
            thread = threading.Thread(
                target=self._pager_output_reader,
                args=(multimon_proc,),
                daemon=True
            )
            thread.start()
            self.output_threads['pager'] = thread

            return {
                'status': 'started',
                'mode': 'pager',
                'frequency': freq,
                'protocols': protocols,
                'gps_enabled': gps_manager.is_running
            }

        except FileNotFoundError as e:
            return {'status': 'error', 'message': str(e)}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _pager_output_reader(self, proc: subprocess.Popen):
        """Read and parse multimon-ng output for pager messages."""
        mode = 'pager'
        stop_event = self.stop_events.get(mode)

        try:
            for line in iter(proc.stdout.readline, b''):
                if stop_event and stop_event.is_set():
                    break

                line = line.decode('utf-8', errors='replace').strip()
                if not line:
                    continue

                parsed = self._parse_pager_message(line)
                if parsed:
                    parsed['received_at'] = datetime.now(timezone.utc).isoformat()

                    gps_pos = gps_manager.position
                    if gps_pos:
                        parsed['agent_gps'] = gps_pos

                    snapshots = self.data_snapshots.get(mode, [])
                    snapshots.append(parsed)
                    if len(snapshots) > 200:
                        snapshots = snapshots[-200:]
                    self.data_snapshots[mode] = snapshots

                    logger.debug(f"Pager: {parsed.get('protocol')} addr={parsed.get('address')}")

        except Exception as e:
            logger.error(f"Pager reader error: {e}")
        finally:
            proc.wait()
            if 'pager_rtl' in self.processes:
                rtl_proc = self.processes['pager_rtl']
                if rtl_proc.poll() is None:
                    rtl_proc.terminate()
                del self.processes['pager_rtl']
            logger.info("Pager reader stopped")

    def _parse_pager_message(self, line: str) -> dict | None:
        """Parse multimon-ng output line for POCSAG/FLEX."""
        # POCSAG with message
        match = re.match(
            r'(POCSAG\d+):\s*Address:\s*(\d+)\s+Function:\s*(\d+)\s+(Alpha|Numeric):\s*(.*)',
            line
        )
        if match:
            return {
                'type': 'pager',
                'protocol': match.group(1),
                'address': match.group(2),
                'function': match.group(3),
                'msg_type': match.group(4),
                'message': match.group(5).strip() or '[No Message]'
            }

        # POCSAG address only (tone)
        match = re.match(
            r'(POCSAG\d+):\s*Address:\s*(\d+)\s+Function:\s*(\d+)\s*$',
            line
        )
        if match:
            return {
                'type': 'pager',
                'protocol': match.group(1),
                'address': match.group(2),
                'function': match.group(3),
                'msg_type': 'Tone',
                'message': '[Tone Only]'
            }

        # FLEX format
        match = re.match(r'FLEX[:\|]\s*(.+)', line)
        if match:
            return {
                'type': 'pager',
                'protocol': 'FLEX',
                'address': 'Unknown',
                'function': '',
                'msg_type': 'Unknown',
                'message': match.group(1).strip()
            }

        return None

    # -------------------------------------------------------------------------
    # AIS MODE (AIS-catcher)
    # -------------------------------------------------------------------------

    def _start_ais(self, params: dict) -> dict:
        """Start AIS vessel tracking using AIS-catcher."""
        gain = params.get('gain', '33')
        device = params.get('device', '0')
        bias_t = params.get('bias_t', False)

        # Find AIS-catcher
        ais_catcher = self._find_ais_catcher()
        if not ais_catcher:
            return {'status': 'error', 'message': 'AIS-catcher not found. Install from https://github.com/jvde-github/AIS-catcher'}

        # Initialize vessel dict
        if not hasattr(self, 'ais_vessels'):
            self.ais_vessels = {}
        self.ais_vessels.clear()

        # Build command - output JSON on TCP port 1234
        cmd = [
            ais_catcher,
            '-d', str(device),
            '-gr', f'TUNER={gain}',
            '-o', '4',  # JSON format
            '-N', '1234',  # TCP output on port 1234
        ]

        if bias_t:
            cmd.extend(['-gr', 'BIASTEE=on'])

        logger.info(f"Starting AIS-catcher: {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True
            )
            self.processes['ais'] = proc

            time.sleep(2)
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode('utf-8', errors='ignore')
                return {'status': 'error', 'message': f'AIS-catcher failed: {stderr[:200]}'}

            # Start TCP reader thread
            thread = threading.Thread(
                target=self._ais_tcp_reader,
                args=(1234,),
                daemon=True
            )
            thread.start()
            self.output_threads['ais'] = thread

            return {
                'status': 'started',
                'mode': 'ais',
                'tcp_port': 1234,
                'gps_enabled': gps_manager.is_running
            }

        except FileNotFoundError:
            return {'status': 'error', 'message': 'AIS-catcher not found'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _find_ais_catcher(self) -> str | None:
        """Find AIS-catcher binary."""
        for name in ['AIS-catcher', 'aiscatcher']:
            path = self._get_tool_path(name)
            if path:
                return path
        for path in ['/usr/local/bin/AIS-catcher', '/usr/bin/AIS-catcher', '/opt/homebrew/bin/AIS-catcher']:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None

    def _ais_tcp_reader(self, port: int):
        """Read JSON vessel data from AIS-catcher TCP port."""
        mode = 'ais'
        stop_event = self.stop_events.get(mode)
        retry_count = 0

        # Initialize vessel dict
        if not hasattr(self, 'ais_vessels'):
            self.ais_vessels = {}

        while not (stop_event and stop_event.is_set()):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect(('localhost', port))
                logger.info(f"Connected to AIS-catcher on port {port}")
                retry_count = 0

                buffer = ""
                sock.settimeout(1.0)

                while not (stop_event and stop_event.is_set()):
                    try:
                        data = sock.recv(4096).decode('utf-8', errors='ignore')
                        if not data:
                            break
                        buffer += data

                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            self._parse_ais_json(line.strip())

                    except socket.timeout:
                        continue

                sock.close()

            except Exception as e:
                retry_count += 1
                if retry_count >= 10:
                    logger.error("Max AIS retries reached")
                    break
                time.sleep(2)

        logger.info("AIS TCP reader stopped")

    def _parse_ais_json(self, line: str):
        """Parse AIS-catcher JSON output."""
        if not line:
            return

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return

        mmsi = msg.get('mmsi')
        if not mmsi:
            return

        mmsi = str(mmsi)
        vessel = self.ais_vessels.get(mmsi) or {'mmsi': mmsi}
        vessel['last_seen'] = datetime.now(timezone.utc).isoformat()

        # Position
        lat = msg.get('latitude') or msg.get('lat')
        lon = msg.get('longitude') or msg.get('lon')
        if lat is not None and lon is not None:
            try:
                lat, lon = float(lat), float(lon)
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    vessel['lat'] = lat
                    vessel['lon'] = lon
            except (ValueError, TypeError):
                pass

        # Speed and course
        for field, max_val in [('speed', 102.3), ('course', 360)]:
            if field in msg:
                try:
                    val = float(msg[field])
                    if val < max_val:
                        vessel[field] = round(val, 1)
                except (ValueError, TypeError):
                    pass

        if 'heading' in msg:
            try:
                heading = int(msg['heading'])
                if heading < 360:
                    vessel['heading'] = heading
            except (ValueError, TypeError):
                pass

        # Static data
        for field in ['name', 'callsign', 'destination', 'shiptype', 'ship_type']:
            if field in msg and msg[field]:
                key = 'ship_type' if field == 'shiptype' else field
                vessel[key] = str(msg[field]).strip()

        gps_pos = gps_manager.position
        if gps_pos:
            vessel['agent_gps'] = gps_pos

        self.ais_vessels[mmsi] = vessel

    # -------------------------------------------------------------------------
    # ACARS MODE (acarsdec)
    # -------------------------------------------------------------------------

    def _start_acars(self, params: dict) -> dict:
        """Start ACARS decoding using acarsdec."""
        gain = params.get('gain', '40')
        device = params.get('device', '0')
        frequencies = params.get('frequencies', ['131.550', '130.025', '129.125', '131.525', '131.725'])

        acarsdec_path = self._get_tool_path('acarsdec')
        if not acarsdec_path:
            return {'status': 'error', 'message': 'acarsdec not found. Install acarsdec.'}

        # Build command with JSON output
        cmd = [acarsdec_path, '-j', '-r', str(device), '-g', str(gain)]
        for freq in frequencies:
            cmd.append(freq)

        logger.info(f"Starting acarsdec: {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.processes['acars'] = proc

            thread = threading.Thread(
                target=self._acars_output_reader,
                args=(proc,),
                daemon=True
            )
            thread.start()
            self.output_threads['acars'] = thread

            # Wait briefly to verify process started successfully
            time.sleep(0.5)
            if proc.poll() is not None:
                # Process already exited - likely SDR busy or other error
                stderr_output = proc.stderr.read().decode('utf-8', errors='replace')
                del self.processes['acars']
                return {'status': 'error', 'message': f'acarsdec failed to start: {stderr_output[:200]}'}

            return {
                'status': 'started',
                'mode': 'acars',
                'frequencies': frequencies,
                'gps_enabled': gps_manager.is_running
            }

        except FileNotFoundError:
            return {'status': 'error', 'message': 'acarsdec not found'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _acars_output_reader(self, proc: subprocess.Popen):
        """Read acarsdec JSON output."""
        mode = 'acars'
        stop_event = self.stop_events.get(mode)

        try:
            for line in iter(proc.stdout.readline, b''):
                if stop_event and stop_event.is_set():
                    break

                line = line.decode('utf-8', errors='replace').strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                    msg['type'] = 'acars'
                    msg['received_at'] = datetime.now(timezone.utc).isoformat()

                    gps_pos = gps_manager.position
                    if gps_pos:
                        msg['agent_gps'] = gps_pos

                    snapshots = self.data_snapshots.get(mode, [])
                    snapshots.append(msg)
                    if len(snapshots) > 100:
                        snapshots = snapshots[-100:]
                    self.data_snapshots[mode] = snapshots

                    logger.debug(f"ACARS: {msg.get('tail', 'Unknown')}")

                except json.JSONDecodeError:
                    pass

        except Exception as e:
            logger.error(f"ACARS reader error: {e}")
        finally:
            proc.wait()
            logger.info("ACARS reader stopped")

    # -------------------------------------------------------------------------
    # APRS MODE (rtl_fm | direwolf)
    # -------------------------------------------------------------------------

    def _start_aprs(self, params: dict) -> dict:
        """Start APRS decoding using rtl_fm | direwolf."""
        freq = params.get('frequency', '144.390')  # North America APRS
        gain = params.get('gain', '40')
        device = params.get('device', '0')
        ppm = params.get('ppm', '0')

        rtl_fm_path = self._get_tool_path('rtl_fm')
        if not rtl_fm_path:
            return {'status': 'error', 'message': 'rtl_fm not found'}

        direwolf_path = self._get_tool_path('direwolf')
        multimon_path = self._get_tool_path('multimon-ng')
        decoder_path = direwolf_path or multimon_path

        if not decoder_path:
            return {'status': 'error', 'message': 'direwolf or multimon-ng not found'}

        # Initialize state
        if not hasattr(self, 'aprs_stations'):
            self.aprs_stations = {}
        self.aprs_stations.clear()

        # Build rtl_fm command for APRS (22050 Hz for AFSK 1200 baud)
        rtl_fm_cmd = [
            rtl_fm_path,
            '-f', f'{freq}M',
            '-s', '22050',
            '-g', str(gain),
            '-d', str(device),
            '-E', 'dc',
            '-A', 'fast',
        ]
        if ppm and str(ppm) != '0':
            rtl_fm_cmd.extend(['-p', str(ppm)])

        # Build decoder command
        if direwolf_path:
            dw_config = '/tmp/intercept_direwolf.conf'
            try:
                with open(dw_config, 'w') as f:
                    f.write("ADEVICE stdin null\nARATE 22050\nMODEM 1200\n")
            except Exception as e:
                return {'status': 'error', 'message': f'Failed to create direwolf config: {e}'}
            decoder_cmd = [direwolf_path, '-c', dw_config, '-r', '22050', '-t', '0', '-']
        else:
            decoder_cmd = [multimon_path, '-t', 'raw', '-a', 'AFSK1200', '-']

        logger.info(f"Starting APRS: {' '.join(rtl_fm_cmd)} | {' '.join(decoder_cmd)}")

        try:
            rtl_fm_proc = subprocess.Popen(
                rtl_fm_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            decoder_proc = subprocess.Popen(
                decoder_cmd,
                stdin=rtl_fm_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            rtl_fm_proc.stdout.close()

            self.processes['aprs'] = decoder_proc
            self.processes['aprs_rtl'] = rtl_fm_proc

            # Wait briefly to verify processes started successfully
            time.sleep(0.5)
            if rtl_fm_proc.poll() is not None:
                stderr_output = rtl_fm_proc.stderr.read().decode('utf-8', errors='replace')
                decoder_proc.terminate()
                del self.processes['aprs']
                del self.processes['aprs_rtl']
                return {'status': 'error', 'message': f'rtl_fm failed to start: {stderr_output[:200]}'}

            thread = threading.Thread(
                target=self._aprs_output_reader,
                args=(decoder_proc, direwolf_path is not None),
                daemon=True
            )
            thread.start()
            self.output_threads['aprs'] = thread

            return {
                'status': 'started',
                'mode': 'aprs',
                'frequency': freq,
                'decoder': 'direwolf' if direwolf_path else 'multimon-ng',
                'gps_enabled': gps_manager.is_running
            }

        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _aprs_output_reader(self, proc: subprocess.Popen, is_direwolf: bool):
        """Read and parse APRS packets."""
        mode = 'aprs'
        stop_event = self.stop_events.get(mode)

        try:
            for line in iter(proc.stdout.readline, b''):
                if stop_event and stop_event.is_set():
                    break

                line = line.decode('utf-8', errors='replace').strip()
                if not line:
                    continue

                parsed = self._parse_aprs_packet(line)
                if parsed:
                    parsed['received_at'] = datetime.now(timezone.utc).isoformat()

                    gps_pos = gps_manager.position
                    if gps_pos:
                        parsed['agent_gps'] = gps_pos

                    callsign = parsed.get('callsign')
                    if callsign:
                        self.aprs_stations[callsign] = parsed

                    snapshots = self.data_snapshots.get(mode, [])
                    snapshots.append(parsed)
                    if len(snapshots) > 100:
                        snapshots = snapshots[-100:]
                    self.data_snapshots[mode] = snapshots

                    logger.debug(f"APRS: {callsign}")

        except Exception as e:
            logger.error(f"APRS reader error: {e}")
        finally:
            proc.wait()
            if 'aprs_rtl' in self.processes:
                rtl_proc = self.processes['aprs_rtl']
                if rtl_proc.poll() is None:
                    rtl_proc.terminate()
                del self.processes['aprs_rtl']
            logger.info("APRS reader stopped")

    def _parse_aprs_packet(self, line: str) -> dict | None:
        """Parse APRS packet from direwolf or multimon-ng."""
        match = re.match(r'([A-Z0-9-]+)>([^:]+):(.+)', line)
        if not match:
            return None

        callsign = match.group(1)
        path = match.group(2)
        data = match.group(3)

        packet = {
            'type': 'aprs',
            'callsign': callsign,
            'path': path,
            'raw': data,
        }

        # Try to extract position
        pos_match = re.search(r'[!=/@](\d{4}\.\d{2})([NS])[/\\](\d{5}\.\d{2})([EW])', data)
        if pos_match:
            lat = float(pos_match.group(1)[:2]) + float(pos_match.group(1)[2:]) / 60
            if pos_match.group(2) == 'S':
                lat = -lat
            lon = float(pos_match.group(3)[:3]) + float(pos_match.group(3)[3:]) / 60
            if pos_match.group(4) == 'W':
                lon = -lon
            packet['lat'] = round(lat, 6)
            packet['lon'] = round(lon, 6)

        return packet

    # -------------------------------------------------------------------------
    # RTLAMR MODE (rtl_tcp + rtlamr)
    # -------------------------------------------------------------------------

    def _start_rtlamr(self, params: dict) -> dict:
        """Start utility meter reading using rtl_tcp + rtlamr."""
        freq = params.get('frequency', '912.0')
        device = params.get('device', '0')
        gain = params.get('gain', '40')
        msg_type = params.get('msgtype', 'scm')
        filter_id = params.get('filterid')

        rtl_tcp_path = self._get_tool_path('rtl_tcp')
        rtlamr_path = self._get_tool_path('rtlamr')

        if not rtl_tcp_path:
            return {'status': 'error', 'message': 'rtl_tcp not found. Install rtl-sdr.'}
        if not rtlamr_path:
            return {'status': 'error', 'message': 'rtlamr not found. Install from https://github.com/bemasher/rtlamr'}

        # Start rtl_tcp server
        rtl_tcp_cmd = [rtl_tcp_path, '-a', '127.0.0.1', '-p', '1234', '-d', str(device)]
        if gain:
            rtl_tcp_cmd.extend(['-g', str(gain)])

        logger.info(f"Starting rtl_tcp: {' '.join(rtl_tcp_cmd)}")

        try:
            rtl_tcp_proc = subprocess.Popen(
                rtl_tcp_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.processes['rtlamr_tcp'] = rtl_tcp_proc

            time.sleep(2)
            if rtl_tcp_proc.poll() is not None:
                stderr = rtl_tcp_proc.stderr.read().decode('utf-8', errors='ignore')
                return {'status': 'error', 'message': f'rtl_tcp failed: {stderr[:200]}'}

            # Build rtlamr command
            rtlamr_cmd = [
                rtlamr_path,
                '-server=127.0.0.1:1234',
                f'-msgtype={msg_type}',
                '-format=json',
                f'-centerfreq={int(float(freq) * 1e6)}',
                '-unique=true',
            ]
            if filter_id:
                rtlamr_cmd.append(f'-filterid={filter_id}')

            logger.info(f"Starting rtlamr: {' '.join(rtlamr_cmd)}")

            rtlamr_proc = subprocess.Popen(
                rtlamr_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.processes['rtlamr'] = rtlamr_proc

            thread = threading.Thread(
                target=self._rtlamr_output_reader,
                args=(rtlamr_proc,),
                daemon=True
            )
            thread.start()
            self.output_threads['rtlamr'] = thread

            return {
                'status': 'started',
                'mode': 'rtlamr',
                'frequency': freq,
                'msgtype': msg_type,
                'gps_enabled': gps_manager.is_running
            }

        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _rtlamr_output_reader(self, proc: subprocess.Popen):
        """Read rtlamr JSON output."""
        mode = 'rtlamr'
        stop_event = self.stop_events.get(mode)

        try:
            for line in iter(proc.stdout.readline, b''):
                if stop_event and stop_event.is_set():
                    break

                line = line.decode('utf-8', errors='replace').strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                    msg['type'] = 'rtlamr'
                    msg['received_at'] = datetime.now(timezone.utc).isoformat()

                    gps_pos = gps_manager.position
                    if gps_pos:
                        msg['agent_gps'] = gps_pos

                    snapshots = self.data_snapshots.get(mode, [])
                    snapshots.append(msg)
                    if len(snapshots) > 100:
                        snapshots = snapshots[-100:]
                    self.data_snapshots[mode] = snapshots

                    logger.debug(f"RTLAMR: meter {msg.get('Message', {}).get('ID', 'Unknown')}")

                except json.JSONDecodeError:
                    pass

        except Exception as e:
            logger.error(f"RTLAMR reader error: {e}")
        finally:
            proc.wait()
            if 'rtlamr_tcp' in self.processes:
                tcp_proc = self.processes['rtlamr_tcp']
                if tcp_proc.poll() is None:
                    tcp_proc.terminate()
                del self.processes['rtlamr_tcp']
            logger.info("RTLAMR reader stopped")

    # -------------------------------------------------------------------------
    # DSC MODE (rtl_fm | dsc-decoder) - Digital Selective Calling
    # -------------------------------------------------------------------------

    def _start_dsc(self, params: dict) -> dict:
        """Start DSC (VHF Channel 70) decoding."""
        device = params.get('device', '0')
        gain = params.get('gain', '40')
        ppm = params.get('ppm', '0')
        freq = '156.525'  # DSC Channel 70

        rtl_fm_path = self._get_tool_path('rtl_fm')
        if not rtl_fm_path:
            return {'status': 'error', 'message': 'rtl_fm not found'}

        # Try to find dsc-decoder
        dsc_decoder = None
        for path in ['/usr/local/bin/dsc-decoder', '/usr/bin/dsc-decoder', './bin/dsc-decoder']:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                dsc_decoder = path
                break

        # Build rtl_fm command for DSC (48kHz sample rate)
        rtl_fm_cmd = [
            rtl_fm_path,
            '-f', f'{freq}M',
            '-s', '48000',
            '-g', str(gain),
            '-d', str(device),
        ]
        if ppm and str(ppm) != '0':
            rtl_fm_cmd.extend(['-p', str(ppm)])

        logger.info(f"Starting DSC: {' '.join(rtl_fm_cmd)}")

        try:
            if dsc_decoder:
                rtl_fm_proc = subprocess.Popen(
                    rtl_fm_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                decoder_proc = subprocess.Popen(
                    [dsc_decoder],
                    stdin=rtl_fm_proc.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                rtl_fm_proc.stdout.close()
                self.processes['dsc'] = decoder_proc
                self.processes['dsc_rtl'] = rtl_fm_proc

                # Wait briefly to verify processes started successfully
                time.sleep(0.5)
                if rtl_fm_proc.poll() is not None:
                    stderr_output = rtl_fm_proc.stderr.read().decode('utf-8', errors='replace')
                    decoder_proc.terminate()
                    del self.processes['dsc']
                    del self.processes['dsc_rtl']
                    return {'status': 'error', 'message': f'rtl_fm failed to start: {stderr_output[:200]}'}
            else:
                rtl_fm_proc = subprocess.Popen(
                    rtl_fm_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.processes['dsc'] = rtl_fm_proc
                logger.warning("No dsc-decoder found - DSC decoding limited")

                # Wait briefly to verify process started successfully
                time.sleep(0.5)
                if rtl_fm_proc.poll() is not None:
                    stderr_output = rtl_fm_proc.stderr.read().decode('utf-8', errors='replace')
                    del self.processes['dsc']
                    return {'status': 'error', 'message': f'rtl_fm failed to start: {stderr_output[:200]}'}

            return {
                'status': 'started',
                'mode': 'dsc',
                'frequency': freq,
                'channel': 70,
                'has_decoder': dsc_decoder is not None,
                'gps_enabled': gps_manager.is_running
            }

        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    # -------------------------------------------------------------------------
    # TSCM MODE (Technical Surveillance Countermeasures)
    # -------------------------------------------------------------------------

    def _start_tscm(self, params: dict) -> dict:
        """Start TSCM scanning - combines WiFi and Bluetooth analysis."""
        # Initialize state
        if not hasattr(self, 'tscm_baseline'):
            self.tscm_baseline = {}
        if not hasattr(self, 'tscm_anomalies'):
            self.tscm_anomalies = []
        self.tscm_anomalies.clear()

        thread = threading.Thread(
            target=self._tscm_analyzer,
            daemon=True
        )
        thread.start()
        self.output_threads['tscm'] = thread

        return {
            'status': 'started',
            'mode': 'tscm',
            'note': 'TSCM analyzes WiFi/BT data for anomalies - no SDR required',
            'gps_enabled': gps_manager.is_running
        }

    def _tscm_analyzer(self):
        """Background TSCM analysis - looks for anomalies in WiFi/BT."""
        mode = 'tscm'
        stop_event = self.stop_events.get(mode)
        baseline_built = False

        while not (stop_event and stop_event.is_set()):
            try:
                current_wifi = dict(self.wifi_networks)
                current_bt = dict(self.bluetooth_devices)

                if not baseline_built and (current_wifi or current_bt):
                    self.tscm_baseline = {
                        'wifi': {k: {'rssi': v.get('signal'), 'essid': v.get('essid')}
                                 for k, v in current_wifi.items()},
                        'bluetooth': {k: {'rssi': v.get('rssi'), 'name': v.get('name')}
                                      for k, v in current_bt.items()},
                        'built_at': datetime.now(timezone.utc).isoformat()
                    }
                    baseline_built = True
                    logger.info(f"TSCM baseline: {len(current_wifi)} WiFi, {len(current_bt)} BT")

                elif baseline_built:
                    anomalies = []

                    for bssid, network in current_wifi.items():
                        if bssid not in self.tscm_baseline.get('wifi', {}):
                            anomalies.append({
                                'type': 'new_wifi',
                                'severity': 'medium',
                                'bssid': bssid,
                                'essid': network.get('essid'),
                                'rssi': network.get('signal'),
                                'detected_at': datetime.now(timezone.utc).isoformat()
                            })

                    for mac, device in current_bt.items():
                        if mac not in self.tscm_baseline.get('bluetooth', {}):
                            anomalies.append({
                                'type': 'new_bluetooth',
                                'severity': 'medium',
                                'mac': mac,
                                'name': device.get('name'),
                                'rssi': device.get('rssi'),
                                'detected_at': datetime.now(timezone.utc).isoformat()
                            })

                    if anomalies:
                        self.tscm_anomalies.extend(anomalies)
                        if len(self.tscm_anomalies) > 100:
                            self.tscm_anomalies = self.tscm_anomalies[-100:]

                        for anomaly in anomalies:
                            logger.info(f"TSCM anomaly: {anomaly['type']}")

                        self.data_snapshots[mode] = self.tscm_anomalies.copy()

                time.sleep(5)

            except Exception as e:
                logger.error(f"TSCM analyzer error: {e}")
                time.sleep(5)

        logger.info("TSCM analyzer stopped")

    # -------------------------------------------------------------------------
    # SATELLITE MODE (TLE-based pass prediction)
    # -------------------------------------------------------------------------

    def _start_satellite(self, params: dict) -> dict:
        """Start satellite pass prediction - no SDR needed."""
        lat = params.get('lat', params.get('latitude'))
        lon = params.get('lon', params.get('longitude'))
        min_elevation = params.get('min_elevation', 10)

        if lat is None or lon is None:
            gps_pos = gps_manager.position
            if gps_pos:
                lat = gps_pos.get('lat')
                lon = gps_pos.get('lon')

        if lat is None or lon is None:
            return {'status': 'error', 'message': 'Observer location required (lat/lon)'}

        thread = threading.Thread(
            target=self._satellite_predictor,
            args=(float(lat), float(lon), int(min_elevation)),
            daemon=True
        )
        thread.start()
        self.output_threads['satellite'] = thread

        return {
            'status': 'started',
            'mode': 'satellite',
            'observer': {'lat': lat, 'lon': lon},
            'min_elevation': min_elevation,
            'note': 'Satellite pass prediction - no SDR required'
        }

    def _satellite_predictor(self, lat: float, lon: float, min_elevation: int):
        """Calculate satellite passes using TLE data."""
        mode = 'satellite'
        stop_event = self.stop_events.get(mode)

        try:
            from skyfield.api import Topos, load

            stations_url = 'https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle'
            satellites = load.tle_file(stations_url)

            ts = load.timescale()
            observer = Topos(latitude_degrees=lat, longitude_degrees=lon)

            logger.info(f"Satellite predictor: {len(satellites)} satellites loaded")

            while not (stop_event and stop_event.is_set()):
                passes = []
                now = ts.now()
                end = ts.utc(now.utc_datetime().year, now.utc_datetime().month,
                            now.utc_datetime().day + 1)

                for sat in satellites[:20]:
                    try:
                        t, events = sat.find_events(observer, now, end, altitude_degrees=min_elevation)

                        for ti, event in zip(t, events):
                            if event == 0:  # Rise
                                difference = sat - observer
                                topocentric = difference.at(ti)
                                alt, az, _ = topocentric.altaz()
                                passes.append({
                                    'satellite': sat.name,
                                    'rise_time': ti.utc_iso(),
                                    'rise_azimuth': round(az.degrees, 1),
                                    'max_elevation': min_elevation,
                                })
                    except Exception:
                        continue

                self.data_snapshots[mode] = passes[:50]
                time.sleep(300)

        except ImportError:
            logger.warning("skyfield not installed - satellite prediction unavailable")
            self.data_snapshots[mode] = [{'error': 'skyfield not installed'}]
        except Exception as e:
            logger.error(f"Satellite predictor error: {e}")

        logger.info("Satellite predictor stopped")

    # -------------------------------------------------------------------------
    # LISTENING POST MODE (Spectrum scanner - signal detection only)
    # -------------------------------------------------------------------------

    def _start_listening_post(self, params: dict) -> dict:
        """
        Start listening post / spectrum scanner.

        Note: Full FFT streaming isn't practical over HTTP agents.
        Instead provides signal detection events and activity log.
        """
        start_freq = params.get('start_freq', 88.0)
        end_freq = params.get('end_freq', 108.0)
        step = params.get('step', 0.1)
        modulation = params.get('modulation', 'wfm')
        squelch = params.get('squelch', 20)
        device = params.get('device', '0')
        gain = params.get('gain', '40')

        rtl_fm_path = self._get_tool_path('rtl_fm')
        if not rtl_fm_path:
            return {'status': 'error', 'message': 'rtl_fm not found'}

        # Initialize state
        if not hasattr(self, 'listening_post_activity'):
            self.listening_post_activity = []
        self.listening_post_activity.clear()
        self.listening_post_current_freq = float(start_freq)

        thread = threading.Thread(
            target=self._listening_post_scanner,
            args=(float(start_freq), float(end_freq), float(step),
                  modulation, int(squelch), str(device), str(gain)),
            daemon=True
        )
        thread.start()
        self.output_threads['listening_post'] = thread

        return {
            'status': 'started',
            'mode': 'listening_post',
            'start_freq': start_freq,
            'end_freq': end_freq,
            'step': step,
            'modulation': modulation,
            'note': 'Provides signal detection events, not full FFT data',
            'gps_enabled': gps_manager.is_running
        }

    def _listening_post_scanner(self, start_freq: float, end_freq: float,
                                 step: float, modulation: str, squelch: int,
                                 device: str, gain: str):
        """Scan frequency range and report signal detections."""
        mode = 'listening_post'
        stop_event = self.stop_events.get(mode)

        rtl_fm_path = self._get_tool_path('rtl_fm')
        current_freq = start_freq
        scan_direction = 1

        while not (stop_event and stop_event.is_set()):
            self.listening_post_current_freq = current_freq

            cmd = [
                rtl_fm_path,
                '-f', f'{current_freq}M',
                '-M', modulation,
                '-s', '22050',
                '-g', gain,
                '-d', device,
                '-l', str(squelch),
            ]

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                signal_detected = False
                start_time = time.time()

                while time.time() - start_time < 1.0:
                    if stop_event and stop_event.is_set():
                        break
                    data = proc.stdout.read(2205)
                    if data and len(data) > 10:
                        # Simple signal detection via audio level
                        try:
                            samples = [int.from_bytes(data[i:i+2], 'little', signed=True)
                                       for i in range(0, min(len(data)-1, 1000), 2)]
                            if samples:
                                rms = (sum(s*s for s in samples) / len(samples)) ** 0.5
                                if rms > 500:
                                    signal_detected = True
                                    break
                        except Exception:
                            pass

                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()

                if signal_detected:
                    event = {
                        'type': 'signal_found',
                        'frequency': current_freq,
                        'modulation': modulation,
                        'detected_at': datetime.now(timezone.utc).isoformat()
                    }

                    gps_pos = gps_manager.position
                    if gps_pos:
                        event['agent_gps'] = gps_pos

                    self.listening_post_activity.append(event)
                    if len(self.listening_post_activity) > 500:
                        self.listening_post_activity = self.listening_post_activity[-500:]

                    self.data_snapshots[mode] = self.listening_post_activity.copy()
                    logger.info(f"Listening post: signal at {current_freq} MHz")

            except Exception as e:
                logger.debug(f"Scanner error at {current_freq}: {e}")

            # Move to next frequency
            current_freq += step * scan_direction
            if current_freq >= end_freq:
                current_freq = end_freq
                scan_direction = -1
            elif current_freq <= start_freq:
                current_freq = start_freq
                scan_direction = 1

            time.sleep(0.1)

        logger.info("Listening post scanner stopped")


# Global mode manager
mode_manager = ModeManager()
_start_time = time.time()


# =============================================================================
# Data Push Loop
# =============================================================================

class DataPushLoop(threading.Thread):
    """Background thread that periodically pushes mode data to controller."""

    def __init__(self, interval_seconds: float = 5.0):
        super().__init__()
        self.daemon = True
        self.interval = interval_seconds
        self.stop_event = threading.Event()

    def run(self):
        """Main push loop."""
        logger.info(f"Data push loop started (interval: {self.interval}s)")

        while not self.stop_event.is_set():
            if push_client and push_client.running:
                # Push data for all running modes
                for mode in list(mode_manager.running_modes.keys()):
                    try:
                        data = mode_manager.get_mode_data(mode)
                        if data.get('data'):  # Only push if there's data
                            push_client.enqueue(
                                scan_type=mode,
                                payload=data,
                                interface=None
                            )
                    except Exception as e:
                        logger.warning(f"Failed to push {mode} data: {e}")

            # Wait for next interval
            self.stop_event.wait(self.interval)

        logger.info("Data push loop stopped")

    def stop(self):
        """Stop the push loop."""
        self.stop_event.set()


# Global push loop
data_push_loop: DataPushLoop | None = None


# =============================================================================
# HTTP Request Handler
# =============================================================================

class InterceptAgentHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the agent API."""

    # Disable default logging
    def log_message(self, format, *args):
        logger.debug(f"{self.client_address[0]} - {format % args}")

    def _check_ip_allowed(self) -> bool:
        """Check if client IP is allowed."""
        if not config.allowed_ips:
            return True

        client_ip = self.client_address[0]
        return client_ip in config.allowed_ips

    def _send_json(self, data: dict, status: int = 200):
        """Send JSON response."""
        body = json.dumps(data).encode('utf-8')

        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        if config.allow_cors:
            self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message: str, status: int = 400):
        """Send error response."""
        self._send_json({'error': message}, status)

    def _read_body(self) -> dict:
        """Read and parse JSON body."""
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            return {}

        body = self.rfile.read(content_length)
        try:
            return json.loads(body.decode('utf-8'))
        except json.JSONDecodeError:
            return {}

    def _parse_path(self) -> tuple[str, dict]:
        """Parse URL path and query parameters."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        query = parse_qs(parsed.query)
        # Flatten single-value query params
        params = {k: v[0] if len(v) == 1 else v for k, v in query.items()}
        return path, params

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        if config.allow_cors:
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-API-Key')
        self.end_headers()

    def do_GET(self):
        """Handle GET requests."""
        if not self._check_ip_allowed():
            self._send_error('Forbidden', 403)
            return

        path, params = self._parse_path()

        # Route handling
        if path == '/capabilities':
            self._send_json(mode_manager.detect_capabilities())

        elif path == '/status':
            self._send_json(mode_manager.get_status())

        elif path == '/health':
            self._send_json({'status': 'healthy', 'version': AGENT_VERSION})

        elif path == '/gps':
            gps_pos = gps_manager.position
            self._send_json({
                'available': gps_manager.is_running,
                'position': gps_pos,
            })

        elif path == '/config':
            # Return non-sensitive config
            cfg = config.to_dict()
            if 'controller_api_key' in cfg:
                del cfg['controller_api_key']
            self._send_json(cfg)

        elif path.startswith('/') and path.count('/') == 2:
            # /{mode}/status or /{mode}/data
            parts = path.split('/')
            mode = parts[1]
            action = parts[2]

            if action == 'status':
                self._send_json(mode_manager.get_mode_status(mode))
            elif action == 'data':
                self._send_json(mode_manager.get_mode_data(mode))
            else:
                self._send_error('Not found', 404)

        else:
            self._send_error('Not found', 404)

    def do_POST(self):
        """Handle POST requests."""
        if not self._check_ip_allowed():
            self._send_error('Forbidden', 403)
            return

        path, _ = self._parse_path()
        body = self._read_body()

        if path == '/config':
            # Update running config (limited fields)
            if 'push_enabled' in body:
                config.push_enabled = bool(body['push_enabled'])
            if 'push_interval' in body:
                config.push_interval = int(body['push_interval'])
            self._send_json({'status': 'updated', 'config': config.to_dict()})

        elif path.startswith('/') and path.count('/') == 2:
            # /{mode}/start or /{mode}/stop
            parts = path.split('/')
            mode = parts[1]
            action = parts[2]

            if action == 'start':
                result = mode_manager.start_mode(mode, body)
                status = 200 if result.get('status') == 'started' else 400
                self._send_json(result, status)
            elif action == 'stop':
                result = mode_manager.stop_mode(mode)
                self._send_json(result)
            else:
                self._send_error('Not found', 404)

        else:
            self._send_error('Not found', 404)


# =============================================================================
# Threaded HTTP Server
# =============================================================================

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server."""
    allow_reuse_address = True
    daemon_threads = True


# =============================================================================
# Main
# =============================================================================

def main():
    global config, push_client, _start_time

    parser = argparse.ArgumentParser(
        description='INTERCEPT Agent - Remote signal intelligence node'
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=8020,
        help='Port to listen on (default: 8020)'
    )
    parser.add_argument(
        '--config', '-c',
        default='intercept_agent.cfg',
        help='Configuration file (default: intercept_agent.cfg)'
    )
    parser.add_argument(
        '--name', '-n',
        help='Agent name (overrides config file)'
    )
    parser.add_argument(
        '--controller',
        help='Controller URL for push mode'
    )
    parser.add_argument(
        '--api-key',
        help='API key for controller authentication'
    )
    parser.add_argument(
        '--allowed-ips',
        help='Comma-separated list of allowed client IPs'
    )
    parser.add_argument(
        '--cors',
        action='store_true',
        help='Enable CORS headers'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load config file
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(os.path.dirname(__file__), config_path)
    config.load_from_file(config_path)

    # Override with command line args
    if args.port:
        config.port = args.port
    if args.name:
        config.name = args.name
    if args.controller:
        config.controller_url = args.controller.rstrip('/')
        config.push_enabled = True
    if args.api_key:
        config.controller_api_key = args.api_key
    if args.allowed_ips:
        config.allowed_ips = [ip.strip() for ip in args.allowed_ips.split(',')]
    if args.cors:
        config.allow_cors = True

    _start_time = time.time()

    print("=" * 60)
    print("  INTERCEPT AGENT")
    print("  Remote Signal Intelligence Node")
    print("=" * 60)
    print()
    print(f"  Agent Name:  {config.name}")
    print(f"  Port:        {config.port}")
    print(f"  CORS:        {'Enabled' if config.allow_cors else 'Disabled'}")

    # Start GPS
    print()
    print("  Initializing GPS...")
    if gps_manager.start():
        print("  GPS:         Connected to gpsd")
    else:
        print("  GPS:         Not available (gpsd not running)")
    if config.allowed_ips:
        print(f"  Allowed IPs: {', '.join(config.allowed_ips)}")
    else:
        print("  Allowed IPs: Any")
    print()

    # Detect capabilities
    caps = mode_manager.detect_capabilities()
    print("  Available Modes:")
    for mode, available in caps['modes'].items():
        status = "OK" if available else "N/A"
        print(f"    - {mode}: {status}")
    print()

    if caps['devices']:
        print("  Detected SDR Devices:")
        for dev in caps['devices']:
            print(f"    - [{dev.get('index', '?')}] {dev.get('name', 'Unknown')}")
        print()

    # Start push client if enabled
    global data_push_loop
    if config.push_enabled and config.controller_url:
        print(f"  Push Mode:   Enabled -> {config.controller_url}")
        push_client = ControllerPushClient(config)
        push_client.start()
        # Start data push loop
        data_push_loop = DataPushLoop(interval_seconds=config.push_interval)
        data_push_loop.start()
    else:
        print("  Push Mode:   Disabled")
    print()

    # Start HTTP server
    server_address = ('', config.port)
    httpd = ThreadedHTTPServer(server_address, InterceptAgentHandler)

    print(f"  Listening on http://0.0.0.0:{config.port}")
    print()
    print("  Press Ctrl+C to stop")
    print()

    # Handle shutdown
    def signal_handler(sig, frame):
        print("\nShutting down...")
        # Stop all running modes
        for mode in list(mode_manager.running_modes.keys()):
            mode_manager.stop_mode(mode)
        if data_push_loop:
            data_push_loop.stop()
        if push_client:
            push_client.stop()
        gps_manager.stop()
        httpd.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if push_client:
            push_client.stop()


if __name__ == '__main__':
    main()
