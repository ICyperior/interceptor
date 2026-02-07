"""IQ processing pipelines for live waterfall during SDR decoding.

Provides two pipeline functions:
- run_fm_iq_pipeline: FM demodulates IQ for pager decoding + FFT for waterfall
- run_passthrough_iq_pipeline: Passes raw IQ to rtl_433 + FFT for waterfall
"""

from __future__ import annotations

import logging
import struct
import threading
import queue
from datetime import datetime
from typing import IO, Optional

import numpy as np

logger = logging.getLogger('intercept.iq_processor')

# FFT parameters
FFT_SIZE = 2048
FFT_INTERVAL_SECONDS = 0.1  # ~10 updates/sec


def iq_to_complex(buf: bytes) -> np.ndarray:
    """Convert raw uint8 IQ bytes to complex float samples.

    RTL-SDR outputs interleaved uint8 I/Q pairs centered at 127.5.
    """
    raw = np.frombuffer(buf, dtype=np.uint8).astype(np.float32)
    raw = (raw - 127.5) / 127.5
    return raw[0::2] + 1j * raw[1::2]


def compute_fft_bins(samples: np.ndarray, fft_size: int = FFT_SIZE) -> list[float]:
    """Compute power spectral density in dB from complex IQ samples.

    Returns a list of power values (dB) for each frequency bin.
    """
    if len(samples) < fft_size:
        # Pad with zeros if not enough samples
        padded = np.zeros(fft_size, dtype=np.complex64)
        padded[:len(samples)] = samples[:fft_size]
        samples = padded
    else:
        samples = samples[:fft_size]

    # Apply Hanning window to reduce spectral leakage
    window = np.hanning(fft_size).astype(np.float32)
    windowed = samples * window

    # FFT and shift DC to center
    spectrum = np.fft.fftshift(np.fft.fft(windowed))

    # Power in dB (avoid log of zero)
    power = np.abs(spectrum) ** 2
    power = np.maximum(power, 1e-20)
    power_db = 10.0 * np.log10(power)

    return power_db.tolist()


def _push_waterfall(waterfall_queue: queue.Queue, bins: list[float],
                    center_freq_mhz: float, sample_rate: int) -> None:
    """Push a waterfall sweep message to the queue."""
    half_span = (sample_rate / 1e6) / 2.0
    msg = {
        'type': 'waterfall_sweep',
        'start_freq': center_freq_mhz - half_span,
        'end_freq': center_freq_mhz + half_span,
        'bins': bins,
        'timestamp': datetime.now().isoformat(),
    }
    try:
        waterfall_queue.put_nowait(msg)
    except queue.Full:
        # Drop oldest and retry
        try:
            waterfall_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            waterfall_queue.put_nowait(msg)
        except queue.Full:
            pass


def run_fm_iq_pipeline(
    iq_stdout: IO[bytes],
    audio_stdin: IO[bytes],
    waterfall_queue: queue.Queue,
    center_freq_mhz: float,
    sample_rate: int,
    stop_event: threading.Event,
) -> None:
    """FM demodulation pipeline: IQ -> FFT + FM demod -> 22050 Hz PCM.

    Reads raw uint8 IQ from rtl_sdr stdout, computes FFT for waterfall,
    FM demodulates, decimates to 22050 Hz, and writes 16-bit PCM to
    multimon-ng stdin.

    Args:
        iq_stdout: rtl_sdr stdout (raw uint8 IQ)
        audio_stdin: multimon-ng stdin (16-bit PCM)
        waterfall_queue: Queue for waterfall sweep messages
        center_freq_mhz: Center frequency in MHz
        sample_rate: IQ sample rate (should be 220500 for 10x decimation to 22050)
        stop_event: Threading event to signal shutdown
    """
    from scipy.signal import decimate as scipy_decimate

    # Decimation factor: sample_rate / 22050
    decim_factor = sample_rate // 22050
    if decim_factor < 1:
        decim_factor = 1

    # Read in chunks: ~100ms worth of IQ data (2 bytes per sample: I + Q)
    chunk_bytes = int(sample_rate * FFT_INTERVAL_SECONDS) * 2
    # Align to even number of bytes (I/Q pairs)
    chunk_bytes = (chunk_bytes // 2) * 2

    # Previous sample for FM demod continuity
    prev_sample = np.complex64(0)

    logger.info(f"FM IQ pipeline started: {center_freq_mhz} MHz, "
                f"sr={sample_rate}, decim={decim_factor}")

    try:
        while not stop_event.is_set():
            raw = iq_stdout.read(chunk_bytes)
            if not raw:
                break

            # Convert to complex IQ
            iq = iq_to_complex(raw)
            if len(iq) == 0:
                continue

            # Compute FFT for waterfall
            bins = compute_fft_bins(iq, FFT_SIZE)
            _push_waterfall(waterfall_queue, bins, center_freq_mhz, sample_rate)

            # FM demodulation via instantaneous phase difference
            # Prepend previous sample for continuity
            iq_with_prev = np.concatenate(([prev_sample], iq))
            prev_sample = iq[-1]

            phase_diff = np.angle(iq_with_prev[1:] * np.conj(iq_with_prev[:-1]))

            # Decimate to 22050 Hz
            if decim_factor > 1:
                audio = scipy_decimate(phase_diff, decim_factor, ftype='fir')
            else:
                audio = phase_diff

            # Scale to 16-bit PCM range
            audio = np.clip(audio * 10000, -32767, 32767).astype(np.int16)

            # Write to multimon-ng
            try:
                audio_stdin.write(audio.tobytes())
                audio_stdin.flush()
            except (BrokenPipeError, OSError):
                break

    except Exception as e:
        logger.error(f"FM IQ pipeline error: {e}")
    finally:
        logger.info("FM IQ pipeline stopped")
        try:
            audio_stdin.close()
        except Exception:
            pass


def run_passthrough_iq_pipeline(
    iq_stdout: IO[bytes],
    decoder_stdin: IO[bytes],
    waterfall_queue: queue.Queue,
    center_freq_mhz: float,
    sample_rate: int,
    stop_event: threading.Event,
) -> None:
    """Passthrough pipeline: IQ -> FFT + raw bytes to decoder.

    Reads raw uint8 IQ from rtl_sdr stdout, computes FFT for waterfall,
    and writes raw IQ bytes unchanged to rtl_433 stdin.

    Args:
        iq_stdout: rtl_sdr stdout (raw uint8 IQ)
        decoder_stdin: rtl_433 stdin (raw cu8 IQ)
        waterfall_queue: Queue for waterfall sweep messages
        center_freq_mhz: Center frequency in MHz
        sample_rate: IQ sample rate (should be 250000 for rtl_433)
        stop_event: Threading event to signal shutdown
    """
    # Read in chunks: ~100ms worth of IQ data
    chunk_bytes = int(sample_rate * FFT_INTERVAL_SECONDS) * 2
    chunk_bytes = (chunk_bytes // 2) * 2

    logger.info(f"Passthrough IQ pipeline started: {center_freq_mhz} MHz, sr={sample_rate}")

    try:
        while not stop_event.is_set():
            raw = iq_stdout.read(chunk_bytes)
            if not raw:
                break

            # Compute FFT for waterfall
            iq = iq_to_complex(raw)
            if len(iq) > 0:
                bins = compute_fft_bins(iq, FFT_SIZE)
                _push_waterfall(waterfall_queue, bins, center_freq_mhz, sample_rate)

            # Pass raw bytes unchanged to decoder
            try:
                decoder_stdin.write(raw)
                decoder_stdin.flush()
            except (BrokenPipeError, OSError):
                break

    except Exception as e:
        logger.error(f"Passthrough IQ pipeline error: {e}")
    finally:
        logger.info("Passthrough IQ pipeline stopped")
        try:
            decoder_stdin.close()
        except Exception:
            pass
