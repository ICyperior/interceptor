"""WeFax auto-capture scheduler.

Automatically captures WeFax broadcasts based on station broadcast schedules.
Uses threading.Timer for scheduling — no external dependencies required.

Unlike the weather satellite scheduler which uses TLE-based orbital prediction,
WeFax stations broadcast on fixed UTC schedules, making scheduling simpler.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from utils.logging import get_logger
from utils.wefax import get_wefax_decoder
from utils.wefax_stations import get_station

logger = get_logger('intercept.wefax_scheduler')

# Import config defaults
try:
    from config import (
        WEFAX_CAPTURE_BUFFER_SECONDS,
        WEFAX_SCHEDULE_REFRESH_MINUTES,
    )
except ImportError:
    WEFAX_SCHEDULE_REFRESH_MINUTES = 30
    WEFAX_CAPTURE_BUFFER_SECONDS = 30


class ScheduledBroadcast:
    """A broadcast scheduled for automatic capture."""

    def __init__(
        self,
        station: str,
        callsign: str,
        frequency_khz: float,
        utc_time: str,
        duration_min: int,
        content: str,
    ):
        self.id: str = str(uuid.uuid4())[:8]
        self.station = station
        self.callsign = callsign
        self.frequency_khz = frequency_khz
        self.utc_time = utc_time
        self.duration_min = duration_min
        self.content = content
        self.status: str = 'scheduled'  # scheduled, capturing, complete, skipped
        self._timer: threading.Timer | None = None
        self._stop_timer: threading.Timer | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'station': self.station,
            'callsign': self.callsign,
            'frequency_khz': self.frequency_khz,
            'utc_time': self.utc_time,
            'duration_min': self.duration_min,
            'content': self.content,
            'status': self.status,
        }


class WeFaxScheduler:
    """Auto-scheduler for WeFax broadcast captures."""

    def __init__(self):
        self._enabled = False
        self._lock = threading.Lock()
        self._broadcasts: list[ScheduledBroadcast] = []
        self._refresh_timer: threading.Timer | None = None
        self._station: str = ''
        self._callsign: str = ''
        self._frequency_khz: float = 0.0
        self._device: int = 0
        self._gain: float = 40.0
        self._ioc: int = 576
        self._lpm: int = 120
        self._direct_sampling: bool = True
        self._progress_callback: Callable[[dict], None] | None = None
        self._event_callback: Callable[[dict[str, Any]], None] | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_callbacks(
        self,
        progress_callback: Callable[[dict], None],
        event_callback: Callable[[dict[str, Any]], None],
    ) -> None:
        """Set callbacks for progress and scheduler events."""
        self._progress_callback = progress_callback
        self._event_callback = event_callback

    def enable(
        self,
        station: str,
        frequency_khz: float,
        device: int = 0,
        gain: float = 40.0,
        ioc: int = 576,
        lpm: int = 120,
        direct_sampling: bool = True,
    ) -> dict[str, Any]:
        """Enable auto-scheduling for a station/frequency.

        Args:
            station: Station callsign.
            frequency_khz: Frequency in kHz.
            device: RTL-SDR device index.
            gain: SDR gain in dB.
            ioc: Index of Cooperation (576 or 288).
            lpm: Lines per minute (120 or 60).
            direct_sampling: Enable direct sampling for HF.

        Returns:
            Status dict with scheduled broadcasts.
        """
        station_data = get_station(station)
        if not station_data:
            return {'status': 'error', 'message': f'Station {station} not found'}

        with self._lock:
            self._station = station_data.get('name', station)
            self._callsign = station
            self._frequency_khz = frequency_khz
            self._device = device
            self._gain = gain
            self._ioc = ioc
            self._lpm = lpm
            self._direct_sampling = direct_sampling
            self._enabled = True

        self._refresh_schedule()

        return self.get_status()

    def disable(self) -> dict[str, Any]:
        """Disable auto-scheduling and cancel all timers."""
        with self._lock:
            self._enabled = False

            # Cancel refresh timer
            if self._refresh_timer:
                self._refresh_timer.cancel()
                self._refresh_timer = None

            # Cancel all broadcast timers
            for b in self._broadcasts:
                if b._timer:
                    b._timer.cancel()
                    b._timer = None
                if b._stop_timer:
                    b._stop_timer.cancel()
                    b._stop_timer = None

            self._broadcasts.clear()

        logger.info("WeFax auto-scheduler disabled")
        return {'status': 'disabled'}

    def skip_broadcast(self, broadcast_id: str) -> bool:
        """Manually skip a scheduled broadcast."""
        with self._lock:
            for b in self._broadcasts:
                if b.id == broadcast_id and b.status == 'scheduled':
                    b.status = 'skipped'
                    if b._timer:
                        b._timer.cancel()
                        b._timer = None
                    logger.info(
                        "Skipped broadcast: %s at %s", b.content, b.utc_time
                    )
                    self._emit_event({
                        'type': 'schedule_capture_skipped',
                        'broadcast': b.to_dict(),
                        'reason': 'manual',
                    })
                    return True
        return False

    def get_status(self) -> dict[str, Any]:
        """Get current scheduler status."""
        with self._lock:
            return {
                'enabled': self._enabled,
                'station': self._station,
                'callsign': self._callsign,
                'frequency_khz': self._frequency_khz,
                'device': self._device,
                'gain': self._gain,
                'ioc': self._ioc,
                'lpm': self._lpm,
                'scheduled_count': sum(
                    1 for b in self._broadcasts if b.status == 'scheduled'
                ),
                'total_broadcasts': len(self._broadcasts),
            }

    def get_broadcasts(self) -> list[dict[str, Any]]:
        """Get list of scheduled broadcasts."""
        with self._lock:
            return [b.to_dict() for b in self._broadcasts]

    def _refresh_schedule(self) -> None:
        """Recompute broadcast schedule and set timers."""
        if not self._enabled:
            return

        station_data = get_station(self._callsign)
        if not station_data:
            logger.error("Station %s not found during refresh", self._callsign)
            return

        schedule = station_data.get('schedule', [])

        with self._lock:
            # Cancel existing timers
            for b in self._broadcasts:
                if b._timer:
                    b._timer.cancel()
                if b._stop_timer:
                    b._stop_timer.cancel()

            # Keep completed/skipped for history, replace scheduled
            history = [
                b for b in self._broadcasts
                if b.status in ('complete', 'skipped', 'capturing')
            ]
            self._broadcasts = history

            now = datetime.now(timezone.utc)
            buffer = WEFAX_CAPTURE_BUFFER_SECONDS

            for entry in schedule:
                utc_time = entry.get('utc', '')
                duration_min = entry.get('duration_min', 20)
                content = entry.get('content', '')

                parts = utc_time.split(':')
                if len(parts) != 2:
                    continue

                try:
                    hour = int(parts[0])
                    minute = int(parts[1])
                except ValueError:
                    continue

                # Compute next occurrence (today or tomorrow)
                broadcast_dt = now.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                capture_end = broadcast_dt + timedelta(
                    minutes=duration_min, seconds=buffer
                )

                # If the broadcast end is already past, schedule for tomorrow
                if capture_end <= now:
                    broadcast_dt += timedelta(days=1)
                    capture_end = broadcast_dt + timedelta(
                        minutes=duration_min, seconds=buffer
                    )

                capture_start = broadcast_dt - timedelta(seconds=buffer)

                # Check if already in history
                history_key = f"{self._callsign}_{utc_time}"
                if any(
                    f"{h.callsign}_{h.utc_time}" == history_key
                    for h in history
                ):
                    continue

                sb = ScheduledBroadcast(
                    station=self._station,
                    callsign=self._callsign,
                    frequency_khz=self._frequency_khz,
                    utc_time=utc_time,
                    duration_min=duration_min,
                    content=content,
                )

                # Schedule capture timer
                delay = max(0.0, (capture_start - now).total_seconds())
                sb._timer = threading.Timer(
                    delay, self._execute_capture, args=[sb]
                )
                sb._timer.daemon = True
                sb._timer.start()

                self._broadcasts.append(sb)

            logger.info(
                "WeFax scheduler refreshed: %d broadcasts scheduled",
                sum(1 for b in self._broadcasts if b.status == 'scheduled'),
            )

        # Schedule next refresh
        if self._refresh_timer:
            self._refresh_timer.cancel()
        self._refresh_timer = threading.Timer(
            WEFAX_SCHEDULE_REFRESH_MINUTES * 60,
            self._refresh_schedule,
        )
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def _execute_capture(self, sb: ScheduledBroadcast) -> None:
        """Execute capture for a scheduled broadcast."""
        if not self._enabled or sb.status != 'scheduled':
            return

        decoder = get_wefax_decoder()

        if decoder.is_running:
            logger.info("Decoder busy, skipping scheduled broadcast: %s", sb.content)
            sb.status = 'skipped'
            self._emit_event({
                'type': 'schedule_capture_skipped',
                'broadcast': sb.to_dict(),
                'reason': 'decoder_busy',
            })
            return

        # Claim SDR device
        try:
            import app as app_module
            error = app_module.claim_sdr_device(self._device, 'wefax')
            if error:
                logger.info(
                    "SDR device busy, skipping: %s - %s", sb.content, error
                )
                sb.status = 'skipped'
                self._emit_event({
                    'type': 'schedule_capture_skipped',
                    'broadcast': sb.to_dict(),
                    'reason': 'device_busy',
                })
                return
        except ImportError:
            pass

        sb.status = 'capturing'

        # Set up callbacks
        if self._progress_callback:
            decoder.set_callback(self._progress_callback)

        def _release_device():
            try:
                import app as app_module
                app_module.release_sdr_device(self._device)
            except ImportError:
                pass

        success = decoder.start(
            frequency_khz=self._frequency_khz,
            station=self._callsign,
            device_index=self._device,
            gain=self._gain,
            ioc=self._ioc,
            lpm=self._lpm,
            direct_sampling=self._direct_sampling,
        )

        if success:
            logger.info("Auto-scheduler started capture: %s", sb.content)
            self._emit_event({
                'type': 'schedule_capture_start',
                'broadcast': sb.to_dict(),
            })

            # Schedule stop timer at broadcast end + buffer
            now = datetime.now(timezone.utc)
            parts = sb.utc_time.split(':')
            broadcast_dt = now.replace(
                hour=int(parts[0]), minute=int(parts[1]),
                second=0, microsecond=0,
            )
            if broadcast_dt < now - timedelta(hours=1):
                broadcast_dt += timedelta(days=1)
            stop_dt = broadcast_dt + timedelta(
                minutes=sb.duration_min,
                seconds=WEFAX_CAPTURE_BUFFER_SECONDS,
            )
            stop_delay = max(0.0, (stop_dt - now).total_seconds())

            if stop_delay > 0:
                sb._stop_timer = threading.Timer(
                    stop_delay, self._stop_capture, args=[sb, _release_device]
                )
                sb._stop_timer.daemon = True
                sb._stop_timer.start()
        else:
            sb.status = 'skipped'
            _release_device()
            self._emit_event({
                'type': 'schedule_capture_skipped',
                'broadcast': sb.to_dict(),
                'reason': 'start_failed',
                'detail': decoder.last_error or 'unknown error',
            })

    def _stop_capture(
        self, sb: ScheduledBroadcast, release_fn: Callable
    ) -> None:
        """Stop capture at broadcast end."""
        decoder = get_wefax_decoder()
        if decoder.is_running:
            decoder.stop()
            logger.info("Auto-scheduler stopped capture: %s", sb.content)

        sb.status = 'complete'
        release_fn()
        self._emit_event({
            'type': 'schedule_capture_complete',
            'broadcast': sb.to_dict(),
        })

    def _emit_event(self, event: dict[str, Any]) -> None:
        """Emit scheduler event to callback."""
        if self._event_callback:
            try:
                self._event_callback(event)
            except Exception as e:
                logger.error("Error in scheduler event callback: %s", e)


# Singleton
_scheduler: WeFaxScheduler | None = None
_scheduler_lock = threading.Lock()


def get_wefax_scheduler() -> WeFaxScheduler:
    """Get or create the global WeFax scheduler instance."""
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = WeFaxScheduler()
    return _scheduler
