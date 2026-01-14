"""
TSCM Threat Detection Engine

Analyzes WiFi, Bluetooth, and RF data to identify potential surveillance devices
and classify threats based on known patterns and baseline comparison.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from data.tscm_frequencies import (
    BLE_TRACKER_SIGNATURES,
    THREAT_TYPES,
    WIFI_CAMERA_PATTERNS,
    get_frequency_risk,
    get_threat_severity,
    is_known_tracker,
    is_potential_camera,
)

logger = logging.getLogger('intercept.tscm.detector')


class ThreatDetector:
    """
    Analyzes scan results to detect potential surveillance threats.
    """

    def __init__(self, baseline: dict | None = None):
        """
        Initialize the threat detector.

        Args:
            baseline: Optional baseline dict containing expected devices
        """
        self.baseline = baseline
        self.baseline_wifi_macs = set()
        self.baseline_bt_macs = set()
        self.baseline_rf_freqs = set()

        if baseline:
            self._load_baseline(baseline)

    def _load_baseline(self, baseline: dict) -> None:
        """Load baseline device identifiers for comparison."""
        # WiFi networks and clients
        for network in baseline.get('wifi_networks', []):
            if 'bssid' in network:
                self.baseline_wifi_macs.add(network['bssid'].upper())
            if 'clients' in network:
                for client in network['clients']:
                    if 'mac' in client:
                        self.baseline_wifi_macs.add(client['mac'].upper())

        # Bluetooth devices
        for device in baseline.get('bt_devices', []):
            if 'mac' in device:
                self.baseline_bt_macs.add(device['mac'].upper())

        # RF frequencies (rounded to nearest 0.1 MHz)
        for freq in baseline.get('rf_frequencies', []):
            if isinstance(freq, dict):
                self.baseline_rf_freqs.add(round(freq.get('frequency', 0), 1))
            else:
                self.baseline_rf_freqs.add(round(freq, 1))

        logger.info(
            f"Loaded baseline: {len(self.baseline_wifi_macs)} WiFi, "
            f"{len(self.baseline_bt_macs)} BT, {len(self.baseline_rf_freqs)} RF"
        )

    def analyze_wifi_device(self, device: dict) -> dict | None:
        """
        Analyze a WiFi device for threats.

        Args:
            device: WiFi device dict with bssid, essid, etc.

        Returns:
            Threat dict if threat detected, None otherwise
        """
        mac = device.get('bssid', device.get('mac', '')).upper()
        ssid = device.get('essid', device.get('ssid', ''))
        vendor = device.get('vendor', '')
        signal = device.get('power', device.get('signal', -100))

        threats = []

        # Check if new device (not in baseline)
        if self.baseline and mac and mac not in self.baseline_wifi_macs:
            threats.append({
                'type': 'new_device',
                'severity': get_threat_severity('new_device', {'signal_strength': signal}),
                'reason': 'Device not present in baseline',
            })

        # Check for hidden camera patterns
        if is_potential_camera(ssid=ssid, mac=mac, vendor=vendor):
            threats.append({
                'type': 'hidden_camera',
                'severity': get_threat_severity('hidden_camera', {'signal_strength': signal}),
                'reason': 'Device matches WiFi camera patterns',
            })

        # Check for hidden SSID with strong signal
        if not ssid and signal and signal > -60:
            threats.append({
                'type': 'anomaly',
                'severity': 'medium',
                'reason': 'Hidden SSID with strong signal',
            })

        if not threats:
            return None

        # Return highest severity threat
        threats.sort(key=lambda t: ['low', 'medium', 'high', 'critical'].index(t['severity']), reverse=True)

        return {
            'threat_type': threats[0]['type'],
            'severity': threats[0]['severity'],
            'source': 'wifi',
            'identifier': mac,
            'name': ssid or 'Hidden Network',
            'signal_strength': signal,
            'details': {
                'all_threats': threats,
                'vendor': vendor,
                'ssid': ssid,
            }
        }

    def analyze_bt_device(self, device: dict) -> dict | None:
        """
        Analyze a Bluetooth device for threats.

        Args:
            device: BT device dict with mac, name, rssi, etc.

        Returns:
            Threat dict if threat detected, None otherwise
        """
        mac = device.get('mac', device.get('address', '')).upper()
        name = device.get('name', '')
        rssi = device.get('rssi', device.get('signal', -100))
        manufacturer = device.get('manufacturer', '')
        device_type = device.get('type', '')
        manufacturer_data = device.get('manufacturer_data')

        threats = []

        # Check if new device (not in baseline)
        if self.baseline and mac and mac not in self.baseline_bt_macs:
            threats.append({
                'type': 'new_device',
                'severity': get_threat_severity('new_device', {'signal_strength': rssi}),
                'reason': 'Device not present in baseline',
            })

        # Check for known trackers
        tracker_info = is_known_tracker(name, manufacturer_data)
        if tracker_info:
            threats.append({
                'type': 'tracker',
                'severity': tracker_info.get('risk', 'high'),
                'reason': f"Known tracker detected: {tracker_info.get('name', 'Unknown')}",
                'tracker_type': tracker_info.get('name'),
            })

        # Check for suspicious BLE beacons (unnamed, persistent)
        if not name and rssi and rssi > -70:
            threats.append({
                'type': 'anomaly',
                'severity': 'medium',
                'reason': 'Unnamed BLE device with strong signal',
            })

        if not threats:
            return None

        # Return highest severity threat
        threats.sort(key=lambda t: ['low', 'medium', 'high', 'critical'].index(t['severity']), reverse=True)

        return {
            'threat_type': threats[0]['type'],
            'severity': threats[0]['severity'],
            'source': 'bluetooth',
            'identifier': mac,
            'name': name or 'Unknown BLE Device',
            'signal_strength': rssi,
            'details': {
                'all_threats': threats,
                'manufacturer': manufacturer,
                'device_type': device_type,
            }
        }

    def analyze_rf_signal(self, signal: dict) -> dict | None:
        """
        Analyze an RF signal for threats.

        Args:
            signal: RF signal dict with frequency, level, etc.

        Returns:
            Threat dict if threat detected, None otherwise
        """
        frequency = signal.get('frequency', 0)
        level = signal.get('level', signal.get('power', -100))
        modulation = signal.get('modulation', '')

        if not frequency:
            return None

        threats = []
        freq_rounded = round(frequency, 1)

        # Check if new frequency (not in baseline)
        if self.baseline and freq_rounded not in self.baseline_rf_freqs:
            risk, band_name = get_frequency_risk(frequency)
            threats.append({
                'type': 'unknown_signal',
                'severity': risk,
                'reason': f'New signal in {band_name}',
            })

        # Check frequency risk even without baseline
        risk, band_name = get_frequency_risk(frequency)
        if risk in ['high', 'critical']:
            threats.append({
                'type': 'unknown_signal',
                'severity': risk,
                'reason': f'Signal in high-risk band: {band_name}',
            })

        if not threats:
            return None

        # Return highest severity threat
        threats.sort(key=lambda t: ['low', 'medium', 'high', 'critical'].index(t['severity']), reverse=True)

        return {
            'threat_type': threats[0]['type'],
            'severity': threats[0]['severity'],
            'source': 'rf',
            'identifier': f'{frequency:.3f} MHz',
            'name': f'RF Signal @ {frequency:.3f} MHz',
            'signal_strength': level,
            'frequency': frequency,
            'details': {
                'all_threats': threats,
                'modulation': modulation,
                'band_name': band_name,
            }
        }

    def analyze_all(
        self,
        wifi_devices: list[dict] | None = None,
        bt_devices: list[dict] | None = None,
        rf_signals: list[dict] | None = None
    ) -> list[dict]:
        """
        Analyze all provided devices and signals for threats.

        Returns:
            List of detected threats sorted by severity
        """
        threats = []

        if wifi_devices:
            for device in wifi_devices:
                threat = self.analyze_wifi_device(device)
                if threat:
                    threats.append(threat)

        if bt_devices:
            for device in bt_devices:
                threat = self.analyze_bt_device(device)
                if threat:
                    threats.append(threat)

        if rf_signals:
            for signal in rf_signals:
                threat = self.analyze_rf_signal(signal)
                if threat:
                    threats.append(threat)

        # Sort by severity (critical first)
        severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        threats.sort(key=lambda t: severity_order.get(t.get('severity', 'low'), 3))

        return threats


def classify_device_threat(
    source: str,
    device: dict,
    baseline: dict | None = None
) -> dict | None:
    """
    Convenience function to classify a single device.

    Args:
        source: Device source ('wifi', 'bluetooth', 'rf')
        device: Device data dict
        baseline: Optional baseline for comparison

    Returns:
        Threat dict if threat detected, None otherwise
    """
    detector = ThreatDetector(baseline)

    if source == 'wifi':
        return detector.analyze_wifi_device(device)
    elif source == 'bluetooth':
        return detector.analyze_bt_device(device)
    elif source == 'rf':
        return detector.analyze_rf_signal(device)

    return None
