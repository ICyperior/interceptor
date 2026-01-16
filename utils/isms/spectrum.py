"""
Spectrum analysis using rtl_power.

Provides functions to scan RF spectrum, compute band metrics,
and detect signal anomalies.
"""

from __future__ import annotations

import csv
import io
import logging
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev
from typing import Generator

logger = logging.getLogger('intercept.isms.spectrum')


@dataclass
class SpectrumBin:
    """A single frequency bin from rtl_power output."""
    freq_hz: float
    power_db: float
    timestamp: datetime
    freq_start: float = 0.0
    freq_end: float = 0.0

    @property
    def freq_mhz(self) -> float:
        """Frequency in MHz."""
        return self.freq_hz / 1_000_000


@dataclass
class BandMetrics:
    """Computed metrics for a frequency band."""
    band_name: str
    freq_start_mhz: float
    freq_end_mhz: float
    noise_floor_db: float
    peak_frequency_mhz: float
    peak_power_db: float
    activity_score: float  # 0-100 based on variance/peaks above noise
    bin_count: int = 0
    avg_power_db: float = 0.0
    power_variance: float = 0.0
    peaks_above_threshold: int = 0


@dataclass
class BurstEvent:
    """Detected burst/transient signal."""
    freq_mhz: float
    power_db: float
    timestamp: datetime
    duration_estimate: float = 0.0
    above_noise_db: float = 0.0


def get_rtl_power_path() -> str | None:
    """Get the path to rtl_power executable."""
    return shutil.which('rtl_power')


def _drain_stderr(process: subprocess.Popen, stop_event: threading.Event) -> None:
    """Drain stderr to prevent buffer deadlock."""
    try:
        while not stop_event.is_set() and process.poll() is None:
            if process.stderr:
                process.stderr.read(1024)
    except Exception:
        pass


def run_rtl_power_scan(
    freq_start_mhz: float,
    freq_end_mhz: float,
    bin_size_hz: int = 10000,
    integration_time: float = 1.0,
    device_index: int = 0,
    gain: int = 40,
    ppm: int = 0,
    single_shot: bool = False,
    output_file: Path | None = None,
) -> Generator[SpectrumBin, None, None]:
    """
    Run rtl_power and yield spectrum bins.

    Args:
        freq_start_mhz: Start frequency in MHz
        freq_end_mhz: End frequency in MHz
        bin_size_hz: Frequency bin size in Hz
        integration_time: Integration time per sweep in seconds
        device_index: RTL-SDR device index
        gain: Gain in dB (0 for auto)
        ppm: Frequency correction in PPM
        single_shot: If True, exit after one complete sweep
        output_file: Optional file to write CSV output

    Yields:
        SpectrumBin objects for each frequency bin
    """
    rtl_power = get_rtl_power_path()
    if not rtl_power:
        logger.error("rtl_power not found in PATH")
        return

    # Build command
    freq_range = f'{freq_start_mhz}M:{freq_end_mhz}M:{bin_size_hz}'

    cmd = [
        rtl_power,
        '-f', freq_range,
        '-i', str(integration_time),
        '-d', str(device_index),
        '-g', str(gain),
        '-p', str(ppm),
    ]

    if single_shot:
        cmd.extend(['-1'])  # Single shot mode

    # Use temp file if not provided
    if output_file is None:
        temp_fd, temp_path = tempfile.mkstemp(suffix='.csv', prefix='rtl_power_')
        output_file = Path(temp_path)
        cleanup_temp = True
    else:
        cleanup_temp = False

    cmd.extend(['-c', '0'])  # Continuous output to stdout

    logger.info(f"Starting rtl_power: {' '.join(cmd)}")

    stop_event = threading.Event()
    stderr_thread = None

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        # Drain stderr in background to prevent deadlock
        stderr_thread = threading.Thread(
            target=_drain_stderr,
            args=(process, stop_event),
            daemon=True
        )
        stderr_thread.start()

        # Parse CSV output line by line
        # rtl_power format: date, time, freq_low, freq_high, step, samples, db_values...
        for line in iter(process.stdout.readline, ''):
            line = line.strip()
            if not line:
                continue

            try:
                parts = line.split(',')
                if len(parts) < 7:
                    continue

                # Parse timestamp
                date_str = parts[0].strip()
                time_str = parts[1].strip()
                try:
                    timestamp = datetime.strptime(
                        f'{date_str} {time_str}',
                        '%Y-%m-%d %H:%M:%S'
                    )
                except ValueError:
                    timestamp = datetime.now()

                # Parse frequency range
                freq_low = float(parts[2])
                freq_high = float(parts[3])
                freq_step = float(parts[4])
                # samples = int(parts[5])

                # Parse power values
                db_values = [float(v) for v in parts[6:] if v.strip()]

                # Yield each bin
                current_freq = freq_low
                for db_value in db_values:
                    yield SpectrumBin(
                        freq_hz=current_freq,
                        power_db=db_value,
                        timestamp=timestamp,
                        freq_start=freq_low,
                        freq_end=freq_high,
                    )
                    current_freq += freq_step

            except (ValueError, IndexError) as e:
                logger.debug(f"Failed to parse rtl_power line: {e}")
                continue

    except Exception as e:
        logger.error(f"rtl_power error: {e}")

    finally:
        stop_event.set()
        if stderr_thread:
            stderr_thread.join(timeout=1.0)
        if cleanup_temp and output_file.exists():
            output_file.unlink()


def compute_band_metrics(
    bins: list[SpectrumBin],
    band_name: str = 'Unknown',
    noise_percentile: float = 10.0,
    activity_threshold_db: float = 6.0,
) -> BandMetrics:
    """
    Compute metrics from spectrum bins.

    Args:
        bins: List of SpectrumBin objects
        band_name: Name for this band
        noise_percentile: Percentile to use for noise floor estimation
        activity_threshold_db: dB above noise to count as activity

    Returns:
        BandMetrics with computed values
    """
    if not bins:
        return BandMetrics(
            band_name=band_name,
            freq_start_mhz=0,
            freq_end_mhz=0,
            noise_floor_db=-100,
            peak_frequency_mhz=0,
            peak_power_db=-100,
            activity_score=0,
        )

    powers = [b.power_db for b in bins]
    freqs = [b.freq_mhz for b in bins]

    # Sort for percentile calculation
    sorted_powers = sorted(powers)
    noise_idx = int(len(sorted_powers) * noise_percentile / 100)
    noise_floor = sorted_powers[noise_idx] if noise_idx < len(sorted_powers) else sorted_powers[0]

    # Find peak
    peak_idx = powers.index(max(powers))
    peak_power = powers[peak_idx]
    peak_freq = freqs[peak_idx]

    # Calculate activity score
    # Based on: variance of power levels and count of peaks above threshold
    threshold = noise_floor + activity_threshold_db
    peaks_above = sum(1 for p in powers if p > threshold)

    # Calculate variance
    try:
        power_var = stdev(powers) ** 2 if len(powers) > 1 else 0
    except Exception:
        power_var = 0

    # Activity score: combination of peak ratio and variance
    peak_ratio = peaks_above / len(bins) if bins else 0
    # Normalize variance (typical range 0-100 dB^2)
    var_component = min(power_var / 100, 1.0)

    # Weighted combination
    activity_score = min(100, (peak_ratio * 70 + var_component * 30))

    return BandMetrics(
        band_name=band_name,
        freq_start_mhz=min(freqs),
        freq_end_mhz=max(freqs),
        noise_floor_db=noise_floor,
        peak_frequency_mhz=peak_freq,
        peak_power_db=peak_power,
        activity_score=activity_score,
        bin_count=len(bins),
        avg_power_db=mean(powers) if powers else -100,
        power_variance=power_var,
        peaks_above_threshold=peaks_above,
    )


def detect_bursts(
    bins: list[SpectrumBin],
    threshold_db: float = 10.0,
    min_power_db: float = -80.0,
    noise_floor_db: float | None = None,
) -> list[BurstEvent]:
    """
    Detect short bursts above noise floor.

    Args:
        bins: List of SpectrumBin objects (should be time-ordered for one frequency)
        threshold_db: dB above noise to consider a burst
        min_power_db: Minimum absolute power to consider
        noise_floor_db: Noise floor (computed if not provided)

    Returns:
        List of detected BurstEvent objects
    """
    if not bins:
        return []

    # Estimate noise floor if not provided
    if noise_floor_db is None:
        sorted_powers = sorted(b.power_db for b in bins)
        noise_idx = int(len(sorted_powers) * 0.1)  # 10th percentile
        noise_floor_db = sorted_powers[noise_idx]

    threshold = noise_floor_db + threshold_db
    threshold = max(threshold, min_power_db)

    bursts = []

    for bin_data in bins:
        if bin_data.power_db > threshold:
            bursts.append(BurstEvent(
                freq_mhz=bin_data.freq_mhz,
                power_db=bin_data.power_db,
                timestamp=bin_data.timestamp,
                above_noise_db=bin_data.power_db - noise_floor_db,
            ))

    return bursts


def parse_rtl_power_csv(csv_path: Path) -> list[SpectrumBin]:
    """
    Parse an rtl_power CSV file.

    Args:
        csv_path: Path to CSV file

    Returns:
        List of SpectrumBin objects
    """
    bins = []

    with open(csv_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                parts = line.split(',')
                if len(parts) < 7:
                    continue

                date_str = parts[0].strip()
                time_str = parts[1].strip()
                try:
                    timestamp = datetime.strptime(
                        f'{date_str} {time_str}',
                        '%Y-%m-%d %H:%M:%S'
                    )
                except ValueError:
                    timestamp = datetime.now()

                freq_low = float(parts[2])
                freq_step = float(parts[4])
                db_values = [float(v) for v in parts[6:] if v.strip()]

                current_freq = freq_low
                for db_value in db_values:
                    bins.append(SpectrumBin(
                        freq_hz=current_freq,
                        power_db=db_value,
                        timestamp=timestamp,
                    ))
                    current_freq += freq_step

            except (ValueError, IndexError):
                continue

    return bins


def group_bins_by_band(
    bins: list[SpectrumBin],
    band_ranges: dict[str, tuple[float, float]],
) -> dict[str, list[SpectrumBin]]:
    """
    Group spectrum bins by predefined band ranges.

    Args:
        bins: List of SpectrumBin objects
        band_ranges: Dict mapping band name to (start_mhz, end_mhz)

    Returns:
        Dict mapping band name to list of bins in that band
    """
    grouped: dict[str, list[SpectrumBin]] = {name: [] for name in band_ranges}

    for bin_data in bins:
        freq_mhz = bin_data.freq_mhz
        for band_name, (start, end) in band_ranges.items():
            if start <= freq_mhz <= end:
                grouped[band_name].append(bin_data)
                break

    return grouped
