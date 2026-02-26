"""CW/Morse code decoder routes."""

from __future__ import annotations

import contextlib
import math
import os
import pty
import queue
import re
import select
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, jsonify, request

import app as app_module
from utils.event_pipeline import process_event
from utils.dependencies import get_tool_path
from utils.logging import sensor_logger as logger
from utils.morse import (
    decode_morse_wav_file,
)
from utils.process import register_process, safe_terminate, unregister_process
from utils.sdr import SDRFactory, SDRType
from utils.sse import sse_stream_fanout
from utils.validation import (
    validate_device_index,
    validate_frequency,
    validate_gain,
    validate_ppm,
)

morse_bp = Blueprint('morse', __name__)

# Track which device is being used
morse_active_device: int | None = None

# Runtime lifecycle state.
MORSE_IDLE = 'idle'
MORSE_STARTING = 'starting'
MORSE_RUNNING = 'running'
MORSE_STOPPING = 'stopping'
MORSE_ERROR = 'error'

morse_state = MORSE_IDLE
morse_state_message = 'Idle'
morse_state_since = time.monotonic()
morse_last_error = ''
morse_runtime_config: dict[str, Any] = {}
morse_session_id = 0

morse_decoder_worker: threading.Thread | None = None
morse_stderr_worker: threading.Thread | None = None
morse_relay_worker: threading.Thread | None = None
morse_stop_event: threading.Event | None = None
morse_control_queue: queue.Queue | None = None

MORSE_LINE_RE = re.compile(r'^\s*(?:MORSE(?:_CW)?(?:\([^)]*\))?)\s*:\s*(.*)$', re.IGNORECASE)


def _set_state(state: str, message: str = '', *, enqueue: bool = True, extra: dict[str, Any] | None = None) -> None:
    """Update lifecycle state and optionally emit a status queue event."""
    global morse_state, morse_state_message, morse_state_since
    morse_state = state
    morse_state_message = message or state
    morse_state_since = time.monotonic()

    if not enqueue:
        return

    payload: dict[str, Any] = {
        'type': 'status',
        'status': state,
        'state': state,
        'message': morse_state_message,
        'session_id': morse_session_id,
        'timestamp': time.strftime('%H:%M:%S'),
    }
    if extra:
        payload.update(extra)
    with contextlib.suppress(queue.Full):
        app_module.morse_queue.put_nowait(payload)


def _drain_queue(q: queue.Queue) -> None:
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break


def _join_thread(worker: threading.Thread | None, timeout_s: float) -> bool:
    if worker is None:
        return True
    worker.join(timeout=timeout_s)
    return not worker.is_alive()


def _close_pipe(pipe_obj: Any) -> None:
    if pipe_obj is None:
        return
    with contextlib.suppress(Exception):
        pipe_obj.close()


def _stdout_target_path() -> str:
    """Return the most reliable stdout path for rtl_* tools on this host."""
    if os.name == 'posix':
        for candidate in ('/proc/self/fd/1', '/dev/fd/1', '/dev/stdout'):
            if Path(candidate).exists():
                return candidate
    return '-'


def _queue_morse_event(payload: dict[str, Any]) -> None:
    with contextlib.suppress(queue.Full):
        app_module.morse_queue.put_nowait(payload)


def _parse_multimon_morse_text(line: str) -> str | None:
    cleaned = str(line or '').strip()
    if not cleaned:
        return None

    matched = MORSE_LINE_RE.match(cleaned)
    if matched:
        return matched.group(1).strip()

    lower = cleaned.lower()
    if lower.startswith(('multimon-ng', 'available demodulators', 'enabled demodulators')):
        return None

    if ':' in cleaned:
        label, payload = cleaned.split(':', 1)
        if 'morse' in label.upper():
            return payload.strip()
        return None

    if len(cleaned) <= 128 and re.fullmatch(r"[A-Za-z0-9 /.,'!?+\-]+", cleaned):
        return cleaned

    return None


def _emit_decoded_text(text: str) -> None:
    filtered = ''.join(ch for ch in str(text or '') if ch == ' ' or 32 <= ord(ch) <= 126)
    if not filtered:
        return

    timestamp = time.strftime('%H:%M:%S')
    for ch in filtered:
        if ch.isspace():
            _queue_morse_event({
                'type': 'morse_space',
                'timestamp': timestamp,
            })
        else:
            _queue_morse_event({
                'type': 'morse_char',
                'char': ch,
                'morse': '',
                'timestamp': timestamp,
            })


def _compress_amplitudes(samples: tuple[int, ...], bins: int = 96) -> list[int]:
    if not samples:
        return []

    step = max(1, len(samples) // bins)
    out: list[int] = []
    for idx in range(0, len(samples), step):
        if len(out) >= bins:
            break
        chunk = samples[idx:idx + step]
        if not chunk:
            continue
        out.append(int(sum(abs(v) for v in chunk) / len(chunk)))
    return out


def _read_pcm_chunk(
    stream: Any,
    chunk_bytes: int,
    stop_event: threading.Event,
    timeout_s: float = 0.2,
) -> bytes | None:
    if stream is None:
        return b''

    try:
        fileno = stream.fileno()
    except Exception:
        if stop_event.is_set():
            return b''
        with contextlib.suppress(Exception):
            return stream.read(chunk_bytes)
        return b''

    while not stop_event.is_set():
        try:
            ready, _, _ = select.select([fileno], [], [], timeout_s)
        except Exception:
            return b''

        if not ready:
            return None

        try:
            return os.read(fileno, chunk_bytes)
        except BlockingIOError:
            continue
        except OSError:
            return b''

    return b''


def _morse_audio_relay_thread(
    rtl_stdout: Any,
    multimon_stdin: Any,
    output_queue: queue.Queue,
    stop_event: threading.Event,
    control_queue: queue.Queue | None,
    runtime_config: dict[str, Any],
    pcm_ready_event: threading.Event,
) -> None:
    chunk_bytes = 4096
    scope_interval = 0.1
    waiting_threshold = 0.7

    tone_freq = _float_value(runtime_config.get('tone_freq'), 700.0)
    wpm = _float_value(runtime_config.get('wpm'), 15.0)
    threshold_mode = str(runtime_config.get('threshold_mode', 'auto')).strip().lower()
    manual_threshold = _float_value(runtime_config.get('manual_threshold'), 0.0)
    threshold_multiplier = _float_value(runtime_config.get('threshold_multiplier'), 2.8)
    threshold_offset = _float_value(runtime_config.get('threshold_offset'), 0.0)
    signal_gate = _float_value(runtime_config.get('min_signal_gate'), 0.0)

    last_scope_emit = 0.0
    last_pcm_at = 0.0
    noise_floor = 0.0
    threshold = manual_threshold if threshold_mode == 'manual' else 0.0

    try:
        while not stop_event.is_set():
            if control_queue is not None:
                while True:
                    try:
                        control_msg = control_queue.get_nowait()
                    except queue.Empty:
                        break

                    cmd = str(control_msg.get('cmd', '')).strip().lower()
                    if cmd == 'shutdown':
                        stop_event.set()
                        break
                    if cmd == 'reset':
                        noise_floor = 0.0
                        threshold = manual_threshold if threshold_mode == 'manual' else 0.0
                        _queue_morse_event({
                            'type': 'info',
                            'text': '[morse] Calibration reset applied',
                        })
                if stop_event.is_set():
                    break

            payload = _read_pcm_chunk(rtl_stdout, chunk_bytes, stop_event)
            now = time.monotonic()

            if payload is None:
                if now - last_scope_emit >= scope_interval:
                    last_scope_emit = now
                    waiting = (last_pcm_at <= 0.0) or ((now - last_pcm_at) >= waiting_threshold)
                    with contextlib.suppress(queue.Full):
                        output_queue.put_nowait({
                            'type': 'scope',
                            'waiting': waiting,
                            'amplitudes': [],
                            'tone_on': False,
                            'level': 0.0,
                            'threshold': round(threshold, 4),
                            'noise_floor': round(noise_floor, 4),
                            'tone_freq': tone_freq,
                            'wpm': wpm,
                        })
                continue

            if not payload:
                break

            last_pcm_at = now
            pcm_ready_event.set()

            try:
                multimon_stdin.write(payload)
                multimon_stdin.flush()
            except (BrokenPipeError, OSError):
                break

            sample_count = len(payload) // 2
            if sample_count <= 0:
                continue
            try:
                samples = struct.unpack(f'<{sample_count}h', payload[:sample_count * 2])
            except struct.error:
                continue

            amplitudes = _compress_amplitudes(samples)
            rms = math.sqrt(sum(s * s for s in samples) / sample_count) / 32768.0
            level = max(0.0, min(1.0, rms))

            if noise_floor <= 0.0:
                noise_floor = level
            elif level <= noise_floor:
                noise_floor = (noise_floor * 0.9) + (level * 0.1)
            else:
                noise_floor = (noise_floor * 0.995) + (level * 0.005)

            if threshold_mode == 'manual':
                threshold = manual_threshold
            else:
                threshold = max(0.0, (noise_floor * threshold_multiplier) + threshold_offset)

            tone_on = level >= max(signal_gate, threshold)

            if now - last_scope_emit >= scope_interval:
                last_scope_emit = now
                with contextlib.suppress(queue.Full):
                    output_queue.put_nowait({
                        'type': 'scope',
                        'waiting': False,
                        'amplitudes': amplitudes,
                        'tone_on': tone_on,
                        'level': round(level, 4),
                        'threshold': round(threshold, 4),
                        'noise_floor': round(noise_floor, 4),
                        'tone_freq': tone_freq,
                        'wpm': wpm,
                    })
    except Exception as exc:
        logger.debug('Morse audio relay error: %s', exc)
    finally:
        _close_pipe(multimon_stdin)


def _morse_multimon_output_thread(
    master_fd: int,
    process: subprocess.Popen[bytes],
    stop_event: threading.Event,
) -> None:
    buffer = ''
    try:
        while not stop_event.is_set():
            try:
                ready, _, _ = select.select([master_fd], [], [], 0.2)
            except Exception:
                break

            if ready:
                try:
                    raw = os.read(master_fd, 2048)
                except OSError:
                    break
                if not raw:
                    if process.poll() is not None:
                        break
                    continue

                buffer += raw.decode('utf-8', errors='replace')
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    if not line:
                        continue
                    text = _parse_multimon_morse_text(line)
                    if text is None:
                        _queue_morse_event({'type': 'info', 'text': f'[multimon] {line}'})
                        continue
                    if text:
                        _emit_decoded_text(text)

            if process.poll() is not None:
                break

        tail = buffer.strip()
        if tail:
            tail_text = _parse_multimon_morse_text(tail)
            if tail_text:
                _emit_decoded_text(tail_text)
    except Exception as exc:
        _queue_morse_event({'type': 'error', 'text': f'multimon output error: {exc}'})
    finally:
        with contextlib.suppress(OSError):
            os.close(master_fd)


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {'1', 'true', 'yes', 'on'}:
        return True
    if text in {'0', 'false', 'no', 'off'}:
        return False
    return default


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _validate_tone_freq(value: Any) -> float:
    """Validate CW tone frequency (300-1200 Hz)."""
    try:
        freq = float(value)
        if not 300 <= freq <= 1200:
            raise ValueError('Tone frequency must be between 300 and 1200 Hz')
        return freq
    except (ValueError, TypeError) as e:
        raise ValueError(f'Invalid tone frequency: {value}') from e


def _validate_wpm(value: Any) -> int:
    """Validate words per minute (5-50)."""
    try:
        wpm = int(value)
        if not 5 <= wpm <= 50:
            raise ValueError('WPM must be between 5 and 50')
        return wpm
    except (ValueError, TypeError) as e:
        raise ValueError(f'Invalid WPM: {value}') from e


def _validate_bandwidth(value: Any) -> int:
    try:
        bw = int(value)
        if bw not in (50, 100, 200, 400):
            raise ValueError('Bandwidth must be one of 50, 100, 200, 400 Hz')
        return bw
    except (TypeError, ValueError) as e:
        raise ValueError(f'Invalid bandwidth: {value}') from e


def _validate_threshold_mode(value: Any) -> str:
    mode = str(value or 'auto').strip().lower()
    if mode not in {'auto', 'manual'}:
        raise ValueError('threshold_mode must be auto or manual')
    return mode


def _validate_wpm_mode(value: Any) -> str:
    mode = str(value or 'auto').strip().lower()
    if mode not in {'auto', 'manual'}:
        raise ValueError('wpm_mode must be auto or manual')
    return mode


def _validate_threshold_multiplier(value: Any) -> float:
    try:
        multiplier = float(value)
        if not 1.1 <= multiplier <= 8.0:
            raise ValueError('threshold_multiplier must be between 1.1 and 8.0')
        return multiplier
    except (TypeError, ValueError) as e:
        raise ValueError(f'Invalid threshold multiplier: {value}') from e


def _validate_non_negative_float(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
        if parsed < 0:
            raise ValueError(f'{field_name} must be non-negative')
        return parsed
    except (TypeError, ValueError) as e:
        raise ValueError(f'Invalid {field_name}: {value}') from e


def _validate_signal_gate(value: Any) -> float:
    try:
        gate = float(value)
        if not 0.0 <= gate <= 1.0:
            raise ValueError('signal_gate must be between 0.0 and 1.0')
        return gate
    except (TypeError, ValueError) as e:
        raise ValueError(f'Invalid signal gate: {value}') from e


def _snapshot_live_resources() -> list[str]:
    alive: list[str] = []
    if morse_decoder_worker and morse_decoder_worker.is_alive():
        alive.append('decoder_thread')
    if morse_stderr_worker and morse_stderr_worker.is_alive():
        alive.append('stderr_thread')
    if morse_relay_worker and morse_relay_worker.is_alive():
        alive.append('relay_thread')
    if app_module.morse_process and app_module.morse_process.poll() is None:
        alive.append('multimon_process')
        rtl_proc = getattr(app_module.morse_process, '_rtl_process', None)
        if rtl_proc is not None and rtl_proc.poll() is None:
            alive.append('rtl_process')
    return alive


@morse_bp.route('/morse/start', methods=['POST'])
def start_morse() -> Response:
    global morse_active_device, morse_decoder_worker, morse_stderr_worker, morse_relay_worker
    global morse_stop_event, morse_control_queue, morse_runtime_config
    global morse_last_error, morse_session_id

    data = request.json or {}

    try:
        freq = validate_frequency(data.get('frequency', '14.060'), min_mhz=0.5, max_mhz=30.0)
        gain = validate_gain(data.get('gain', '0'))
        ppm = validate_ppm(data.get('ppm', '0'))
        device = validate_device_index(data.get('device', '0'))
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

    try:
        tone_freq = _validate_tone_freq(data.get('tone_freq', '700'))
        wpm = _validate_wpm(data.get('wpm', '15'))
        bandwidth_hz = _validate_bandwidth(data.get('bandwidth_hz', '200'))
        threshold_mode = _validate_threshold_mode(data.get('threshold_mode', 'auto'))
        wpm_mode = _validate_wpm_mode(data.get('wpm_mode', 'auto'))
        threshold_multiplier = _validate_threshold_multiplier(data.get('threshold_multiplier', '2.8'))
        manual_threshold = _validate_non_negative_float(data.get('manual_threshold', '0'), 'manual threshold')
        threshold_offset = _validate_non_negative_float(data.get('threshold_offset', '0'), 'threshold offset')
        min_signal_gate = _validate_signal_gate(data.get('signal_gate', '0'))
        auto_tone_track = _bool_value(data.get('auto_tone_track', True), True)
        tone_lock = _bool_value(data.get('tone_lock', False), False)
        wpm_lock = _bool_value(data.get('wpm_lock', False), False)
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

    with app_module.morse_lock:
        if morse_state in {MORSE_STARTING, MORSE_RUNNING, MORSE_STOPPING}:
            return jsonify({
                'status': 'error',
                'message': f'Morse decoder is {morse_state}',
                'state': morse_state,
            }), 409

        device_int = int(device)
        error = app_module.claim_sdr_device(device_int, 'morse')
        if error:
            return jsonify({
                'status': 'error',
                'error_type': 'DEVICE_BUSY',
                'message': error,
            }), 409

        morse_active_device = device_int
        morse_last_error = ''
        morse_session_id += 1

        _drain_queue(app_module.morse_queue)
        _set_state(MORSE_STARTING, 'Starting decoder...')

    sample_rate = 22050
    bias_t = _bool_value(data.get('bias_t', False), False)

    sdr_type_str = data.get('sdr_type', 'rtlsdr')
    try:
        sdr_type = SDRType(sdr_type_str)
    except ValueError:
        sdr_type = SDRType.RTL_SDR

    sdr_device = SDRFactory.create_default_device(sdr_type, index=device)
    builder = SDRFactory.get_builder(sdr_device.sdr_type)

    multimon_path = get_tool_path('multimon-ng')
    if not multimon_path:
        msg = 'multimon-ng not found'
        with app_module.morse_lock:
            if morse_active_device is not None:
                app_module.release_sdr_device(morse_active_device)
                morse_active_device = None
            morse_last_error = msg
            _set_state(MORSE_ERROR, msg)
            _set_state(MORSE_IDLE, 'Idle')
        return jsonify({'status': 'error', 'message': msg}), 400

    multimon_cmd = [multimon_path, '-t', 'raw', '-a', 'MORSE_CW', '-f', 'alpha', '-']

    def _build_rtl_cmd(direct_sampling_mode: int | None) -> list[str]:
        fm_kwargs: dict[str, Any] = {
            'device': sdr_device,
            'frequency_mhz': freq,
            'sample_rate': sample_rate,
            'gain': float(gain) if gain and gain != '0' else None,
            'ppm': int(ppm) if ppm and ppm != '0' else None,
            'modulation': 'usb',
            'bias_t': bias_t,
        }
        if direct_sampling_mode in (1, 2):
            fm_kwargs['direct_sampling'] = int(direct_sampling_mode)

        cmd = list(builder.build_fm_demod_command(**fm_kwargs))
        insert_at = len(cmd) - 1 if cmd else 0
        if insert_at < 0:
            insert_at = 0

        if sdr_device.sdr_type == SDRType.RTL_SDR:
            if '-l' not in cmd:
                cmd[insert_at:insert_at] = ['-l', '0']
                insert_at += 2
            if '-r' not in cmd:
                cmd[insert_at:insert_at] = ['-r', str(sample_rate)]
                insert_at += 2
            if '-A' not in cmd:
                cmd[insert_at:insert_at] = ['-A', 'fast']
                insert_at += 2
            if '-E' not in cmd:
                cmd[insert_at:insert_at] = ['-E', 'dc']

        out_target = _stdout_target_path()
        if cmd:
            if cmd[-1] == '-':
                cmd[-1] = out_target
            elif cmd[-1] not in {out_target, '/dev/stdout', '/proc/self/fd/1', '/dev/fd/1'}:
                cmd.append(out_target)

        return cmd

    can_try_direct_sampling = bool(sdr_device.sdr_type == SDRType.RTL_SDR and float(freq) < 24.0)
    direct_sampling_attempts: list[int | None] = [2, 1, None] if can_try_direct_sampling else [None]

    runtime_config: dict[str, Any] = {
        'sample_rate': sample_rate,
        'tone_freq': tone_freq,
        'wpm': wpm,
        'bandwidth_hz': bandwidth_hz,
        'auto_tone_track': auto_tone_track,
        'tone_lock': tone_lock,
        'threshold_mode': threshold_mode,
        'manual_threshold': manual_threshold,
        'threshold_multiplier': threshold_multiplier,
        'threshold_offset': threshold_offset,
        'wpm_mode': wpm_mode,
        'wpm_lock': wpm_lock,
        'min_signal_gate': min_signal_gate,
        'source': 'rtl_fm',
    }

    active_rtl_process: subprocess.Popen[bytes] | None = None
    active_multimon_process: subprocess.Popen[bytes] | None = None
    active_stop_event: threading.Event | None = None
    active_control_queue: queue.Queue | None = None
    active_decoder_thread: threading.Thread | None = None
    active_stderr_thread: threading.Thread | None = None
    active_relay_thread: threading.Thread | None = None
    active_master_fd: int | None = None
    rtl_process: subprocess.Popen[bytes] | None = None
    multimon_process: subprocess.Popen[bytes] | None = None
    stop_event: threading.Event | None = None
    control_queue: queue.Queue | None = None
    decoder_thread: threading.Thread | None = None
    stderr_thread: threading.Thread | None = None
    relay_thread: threading.Thread | None = None
    master_fd: int | None = None

    def _cleanup_attempt(
        rtl_proc: subprocess.Popen[bytes] | None,
        multimon_proc: subprocess.Popen[bytes] | None,
        stop_evt: threading.Event | None,
        control_q: queue.Queue | None,
        decoder_worker: threading.Thread | None,
        stderr_worker: threading.Thread | None,
        relay_worker: threading.Thread | None,
        master_fd: int | None,
    ) -> None:
        if stop_evt is not None:
            stop_evt.set()
        if control_q is not None:
            with contextlib.suppress(queue.Full):
                control_q.put_nowait({'cmd': 'shutdown'})

        if master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(master_fd)

        if rtl_proc is not None:
            _close_pipe(getattr(rtl_proc, 'stdout', None))
            _close_pipe(getattr(rtl_proc, 'stderr', None))
        if multimon_proc is not None:
            _close_pipe(getattr(multimon_proc, 'stdin', None))

        if rtl_proc is not None:
            safe_terminate(rtl_proc, timeout=0.4)
            unregister_process(rtl_proc)
        if multimon_proc is not None:
            safe_terminate(multimon_proc, timeout=0.4)
            unregister_process(multimon_proc)

        _join_thread(relay_worker, timeout_s=0.35)
        _join_thread(decoder_worker, timeout_s=0.35)
        _join_thread(stderr_worker, timeout_s=0.35)

    full_cmd = ''
    attempt_errors: list[str] = []

    try:
        for attempt_index, direct_sampling_mode in enumerate(direct_sampling_attempts, start=1):
            rtl_process = None
            multimon_process = None
            stop_event = None
            control_queue = None
            decoder_thread = None
            stderr_thread = None
            relay_thread = None
            master_fd = None
            runtime_config.pop('startup_waiting', None)
            runtime_config.pop('startup_warning', None)

            rtl_cmd = _build_rtl_cmd(direct_sampling_mode)
            direct_mode_label = direct_sampling_mode if direct_sampling_mode is not None else 'none'
            full_cmd = ' '.join(rtl_cmd) + ' | ' + ' '.join(multimon_cmd)
            logger.info(
                'Morse decoder attempt %s/%s (source=rtl_fm direct_mode=%s): %s',
                attempt_index,
                len(direct_sampling_attempts),
                direct_mode_label,
                full_cmd,
            )
            _queue_morse_event({'type': 'info', 'text': f'[cmd] {full_cmd}'})

            rtl_process = subprocess.Popen(
                rtl_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            register_process(rtl_process)

            stop_event = threading.Event()
            control_queue = queue.Queue(maxsize=16)
            pcm_ready_event = threading.Event()
            stderr_lines: list[str] = []

            def monitor_stderr(
                proc: subprocess.Popen[bytes] = rtl_process,
                proc_stop_event: threading.Event = stop_event,
                capture_lines: list[str] = stderr_lines,
            ) -> None:
                stderr_stream = proc.stderr
                if stderr_stream is None:
                    return
                try:
                    while not proc_stop_event.is_set():
                        line = stderr_stream.readline()
                        if not line:
                            if proc.poll() is not None:
                                break
                            time.sleep(0.02)
                            continue
                        err_text = line.decode('utf-8', errors='replace').strip()
                        if not err_text:
                            continue
                        if len(capture_lines) >= 40:
                            del capture_lines[:10]
                        capture_lines.append(err_text)
                        _queue_morse_event({'type': 'info', 'text': f'[rtl_fm] {err_text}'})
                except (ValueError, OSError):
                    return
                except Exception:
                    return

            stderr_thread = threading.Thread(target=monitor_stderr, daemon=True, name='morse-stderr')
            stderr_thread.start()

            master_fd, slave_fd = pty.openpty()
            try:
                multimon_process = subprocess.Popen(
                    multimon_cmd,
                    stdin=subprocess.PIPE,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    close_fds=True,
                )
            finally:
                with contextlib.suppress(OSError):
                    os.close(slave_fd)
            register_process(multimon_process)

            if rtl_process.stdout is None:
                raise RuntimeError('rtl_fm stdout unavailable')
            if multimon_process.stdin is None:
                raise RuntimeError('multimon-ng stdin unavailable')

            relay_thread = threading.Thread(
                target=_morse_audio_relay_thread,
                args=(
                    rtl_process.stdout,
                    multimon_process.stdin,
                    app_module.morse_queue,
                    stop_event,
                    control_queue,
                    runtime_config,
                    pcm_ready_event,
                ),
                daemon=True,
                name='morse-relay',
            )
            relay_thread.start()

            decoder_thread = threading.Thread(
                target=_morse_multimon_output_thread,
                args=(master_fd, multimon_process, stop_event),
                daemon=True,
                name='morse-decoder',
            )
            decoder_thread.start()

            startup_deadline = time.monotonic() + 2.5
            startup_ok = False
            startup_error = ''

            while time.monotonic() < startup_deadline:
                if pcm_ready_event.is_set():
                    startup_ok = True
                    break
                if rtl_process.poll() is not None:
                    startup_error = f'rtl_fm exited during startup (code {rtl_process.returncode})'
                    break
                if multimon_process.poll() is not None:
                    startup_error = f'multimon-ng exited during startup (code {multimon_process.returncode})'
                    break
                time.sleep(0.05)

            if not startup_ok:
                if not startup_error:
                    startup_error = 'No PCM samples received within startup timeout'
                if stderr_lines:
                    startup_error = f'{startup_error}; stderr: {stderr_lines[-1]}'
                is_last_attempt = attempt_index == len(direct_sampling_attempts)
                if is_last_attempt and rtl_process.poll() is None and multimon_process.poll() is None:
                    startup_ok = True
                    runtime_config['startup_waiting'] = True
                    runtime_config['startup_warning'] = startup_error
                    logger.warning(
                        'Morse startup continuing without PCM (attempt %s/%s): %s',
                        attempt_index,
                        len(direct_sampling_attempts),
                        startup_error,
                    )
                    _queue_morse_event({
                        'type': 'info',
                        'text': '[morse] waiting for PCM stream...',
                    })

            if startup_ok:
                runtime_config['direct_sampling_mode'] = direct_sampling_mode
                runtime_config['direct_sampling'] = (
                    int(direct_sampling_mode) if direct_sampling_mode is not None else 0
                )
                runtime_config['command'] = full_cmd

                active_rtl_process = rtl_process
                active_multimon_process = multimon_process
                active_stop_event = stop_event
                active_control_queue = control_queue
                active_decoder_thread = decoder_thread
                active_stderr_thread = stderr_thread
                active_relay_thread = relay_thread
                active_master_fd = master_fd
                break

            attempt_errors.append(
                f'attempt {attempt_index}/{len(direct_sampling_attempts)} '
                f'(source=rtl_fm direct_mode={direct_mode_label}): {startup_error}'
            )
            logger.warning('Morse startup attempt failed: %s', attempt_errors[-1])
            _queue_morse_event({'type': 'info', 'text': f'[morse] startup attempt failed: {startup_error}'})

            _cleanup_attempt(
                rtl_process,
                multimon_process,
                stop_event,
                control_queue,
                decoder_thread,
                stderr_thread,
                relay_thread,
                master_fd,
            )
            rtl_process = None
            multimon_process = None
            stop_event = None
            control_queue = None
            decoder_thread = None
            stderr_thread = None
            relay_thread = None
            master_fd = None

        if (
            active_rtl_process is None
            or active_multimon_process is None
            or active_stop_event is None
            or active_control_queue is None
            or active_decoder_thread is None
            or active_stderr_thread is None
            or active_relay_thread is None
            or active_master_fd is None
        ):
            msg = 'SDR capture started but no PCM stream was received.'
            if attempt_errors:
                msg += ' ' + ' | '.join(attempt_errors)
            logger.error('Morse startup failed: %s', msg)
            with app_module.morse_lock:
                if morse_active_device is not None:
                    app_module.release_sdr_device(morse_active_device)
                    morse_active_device = None
                morse_last_error = msg
                _set_state(MORSE_ERROR, msg)
                _set_state(MORSE_IDLE, 'Idle')
            return jsonify({'status': 'error', 'message': msg}), 500

        with app_module.morse_lock:
            app_module.morse_process = active_multimon_process
            app_module.morse_process._rtl_process = active_rtl_process
            app_module.morse_process._stop_decoder = active_stop_event
            app_module.morse_process._decoder_thread = active_decoder_thread
            app_module.morse_process._stderr_thread = active_stderr_thread
            app_module.morse_process._relay_thread = active_relay_thread
            app_module.morse_process._control_queue = active_control_queue
            app_module.morse_process._master_fd = active_master_fd

            morse_stop_event = active_stop_event
            morse_control_queue = active_control_queue
            morse_decoder_worker = active_decoder_thread
            morse_stderr_worker = active_stderr_thread
            morse_relay_worker = active_relay_thread
            morse_runtime_config = dict(runtime_config)
            _set_state(MORSE_RUNNING, 'Listening')

        return jsonify({
            'status': 'started',
            'state': MORSE_RUNNING,
            'command': full_cmd,
            'tone_freq': tone_freq,
            'wpm': wpm,
            'config': runtime_config,
            'session_id': morse_session_id,
        })

    except FileNotFoundError as e:
        _cleanup_attempt(
            rtl_process if rtl_process is not None else active_rtl_process,
            multimon_process if multimon_process is not None else active_multimon_process,
            stop_event if stop_event is not None else active_stop_event,
            control_queue if control_queue is not None else active_control_queue,
            decoder_thread if decoder_thread is not None else active_decoder_thread,
            stderr_thread if stderr_thread is not None else active_stderr_thread,
            relay_thread if relay_thread is not None else active_relay_thread,
            master_fd if master_fd is not None else active_master_fd,
        )
        with app_module.morse_lock:
            if morse_active_device is not None:
                app_module.release_sdr_device(morse_active_device)
                morse_active_device = None
            morse_last_error = f'Tool not found: {e.filename}'
            _set_state(MORSE_ERROR, morse_last_error)
            _set_state(MORSE_IDLE, 'Idle')
        return jsonify({'status': 'error', 'message': morse_last_error}), 400

    except Exception as e:
        _cleanup_attempt(
            rtl_process if rtl_process is not None else active_rtl_process,
            multimon_process if multimon_process is not None else active_multimon_process,
            stop_event if stop_event is not None else active_stop_event,
            control_queue if control_queue is not None else active_control_queue,
            decoder_thread if decoder_thread is not None else active_decoder_thread,
            stderr_thread if stderr_thread is not None else active_stderr_thread,
            relay_thread if relay_thread is not None else active_relay_thread,
            master_fd if master_fd is not None else active_master_fd,
        )
        with app_module.morse_lock:
            if morse_active_device is not None:
                app_module.release_sdr_device(morse_active_device)
                morse_active_device = None
            morse_last_error = str(e)
            _set_state(MORSE_ERROR, morse_last_error)
            _set_state(MORSE_IDLE, 'Idle')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@morse_bp.route('/morse/stop', methods=['POST'])
def stop_morse() -> Response:
    global morse_active_device, morse_decoder_worker, morse_stderr_worker, morse_relay_worker
    global morse_stop_event, morse_control_queue

    stop_started = time.perf_counter()

    with app_module.morse_lock:
        if morse_state == MORSE_STOPPING:
            return jsonify({'status': 'stopping', 'state': MORSE_STOPPING}), 202

        proc = app_module.morse_process
        rtl_proc = getattr(proc, '_rtl_process', None) if proc else None
        stop_event = morse_stop_event or getattr(proc, '_stop_decoder', None)
        decoder_thread = morse_decoder_worker or getattr(proc, '_decoder_thread', None)
        stderr_thread = morse_stderr_worker or getattr(proc, '_stderr_thread', None)
        relay_thread = morse_relay_worker or getattr(proc, '_relay_thread', None)
        control_queue = morse_control_queue or getattr(proc, '_control_queue', None)
        master_fd = getattr(proc, '_master_fd', None) if proc else None
        active_device = morse_active_device

        if (
            not proc
            and not rtl_proc
            and not stop_event
            and not decoder_thread
            and not stderr_thread
            and not relay_thread
        ):
            _set_state(MORSE_IDLE, 'Idle', enqueue=False)
            return jsonify({'status': 'not_running', 'state': MORSE_IDLE})

        _set_state(MORSE_STOPPING, 'Stopping decoder...')

        app_module.morse_process = None
        morse_stop_event = None
        morse_control_queue = None
        morse_decoder_worker = None
        morse_stderr_worker = None
        morse_relay_worker = None

    cleanup_steps: list[str] = []

    def _mark(step: str) -> None:
        cleanup_steps.append(step)
        logger.debug(f'[morse.stop] {step}')

    _mark('enter stop')

    if stop_event is not None:
        stop_event.set()
        _mark('stop_event set')

    if control_queue is not None:
        with contextlib.suppress(queue.Full):
            control_queue.put_nowait({'cmd': 'shutdown'})
        _mark('control_queue shutdown signal sent')

    if master_fd is not None:
        with contextlib.suppress(OSError):
            os.close(master_fd)
        _mark('pty master fd closed')

    if rtl_proc is not None:
        _close_pipe(getattr(rtl_proc, 'stdout', None))
        _close_pipe(getattr(rtl_proc, 'stderr', None))
        _mark('rtl_fm pipes closed')

    if proc is not None:
        _close_pipe(getattr(proc, 'stdin', None))
        _mark('multimon stdin closed')

    if rtl_proc is not None:
        safe_terminate(rtl_proc, timeout=0.6)
        unregister_process(rtl_proc)
        _mark('rtl_fm process terminated')

    if proc is not None:
        safe_terminate(proc, timeout=0.6)
        unregister_process(proc)
        _mark('multimon process terminated')

    relay_joined = _join_thread(relay_thread, timeout_s=0.45)
    decoder_joined = _join_thread(decoder_thread, timeout_s=0.45)
    stderr_joined = _join_thread(stderr_thread, timeout_s=0.45)
    _mark(f'relay thread joined={relay_joined}')
    _mark(f'decoder thread joined={decoder_joined}')
    _mark(f'stderr thread joined={stderr_joined}')

    if active_device is not None:
        app_module.release_sdr_device(active_device)
        _mark(f'SDR device {active_device} released')

    stop_ms = round((time.perf_counter() - stop_started) * 1000.0, 1)
    alive_after = []
    if not relay_joined:
        alive_after.append('relay_thread')
    if not decoder_joined:
        alive_after.append('decoder_thread')
    if not stderr_joined:
        alive_after.append('stderr_thread')
    if rtl_proc is not None and rtl_proc.poll() is None:
        alive_after.append('rtl_process')
    if proc is not None and proc.poll() is None:
        alive_after.append('multimon_process')

    with app_module.morse_lock:
        morse_active_device = None
        _set_state(MORSE_IDLE, 'Stopped', extra={
            'stop_ms': stop_ms,
            'cleanup_steps': cleanup_steps,
            'alive': alive_after,
        })

    with contextlib.suppress(queue.Full):
        app_module.morse_queue.put_nowait({
            'type': 'status',
            'status': 'stopped',
            'state': MORSE_IDLE,
            'stop_ms': stop_ms,
            'cleanup_steps': cleanup_steps,
            'alive': alive_after,
            'timestamp': time.strftime('%H:%M:%S'),
        })

    if stop_ms > 500.0 or alive_after:
        logger.warning(
            '[morse.stop] slow/partial cleanup: stop_ms=%s alive=%s steps=%s',
            stop_ms,
            ','.join(alive_after) if alive_after else 'none',
            '; '.join(cleanup_steps),
        )
    else:
        logger.info('[morse.stop] cleanup complete in %sms', stop_ms)

    return jsonify({
        'status': 'stopped',
        'state': MORSE_IDLE,
        'stop_ms': stop_ms,
        'alive': alive_after,
        'cleanup_steps': cleanup_steps,
    })


@morse_bp.route('/morse/calibrate', methods=['POST'])
def calibrate_morse() -> Response:
    """Reset decoder threshold/timing estimators without restarting the process."""
    with app_module.morse_lock:
        if morse_state != MORSE_RUNNING or morse_control_queue is None:
            return jsonify({
                'status': 'not_running',
                'state': morse_state,
                'message': 'Morse decoder is not running',
            }), 409

        with contextlib.suppress(queue.Full):
            morse_control_queue.put_nowait({'cmd': 'reset'})

    with contextlib.suppress(queue.Full):
        app_module.morse_queue.put_nowait({
            'type': 'info',
            'text': '[morse] Calibration reset requested',
        })

    return jsonify({'status': 'ok', 'state': morse_state})


@morse_bp.route('/morse/decode-file', methods=['POST'])
def decode_morse_file() -> Response:
    """Decode Morse from an uploaded WAV file."""
    if 'audio' not in request.files:
        return jsonify({'status': 'error', 'message': 'No audio file provided'}), 400

    audio_file = request.files['audio']
    if not audio_file.filename:
        return jsonify({'status': 'error', 'message': 'No file selected'}), 400

    # Parse optional tuning/decoder parameters from form fields.
    form = request.form or {}
    try:
        tone_freq = _validate_tone_freq(form.get('tone_freq', '700'))
        wpm = _validate_wpm(form.get('wpm', '15'))
        bandwidth_hz = _validate_bandwidth(form.get('bandwidth_hz', '200'))
        threshold_mode = _validate_threshold_mode(form.get('threshold_mode', 'auto'))
        wpm_mode = _validate_wpm_mode(form.get('wpm_mode', 'auto'))
        threshold_multiplier = _validate_threshold_multiplier(form.get('threshold_multiplier', '2.8'))
        manual_threshold = _validate_non_negative_float(form.get('manual_threshold', '0'), 'manual threshold')
        threshold_offset = _validate_non_negative_float(form.get('threshold_offset', '0'), 'threshold offset')
        signal_gate = _validate_signal_gate(form.get('signal_gate', '0'))
        auto_tone_track = _bool_value(form.get('auto_tone_track', 'true'), True)
        tone_lock = _bool_value(form.get('tone_lock', 'false'), False)
        wpm_lock = _bool_value(form.get('wpm_lock', 'false'), False)
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = Path(tmp.name)

    try:
        result = decode_morse_wav_file(
            tmp_path,
            sample_rate=8000,
            tone_freq=tone_freq,
            wpm=wpm,
            bandwidth_hz=bandwidth_hz,
            auto_tone_track=auto_tone_track,
            tone_lock=tone_lock,
            threshold_mode=threshold_mode,
            manual_threshold=manual_threshold,
            threshold_multiplier=threshold_multiplier,
            threshold_offset=threshold_offset,
            wpm_mode=wpm_mode,
            wpm_lock=wpm_lock,
            min_signal_gate=signal_gate,
        )

        text = str(result.get('text', ''))
        raw = str(result.get('raw', ''))
        metrics = result.get('metrics', {})

        return jsonify({
            'status': 'ok',
            'text': text,
            'raw': raw,
            'char_count': len(text.replace(' ', '')),
            'word_count': len([w for w in text.split(' ') if w]),
            'metrics': metrics,
        })
    except Exception as e:
        logger.error(f'Morse decode-file error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        with contextlib.suppress(Exception):
            tmp_path.unlink(missing_ok=True)


@morse_bp.route('/morse/status')
def morse_status() -> Response:
    with app_module.morse_lock:
        running = (
            app_module.morse_process is not None
            and app_module.morse_process.poll() is None
            and morse_state in {MORSE_RUNNING, MORSE_STARTING, MORSE_STOPPING}
        )
        since_ms = round((time.monotonic() - morse_state_since) * 1000.0, 1)
        return jsonify({
            'running': running,
            'state': morse_state,
            'message': morse_state_message,
            'since_ms': since_ms,
            'session_id': morse_session_id,
            'config': morse_runtime_config,
            'alive': _snapshot_live_resources(),
            'error': morse_last_error,
        })


@morse_bp.route('/morse/stream')
def morse_stream() -> Response:
    def _on_msg(msg: dict[str, Any]) -> None:
        process_event('morse', msg, msg.get('type'))

    response = Response(
        sse_stream_fanout(
            source_queue=app_module.morse_queue,
            channel_key='morse',
            timeout=1.0,
            keepalive_interval=30.0,
            on_message=_on_msg,
        ),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response
