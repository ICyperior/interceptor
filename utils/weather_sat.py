"""Weather Satellite decoder for NOAA APT and Meteor LRPT imagery.

Provides automated capture and decoding of weather satellite images using SatDump.

Supported satellites:
    - NOAA-15: 137.620 MHz (APT)
    - NOAA-18: 137.9125 MHz (APT)
    - NOAA-19: 137.100 MHz (APT)
    - Meteor-M2-3: 137.900 MHz (LRPT)

Uses SatDump CLI for live SDR capture and decoding, with fallback to
rtl_fm capture for manual decoding when SatDump is unavailable.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable

from utils.logging import get_logger

logger = get_logger('intercept.weather_sat')


# Weather satellite definitions
WEATHER_SATELLITES = {
    'NOAA-15': {
        'name': 'NOAA 15',
        'frequency': 137.620,
        'mode': 'APT',
        'pipeline': 'noaa_apt',
        'tle_key': 'NOAA-15',
        'description': 'NOAA-15 APT (analog weather imagery)',
        'active': True,
    },
    'NOAA-18': {
        'name': 'NOAA 18',
        'frequency': 137.9125,
        'mode': 'APT',
        'pipeline': 'noaa_apt',
        'tle_key': 'NOAA-18',
        'description': 'NOAA-18 APT (analog weather imagery)',
        'active': True,
    },
    'NOAA-19': {
        'name': 'NOAA 19',
        'frequency': 137.100,
        'mode': 'APT',
        'pipeline': 'noaa_apt',
        'tle_key': 'NOAA-19',
        'description': 'NOAA-19 APT (analog weather imagery)',
        'active': True,
    },
    'METEOR-M2-3': {
        'name': 'Meteor-M2-3',
        'frequency': 137.900,
        'mode': 'LRPT',
        'pipeline': 'meteor_m2-x_lrpt',
        'tle_key': 'METEOR-M2-3',
        'description': 'Meteor-M2-3 LRPT (digital color imagery)',
        'active': True,
    },
}

# Default sample rate for weather satellite reception
DEFAULT_SAMPLE_RATE = 1000000  # 1 MHz


@dataclass
class WeatherSatImage:
    """Decoded weather satellite image."""
    filename: str
    path: Path
    satellite: str
    mode: str  # APT or LRPT
    timestamp: datetime
    frequency: float
    size_bytes: int = 0
    product: str = ''  # e.g. 'RGB', 'Thermal', 'Channel 1'

    def to_dict(self) -> dict:
        return {
            'filename': self.filename,
            'satellite': self.satellite,
            'mode': self.mode,
            'timestamp': self.timestamp.isoformat(),
            'frequency': self.frequency,
            'size_bytes': self.size_bytes,
            'product': self.product,
            'url': f'/weather-sat/images/{self.filename}',
        }


@dataclass
class CaptureProgress:
    """Weather satellite capture/decode progress update."""
    status: str  # 'idle', 'capturing', 'decoding', 'complete', 'error'
    satellite: str = ''
    frequency: float = 0.0
    mode: str = ''
    message: str = ''
    progress_percent: int = 0
    elapsed_seconds: int = 0
    image: WeatherSatImage | None = None

    def to_dict(self) -> dict:
        result = {
            'type': 'weather_sat_progress',
            'status': self.status,
            'satellite': self.satellite,
            'frequency': self.frequency,
            'mode': self.mode,
            'message': self.message,
            'progress': self.progress_percent,
            'elapsed_seconds': self.elapsed_seconds,
        }
        if self.image:
            result['image'] = self.image.to_dict()
        return result


class WeatherSatDecoder:
    """Weather satellite decoder using SatDump CLI.

    Manages live SDR capture and decoding of NOAA APT and Meteor LRPT
    satellite transmissions.
    """

    def __init__(self, output_dir: str | Path | None = None):
        self._process: subprocess.Popen | None = None
        self._running = False
        self._lock = threading.Lock()
        self._callback: Callable[[CaptureProgress], None] | None = None
        self._output_dir = Path(output_dir) if output_dir else Path('data/weather_sat')
        self._images: list[WeatherSatImage] = []
        self._reader_thread: threading.Thread | None = None
        self._watcher_thread: threading.Thread | None = None
        self._current_satellite: str = ''
        self._current_frequency: float = 0.0
        self._current_mode: str = ''
        self._capture_start_time: float = 0
        self._device_index: int = 0
        self._capture_output_dir: Path | None = None

        # Ensure output directory exists
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Detect available decoder
        self._decoder = self._detect_decoder()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def decoder_available(self) -> str | None:
        """Return name of available decoder or None."""
        return self._decoder

    @property
    def current_satellite(self) -> str:
        return self._current_satellite

    @property
    def current_frequency(self) -> float:
        return self._current_frequency

    def _detect_decoder(self) -> str | None:
        """Detect which weather satellite decoder is available."""
        if shutil.which('satdump'):
            logger.info("SatDump decoder detected")
            return 'satdump'

        logger.warning(
            "SatDump not found. Install SatDump for weather satellite decoding. "
            "See: https://github.com/SatDump/SatDump"
        )
        return None

    def set_callback(self, callback: Callable[[CaptureProgress], None]) -> None:
        """Set callback for capture progress updates."""
        self._callback = callback

    def start(
        self,
        satellite: str,
        device_index: int = 0,
        gain: float = 40.0,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        bias_t: bool = False,
    ) -> bool:
        """Start weather satellite capture and decode.

        Args:
            satellite: Satellite key (e.g. 'NOAA-18', 'METEOR-M2-3')
            device_index: RTL-SDR device index
            gain: SDR gain in dB
            sample_rate: Sample rate in Hz
            bias_t: Enable bias-T power for LNA

        Returns:
            True if started successfully
        """
        with self._lock:
            if self._running:
                return True

            if not self._decoder:
                logger.error("No weather satellite decoder available")
                self._emit_progress(CaptureProgress(
                    status='error',
                    message='SatDump not installed. Build from source or install via package manager.'
                ))
                return False

            sat_info = WEATHER_SATELLITES.get(satellite)
            if not sat_info:
                logger.error(f"Unknown satellite: {satellite}")
                self._emit_progress(CaptureProgress(
                    status='error',
                    message=f'Unknown satellite: {satellite}'
                ))
                return False

            self._current_satellite = satellite
            self._current_frequency = sat_info['frequency']
            self._current_mode = sat_info['mode']
            self._device_index = device_index
            self._capture_start_time = time.time()

            try:
                self._start_satdump(sat_info, device_index, gain, sample_rate, bias_t)
                self._running = True

                logger.info(
                    f"Weather satellite capture started: {satellite} "
                    f"({sat_info['frequency']} MHz, {sat_info['mode']})"
                )
                self._emit_progress(CaptureProgress(
                    status='capturing',
                    satellite=satellite,
                    frequency=sat_info['frequency'],
                    mode=sat_info['mode'],
                    message=f"Capturing {sat_info['name']} on {sat_info['frequency']} MHz ({sat_info['mode']})..."
                ))

                return True

            except Exception as e:
                logger.error(f"Failed to start weather satellite capture: {e}")
                self._emit_progress(CaptureProgress(
                    status='error',
                    satellite=satellite,
                    message=str(e)
                ))
                return False

    def _start_satdump(
        self,
        sat_info: dict,
        device_index: int,
        gain: float,
        sample_rate: int,
        bias_t: bool,
    ) -> None:
        """Start SatDump live capture and decode."""
        # Create timestamped output directory for this capture
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        sat_name = sat_info['tle_key'].replace(' ', '_')
        self._capture_output_dir = self._output_dir / f"{sat_name}_{timestamp}"
        self._capture_output_dir.mkdir(parents=True, exist_ok=True)

        freq_hz = int(sat_info['frequency'] * 1_000_000)

        cmd = [
            'satdump', 'live',
            sat_info['pipeline'],
            'rtlsdr',
            '--source_id', str(device_index),
            '--frequency', str(freq_hz),
            '--samplerate', str(sample_rate),
            '--gain', str(gain),
            '--output_folder', str(self._capture_output_dir),
        ]

        if bias_t:
            cmd.append('--bias')

        logger.info(f"Starting SatDump: {' '.join(cmd)}")

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Start reader thread to monitor output
        self._reader_thread = threading.Thread(
            target=self._read_satdump_output, daemon=True
        )
        self._reader_thread.start()

        # Start image watcher thread
        self._watcher_thread = threading.Thread(
            target=self._watch_images, daemon=True
        )
        self._watcher_thread.start()

    def _read_satdump_output(self) -> None:
        """Read SatDump stdout/stderr for progress updates."""
        if not self._process or not self._process.stdout:
            return

        try:
            for line in iter(self._process.stdout.readline, ''):
                if not self._running:
                    break

                line = line.strip()
                if not line:
                    continue

                logger.debug(f"satdump: {line}")

                # Parse progress from SatDump output
                elapsed = int(time.time() - self._capture_start_time)

                # SatDump outputs progress info - parse key indicators
                if 'Progress' in line or 'progress' in line:
                    # Try to extract percentage
                    match = re.search(r'(\d+(?:\.\d+)?)\s*%', line)
                    pct = int(float(match.group(1))) if match else 0
                    self._emit_progress(CaptureProgress(
                        status='decoding',
                        satellite=self._current_satellite,
                        frequency=self._current_frequency,
                        mode=self._current_mode,
                        message=line,
                        progress_percent=pct,
                        elapsed_seconds=elapsed,
                    ))
                elif 'Saved' in line or 'saved' in line or 'Writing' in line:
                    self._emit_progress(CaptureProgress(
                        status='decoding',
                        satellite=self._current_satellite,
                        frequency=self._current_frequency,
                        mode=self._current_mode,
                        message=line,
                        elapsed_seconds=elapsed,
                    ))
                elif 'error' in line.lower() or 'fail' in line.lower():
                    self._emit_progress(CaptureProgress(
                        status='capturing',
                        satellite=self._current_satellite,
                        frequency=self._current_frequency,
                        mode=self._current_mode,
                        message=line,
                        elapsed_seconds=elapsed,
                    ))
                else:
                    # Generic progress update every ~10 seconds
                    if elapsed % 10 == 0:
                        self._emit_progress(CaptureProgress(
                            status='capturing',
                            satellite=self._current_satellite,
                            frequency=self._current_frequency,
                            mode=self._current_mode,
                            message=f"Capturing... ({elapsed}s elapsed)",
                            elapsed_seconds=elapsed,
                        ))

        except Exception as e:
            logger.error(f"Error reading SatDump output: {e}")
        finally:
            # Process ended
            if self._running:
                self._running = False
                elapsed = int(time.time() - self._capture_start_time)
                self._emit_progress(CaptureProgress(
                    status='complete',
                    satellite=self._current_satellite,
                    frequency=self._current_frequency,
                    mode=self._current_mode,
                    message=f"Capture complete ({elapsed}s)",
                    elapsed_seconds=elapsed,
                ))

    def _watch_images(self) -> None:
        """Watch output directory for new decoded images."""
        if not self._capture_output_dir:
            return

        known_files: set[str] = set()

        while self._running:
            time.sleep(2)

            try:
                # Recursively scan for image files
                for ext in ('*.png', '*.jpg', '*.jpeg'):
                    for filepath in self._capture_output_dir.rglob(ext):
                        if filepath.name in known_files:
                            continue

                        # Skip tiny files (likely incomplete)
                        try:
                            stat = filepath.stat()
                            if stat.st_size < 1000:
                                continue
                        except OSError:
                            continue

                        known_files.add(filepath.name)

                        # Determine product type from filename/path
                        product = self._parse_product_name(filepath)

                        # Copy image to main output dir for serving
                        serve_name = f"{self._current_satellite}_{filepath.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                        serve_path = self._output_dir / serve_name
                        try:
                            shutil.copy2(filepath, serve_path)
                        except OSError:
                            serve_path = filepath
                            serve_name = filepath.name

                        image = WeatherSatImage(
                            filename=serve_name,
                            path=serve_path,
                            satellite=self._current_satellite,
                            mode=self._current_mode,
                            timestamp=datetime.now(timezone.utc),
                            frequency=self._current_frequency,
                            size_bytes=stat.st_size,
                            product=product,
                        )
                        self._images.append(image)

                        logger.info(f"New weather satellite image: {serve_name} ({product})")
                        self._emit_progress(CaptureProgress(
                            status='complete',
                            satellite=self._current_satellite,
                            frequency=self._current_frequency,
                            mode=self._current_mode,
                            message=f'Image decoded: {product}',
                            image=image,
                        ))

            except Exception as e:
                logger.error(f"Error watching images: {e}")

    def _parse_product_name(self, filepath: Path) -> str:
        """Parse a human-readable product name from the image filepath."""
        name = filepath.stem.lower()
        parts = filepath.parts

        # Common SatDump product names
        if 'rgb' in name:
            return 'RGB Composite'
        if 'msa' in name or 'multispectral' in name:
            return 'Multispectral Analysis'
        if 'thermal' in name or 'temp' in name:
            return 'Thermal'
        if 'ndvi' in name:
            return 'NDVI Vegetation'
        if 'channel' in name or 'ch' in name:
            match = re.search(r'(?:channel|ch)\s*(\d+)', name)
            if match:
                return f'Channel {match.group(1)}'
        if 'avhrr' in name:
            return 'AVHRR'
        if 'msu' in name or 'mtvza' in name:
            return 'MSU-MR'

        # Check parent directories for clues
        for part in parts:
            if 'rgb' in part.lower():
                return 'RGB Composite'
            if 'channel' in part.lower():
                return 'Channel Data'

        return filepath.stem

    def stop(self) -> None:
        """Stop weather satellite capture."""
        with self._lock:
            self._running = False

            if self._process:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                except Exception:
                    try:
                        self._process.kill()
                    except Exception:
                        pass
                self._process = None

            elapsed = int(time.time() - self._capture_start_time) if self._capture_start_time else 0
            logger.info(f"Weather satellite capture stopped after {elapsed}s")

    def get_images(self) -> list[WeatherSatImage]:
        """Get list of decoded images."""
        self._scan_images()
        return list(self._images)

    def _scan_images(self) -> None:
        """Scan output directory for images not yet tracked."""
        known_filenames = {img.filename for img in self._images}

        for ext in ('*.png', '*.jpg', '*.jpeg'):
            for filepath in self._output_dir.glob(ext):
                if filepath.name in known_filenames:
                    continue
                # Skip tiny files
                try:
                    stat = filepath.stat()
                    if stat.st_size < 1000:
                        continue
                except OSError:
                    continue

                # Parse satellite name from filename
                satellite = 'Unknown'
                for sat_key in WEATHER_SATELLITES:
                    if sat_key in filepath.name:
                        satellite = sat_key
                        break

                sat_info = WEATHER_SATELLITES.get(satellite, {})

                image = WeatherSatImage(
                    filename=filepath.name,
                    path=filepath,
                    satellite=satellite,
                    mode=sat_info.get('mode', 'Unknown'),
                    timestamp=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    frequency=sat_info.get('frequency', 0.0),
                    size_bytes=stat.st_size,
                    product=self._parse_product_name(filepath),
                )
                self._images.append(image)

    def delete_image(self, filename: str) -> bool:
        """Delete a decoded image."""
        filepath = self._output_dir / filename
        if filepath.exists():
            try:
                filepath.unlink()
                self._images = [img for img in self._images if img.filename != filename]
                return True
            except OSError as e:
                logger.error(f"Failed to delete image {filename}: {e}")
        return False

    def _emit_progress(self, progress: CaptureProgress) -> None:
        """Emit progress update to callback."""
        if self._callback:
            try:
                self._callback(progress)
            except Exception as e:
                logger.error(f"Error in progress callback: {e}")

    def get_status(self) -> dict:
        """Get current decoder status."""
        elapsed = 0
        if self._running and self._capture_start_time:
            elapsed = int(time.time() - self._capture_start_time)

        return {
            'available': self._decoder is not None,
            'decoder': self._decoder,
            'running': self._running,
            'satellite': self._current_satellite,
            'frequency': self._current_frequency,
            'mode': self._current_mode,
            'elapsed_seconds': elapsed,
            'image_count': len(self._images),
        }


# Global decoder instance
_decoder: WeatherSatDecoder | None = None


def get_weather_sat_decoder() -> WeatherSatDecoder:
    """Get or create the global weather satellite decoder instance."""
    global _decoder
    if _decoder is None:
        _decoder = WeatherSatDecoder()
    return _decoder


def is_weather_sat_available() -> bool:
    """Check if weather satellite decoding is available."""
    decoder = get_weather_sat_decoder()
    return decoder.decoder_available is not None
