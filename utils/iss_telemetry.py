"""ISS live telemetry bridge.

Maintains a single upstream connection to NASA's public Lightstreamer feed
(``push.lightstreamer.com``, adapter set ``ISSLIVE``) using the text TLCP
protocol, caches the latest value of a curated set of telemetry items, and
exposes a snapshot for the ISS dashboard to poll.

Design notes:
- One background thread holds the upstream connection for the whole process,
  regardless of how many browsers are watching (be polite to NASA's server).
- The connection is opened lazily on the first snapshot request and dropped
  after a period with no consumers, then reopened on demand.
- The ISS drops telemetry several times per orbit (Loss of Signal). When no
  item has updated within ``LOS_TIMEOUT`` seconds we report ``aos=False`` and
  the dashboard shows an LOS state rather than stale numbers.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_LS_BASE = "https://push.lightstreamer.com/lightstreamer/"
# Public web-client id accepted by the demo server (same one the ISS Mimic /
# Lightstreamer ISS example use). Not a secret.
_LS_CID = "mgQkwtwdysogQz2BJ4Ji kOj2Bg"
_ADAPTER_SET = "ISSLIVE"

# Curated telemetry: PUI -> (friendly key, unit). Only calibrated items with
# real units, plus the solar-array angles that drive the panel animation.
TELEMETRY_ITEMS: dict[str, tuple[str, str]] = {
    # Solar Array Rotary Joints (deg)
    "S0000003": ("sarj_starboard", "deg"),
    "S0000004": ("sarj_port", "deg"),
    # Beta Gimbal Assembly angles per array wing (deg)
    "S4000007": ("bga_1a", "deg"),
    "S4000008": ("bga_3a", "deg"),
    "S6000007": ("bga_3b", "deg"),
    "S6000008": ("bga_1b", "deg"),
    "P4000007": ("bga_2a", "deg"),
    "P4000008": ("bga_4a", "deg"),
    "P6000007": ("bga_4b", "deg"),
    "P6000008": ("bga_2b", "deg"),
    # Environment (real units)
    "USLAB000058": ("cabin_pressure", "psi"),
    "NODE3000001": ("ppo2", "psia"),
    "NODE3000003": ("ppco2", "psia"),
    # Attitude rates (deg/s)
    "USLAB000025": ("rate_x", "deg/s"),
    "USLAB000026": ("rate_y", "deg/s"),
    "USLAB000027": ("rate_z", "deg/s"),
    # Station time
    "TIME_000001": ("gmt", "ms"),
}

LOS_TIMEOUT = 12.0        # seconds without any update -> Loss of Signal
IDLE_DISCONNECT = 120.0   # seconds without a consumer -> drop upstream
_RECONNECT_BACKOFF = 5.0


class ISSTelemetryClient:
    """Singleton bridge to the ISSLIVE Lightstreamer feed."""

    def __init__(self) -> None:
        self._puis = list(TELEMETRY_ITEMS)
        self._values: dict[str, float] = {}
        self._last_update_ts = 0.0
        self._last_request_ts = 0.0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connected = False

    # ── public API ──────────────────────────────────────────────────────
    def get_snapshot(self) -> dict:
        """Return the latest telemetry snapshot and AOS/LOS state."""
        self._last_request_ts = time.time()
        self._ensure_running()
        now = time.time()
        with self._lock:
            values = dict(self._values)
            last = self._last_update_ts
            connected = self._connected
        aos = bool(last) and (now - last) < LOS_TIMEOUT
        mapped = {}
        for pui, (key, unit) in TELEMETRY_ITEMS.items():
            if pui in values:
                mapped[key] = {"value": values[pui], "unit": unit}
        return {
            "status": "success",
            "aos": aos,
            "connected": connected,
            "age_seconds": round(now - last, 1) if last else None,
            "telemetry": mapped,
        }

    # ── lifecycle ───────────────────────────────────────────────────────
    def _ensure_running(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="iss-telemetry", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            # Stop holding the upstream connection when nobody is watching.
            if time.time() - self._last_request_ts > IDLE_DISCONNECT:
                break
            try:
                self._connect_and_read()
            except Exception as e:
                logger.debug(f"ISS telemetry connection error: {e}")
            self._connected = False
            if self._stop.wait(_RECONNECT_BACKOFF):
                break

    def _connect_and_read(self) -> None:
        resp = self._post(
            "create_session.txt?LS_protocol=TLCP-2.1.0",
            {"LS_cid": _LS_CID, "LS_adapter_set": _ADAPTER_SET},
            stream=True,
        )
        first = resp.readline().decode(errors="replace").strip()
        if not first.startswith("CONOK"):
            resp.close()
            return
        session = first.split(",")[1]
        self._connected = True

        self._post(
            "control.txt?LS_protocol=TLCP-2.1.0",
            {
                "LS_session": session, "LS_reqId": "1", "LS_op": "add", "LS_subId": "1",
                "LS_data_adapter": "DEFAULT", "LS_group": " ".join(self._puis),
                "LS_schema": "TimeStamp Value", "LS_mode": "MERGE", "LS_snapshot": "true",
            },
        ).read()

        while not self._stop.is_set():
            if time.time() - self._last_request_ts > IDLE_DISCONNECT:
                break
            raw = resp.readline()
            if not raw:
                break  # stream closed
            line = raw.decode(errors="replace").rstrip("\r\n")
            if line.startswith("U,"):
                self._handle_update(line)
        resp.close()

    def _handle_update(self, line: str) -> None:
        # Format: U,<subId>,<itemPos>,<field1>|<field2>  (fields: TimeStamp|Value)
        try:
            _, _sub, pos, rest = line.split(",", 3)
        except ValueError:
            return
        try:
            pui = self._puis[int(pos) - 1]
        except (ValueError, IndexError):
            return
        fields = rest.split("|")
        # Value is the 2nd field; "$" means "unchanged from snapshot".
        value_str = fields[1] if len(fields) > 1 else ""
        if value_str in ("", "$", "#"):
            return
        try:
            value = float(value_str)
        except ValueError:
            return
        with self._lock:
            self._values[pui] = value
            self._last_update_ts = time.time()

    @staticmethod
    def _post(path: str, params: dict, stream: bool = False):
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(_LS_BASE + path, data=data)
        # A generous timeout: the server sends PROBE keepalives, so a healthy
        # stream never idles this long; a timeout means the link is dead.
        return urllib.request.urlopen(req, timeout=30 if stream else 15)


_client: ISSTelemetryClient | None = None
_client_lock = threading.Lock()


def get_iss_telemetry_client() -> ISSTelemetryClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = ISSTelemetryClient()
    return _client
