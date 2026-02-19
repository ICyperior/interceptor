"""DMR / P25 / Digital Voice decoding routes."""

from __future__ import annotations

import os
import queue
import re
import select
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import Generator, Optional

from flask import Blueprint, jsonify, request, Response

import app as app_module
from utils.logging import get_logger
from utils.sse import sse_stream_fanout
from utils.event_pipeline import process_event
from utils.process import register_process, unregister_process
from utils.validation import validate_frequency, validate_gain, validate_device_index, validate_ppm
from utils.sdr import SDRFactory, SDRType
from utils.constants import (
    SSE_QUEUE_TIMEOUT,
    SSE_KEEPALIVE_INTERVAL,
    QUEUE_MAX_SIZE,
)

logger = get_logger('intercept.dmr')

dmr_bp = Blueprint('dmr', __name__, url_prefix='/dmr')

# ============================================
# GLOBAL STATE
# ============================================

dmr_rtl_process: Optional[subprocess.Popen] = None
dmr_dsd_process: Optional[subprocess.Popen] = None
dmr_thread: Optional[threading.Thread] = None
dmr_running = False
dmr_has_audio = False  # True when ffmpeg available and dsd outputs audio
dmr_lock = threading.Lock()
dmr_queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
dmr_active_device: Optional[int] = None

# Audio mux: the sole reader of dsd-fme stdout.  Fans out bytes to all
# active ffmpeg stdin sinks when streaming clients are connected.
# This prevents dsd-fme from blocking on stdout (which would also
# freeze stderr / text data output).
_ffmpeg_sinks: set[object] = set()
_ffmpeg_sinks_lock = threading.Lock()

VALID_PROTOCOLS = ['auto', 'dmr', 'p25', 'nxdn', 'dstar', 'provoice']

# Classic dsd flags
_DSD_PROTOCOL_FLAGS = {
    'auto': [],
    'dmr': ['-fd'],
    'p25': ['-fp'],
    'nxdn': ['-fn'],
    'dstar': ['-fi'],
    'provoice': ['-fv'],
}

# dsd-fme remapped several flags from classic DSD:
#   -fs = DMR Simplex (NOT -fd which is D-STAR!),
#   -fd = D-STAR (NOT DMR!), -fp = ProVoice (NOT P25),
#   -fi = NXDN48 (NOT D-Star), -f1 = P25 Phase 1,
#   -ft = XDMA multi-protocol decoder
_DSD_FME_PROTOCOL_FLAGS = {
    'auto': ['-fa'],       # Broad auto: P25 (P1/P2), DMR, D-STAR, YSF, X2-TDMA
    'dmr': ['-fs'],        # DMR Simplex (-fd is D-STAR in dsd-fme!)
    'p25': ['-ft'],        # P25 P1/P2 coverage (also includes DMR in dsd-fme)
    'nxdn': ['-fn'],       # NXDN96
    'dstar': ['-fd'],      # D-STAR (-fd in dsd-fme, NOT DMR!)
    'provoice': ['-fp'],   # ProVoice (-fp in dsd-fme, not -fv)
}

# Modulation hints: force C4FM for protocols that use it, improving
# sync reliability vs letting dsd-fme auto-detect modulation type.
_DSD_FME_MODULATION = {
    'dmr': ['-mc'],        # C4FM
    'nxdn': ['-mc'],       # C4FM
}

# ============================================
# HELPERS
# ============================================


def find_dsd() -> tuple[str | None, bool]:
    """Find DSD (Digital Speech Decoder) binary.

    Checks for dsd-fme first (common fork), then falls back to dsd.
    Returns (path, is_fme) tuple.
    """
    path = shutil.which('dsd-fme')
    if path:
        return path, True
    path = shutil.which('dsd')
    if path:
        return path, False
    return None, False


def find_rtl_fm() -> str | None:
    """Find rtl_fm binary."""
    return shutil.which('rtl_fm')


def find_rx_fm() -> str | None:
    """Find SoapySDR rx_fm binary."""
    return shutil.which('rx_fm')


def find_ffmpeg() -> str | None:
    """Find ffmpeg for audio encoding."""
    return shutil.which('ffmpeg')


def parse_dsd_output(line: str) -> dict | None:
    """Parse a line of DSD stderr output into a structured event.

    Handles output from both classic ``dsd`` and ``dsd-fme`` which use
    different formatting for talkgroup / source / voice frame lines.
    """
    line = line.strip()
    if not line:
        return None

    # Skip DSD/dsd-fme startup banner lines (ASCII art, version info, etc.)
    # Only filter lines that are purely decorative — dsd-fme uses box-drawing
    # characters (│, ─) as column separators in DATA lines, so we must not
    # discard lines that also contain alphanumeric content.
    stripped_of_box = re.sub(r'[╔╗╚╝║═██▀▄╗╝╩╦╠╣╬│┤├┘└┐┌─┼█▓▒░\s]', '', line)
    if not stripped_of_box:
        return None
    if re.match(r'^\s*(Build Version|MBElib|CODEC2|Audio (Out|In)|Decoding )', line):
        return None

    ts = datetime.now().strftime('%H:%M:%S')

    # Sync detection: "Sync: +DMR (data)" or "Sync: +P25 Phase 1"
    sync_match = re.match(r'Sync:\s*\+?(\S+.*)', line)
    if sync_match:
        return {
            'type': 'sync',
            'protocol': sync_match.group(1).strip(),
            'timestamp': ts,
        }

    # Talkgroup and Source — check BEFORE slot so "Slot 1 Voice LC, TG: …"
    # is captured as a call event rather than a bare slot event.
    # Classic dsd:   "TG: 12345  Src: 67890"
    # dsd-fme:       "TG: 12345, Src: 67890"  or  "Talkgroup: 12345, Source: 67890"
    #                "TGT: 12345 | SRC: 67890" (pipe-delimited variant)
    tg_match = re.search(
        r'(?:TGT?|Talkgroup)[:\s]+(\d+)[,|│\s]+(?:Src|Source|SRC)[:\s]+(\d+)', line, re.IGNORECASE
    )
    if tg_match:
        result = {
            'type': 'call',
            'talkgroup': int(tg_match.group(1)),
            'source_id': int(tg_match.group(2)),
            'timestamp': ts,
        }
        # Extract slot if present on the same line
        slot_inline = re.search(r'Slot\s*(\d+)', line)
        if slot_inline:
            result['slot'] = int(slot_inline.group(1))
        return result

    # P25 NAC (Network Access Code) — check before voice/slot
    nac_match = re.search(r'NAC[:\s]+([0-9A-Fa-f]+)', line)
    if nac_match:
        return {
            'type': 'nac',
            'nac': nac_match.group(1),
            'timestamp': ts,
        }

    # Voice frame detection — check BEFORE bare slot match
    # Classic dsd: "Voice" keyword in frame lines
    # dsd-fme: "voice" or "Voice LC" or "VOICE" in output
    if re.search(r'\bvoice\b', line, re.IGNORECASE):
        result = {
            'type': 'voice',
            'detail': line,
            'timestamp': ts,
        }
        slot_inline = re.search(r'Slot\s*(\d+)', line)
        if slot_inline:
            result['slot'] = int(slot_inline.group(1))
        return result

    # Bare slot info (only when line is *just* slot info, not voice/call)
    slot_match = re.match(r'\s*Slot\s*(\d+)\s*$', line)
    if slot_match:
        return {
            'type': 'slot',
            'slot': int(slot_match.group(1)),
            'timestamp': ts,
        }

    # dsd-fme status lines we can surface: "TDMA", "CACH", "PI", "BS", etc.
    # Also catches "Closing", "Input", and other lifecycle lines.
    # Forward as raw so the frontend can show decoder is alive.
    return {
        'type': 'raw',
        'text': line[:200],
        'timestamp': ts,
    }


_HEARTBEAT_INTERVAL = 3.0  # seconds between heartbeats when decoder is idle

# 100ms of silence at 8kHz 16-bit mono = 1600 bytes
_SILENCE_CHUNK = b'\x00' * 1600


def _register_audio_sink(sink: object) -> None:
    """Register an ffmpeg stdin sink for mux fanout."""
    with _ffmpeg_sinks_lock:
        _ffmpeg_sinks.add(sink)


def _unregister_audio_sink(sink: object) -> None:
    """Remove an ffmpeg stdin sink from mux fanout."""
    with _ffmpeg_sinks_lock:
        _ffmpeg_sinks.discard(sink)


def _get_audio_sinks() -> tuple[object, ...]:
    """Snapshot current audio sinks for lock-free iteration."""
    with _ffmpeg_sinks_lock:
        return tuple(_ffmpeg_sinks)


def _stop_process(proc: Optional[subprocess.Popen]) -> None:
    """Terminate and unregister a subprocess if present."""
    if not proc:
        return
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    unregister_process(proc)


def _reset_runtime_state(*, release_device: bool) -> None:
    """Reset process + runtime state and optionally release SDR ownership."""
    global dmr_rtl_process, dmr_dsd_process
    global dmr_running, dmr_has_audio, dmr_active_device

    _stop_process(dmr_dsd_process)
    _stop_process(dmr_rtl_process)
    dmr_rtl_process = None
    dmr_dsd_process = None
    dmr_running = False
    dmr_has_audio = False
    with _ffmpeg_sinks_lock:
        _ffmpeg_sinks.clear()

    if release_device and dmr_active_device is not None:
        app_module.release_sdr_device(dmr_active_device)
        dmr_active_device = None


def _dsd_audio_mux(dsd_stdout):
    """Mux thread: sole reader of dsd-fme stdout.

    Always drains dsd-fme's audio output to prevent the process from
    blocking on stdout writes (which would also freeze stderr / text
    data). When streaming clients are connected, forwards data to all
    active ffmpeg stdin sinks with silence fill during voice gaps.
    """
    try:
        while dmr_running:
            ready, _, _ = select.select([dsd_stdout], [], [], 0.1)
            if ready:
                data = os.read(dsd_stdout.fileno(), 4096)
                if not data:
                    break
                sinks = _get_audio_sinks()
                for sink in sinks:
                    try:
                        sink.write(data)
                        sink.flush()
                    except (BrokenPipeError, OSError, ValueError):
                        _unregister_audio_sink(sink)
            else:
                # No audio from decoder — feed silence if client connected
                sinks = _get_audio_sinks()
                for sink in sinks:
                    try:
                        sink.write(_SILENCE_CHUNK)
                        sink.flush()
                    except (BrokenPipeError, OSError, ValueError):
                        _unregister_audio_sink(sink)
    except (OSError, ValueError):
        pass


def _queue_put(event: dict):
    """Put an event on the DMR queue, dropping oldest if full."""
    try:
        dmr_queue.put_nowait(event)
    except queue.Full:
        try:
            dmr_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            dmr_queue.put_nowait(event)
        except queue.Full:
            pass


def stream_dsd_output(rtl_process: subprocess.Popen, dsd_process: subprocess.Popen):
    """Read DSD stderr output and push parsed events to the queue.

    Uses select() with a timeout so we can send periodic heartbeat
    events while readline() would otherwise block indefinitely during
    silence (no signal being decoded).
    """
    global dmr_running

    try:
        _queue_put({'type': 'status', 'text': 'started'})
        last_heartbeat = time.time()

        while dmr_running:
            if dsd_process.poll() is not None:
                break

            # Wait up to 1s for data on stderr instead of blocking forever
            ready, _, _ = select.select([dsd_process.stderr], [], [], 1.0)

            if ready:
                line = dsd_process.stderr.readline()
                if not line:
                    if dsd_process.poll() is not None:
                        break
                    continue

                text = line.decode('utf-8', errors='replace').strip()
                if not text:
                    continue

                logger.debug("DSD raw: %s", text)
                parsed = parse_dsd_output(text)
                if parsed:
                    _queue_put(parsed)
                last_heartbeat = time.time()
            else:
                # No stderr output — send heartbeat so frontend knows
                # decoder is still alive and listening
                now = time.time()
                if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
                    _queue_put({
                        'type': 'heartbeat',
                        'timestamp': datetime.now().strftime('%H:%M:%S'),
                    })
                    last_heartbeat = now

    except Exception as e:
        logger.error(f"DSD stream error: {e}")
    finally:
        global dmr_active_device, dmr_rtl_process, dmr_dsd_process
        global dmr_has_audio
        dmr_running = False
        dmr_has_audio = False
        with _ffmpeg_sinks_lock:
            _ffmpeg_sinks.clear()
        # Capture exit info for diagnostics
        rc = dsd_process.poll()
        reason = 'stopped'
        detail = ''
        if rc is not None and rc != 0:
            reason = 'crashed'
            try:
                remaining = dsd_process.stderr.read(1024)
                if remaining:
                    detail = remaining.decode('utf-8', errors='replace').strip()[:200]
            except Exception:
                pass
            logger.warning(f"DSD process exited with code {rc}: {detail}")
        # Cleanup decoder + demod processes
        _stop_process(dsd_process)
        _stop_process(rtl_process)
        dmr_rtl_process = None
        dmr_dsd_process = None
        _queue_put({'type': 'status', 'text': reason, 'exit_code': rc, 'detail': detail})
        # Release SDR device
        if dmr_active_device is not None:
            app_module.release_sdr_device(dmr_active_device)
            dmr_active_device = None
        logger.info("DSD stream thread stopped")


# ============================================
# API ENDPOINTS
# ============================================

@dmr_bp.route('/tools')
def check_tools() -> Response:
    """Check for required tools."""
    dsd_path, _ = find_dsd()
    rtl_fm = find_rtl_fm()
    rx_fm = find_rx_fm()
    ffmpeg = find_ffmpeg()
    return jsonify({
        'dsd': dsd_path is not None,
        'rtl_fm': rtl_fm is not None,
        'rx_fm': rx_fm is not None,
        'ffmpeg': ffmpeg is not None,
        'available': dsd_path is not None and (rtl_fm is not None or rx_fm is not None),
        'protocols': VALID_PROTOCOLS,
    })


@dmr_bp.route('/start', methods=['POST'])
def start_dmr() -> Response:
    """Start digital voice decoding."""
    global dmr_rtl_process, dmr_dsd_process, dmr_thread
    global dmr_running, dmr_has_audio, dmr_active_device

    dsd_path, is_fme = find_dsd()
    if not dsd_path:
        return jsonify({'status': 'error', 'message': 'dsd not found. Install dsd-fme or dsd.'}), 503

    data = request.json or {}

    try:
        frequency = validate_frequency(data.get('frequency', 462.5625))
        gain = int(validate_gain(data.get('gain', 40)))
        device = validate_device_index(data.get('device', 0))
        protocol = str(data.get('protocol', 'auto')).lower()
        ppm = validate_ppm(data.get('ppm', 0))
    except (ValueError, TypeError) as e:
        return jsonify({'status': 'error', 'message': f'Invalid parameter: {e}'}), 400

    sdr_type_str = str(data.get('sdr_type', 'rtlsdr')).lower()
    try:
        sdr_type = SDRType(sdr_type_str)
    except ValueError:
        sdr_type = SDRType.RTL_SDR

    if protocol not in VALID_PROTOCOLS:
        return jsonify({'status': 'error', 'message': f'Invalid protocol. Use: {", ".join(VALID_PROTOCOLS)}'}), 400

    if sdr_type == SDRType.RTL_SDR:
        if not find_rtl_fm():
            return jsonify({'status': 'error', 'message': 'rtl_fm not found. Install rtl-sdr tools.'}), 503
    else:
        if not find_rx_fm():
            return jsonify({
                'status': 'error',
                'message': f'rx_fm not found. Install SoapySDR tools for {sdr_type.value}.'
            }), 503

    # Clear stale queue
    try:
        while True:
            dmr_queue.get_nowait()
    except queue.Empty:
        pass

    # Reserve running state before we start claiming resources/processes
    # so concurrent /start requests cannot race each other.
    with dmr_lock:
        if dmr_running:
            return jsonify({'status': 'error', 'message': 'Already running'}), 409
        dmr_running = True
        dmr_has_audio = False

    # Claim SDR device — use protocol name so the device panel shows
    # "D-STAR", "P25", etc. instead of always "DMR"
    mode_label = protocol.upper() if protocol != 'auto' else 'DMR'
    error = app_module.claim_sdr_device(device, mode_label)
    if error:
        with dmr_lock:
            dmr_running = False
        return jsonify({'status': 'error', 'error_type': 'DEVICE_BUSY', 'message': error}), 409

    dmr_active_device = device

    # Build FM demodulation command via SDR abstraction.
    try:
        sdr_device = SDRFactory.create_default_device(sdr_type, index=device)
        builder = SDRFactory.get_builder(sdr_type)
        rtl_cmd = builder.build_fm_demod_command(
            device=sdr_device,
            frequency_mhz=frequency,
            sample_rate=48000,
            gain=float(gain) if gain > 0 else None,
            ppm=int(ppm) if ppm != 0 else None,
            modulation='fm',
            squelch=None,
            bias_t=bool(data.get('bias_t', False)),
        )
        if sdr_type == SDRType.RTL_SDR:
            # Keep squelch fully open for digital bitstreams.
            rtl_cmd.extend(['-l', '0'])
    except Exception as e:
        _reset_runtime_state(release_device=True)
        return jsonify({'status': 'error', 'message': f'Failed to build SDR command: {e}'}), 500

    # Build DSD command
    # Audio output: pipe decoded audio (8kHz s16le PCM) to stdout for
    # ffmpeg transcoding.  Both dsd-fme and classic dsd support '-o -'.
    # If ffmpeg is unavailable, fall back to discarding audio.
    ffmpeg_path = find_ffmpeg()
    if ffmpeg_path:
        audio_out = '-'
    else:
        audio_out = 'null' if is_fme else '-'
        logger.warning("ffmpeg not found — audio streaming disabled, data-only mode")
    dsd_cmd = [dsd_path, '-i', '-', '-o', audio_out]
    if is_fme:
        dsd_cmd.extend(_DSD_FME_PROTOCOL_FLAGS.get(protocol, []))
        dsd_cmd.extend(_DSD_FME_MODULATION.get(protocol, []))
        # Event log to stderr so we capture TG/Source/Voice data that
        # dsd-fme may not output on stderr by default.
        dsd_cmd.extend(['-J', '/dev/stderr'])
        # Relax CRC checks for marginal signals — lets more frames
        # through at the cost of occasional decode errors.
        if data.get('relaxCrc', False):
            dsd_cmd.append('-F')
    else:
        dsd_cmd.extend(_DSD_PROTOCOL_FLAGS.get(protocol, []))

    try:
        dmr_rtl_process = subprocess.Popen(
            rtl_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        register_process(dmr_rtl_process)

        # DSD stdout → PIPE when ffmpeg available (audio pipeline),
        # otherwise DEVNULL (data-only mode)
        dsd_stdout = subprocess.PIPE if ffmpeg_path else subprocess.DEVNULL
        dmr_dsd_process = subprocess.Popen(
            dsd_cmd,
            stdin=dmr_rtl_process.stdout,
            stdout=dsd_stdout,
            stderr=subprocess.PIPE,
        )
        register_process(dmr_dsd_process)

        # Allow rtl_fm to send directly to dsd
        dmr_rtl_process.stdout.close()

        # Start mux thread: always drains dsd-fme stdout to prevent the
        # process from blocking (which would freeze stderr / text data).
        # ffmpeg is started lazily per-client in /dmr/audio/stream.
        if ffmpeg_path and dmr_dsd_process.stdout:
            dmr_has_audio = True
            threading.Thread(
                target=_dsd_audio_mux,
                args=(dmr_dsd_process.stdout,),
                daemon=True,
            ).start()

        time.sleep(0.3)

        rtl_rc = dmr_rtl_process.poll()
        dsd_rc = dmr_dsd_process.poll()
        if rtl_rc is not None or dsd_rc is not None:
            # Process died — capture stderr for diagnostics
            rtl_err = ''
            if dmr_rtl_process.stderr:
                rtl_err = dmr_rtl_process.stderr.read().decode('utf-8', errors='replace')[:500]
            dsd_err = ''
            if dmr_dsd_process.stderr:
                dsd_err = dmr_dsd_process.stderr.read().decode('utf-8', errors='replace')[:500]
            logger.error(f"DSD pipeline died: rtl_fm rc={rtl_rc} err={rtl_err!r}, dsd rc={dsd_rc} err={dsd_err!r}")
            # Terminate surviving processes and release resources.
            _reset_runtime_state(release_device=True)
            # Surface a clear error to the user
            detail = rtl_err.strip() or dsd_err.strip()
            if 'usb_claim_interface' in rtl_err or 'Failed to open' in rtl_err:
                msg = f'SDR device {device} is busy — it may be in use by another mode or process. Try a different device.'
            elif detail:
                msg = f'Failed to start DSD pipeline: {detail}'
            else:
                msg = 'Failed to start DSD pipeline'
            return jsonify({'status': 'error', 'message': msg}), 500

        # Drain rtl_fm stderr in background to prevent pipe blocking
        def _drain_rtl_stderr(proc):
            try:
                for line in proc.stderr:
                    pass
            except Exception:
                pass

        threading.Thread(target=_drain_rtl_stderr, args=(dmr_rtl_process,), daemon=True).start()

        dmr_thread = threading.Thread(
            target=stream_dsd_output,
            args=(dmr_rtl_process, dmr_dsd_process),
            daemon=True,
        )
        dmr_thread.start()

        return jsonify({
            'status': 'started',
            'frequency': frequency,
            'protocol': protocol,
            'sdr_type': sdr_type.value,
            'has_audio': dmr_has_audio,
        })

    except Exception as e:
        logger.error(f"Failed to start DMR: {e}")
        _reset_runtime_state(release_device=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@dmr_bp.route('/stop', methods=['POST'])
def stop_dmr() -> Response:
    """Stop digital voice decoding."""
    with dmr_lock:
        _reset_runtime_state(release_device=True)

    return jsonify({'status': 'stopped'})


@dmr_bp.route('/status')
def dmr_status() -> Response:
    """Get DMR decoder status."""
    return jsonify({
        'running': dmr_running,
        'device': dmr_active_device,
        'has_audio': dmr_has_audio,
    })


@dmr_bp.route('/audio/stream')
def stream_dmr_audio() -> Response:
    """Stream decoded digital voice audio as WAV.

    Starts a per-client ffmpeg encoder.  The global mux thread
    (_dsd_audio_mux) forwards DSD audio to this ffmpeg's stdin while
    the client is connected, and discards audio otherwise.  This avoids
    the pipe-buffer deadlock that occurs when ffmpeg is started at
    decoder launch (its stdout fills up before any HTTP client reads
    it, back-pressuring the entire pipeline and freezing stderr/text
    data output).
    """
    if not dmr_running or not dmr_has_audio:
        return Response(b'', mimetype='audio/wav', status=204)

    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        return Response(b'', mimetype='audio/wav', status=503)

    encoder_cmd = [
        ffmpeg_path, '-hide_banner', '-loglevel', 'error',
        '-fflags', 'nobuffer', '-flags', 'low_delay',
        '-probesize', '32', '-analyzeduration', '0',
        '-f', 's16le', '-ar', '8000', '-ac', '1', '-i', 'pipe:0',
        '-acodec', 'pcm_s16le', '-ar', '44100', '-f', 'wav', 'pipe:1',
    ]
    audio_proc = subprocess.Popen(
        encoder_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Drain ffmpeg stderr to prevent blocking
    threading.Thread(
        target=lambda p: [None for _ in p.stderr],
        args=(audio_proc,), daemon=True,
    ).start()

    if audio_proc.stdin:
        _register_audio_sink(audio_proc.stdin)

    def generate():
        try:
            while dmr_running and audio_proc.poll() is None:
                ready, _, _ = select.select([audio_proc.stdout], [], [], 2.0)
                if ready:
                    chunk = audio_proc.stdout.read(4096)
                    if chunk:
                        yield chunk
                    else:
                        break
                else:
                    if audio_proc.poll() is not None:
                        break
        except GeneratorExit:
            pass
        except Exception as e:
            logger.error(f"DMR audio stream error: {e}")
        finally:
            # Disconnect mux → ffmpeg, then clean up
            if audio_proc.stdin:
                _unregister_audio_sink(audio_proc.stdin)
            try:
                audio_proc.stdin.close()
            except Exception:
                pass
            try:
                audio_proc.terminate()
                audio_proc.wait(timeout=2)
            except Exception:
                try:
                    audio_proc.kill()
                except Exception:
                    pass

    return Response(
        generate(),
        mimetype='audio/wav',
        headers={
            'Content-Type': 'audio/wav',
            'Cache-Control': 'no-cache, no-store',
            'X-Accel-Buffering': 'no',
            'Transfer-Encoding': 'chunked',
        },
    )


@dmr_bp.route('/stream')
def stream_dmr() -> Response:
    """SSE stream for DMR decoder events."""
    def _on_msg(msg: dict[str, Any]) -> None:
        process_event('dmr', msg, msg.get('type'))

    response = Response(
        sse_stream_fanout(
            source_queue=dmr_queue,
            channel_key='dmr',
            timeout=SSE_QUEUE_TIMEOUT,
            keepalive_interval=SSE_KEEPALIVE_INTERVAL,
            on_message=_on_msg,
        ),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response
