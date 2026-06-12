"""Shared tool/interface capability detection.

Extracted from the standalone agent (``intercept_agent.py``) so the app and the
agent share one implementation and cannot drift. Mode availability is derived
from :func:`utils.dependencies.check_all_dependencies`; interface detection
probes the host for WiFi interfaces and Bluetooth adapters.

This module is intentionally config-agnostic: it reports raw tool/hardware
availability. Callers that gate modes behind their own configuration apply that
gating on top of the values returned here.
"""

from __future__ import annotations

import platform
import re
import subprocess

from utils.dependencies import check_all_dependencies, check_tool
from utils.logging import get_logger

logger = get_logger("intercept.capabilities")

# Mapping from utils.dependencies mode key -> capability/mode key used by callers.
MODE_DEPENDENCY_MAP = {
    "pager": "pager",
    "sensor": "sensor",
    "aircraft": "adsb",
    "ais": "ais",
    "acars": "acars",
    "aprs": "aprs",
    "wifi": "wifi",
    "bluetooth": "bluetooth",
    "tscm": "tscm",
    "satellite": "satellite",
}

# Modes not represented in utils.dependencies; keyed by cap mode -> required tools.
EXTRA_MODE_TOOLS = {
    "dsc": ["rtl_fm"],
    "rtlamr": ["rtlamr"],
    "listening_post": ["rtl_fm"],
}

# Fallback tool checks when the dependencies module is unavailable.
FALLBACK_TOOL_CHECKS = {
    "pager": ["rtl_fm", "multimon-ng"],
    "sensor": ["rtl_433"],
    "adsb": ["dump1090"],
    "ais": ["AIS-catcher"],
    "acars": ["acarsdec"],
    "aprs": ["rtl_fm", "direwolf"],
    "wifi": ["airmon-ng", "airodump-ng"],
    "bluetooth": ["bluetoothctl"],
    "dsc": ["rtl_fm"],
    "rtlamr": ["rtlamr"],
    "satellite": [],
    "listening_post": ["rtl_fm"],
    "tscm": ["rtl_fm"],
}


def detect_mode_availability(dep_status: dict | None = None) -> dict[str, bool]:
    """Detect mode availability from tool dependencies.

    Returns a ``{cap_mode: bool}`` map of raw tool readiness. Falls back to
    direct tool checks if :func:`check_all_dependencies` raises.

    Args:
        dep_status: Pre-computed result of :func:`check_all_dependencies`.  When
            supplied the probe is skipped entirely, avoiding a second call when
            the caller has already fetched it.
    """
    modes: dict[str, bool] = {}
    try:
        if dep_status is None:
            dep_status = check_all_dependencies()
    except Exception as e:
        logger.warning(f"Dependency check failed, using fallback: {e}")
        return _detect_mode_availability_fallback()

    for dep_mode, cap_mode in MODE_DEPENDENCY_MAP.items():
        if dep_mode in dep_status:
            modes[cap_mode] = dep_status[dep_mode]["ready"]
        else:
            modes[cap_mode] = False

    # Modes not in dependencies.py
    for cap_mode, tools in EXTRA_MODE_TOOLS.items():
        modes[cap_mode] = all(check_tool(tool) for tool in tools) if tools else True

    return modes


def _detect_mode_availability_fallback() -> dict[str, bool]:
    """Fallback mode availability when the dependencies module is unavailable.

    Note: this uses ``utils.dependencies.check_tool``, which also searches
    Homebrew paths (a strict superset of ``shutil.which``).
    """
    modes: dict[str, bool] = {}
    for mode, tools in FALLBACK_TOOL_CHECKS.items():
        if not tools:
            modes[mode] = True
        elif mode == "adsb":
            modes[mode] = check_tool("dump1090") or check_tool("dump1090-fa") or check_tool("readsb")
        else:
            modes[mode] = all(check_tool(tool) for tool in tools)
    return modes


def detect_interfaces() -> dict[str, list]:
    """Detect WiFi interfaces and Bluetooth adapters on the host.

    Returns ``{"wifi_interfaces": [...], "bt_adapters": [...], "sdr_devices": []}``.
    ``sdr_devices`` is left empty here; SDR enumeration is handled by callers.
    """
    interfaces: dict[str, list] = {
        "wifi_interfaces": [],
        "bt_adapters": [],
        "sdr_devices": [],
    }

    # Detect WiFi interfaces
    if platform.system() == "Darwin":  # macOS
        try:
            result = subprocess.run(
                ["networksetup", "-listallhardwareports"], capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.split("\n")
            for i, line in enumerate(lines):
                if "Wi-Fi" in line or "AirPort" in line:
                    port_name = line.replace("Hardware Port:", "").strip()
                    for j in range(i + 1, min(i + 3, len(lines))):
                        if "Device:" in lines[j]:
                            device = lines[j].split("Device:")[1].strip()
                            interfaces["wifi_interfaces"].append(
                                {
                                    "name": device,
                                    "display_name": f"{port_name} ({device})",
                                    "type": "internal",
                                    "monitor_capable": False,
                                }
                            )
                            break
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass
    else:  # Linux
        try:
            result = subprocess.run(["iw", "dev"], capture_output=True, text=True, timeout=5)
            current_iface = None
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line.startswith("Interface"):
                    current_iface = line.split()[1]
                elif current_iface and "type" in line:
                    iface_type = line.split()[-1]
                    interfaces["wifi_interfaces"].append(
                        {
                            "name": current_iface,
                            "display_name": f"Wireless ({current_iface}) - {iface_type}",
                            "type": iface_type,
                            "monitor_capable": True,
                        }
                    )
                    current_iface = None
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            # Fall back to iwconfig
            try:
                result = subprocess.run(["iwconfig"], capture_output=True, text=True, timeout=5)
                for line in result.stdout.split("\n"):
                    if "IEEE 802.11" in line:
                        iface = line.split()[0]
                        interfaces["wifi_interfaces"].append(
                            {
                                "name": iface,
                                "display_name": f"Wireless ({iface})",
                                "type": "managed",
                                "monitor_capable": True,
                            }
                        )
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass

    # Detect Bluetooth adapters
    if platform.system() == "Linux":
        try:
            result = subprocess.run(["hciconfig"], capture_output=True, text=True, timeout=5)
            blocks = re.split(r"(?=^hci\d+:)", result.stdout, flags=re.MULTILINE)
            for block in blocks:
                if block.strip():
                    first_line = block.split("\n")[0]
                    match = re.match(r"(hci\d+):", first_line)
                    if match:
                        iface_name = match.group(1)
                        is_up = "UP RUNNING" in block or "\tUP " in block
                        interfaces["bt_adapters"].append(
                            {
                                "name": iface_name,
                                "display_name": f"Bluetooth Adapter ({iface_name})",
                                "type": "hci",
                                "status": "up" if is_up else "down",
                            }
                        )
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            # Try bluetoothctl as fallback
            try:
                result = subprocess.run(["bluetoothctl", "list"], capture_output=True, text=True, timeout=5)
                for line in result.stdout.split("\n"):
                    if "Controller" in line:
                        parts = line.split()
                        if len(parts) >= 3:
                            addr = parts[1]
                            name = " ".join(parts[2:]) if len(parts) > 2 else "Bluetooth"
                            interfaces["bt_adapters"].append(
                                {
                                    "name": addr,
                                    "display_name": f"{name} ({addr[-8:]})",
                                    "type": "controller",
                                    "status": "available",
                                }
                            )
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass
    elif platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["system_profiler", "SPBluetoothDataType"], capture_output=True, text=True, timeout=10
            )
            bt_name = "Built-in Bluetooth"
            bt_addr = ""
            for line in result.stdout.split("\n"):
                if "Address:" in line:
                    bt_addr = line.split("Address:")[1].strip()
                    break
            interfaces["bt_adapters"].append(
                {
                    "name": "default",
                    "display_name": f"{bt_name}" + (f" ({bt_addr[-8:]})" if bt_addr else ""),
                    "type": "macos",
                    "status": "available",
                }
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            interfaces["bt_adapters"].append(
                {"name": "default", "display_name": "Built-in Bluetooth", "type": "macos", "status": "available"}
            )

    return interfaces
