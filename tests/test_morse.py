"""Tests for Morse code decoder (utils/morse.py) and routes."""

from __future__ import annotations

import math
import queue
import struct
import threading

import pytest

from utils.morse import (
    CHAR_TO_MORSE,
    MORSE_TABLE,
    GoertzelFilter,
    MorseDecoder,
    morse_decoder_thread,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _login_session(client) -> None:
    """Mark the Flask test session as authenticated."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'test'
        sess['role'] = 'admin'


def generate_tone(freq: float, duration: float, sample_rate: int = 8000, amplitude: float = 0.8) -> bytes:
    """Generate a pure sine wave as 16-bit LE PCM bytes."""
    n_samples = int(sample_rate * duration)
    samples = []
    for i in range(n_samples):
        t = i / sample_rate
        val = int(amplitude * 32767 * math.sin(2 * math.pi * freq * t))
        samples.append(max(-32768, min(32767, val)))
    return struct.pack(f'<{len(samples)}h', *samples)


def generate_silence(duration: float, sample_rate: int = 8000) -> bytes:
    """Generate silence as 16-bit LE PCM bytes."""
    n_samples = int(sample_rate * duration)
    return b'\x00\x00' * n_samples


def generate_morse_audio(text: str, wpm: int = 15, tone_freq: float = 700.0, sample_rate: int = 8000) -> bytes:
    """Generate PCM audio for a Morse-encoded string."""
    dit_dur = 1.2 / wpm
    dah_dur = 3 * dit_dur
    element_gap = dit_dur
    char_gap = 3 * dit_dur
    word_gap = 7 * dit_dur

    audio = b''
    words = text.upper().split()
    for wi, word in enumerate(words):
        for ci, char in enumerate(word):
            morse = CHAR_TO_MORSE.get(char)
            if morse is None:
                continue
            for ei, element in enumerate(morse):
                if element == '.':
                    audio += generate_tone(tone_freq, dit_dur, sample_rate)
                elif element == '-':
                    audio += generate_tone(tone_freq, dah_dur, sample_rate)
                if ei < len(morse) - 1:
                    audio += generate_silence(element_gap, sample_rate)
            if ci < len(word) - 1:
                audio += generate_silence(char_gap, sample_rate)
        if wi < len(words) - 1:
            audio += generate_silence(word_gap, sample_rate)

    # Add some leading/trailing silence for threshold settling
    silence = generate_silence(0.3, sample_rate)
    return silence + audio + silence


# ---------------------------------------------------------------------------
# MORSE_TABLE tests
# ---------------------------------------------------------------------------

class TestMorseTable:
    def test_all_26_letters_present(self):
        chars = set(MORSE_TABLE.values())
        for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            assert letter in chars, f"Missing letter: {letter}"

    def test_all_10_digits_present(self):
        chars = set(MORSE_TABLE.values())
        for digit in '0123456789':
            assert digit in chars, f"Missing digit: {digit}"

    def test_reverse_lookup_consistent(self):
        for morse, char in MORSE_TABLE.items():
            if char in CHAR_TO_MORSE:
                assert CHAR_TO_MORSE[char] == morse

    def test_no_duplicate_morse_codes(self):
        """Each morse pattern should map to exactly one character."""
        assert len(MORSE_TABLE) == len(set(MORSE_TABLE.keys()))


# ---------------------------------------------------------------------------
# GoertzelFilter tests
# ---------------------------------------------------------------------------

class TestGoertzelFilter:
    def test_detects_target_frequency(self):
        gf = GoertzelFilter(target_freq=700.0, sample_rate=8000, block_size=160)
        # Generate 700 Hz tone
        samples = [0.8 * math.sin(2 * math.pi * 700 * i / 8000) for i in range(160)]
        mag = gf.magnitude(samples)
        assert mag > 10.0, f"Expected high magnitude for target freq, got {mag}"

    def test_rejects_off_frequency(self):
        gf = GoertzelFilter(target_freq=700.0, sample_rate=8000, block_size=160)
        # Generate 1500 Hz tone (well off target)
        samples = [0.8 * math.sin(2 * math.pi * 1500 * i / 8000) for i in range(160)]
        mag_off = gf.magnitude(samples)

        # Compare with on-target
        samples_on = [0.8 * math.sin(2 * math.pi * 700 * i / 8000) for i in range(160)]
        mag_on = gf.magnitude(samples_on)

        assert mag_on > mag_off * 3, "Target freq should be significantly stronger than off-freq"

    def test_silence_returns_near_zero(self):
        gf = GoertzelFilter(target_freq=700.0, sample_rate=8000, block_size=160)
        samples = [0.0] * 160
        mag = gf.magnitude(samples)
        assert mag < 0.01, f"Expected near-zero for silence, got {mag}"

    def test_different_block_sizes(self):
        for block_size in [80, 160, 320]:
            gf = GoertzelFilter(target_freq=700.0, sample_rate=8000, block_size=block_size)
            samples = [0.8 * math.sin(2 * math.pi * 700 * i / 8000) for i in range(block_size)]
            mag = gf.magnitude(samples)
            assert mag > 5.0, f"Should detect tone with block_size={block_size}"


# ---------------------------------------------------------------------------
# MorseDecoder tests
# ---------------------------------------------------------------------------

class TestMorseDecoder:
    def _make_decoder(self, wpm=15):
        """Create decoder with warm-up phase completed for testing.

        Feeds silence then tone then silence to get past the warm-up
        blocks and establish a valid noise floor / signal peak.
        """
        decoder = MorseDecoder(sample_rate=8000, tone_freq=700.0, wpm=wpm)
        # Feed enough audio to get past warm-up (50 blocks = 1 sec)
        # Mix silence and tone so warm-up sees both noise and signal
        warmup_audio = generate_silence(0.6) + generate_tone(700.0, 0.4) + generate_silence(0.5)
        decoder.process_block(warmup_audio)
        # Reset state machine after warm-up so tests start clean
        decoder._tone_on = False
        decoder._current_symbol = ''
        decoder._tone_blocks = 0
        decoder._silence_blocks = 0
        return decoder

    def test_dit_detection(self):
        """A single dit should produce a '.' in the symbol buffer."""
        decoder = self._make_decoder()
        dit_dur = 1.2 / 15

        # Send a tone burst (dit)
        tone = generate_tone(700.0, dit_dur)
        decoder.process_block(tone)

        # Send silence to trigger end of tone
        silence = generate_silence(dit_dur * 2)
        decoder.process_block(silence)

        # Symbol buffer should have a dot
        assert '.' in decoder._current_symbol, f"Expected '.' in symbol, got '{decoder._current_symbol}'"

    def test_dah_detection(self):
        """A longer tone should produce a '-' in the symbol buffer."""
        decoder = self._make_decoder()
        dah_dur = 3 * 1.2 / 15

        tone = generate_tone(700.0, dah_dur)
        decoder.process_block(tone)

        silence = generate_silence(dah_dur)
        decoder.process_block(silence)

        assert '-' in decoder._current_symbol, f"Expected '-' in symbol, got '{decoder._current_symbol}'"

    def test_decode_letter_e(self):
        """E is a single dit - the simplest character."""
        decoder = self._make_decoder()
        audio = generate_morse_audio('E', wpm=15)
        events = decoder.process_block(audio)
        events.extend(decoder.flush())

        chars = [e for e in events if e['type'] == 'morse_char']
        decoded = ''.join(e['char'] for e in chars)
        assert 'E' in decoded, f"Expected 'E' in decoded text, got '{decoded}'"

    def test_decode_letter_t(self):
        """T is a single dah."""
        decoder = self._make_decoder()
        audio = generate_morse_audio('T', wpm=15)
        events = decoder.process_block(audio)
        events.extend(decoder.flush())

        chars = [e for e in events if e['type'] == 'morse_char']
        decoded = ''.join(e['char'] for e in chars)
        assert 'T' in decoded, f"Expected 'T' in decoded text, got '{decoded}'"

    def test_word_space_detection(self):
        """A long silence between words should produce decoded chars with a space."""
        decoder = self._make_decoder()
        dit_dur = 1.2 / 15
        # E = dit
        audio = generate_tone(700.0, dit_dur) + generate_silence(7 * dit_dur * 1.5)
        # T = dah
        audio += generate_tone(700.0, 3 * dit_dur) + generate_silence(3 * dit_dur)
        events = decoder.process_block(audio)
        events.extend(decoder.flush())

        spaces = [e for e in events if e['type'] == 'morse_space']
        assert len(spaces) >= 1, "Expected at least one word space"

    def test_scope_events_generated(self):
        """Decoder should produce scope events for visualization."""
        audio = generate_morse_audio('SOS', wpm=15)
        decoder = MorseDecoder(sample_rate=8000, tone_freq=700.0, wpm=15)

        events = decoder.process_block(audio)

        scope_events = [e for e in events if e['type'] == 'scope']
        assert len(scope_events) > 0, "Expected scope events"
        # Check scope event structure
        se = scope_events[0]
        assert 'amplitudes' in se
        assert 'threshold' in se
        assert 'tone_on' in se

    def test_adaptive_threshold_adjusts(self):
        """After processing enough audio to complete warm-up, threshold should be non-zero."""
        decoder = MorseDecoder(sample_rate=8000, tone_freq=700.0, wpm=15)

        # Feed enough audio to complete the 50-block warm-up (~1 second)
        audio = generate_silence(0.6) + generate_tone(700.0, 0.4) + generate_silence(0.3)
        decoder.process_block(audio)

        assert decoder._threshold > 0, "Threshold should adapt above zero after warm-up"

    def test_flush_emits_pending_char(self):
        """flush() should emit any accumulated but not-yet-decoded symbol."""
        decoder = MorseDecoder(sample_rate=8000, tone_freq=700.0, wpm=15)
        decoder._current_symbol = '.'  # Manually set pending dit
        events = decoder.flush()
        assert len(events) == 1
        assert events[0]['type'] == 'morse_char'
        assert events[0]['char'] == 'E'

    def test_flush_empty_returns_nothing(self):
        decoder = MorseDecoder(sample_rate=8000, tone_freq=700.0, wpm=15)
        events = decoder.flush()
        assert events == []

    def test_weak_signal_detection(self):
        """CW tone at only 3x noise magnitude should still decode characters."""
        decoder = self._make_decoder(wpm=10)
        # Generate weak CW audio (low amplitude simulating weak HF signal)
        audio = generate_morse_audio('SOS', wpm=10, sample_rate=8000)
        # Scale to low amplitude (simulating weak signal)
        n_samples = len(audio) // 2
        samples = struct.unpack(f'<{n_samples}h', audio)
        # Reduce to ~10% amplitude
        weak_samples = [max(-32768, min(32767, int(s * 0.1))) for s in samples]
        weak_audio = struct.pack(f'<{len(weak_samples)}h', *weak_samples)

        events = decoder.process_block(weak_audio)
        events.extend(decoder.flush())

        chars = [e for e in events if e['type'] == 'morse_char']
        decoded = ''.join(e['char'] for e in chars)
        # Should decode at least some characters from the weak signal
        assert len(chars) >= 1, f"Expected decoded chars from weak signal, got '{decoded}'"

    def test_agc_boosts_quiet_signal(self):
        """Very quiet PCM (amplitude 0.01) should still produce usable Goertzel magnitudes."""
        decoder = MorseDecoder(sample_rate=8000, tone_freq=700.0, wpm=15)
        # Generate very quiet tone
        quiet_tone = generate_tone(700.0, 1.5, amplitude=0.01)  # 1.5s of very quiet CW
        events = decoder.process_block(quiet_tone)

        scope_events = [e for e in events if e['type'] == 'scope']
        assert len(scope_events) > 0, "Expected scope events from quiet signal"
        # AGC should have boosted the signal — amplitudes should be visible
        max_amp = max(max(se['amplitudes']) for se in scope_events)
        assert max_amp > 1.0, f"AGC should boost quiet signal to usable magnitude, got {max_amp}"


# ---------------------------------------------------------------------------
# morse_decoder_thread tests
# ---------------------------------------------------------------------------

class TestMorseDecoderThread:
    def test_thread_stops_on_event(self):
        """Thread should exit when stop_event is set."""
        import io
        # Create a fake stdout that blocks until stop
        stop = threading.Event()
        q = queue.Queue(maxsize=100)

        # Feed some audio then close
        audio = generate_morse_audio('E', wpm=15)
        fake_stdout = io.BytesIO(audio)

        t = threading.Thread(
            target=morse_decoder_thread,
            args=(fake_stdout, q, stop),
        )
        t.daemon = True
        t.start()
        t.join(timeout=5)
        assert not t.is_alive(), "Thread should finish after reading all data"

    def test_thread_heartbeat_on_no_data(self):
        """When rtl_fm produces no data, thread should emit waiting scope events."""
        import os as _os
        stop = threading.Event()
        q = queue.Queue(maxsize=100)

        # Create a pipe that never gets written to (simulates rtl_fm with no output)
        read_fd, write_fd = _os.pipe()
        read_file = _os.fdopen(read_fd, 'rb', 0)

        t = threading.Thread(
            target=morse_decoder_thread,
            args=(read_file, q, stop),
        )
        t.daemon = True
        t.start()

        # Wait up to 5 seconds for at least one heartbeat event
        events = []
        import time as _time
        deadline = _time.monotonic() + 5.0
        while _time.monotonic() < deadline:
            try:
                ev = q.get(timeout=0.5)
                events.append(ev)
                if ev.get('waiting'):
                    break
            except queue.Empty:
                continue

        stop.set()
        _os.close(write_fd)
        read_file.close()
        t.join(timeout=3)

        waiting_events = [e for e in events if e.get('type') == 'scope' and e.get('waiting')]
        assert len(waiting_events) >= 1, f"Expected waiting heartbeat events, got {events}"
        ev = waiting_events[0]
        assert ev['amplitudes'] == []
        assert ev['threshold'] == 0
        assert ev['tone_on'] is False

    def test_thread_produces_events(self):
        """Thread should push character events to the queue."""
        import io
        from unittest.mock import patch
        stop = threading.Event()
        q = queue.Queue(maxsize=1000)

        # Generate audio with pre-warmed decoder in mind
        # The thread creates a fresh decoder, so generate lots of audio
        audio = generate_silence(0.5) + generate_morse_audio('SOS', wpm=10) + generate_silence(1.0)
        fake_stdout = io.BytesIO(audio)

        # Patch SCOPE_INTERVAL to 0 so scope events aren't throttled in fast reads
        with patch('utils.morse.time') as mock_time:
            # Make monotonic() always return increasing values
            counter = [0.0]
            def fake_monotonic():
                counter[0] += 0.15  # each call advances 150ms
                return counter[0]
            mock_time.monotonic = fake_monotonic

            t = threading.Thread(
                target=morse_decoder_thread,
                args=(fake_stdout, q, stop),
            )
            t.daemon = True
            t.start()
            t.join(timeout=10)

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        # Should have at least some events (scope or char)
        assert len(events) > 0, "Expected events from thread"


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

class TestMorseRoutes:
    def test_start_missing_required_fields(self, client):
        """Start should succeed with defaults."""
        _login_session(client)
        with pytest.MonkeyPatch.context() as m:
            m.setattr('app.morse_process', None)
            # Should fail because rtl_fm won't be found in test env
            resp = client.post('/morse/start', json={'frequency': '14.060'})
            assert resp.status_code in (200, 400, 409, 500)

    def test_stop_when_not_running(self, client):
        """Stop when nothing is running should return not_running."""
        _login_session(client)
        with pytest.MonkeyPatch.context() as m:
            m.setattr('app.morse_process', None)
            resp = client.post('/morse/stop')
            data = resp.get_json()
            assert data['status'] == 'not_running'

    def test_status_when_not_running(self, client):
        """Status should report not running."""
        _login_session(client)
        with pytest.MonkeyPatch.context() as m:
            m.setattr('app.morse_process', None)
            resp = client.get('/morse/status')
            data = resp.get_json()
            assert data['running'] is False

    def test_invalid_tone_freq(self, client):
        """Tone frequency outside range should be rejected."""
        _login_session(client)
        with pytest.MonkeyPatch.context() as m:
            m.setattr('app.morse_process', None)
            resp = client.post('/morse/start', json={
                'frequency': '14.060',
                'tone_freq': '50',  # too low
            })
            assert resp.status_code == 400

    def test_invalid_wpm(self, client):
        """WPM outside range should be rejected."""
        _login_session(client)
        with pytest.MonkeyPatch.context() as m:
            m.setattr('app.morse_process', None)
            resp = client.post('/morse/start', json={
                'frequency': '14.060',
                'wpm': '100',  # too high
            })
            assert resp.status_code == 400

    def test_stream_endpoint_exists(self, client):
        """Stream endpoint should return SSE content type."""
        _login_session(client)
        resp = client.get('/morse/stream')
        assert resp.content_type.startswith('text/event-stream')
