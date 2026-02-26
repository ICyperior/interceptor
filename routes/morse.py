"""CW/Morse code decoder routes."""

from __future__ import annotations

import contextlib
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, jsonify, request

import app as app_module
from utils.event_pipeline import process_event
from utils.logging import sensor_logger as logger
from utils.morse import (
    decode_morse_wav_file,
    morse_decoder_thread,
    morse_iq_decoder_thread,
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
morse_stop_event: threading.Event | None = None
morse_control_queue: queue.Queue | None = None


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
    if app_module.morse_process and app_module.morse_process.poll() is None:
        alive.append('rtl_process')
    return alive


@morse_bp.route('/morse/start', methods=['POST'])
def start_morse() -> Response:
    global morse_active_device, morse_decoder_worker, morse_stderr_worker
    global morse_stop_event, morse_control_queue, morse_runtime_config
    global morse_last_error, morse_session_id

    data = request.json or {}

    # Validate standard SDR inputs
    try:
        freq = validate_frequency(data.get('frequency', '14.060'), min_mhz=0.5, max_mhz=30.0)
        gain = validate_gain(data.get('gain', '0'))
        ppm = validate_ppm(data.get('ppm', '0'))
        device = validate_device_index(data.get('device', '0'))
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

    # Validate Morse-specific inputs
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

        # Claim SDR device
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

    # Use pager-proven audio rate for rtl_fm compatibility across builds.
    sample_rate = 22050
    bias_t = _bool_value(data.get('bias_t', False), False)

    sdr_type_str = data.get('sdr_type', 'rtlsdr')
    try:
        sdr_type = SDRType(sdr_type_str)
    except ValueError:
        sdr_type = SDRType.RTL_SDR

    sdr_device = SDRFactory.create_default_device(sdr_type, index=device)
    builder = SDRFactory.get_builder(sdr_device.sdr_type)

    def _build_rtl_cmd(
        *,
        direct_sampling_mode: int | None,
        force_squelch_off: bool,
        add_resample_rate: bool,
        add_dc_fast: bool,
    ) -> list[str]:
        fm_kwargs: dict[str, Any] = {
            'device': sdr_device,
            'frequency_mhz': freq,
            'sample_rate': sample_rate,
            'gain': float(gain) if gain and gain != '0' else None,
            'ppm': int(ppm) if ppm and ppm != '0' else None,
            'modulation': 'usb',
            'bias_t': bias_t,
        }

        # Only rtl_fm supports direct sampling flags.
        if direct_sampling_mode in (1, 2):
            fm_kwargs['direct_sampling'] = int(direct_sampling_mode)

        cmd = builder.build_fm_demod_command(**fm_kwargs)

        # Some rtl_fm builds behave as if squelch is enabled unless -l is explicit.
        # Force continuous audio for CW analysis.
        if force_squelch_off and sdr_device.sdr_type == SDRType.RTL_SDR and '-l' not in cmd:
            if cmd and cmd[-1] == '-':
                cmd[-1:-1] = ['-l', '0']
            else:
                cmd.extend(['-l', '0'])

        if sdr_device.sdr_type == SDRType.RTL_SDR:
            insert_at = len(cmd) - 1 if cmd and cmd[-1] == '-' else len(cmd)
            if add_resample_rate and '-r' not in cmd:
                cmd[insert_at:insert_at] = ['-r', str(sample_rate)]
                insert_at += 2
            if add_dc_fast:
                # Used in other stable modes to improve rtl_fm stream behavior.
                if '-A' not in cmd:
                    cmd[insert_at:insert_at] = ['-A', 'fast']
                    insert_at += 2
                if '-E' not in cmd or 'dc' not in cmd:
                    cmd[insert_at:insert_at] = ['-E', 'dc']
        return cmd

    # Use a hardware-friendly IQ rate (matches common RTL-SDR stable rates
    # and waterfall defaults) before decimating to audio.
    iq_sample_rate = 1024000

    def _build_iq_cmd(*, direct_sampling_mode: int | None) -> tuple[list[str], float]:
        # CW USB-style offset tuning: keep the configured RF frequency sounding
        # near the selected tone frequency in the software demod chain.
        tune_mhz = max(0.5, float(freq) - (float(tone_freq) / 1_000_000.0))
        iq_cmd = builder.build_iq_capture_command(
            device=sdr_device,
            frequency_mhz=tune_mhz,
            sample_rate=iq_sample_rate,
            gain=float(gain) if gain and gain != '0' else None,
            ppm=int(ppm) if ppm and ppm != '0' else None,
            bias_t=bias_t,
        )
        if (
            sdr_device.sdr_type == SDRType.RTL_SDR
            and direct_sampling_mode is not None
            and '-D' not in iq_cmd
        ):
            if iq_cmd and iq_cmd[-1] == '-':
                iq_cmd[-1:-1] = ['-D', str(direct_sampling_mode)]
            else:
                iq_cmd.extend(['-D', str(direct_sampling_mode)])
        # Some rtl_sdr builds treat "-" as a literal filename instead of stdout.
        # Use /dev/stdout explicitly on Unix-like systems for deterministic piping.
        if iq_cmd:
            if iq_cmd[-1] == '-':
                iq_cmd[-1] = '/dev/stdout'
            elif '/dev/stdout' not in iq_cmd:
                iq_cmd.append('/dev/stdout')
        return iq_cmd, tune_mhz

    can_try_direct_sampling = bool(sdr_device.sdr_type == SDRType.RTL_SDR and freq < 24.0)
    if can_try_direct_sampling:
        # IQ-first strategy: avoid repeated rtl_fm/rtl_sdr handoffs that can
        # leave the tuner in a bad state on some Linux builds.
        command_attempts: list[dict[str, Any]] = [
            {
                'source': 'iq',
                'direct_sampling_mode': 2,
            },
            {
                'source': 'iq',
                'direct_sampling_mode': 1,
            },
            {
                'source': 'iq',
                'direct_sampling_mode': None,
            },
            {
                'source': 'rtl_fm',
                'direct_sampling_mode': 2,
                'force_squelch_off': False,
                'add_resample_rate': True,
                'add_dc_fast': True,
            },
            {
                'source': 'rtl_fm',
                'direct_sampling_mode': 1,
                'force_squelch_off': False,
                'add_resample_rate': True,
                'add_dc_fast': True,
            },
            {
                'source': 'rtl_fm',
                'direct_sampling_mode': None,
                'force_squelch_off': False,
                'add_resample_rate': True,
                'add_dc_fast': True,
            },
        ]
    else:
        command_attempts = [
            {
                'source': 'iq',
                'direct_sampling_mode': None,
            },
            {
                'source': 'rtl_fm',
                'direct_sampling_mode': None,
                'force_squelch_off': False,
                'add_resample_rate': True,
                'add_dc_fast': True,
            },
        ]

    rtl_process: subprocess.Popen | None = None
    stop_event: threading.Event | None = None
    decoder_thread: threading.Thread | None = None
    stderr_thread: threading.Thread | None = None
    control_queue: queue.Queue | None = None

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
    }

    try:
        def _cleanup_attempt(
            proc: subprocess.Popen | None,
            attempt_stop_event: threading.Event | None,
            attempt_control_queue: queue.Queue | None,
            attempt_decoder_thread: threading.Thread | None,
            attempt_stderr_thread: threading.Thread | None,
        ) -> None:
            if attempt_stop_event is not None:
                attempt_stop_event.set()
            if attempt_control_queue is not None:
                with contextlib.suppress(queue.Full):
                    attempt_control_queue.put_nowait({'cmd': 'shutdown'})
            if proc is not None:
                # Close stdout to unblock decoder reads. Keep stderr open until
                # after stderr monitor thread exits to avoid ValueError races.
                _close_pipe(getattr(proc, 'stdout', None))
                # Keep startup retries responsive; avoid long waits inside
                # generic safe_terminate() during a failed attempt.
                if proc.poll() is None:
                    with contextlib.suppress(Exception):
                        proc.terminate()
                    with contextlib.suppress(subprocess.TimeoutExpired, Exception):
                        proc.wait(timeout=0.15)
                if proc.poll() is None:
                    with contextlib.suppress(Exception):
                        proc.kill()
                    with contextlib.suppress(subprocess.TimeoutExpired, Exception):
                        proc.wait(timeout=0.25)
                unregister_process(proc)
            _join_thread(attempt_decoder_thread, timeout_s=0.20)
            stderr_joined = _join_thread(attempt_stderr_thread, timeout_s=0.35)
            if proc is not None:
                if not stderr_joined:
                    # Force-close the pipe if stderr reader is still blocked.
                    _close_pipe(getattr(proc, 'stderr', None))
                    _join_thread(attempt_stderr_thread, timeout_s=0.15)
                _close_pipe(getattr(proc, 'stderr', None))

        attempt_errors: list[str] = []
        full_cmd = ''

        for attempt_index, attempt in enumerate(command_attempts, start=1):
            runtime_config.pop('startup_waiting', None)
            runtime_config.pop('startup_warning', None)
            source = str(attempt.get('source', 'rtl_fm')).strip().lower()
            force_squelch_off = bool(attempt.get('force_squelch_off', True))
            add_resample_rate = bool(attempt.get('add_resample_rate', False))
            add_dc_fast = bool(attempt.get('add_dc_fast', False))
            direct_sampling_mode_raw = attempt.get('direct_sampling_mode')
            try:
                direct_sampling_mode = (
                    int(direct_sampling_mode_raw)
                    if direct_sampling_mode_raw is not None
                    else None
                )
            except (TypeError, ValueError):
                direct_sampling_mode = None

            if source == 'iq':
                rtl_cmd, tuned_freq_mhz = _build_iq_cmd(
                    direct_sampling_mode=int(direct_sampling_mode)
                    if direct_sampling_mode is not None else None,
                )
                thread_target = morse_iq_decoder_thread
                attempt_desc = (
                    f'source=iq direct_mode={direct_sampling_mode if direct_sampling_mode is not None else "none"} '
                    f'iq_sr={iq_sample_rate}'
                )
            else:
                rtl_cmd = _build_rtl_cmd(
                    direct_sampling_mode=direct_sampling_mode,
                    force_squelch_off=force_squelch_off,
                    add_resample_rate=add_resample_rate,
                    add_dc_fast=add_dc_fast,
                )
                tuned_freq_mhz = float(freq)
                thread_target = morse_decoder_thread
                attempt_desc = (
                    f'source=rtl_fm direct_mode={direct_sampling_mode if direct_sampling_mode is not None else "none"} '
                    f'squelch_forced={int(force_squelch_off)} '
                    f'resample={int(add_resample_rate)} dc_fast={int(add_dc_fast)}'
                )

            full_cmd = ' '.join(rtl_cmd)
            logger.info(
                f'Morse decoder attempt {attempt_index}/{len(command_attempts)} '
                f'({attempt_desc}): {full_cmd}'
            )

            with contextlib.suppress(queue.Full):
                app_module.morse_queue.put_nowait({
                    'type': 'info',
                    'text': f'[cmd] {full_cmd}',
                })

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
            stream_ready_event = threading.Event()
            attempt_stderr_lines: list[str] = []

            def monitor_stderr(
                proc: subprocess.Popen = rtl_process,
                proc_stop_event: threading.Event = stop_event,
                tool_label: str = rtl_cmd[0],
                stderr_lines: list[str] = attempt_stderr_lines,
            ) -> None:
                try:
                    stderr_stream = proc.stderr
                    if stderr_stream is None:
                        return
                    while not proc_stop_event.is_set():
                        line = stderr_stream.readline()
                        if not line:
                            if proc.poll() is not None:
                                break
                            time.sleep(0.02)
                            continue
                        err_text = line.decode('utf-8', errors='replace').strip()
                        if err_text:
                            if len(stderr_lines) >= 40:
                                del stderr_lines[:10]
                            stderr_lines.append(err_text)
                            with contextlib.suppress(queue.Full):
                                app_module.morse_queue.put_nowait({
                                    'type': 'info',
                                    'text': f'[{tool_label}] {err_text}',
                                })
                except ValueError:
                    # Pipe was closed during shutdown; expected during retries.
                    return
                except Exception:
                    return

            stderr_thread = threading.Thread(target=monitor_stderr, daemon=True, name='morse-stderr')
            stderr_thread.start()

            if source == 'iq':
                decoder_thread = threading.Thread(
                    target=thread_target,
                    args=(
                        rtl_process.stdout,
                        app_module.morse_queue,
                        stop_event,
                        iq_sample_rate,
                    ),
                    kwargs={
                        'sample_rate': sample_rate,
                        'tone_freq': tone_freq,
                        'wpm': wpm,
                        'decoder_config': runtime_config,
                        'control_queue': control_queue,
                        'pcm_ready_event': pcm_ready_event,
                        'stream_ready_event': stream_ready_event,
                    },
                    daemon=True,
                    name='morse-decoder',
                )
            else:
                decoder_thread = threading.Thread(
                    target=thread_target,
                    args=(
                        rtl_process.stdout,
                        app_module.morse_queue,
                        stop_event,
                        sample_rate,
                        tone_freq,
                        wpm,
                    ),
                    kwargs={
                        'decoder_config': runtime_config,
                        'control_queue': control_queue,
                        'pcm_ready_event': pcm_ready_event,
                        'stream_ready_event': stream_ready_event,
                        'strip_text_chunks': False,
                    },
                    daemon=True,
                    name='morse-decoder',
                )
            decoder_thread.start()

            startup_deadline = time.monotonic() + (4.0 if source == 'iq' else 2.0)
            startup_ok = False
            startup_error = ''

            while time.monotonic() < startup_deadline:
                if pcm_ready_event.is_set():
                    startup_ok = True
                    break
                if rtl_process.poll() is not None:
                    startup_error = f'{rtl_cmd[0]} exited during startup (code {rtl_process.returncode})'
                    break
                time.sleep(0.05)

            if not startup_ok:
                if not startup_error:
                    startup_error = 'No PCM samples received within startup timeout'
                if attempt_stderr_lines:
                    startup_error = f'{startup_error}; stderr: {attempt_stderr_lines[-1]}'
                if stream_ready_event.is_set():
                    startup_error = f'{startup_error}; stream=alive'

                is_last_attempt = attempt_index == len(command_attempts)
                if (
                    is_last_attempt
                    and rtl_process.poll() is None
                    and decoder_thread.is_alive()
                ):
                    # Avoid hard-failing startup when SDR is alive but muted.
                    startup_ok = True
                    runtime_config['startup_waiting'] = True
                    runtime_config['startup_warning'] = startup_error
                    logger.warning(
                        'Morse startup continuing without PCM (attempt %s/%s): %s',
                        attempt_index,
                        len(command_attempts),
                        startup_error,
                    )
                    with contextlib.suppress(queue.Full):
                        app_module.morse_queue.put_nowait({
                            'type': 'info',
                            'text': '[morse] stream alive but no PCM yet; continuing in waiting mode',
                        })

            if startup_ok:
                runtime_config['source'] = source
                runtime_config['command'] = full_cmd
                runtime_config['tuned_frequency_mhz'] = tuned_freq_mhz
                runtime_config['direct_sampling'] = (
                    int(direct_sampling_mode)
                    if source == 'iq' and direct_sampling_mode is not None
                    else (int(direct_sampling_mode) if direct_sampling_mode is not None else 0)
                )
                runtime_config['iq_sample_rate'] = iq_sample_rate if source == 'iq' else None
                runtime_config['direct_sampling_mode'] = direct_sampling_mode if source == 'iq' else None
                break

            attempt_errors.append(
                f'attempt {attempt_index}/{len(command_attempts)} ({attempt_desc}): {startup_error}'
            )
            logger.warning(f'Morse startup attempt failed: {attempt_errors[-1]}')

            with contextlib.suppress(queue.Full):
                app_module.morse_queue.put_nowait({
                    'type': 'info',
                    'text': f'[morse] startup attempt failed: {startup_error}',
                })

            _cleanup_attempt(
                rtl_process,
                stop_event,
                control_queue,
                decoder_thread,
                stderr_thread,
            )
            rtl_process = None
            stop_event = None
            control_queue = None
            decoder_thread = None
            stderr_thread = None

        if rtl_process is None or stop_event is None or control_queue is None or decoder_thread is None:
            msg = 'SDR capture started but no PCM stream was received.'
            if attempt_errors:
                msg = msg + ' ' + ' | '.join(attempt_errors)
            logger.error(f'Morse startup failed: {msg}')
            with app_module.morse_lock:
                if morse_active_device is not None:
                    app_module.release_sdr_device(morse_active_device)
                    morse_active_device = None
                morse_last_error = msg
                _set_state(MORSE_ERROR, msg)
                _set_state(MORSE_IDLE, 'Idle')
            return jsonify({'status': 'error', 'message': msg}), 500

        with app_module.morse_lock:
            app_module.morse_process = rtl_process
            app_module.morse_process._stop_decoder = stop_event
            app_module.morse_process._decoder_thread = decoder_thread
            app_module.morse_process._stderr_thread = stderr_thread
            app_module.morse_process._control_queue = control_queue

            morse_stop_event = stop_event
            morse_control_queue = control_queue
            morse_decoder_worker = decoder_thread
            morse_stderr_worker = stderr_thread
            morse_runtime_config = dict(runtime_config)
            _set_state(MORSE_RUNNING, 'Listening')

        with contextlib.suppress(queue.Full):
            app_module.morse_queue.put_nowait({
                'type': 'info',
                'text': f'[cmd] {full_cmd}',
            })

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
        if rtl_process is not None:
            unregister_process(rtl_process)
        with app_module.morse_lock:
            if morse_active_device is not None:
                app_module.release_sdr_device(morse_active_device)
                morse_active_device = None
            morse_last_error = f'Tool not found: {e.filename}'
            _set_state(MORSE_ERROR, morse_last_error)
            _set_state(MORSE_IDLE, 'Idle')
        return jsonify({'status': 'error', 'message': morse_last_error}), 400

    except Exception as e:
        if rtl_process is not None:
            safe_terminate(rtl_process, timeout=0.5)
            unregister_process(rtl_process)
        if stop_event is not None:
            stop_event.set()
        _join_thread(decoder_thread, timeout_s=0.25)
        _join_thread(stderr_thread, timeout_s=0.25)
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
    global morse_active_device, morse_decoder_worker, morse_stderr_worker
    global morse_stop_event, morse_control_queue

    stop_started = time.perf_counter()

    with app_module.morse_lock:
        if morse_state == MORSE_STOPPING:
            return jsonify({'status': 'stopping', 'state': MORSE_STOPPING}), 202

        proc = app_module.morse_process
        stop_event = morse_stop_event or getattr(proc, '_stop_decoder', None)
        decoder_thread = morse_decoder_worker or getattr(proc, '_decoder_thread', None)
        stderr_thread = morse_stderr_worker or getattr(proc, '_stderr_thread', None)
        control_queue = morse_control_queue or getattr(proc, '_control_queue', None)
        active_device = morse_active_device

        if not proc and not stop_event and not decoder_thread and not stderr_thread:
            _set_state(MORSE_IDLE, 'Idle', enqueue=False)
            return jsonify({'status': 'not_running', 'state': MORSE_IDLE})

        # Prevent new starts while cleanup is in progress.
        _set_state(MORSE_STOPPING, 'Stopping decoder...')

        # Detach global runtime pointers immediately to avoid double-stop races.
        app_module.morse_process = None
        morse_stop_event = None
        morse_control_queue = None
        morse_decoder_worker = None
        morse_stderr_worker = None

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

    if proc is not None:
        _close_pipe(getattr(proc, 'stdout', None))
        _mark('stdout pipe closed')

        safe_terminate(proc, timeout=0.6)
        unregister_process(proc)
        _mark('rtl_fm process terminated')

    decoder_joined = _join_thread(decoder_thread, timeout_s=0.45)
    stderr_joined = _join_thread(stderr_thread, timeout_s=0.45)
    if proc is not None:
        if not stderr_joined:
            _close_pipe(getattr(proc, 'stderr', None))
            stderr_joined = _join_thread(stderr_thread, timeout_s=0.20)
            _mark('stderr pipe force-closed')
        _close_pipe(getattr(proc, 'stderr', None))
        _mark('stderr pipe closed')
    _mark(f'decoder thread joined={decoder_joined}')
    _mark(f'stderr thread joined={stderr_joined}')

    if active_device is not None:
        app_module.release_sdr_device(active_device)
        _mark(f'SDR device {active_device} released')

    stop_ms = round((time.perf_counter() - stop_started) * 1000.0, 1)
    alive_after = []
    if not decoder_joined:
        alive_after.append('decoder_thread')
    if not stderr_joined:
        alive_after.append('stderr_thread')

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
