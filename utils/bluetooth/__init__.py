"""
Bluetooth scanning package for INTERCEPT.

Provides unified Bluetooth scanning with DBus/BlueZ and fallback backends,
device aggregation, RSSI statistics, and observable heuristics.
"""

from .aggregator import DeviceAggregator
from .capability_check import check_capabilities, quick_adapter_check
from .constants import (
    # Range bands
    RANGE_VERY_CLOSE,
    RANGE_CLOSE,
    RANGE_NEARBY,
    RANGE_FAR,
    RANGE_UNKNOWN,
    # Protocols
    PROTOCOL_BLE,
    PROTOCOL_CLASSIC,
    PROTOCOL_AUTO,
    # Address types
    ADDRESS_TYPE_PUBLIC,
    ADDRESS_TYPE_RANDOM,
    ADDRESS_TYPE_RANDOM_STATIC,
    ADDRESS_TYPE_RPA,
    ADDRESS_TYPE_NRPA,
)
from .heuristics import HeuristicsEngine, evaluate_device_heuristics, evaluate_all_devices
from .models import BTDeviceAggregate, BTObservation, ScanStatus, SystemCapabilities
from .scanner import BluetoothScanner, get_bluetooth_scanner, reset_bluetooth_scanner

__all__ = [
    # Main scanner
    'BluetoothScanner',
    'get_bluetooth_scanner',
    'reset_bluetooth_scanner',

    # Models
    'BTObservation',
    'BTDeviceAggregate',
    'ScanStatus',
    'SystemCapabilities',

    # Aggregator
    'DeviceAggregator',

    # Heuristics
    'HeuristicsEngine',
    'evaluate_device_heuristics',
    'evaluate_all_devices',

    # Capability checks
    'check_capabilities',
    'quick_adapter_check',

    # Constants
    'RANGE_VERY_CLOSE',
    'RANGE_CLOSE',
    'RANGE_NEARBY',
    'RANGE_FAR',
    'RANGE_UNKNOWN',
    'PROTOCOL_BLE',
    'PROTOCOL_CLASSIC',
    'PROTOCOL_AUTO',
    'ADDRESS_TYPE_PUBLIC',
    'ADDRESS_TYPE_RANDOM',
    'ADDRESS_TYPE_RANDOM_STATIC',
    'ADDRESS_TYPE_RPA',
    'ADDRESS_TYPE_NRPA',
]
