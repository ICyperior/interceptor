from __future__ import annotations

import subprocess
import re
from typing import Any

from .dependencies import check_tool


def cleanup_stale_processes() -> None:
    """Kill any stale processes from previous runs (but not system services)."""
    # Note: dump1090 is NOT included here as users may run it as a system service
    processes_to_kill = ['rtl_adsb', 'rtl_433', 'multimon-ng', 'rtl_fm']
    for proc_name in processes_to_kill:
        try:
            subprocess.run(['pkill', '-9', proc_name], capture_output=True)
        except (subprocess.SubprocessError, OSError):
            pass


def is_valid_mac(mac: str | None) -> bool:
    """Validate MAC address format."""
    if not mac:
        return False
    return bool(re.match(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$', mac))


def is_valid_channel(channel: str | int | None) -> bool:
    """Validate WiFi channel number."""
    try:
        ch = int(channel)  # type: ignore[arg-type]
        return 1 <= ch <= 200
    except (ValueError, TypeError):
        return False


def detect_devices() -> list[dict[str, Any]]:
    """Detect RTL-SDR devices."""
    devices: list[dict[str, Any]] = []

    if not check_tool('rtl_test'):
        return devices

    try:
        result = subprocess.run(
            ['rtl_test', '-t'],
            capture_output=True,
            text=True,
            timeout=5
        )
        output = result.stderr + result.stdout

        # Parse device info
        device_pattern = r'(\d+):\s+(.+?)(?:,\s*SN:\s*(\S+))?$'

        for line in output.split('\n'):
            line = line.strip()
            match = re.match(device_pattern, line)
            if match:
                devices.append({
                    'index': int(match.group(1)),
                    'name': match.group(2).strip().rstrip(','),
                    'serial': match.group(3) or 'N/A'
                })

        if not devices:
            found_match = re.search(r'Found (\d+) device', output)
            if found_match:
                count = int(found_match.group(1))
                for i in range(count):
                    devices.append({
                        'index': i,
                        'name': f'RTL-SDR Device {i}',
                        'serial': 'Unknown'
                    })

    except Exception:
        pass

    return devices
