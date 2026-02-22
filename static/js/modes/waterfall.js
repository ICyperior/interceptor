/*
 * Spectrum Waterfall Mode
 * Real-time SDR waterfall with click-to-tune and integrated monitor audio.
 */
const Waterfall = (function () {
    'use strict';

    let _ws = null;
    let _es = null;
    let _transport = 'ws';
    let _wsOpened = false;
    let _wsFallbackTimer = null;
    let _sseStartPromise = null;
    let _sseStartConfigKey = '';
    let _active = false;
    let _running = false;
    let _listenersAttached = false;
    let _controlListenersAttached = false;

    let _retuneTimer = null;
    let _monitorRetuneTimer = null;

    let _peakHold = false;
    let _showAnnotations = true;
    let _autoRange = true;
    let _dbMin = -100;
    let _dbMax = -20;
    let _palette = 'turbo';

    let _specCanvas = null;
    let _specCtx = null;
    let _wfCanvas = null;
    let _wfCtx = null;
    let _peakLine = null;

    let _startMhz = 98.8;
    let _endMhz = 101.2;
    let _monitorFreqMhz = 100.0;

    let _monitoring = false;
    let _monitorMuted = false;
    let _resumeWaterfallAfterMonitor = false;
    let _startingMonitor = false;
    let _monitorSource = 'process';
    let _pendingSharedMonitorRearm = false;
    let _audioConnectNonce = 0;
    let _audioAnalyser = null;
    let _audioContext = null;
    let _audioSourceNode = null;
    let _smeterRaf = null;
    let _audioUnlockRequired = false;

    let _devices = [];

    const PALETTES = {};

    const RF_BANDS = [
        [0.535, 1.705, 'AM', 'rgba(255,200,50,0.15)'],
        [87.5, 108.0, 'FM', 'rgba(255,100,100,0.15)'],
        [108.0, 137.0, 'Aviation', 'rgba(100,220,100,0.12)'],
        [137.5, 137.9125, 'NOAA APT', 'rgba(50,200,255,0.25)'],
        [144.0, 148.0, '2m Ham', 'rgba(255,165,0,0.20)'],
        [156.0, 174.0, 'Marine', 'rgba(50,150,255,0.15)'],
        [162.4, 162.55, 'Wx Radio', 'rgba(50,255,200,0.35)'],
        [420.0, 450.0, '70cm Ham', 'rgba(255,165,0,0.18)'],
        [433.05, 434.79, 'ISM 433', 'rgba(255,80,255,0.25)'],
        [446.0, 446.2, 'PMR446', 'rgba(180,80,255,0.30)'],
        [868.0, 868.6, 'ISM 868', 'rgba(255,80,255,0.22)'],
        [902.0, 928.0, 'ISM 915', 'rgba(255,80,255,0.18)'],
        [1089.95, 1090.05, 'ADS-B', 'rgba(50,255,80,0.45)'],
        [2400.0, 2500.0, '2.4G WiFi', 'rgba(255,165,0,0.12)'],
        [5725.0, 5875.0, '5.8G WiFi', 'rgba(255,165,0,0.12)'],
    ];

    const PRESETS = {
        fm: { center: 98.0, span: 20.0, mode: 'wfm', step: 0.1 },
        air: { center: 124.5, span: 8.0, mode: 'am', step: 0.025 },
        marine: { center: 161.0, span: 4.0, mode: 'fm', step: 0.025 },
        ham2m: { center: 146.0, span: 4.0, mode: 'fm', step: 0.0125 },
    };
    const WS_OPEN_FALLBACK_MS = 6500;

    function _setStatus(text) {
        const el = document.getElementById('wfStatus');
        if (el) {
            el.textContent = text || '';
        }
    }

    function _setVisualStatus(text) {
        const el = document.getElementById('wfVisualStatus');
        if (el) {
            el.textContent = text || 'IDLE';
        }
    }

    function _setMonitorState(text) {
        const el = document.getElementById('wfMonitorState');
        if (el) {
            el.textContent = text || 'No audio monitor';
        }
    }

    function _buildPalettes() {
        function lerp(a, b, t) {
            return a + (b - a) * t;
        }
        function lerpRGB(c1, c2, t) {
            return [lerp(c1[0], c2[0], t), lerp(c1[1], c2[1], t), lerp(c1[2], c2[2], t)];
        }
        function buildLUT(stops) {
            const lut = new Uint8Array(256 * 3);
            for (let i = 0; i < 256; i += 1) {
                const t = i / 255;
                let s = 0;
                while (s < stops.length - 2 && t > stops[s + 1][0]) s += 1;
                const t0 = stops[s][0];
                const t1 = stops[s + 1][0];
                const local = t0 === t1 ? 0 : (t - t0) / (t1 - t0);
                const rgb = lerpRGB(stops[s][1], stops[s + 1][1], local);
                lut[i * 3] = Math.round(rgb[0]);
                lut[i * 3 + 1] = Math.round(rgb[1]);
                lut[i * 3 + 2] = Math.round(rgb[2]);
            }
            return lut;
        }
        PALETTES.turbo = buildLUT([
            [0, [48, 18, 59]],
            [0.25, [65, 182, 196]],
            [0.5, [253, 231, 37]],
            [0.75, [246, 114, 48]],
            [1, [178, 24, 43]],
        ]);
        PALETTES.plasma = buildLUT([
            [0, [13, 8, 135]],
            [0.33, [126, 3, 168]],
            [0.66, [249, 124, 1]],
            [1, [240, 249, 33]],
        ]);
        PALETTES.inferno = buildLUT([
            [0, [0, 0, 4]],
            [0.33, [65, 1, 88]],
            [0.66, [253, 163, 23]],
            [1, [252, 255, 164]],
        ]);
        PALETTES.viridis = buildLUT([
            [0, [68, 1, 84]],
            [0.33, [59, 82, 139]],
            [0.66, [33, 145, 140]],
            [1, [253, 231, 37]],
        ]);
    }

    function _colorize(val, lut) {
        const idx = Math.max(0, Math.min(255, Math.round(val * 255)));
        return [lut[idx * 3], lut[idx * 3 + 1], lut[idx * 3 + 2]];
    }

    function _parseFrame(buf) {
        if (!buf || buf.byteLength < 11) return null;
        const view = new DataView(buf);
        if (view.getUint8(0) !== 0x01) return null;
        const startMhz = view.getFloat32(1, true);
        const endMhz = view.getFloat32(5, true);
        const numBins = view.getUint16(9, true);
        if (buf.byteLength < 11 + numBins) return null;
        const bins = new Uint8Array(buf, 11, numBins);
        return { numBins, bins, startMhz, endMhz };
    }

    function _getNumber(id, fallback) {
        const el = document.getElementById(id);
        if (!el) return fallback;
        const value = parseFloat(el.value);
        return Number.isFinite(value) ? value : fallback;
    }

    function _clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function _wait(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    function _ctx2d(canvas, options) {
        if (!canvas) return null;
        try {
            return canvas.getContext('2d', options);
        } catch (_) {
            return canvas.getContext('2d');
        }
    }

    function _ssePayloadKey(payload) {
        return JSON.stringify([
            payload.start_freq,
            payload.end_freq,
            payload.bin_size,
            payload.gain,
            payload.device,
            payload.interval,
            payload.max_bins,
        ]);
    }

    function _isWaterfallAlreadyRunningConflict(response, body) {
        if (body?.already_running === true) return true;
        if (!response || response.status !== 409) return false;
        const msg = String(body?.message || '').toLowerCase();
        return msg.includes('already running');
    }

    function _isWaterfallDeviceBusy(response, body) {
        return !!response && response.status === 409 && body?.error_type === 'DEVICE_BUSY';
    }

    function _clearWsFallbackTimer() {
        if (_wsFallbackTimer) {
            clearTimeout(_wsFallbackTimer);
            _wsFallbackTimer = null;
        }
    }

    function _closeSseStream() {
        if (_es) {
            try {
                _es.close();
            } catch (_) {
                // Ignore EventSource close failures.
            }
            _es = null;
        }
    }

    function _normalizeSweepBins(rawBins) {
        if (!Array.isArray(rawBins) || rawBins.length === 0) return null;
        const bins = rawBins.map((v) => Number(v));
        if (!bins.some((v) => Number.isFinite(v))) return null;

        let min = _autoRange ? Infinity : _dbMin;
        let max = _autoRange ? -Infinity : _dbMax;
        if (_autoRange) {
            for (let i = 0; i < bins.length; i += 1) {
                const value = bins[i];
                if (!Number.isFinite(value)) continue;
                if (value < min) min = value;
                if (value > max) max = value;
            }
            if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
            const pad = Math.max(8, (max - min) * 0.08);
            min -= pad;
            max += pad;
        }

        if (max <= min) max = min + 1;
        const out = new Uint8Array(bins.length);
        const span = max - min;
        for (let i = 0; i < bins.length; i += 1) {
            const value = Number.isFinite(bins[i]) ? bins[i] : min;
            const norm = _clamp((value - min) / span, 0, 1);
            out[i] = Math.round(norm * 255);
        }
        return out;
    }

    function _setUnlockVisible(show) {
        const btn = document.getElementById('wfAudioUnlockBtn');
        if (btn) btn.style.display = show ? '' : 'none';
    }

    function _isAutoplayError(err) {
        if (!err) return false;
        const name = String(err.name || '').toLowerCase();
        const msg = String(err.message || '').toLowerCase();
        return name === 'notallowederror'
            || msg.includes('notallowed')
            || msg.includes('gesture')
            || msg.includes('user didn\'t interact');
    }

    function _waitForPlayback(player, timeoutMs) {
        return new Promise((resolve) => {
            let done = false;
            let timer = null;

            const finish = (ok) => {
                if (done) return;
                done = true;
                if (timer) clearTimeout(timer);
                events.forEach((evt) => player.removeEventListener(evt, onReady));
                failEvents.forEach((evt) => player.removeEventListener(evt, onFail));
                resolve(ok);
            };

            const onReady = () => finish(true);
            const onFail = () => finish(false);
            const events = ['playing', 'timeupdate', 'canplay', 'loadeddata'];
            const failEvents = ['error', 'abort', 'stalled', 'ended'];

            events.forEach((evt) => player.addEventListener(evt, onReady));
            failEvents.forEach((evt) => player.addEventListener(evt, onFail));

            timer = setTimeout(() => {
                finish(!player.paused && (player.currentTime > 0 || player.readyState >= 2));
            }, timeoutMs);

            if (!player.paused && (player.currentTime > 0 || player.readyState >= 2)) {
                finish(true);
            }
        });
    }

    function _readStepLabel() {
        const stepEl = document.getElementById('wfStepSize');
        if (!stepEl) return 'STEP 100 kHz';
        const option = stepEl.options[stepEl.selectedIndex];
        if (option && option.textContent) return `STEP ${option.textContent.trim()}`;
        const value = parseFloat(stepEl.value);
        if (!Number.isFinite(value)) return 'STEP --';
        return value >= 1 ? `STEP ${value.toFixed(0)} MHz` : `STEP ${(value * 1000).toFixed(0)} kHz`;
    }

    function _getMonitorMode() {
        return document.getElementById('wfMonitorMode')?.value || 'wfm';
    }

    function _setModeButtons(mode) {
        document.querySelectorAll('.wf-mode-btn').forEach((btn) => {
            btn.classList.toggle('is-active', btn.dataset.mode === mode);
        });
    }

    function _setMonitorMode(mode) {
        const safeMode = ['wfm', 'fm', 'am', 'usb', 'lsb'].includes(mode) ? mode : 'wfm';
        const select = document.getElementById('wfMonitorMode');
        if (select) {
            select.value = safeMode;
        }
        _setModeButtons(safeMode);
        const modeReadout = document.getElementById('wfRxModeReadout');
        if (modeReadout) modeReadout.textContent = safeMode.toUpperCase();
    }

    function _setSmeter(levelPct, text) {
        const bar = document.getElementById('wfSmeterBar');
        const label = document.getElementById('wfSmeterText');
        if (bar) bar.style.width = `${_clamp(levelPct, 0, 100).toFixed(1)}%`;
        if (label) label.textContent = text || 'S0';
    }

    function _stopSmeter() {
        if (_smeterRaf) {
            cancelAnimationFrame(_smeterRaf);
            _smeterRaf = null;
        }
        _setSmeter(0, 'S0');
    }

    function _startSmeter(player) {
        if (!player) return;
        try {
            if (!_audioContext) {
                _audioContext = new (window.AudioContext || window.webkitAudioContext)();
            }

            if (_audioContext.state === 'suspended') {
                _audioContext.resume().catch(() => {});
            }

            if (!_audioSourceNode) {
                _audioSourceNode = _audioContext.createMediaElementSource(player);
            }

            if (!_audioAnalyser) {
                _audioAnalyser = _audioContext.createAnalyser();
                _audioAnalyser.fftSize = 2048;
                _audioAnalyser.smoothingTimeConstant = 0.7;
                _audioSourceNode.connect(_audioAnalyser);
                _audioAnalyser.connect(_audioContext.destination);
            }
        } catch (_) {
            return;
        }

        const samples = new Uint8Array(_audioAnalyser.frequencyBinCount);
        const render = () => {
            if (!_monitoring || !_audioAnalyser) {
                _setSmeter(0, 'S0');
                return;
            }
            _audioAnalyser.getByteFrequencyData(samples);
            let sum = 0;
            for (let i = 0; i < samples.length; i += 1) sum += samples[i];
            const avg = sum / (samples.length || 1);
            const pct = _clamp((avg / 180) * 100, 0, 100);
            let sText = 'S0';
            const sUnit = Math.round((pct / 100) * 9);
            if (sUnit >= 9) {
                const over = Math.max(0, Math.round((pct - 88) * 1.8));
                sText = over > 0 ? `S9+${over}` : 'S9';
            } else {
                sText = `S${Math.max(0, sUnit)}`;
            }
            _setSmeter(pct, sText);
            _smeterRaf = requestAnimationFrame(render);
        };

        _stopSmeter();
        _smeterRaf = requestAnimationFrame(render);
    }

    function _currentCenter() {
        return _getNumber('wfCenterFreq', 100.0);
    }

    function _currentSpan() {
        return _getNumber('wfSpanMhz', 2.4);
    }

    function _updateRunButtons() {
        const startBtn = document.getElementById('wfStartBtn');
        const stopBtn = document.getElementById('wfStopBtn');
        if (startBtn) startBtn.style.display = _running ? 'none' : '';
        if (stopBtn) stopBtn.style.display = _running ? '' : 'none';
    }

    function _updateTuneLine() {
        const span = _endMhz - _startMhz;
        const pct = span > 0 ? (_monitorFreqMhz - _startMhz) / span : 0.5;
        const visible = Number.isFinite(pct) && pct >= 0 && pct <= 1;

        ['wfTuneLineSpec', 'wfTuneLineWf'].forEach((id) => {
            const line = document.getElementById(id);
            if (!line) return;
            if (visible) {
                line.style.left = `${(pct * 100).toFixed(4)}%`;
                line.classList.add('is-visible');
            } else {
                line.classList.remove('is-visible');
            }
        });
    }

    function _updateFreqDisplay() {
        const center = _currentCenter();
        const span = _currentSpan();

        const hiddenCenter = document.getElementById('wfCenterFreq');
        if (hiddenCenter) hiddenCenter.value = center.toFixed(4);

        const centerDisplay = document.getElementById('wfFreqCenterDisplay');
        if (centerDisplay && document.activeElement !== centerDisplay) {
            centerDisplay.value = center.toFixed(4);
        }

        const spanEl = document.getElementById('wfSpanDisplay');
        if (spanEl) {
            spanEl.textContent = span >= 1
                ? `${span.toFixed(3)} MHz`
                : `${(span * 1000).toFixed(1)} kHz`;
        }

        const rangeEl = document.getElementById('wfRangeDisplay');
        if (rangeEl) {
            rangeEl.textContent = `${_startMhz.toFixed(4)} - ${_endMhz.toFixed(4)} MHz`;
        }

        const tuneEl = document.getElementById('wfTuneDisplay');
        if (tuneEl) {
            tuneEl.textContent = `Tune ${_monitorFreqMhz.toFixed(4)} MHz`;
        }

        const rxReadout = document.getElementById('wfRxFreqReadout');
        if (rxReadout) rxReadout.textContent = center.toFixed(4);

        const stepReadout = document.getElementById('wfRxStepReadout');
        if (stepReadout) stepReadout.textContent = _readStepLabel();

        const modeReadout = document.getElementById('wfRxModeReadout');
        if (modeReadout) modeReadout.textContent = _getMonitorMode().toUpperCase();

        _updateTuneLine();
    }

    function _drawBandAnnotations(width, height) {
        const span = _endMhz - _startMhz;
        if (span <= 0) return;

        _specCtx.save();
        _specCtx.font = '9px var(--font-mono, monospace)';
        _specCtx.textBaseline = 'top';
        _specCtx.textAlign = 'center';

        for (const [bStart, bEnd, bLabel, bColor] of RF_BANDS) {
            if (bEnd < _startMhz || bStart > _endMhz) continue;
            const x0 = Math.max(0, ((bStart - _startMhz) / span) * width);
            const x1 = Math.min(width, ((bEnd - _startMhz) / span) * width);
            const bw = x1 - x0;

            _specCtx.fillStyle = bColor;
            _specCtx.fillRect(x0, 0, bw, height);

            if (bw > 25) {
                _specCtx.fillStyle = 'rgba(255,255,255,0.75)';
                _specCtx.fillText(bLabel, x0 + bw / 2, 3);
            }
        }

        _specCtx.restore();
    }

    function _drawDbScale(width, height) {
        if (_autoRange) return;
        const range = _dbMax - _dbMin;
        if (range <= 0) return;

        _specCtx.save();
        _specCtx.font = '9px var(--font-mono, monospace)';
        _specCtx.textBaseline = 'middle';
        _specCtx.textAlign = 'left';

        for (let i = 0; i <= 5; i += 1) {
            const t = i / 5;
            const db = _dbMax - t * range;
            const y = t * height;
            _specCtx.strokeStyle = 'rgba(255,255,255,0.07)';
            _specCtx.lineWidth = 1;
            _specCtx.beginPath();
            _specCtx.moveTo(0, y);
            _specCtx.lineTo(width, y);
            _specCtx.stroke();
            _specCtx.fillStyle = 'rgba(255,255,255,0.48)';
            _specCtx.fillText(`${Math.round(db)} dB`, 3, Math.max(6, Math.min(height - 6, y)));
        }

        _specCtx.restore();
    }

    function _drawCenterLine(width, height) {
        _specCtx.save();
        _specCtx.strokeStyle = 'rgba(255,215,0,0.45)';
        _specCtx.lineWidth = 1;
        _specCtx.setLineDash([4, 4]);
        _specCtx.beginPath();
        _specCtx.moveTo(width / 2, 0);
        _specCtx.lineTo(width / 2, height);
        _specCtx.stroke();
        _specCtx.restore();
    }

    function _drawSpectrum(bins) {
        if (!_specCtx || !_specCanvas || !bins || bins.length === 0) return;

        const width = _specCanvas.width;
        const height = _specCanvas.height;
        _specCtx.clearRect(0, 0, width, height);
        _specCtx.fillStyle = '#000';
        _specCtx.fillRect(0, 0, width, height);

        if (_showAnnotations) _drawBandAnnotations(width, height);
        _drawDbScale(width, height);

        const n = bins.length;

        _specCtx.beginPath();
        _specCtx.moveTo(0, height);
        for (let i = 0; i < n; i += 1) {
            const x = (i / (n - 1)) * width;
            const y = height - (bins[i] / 255) * height;
            _specCtx.lineTo(x, y);
        }
        _specCtx.lineTo(width, height);
        _specCtx.closePath();
        _specCtx.fillStyle = 'rgba(74,163,255,0.16)';
        _specCtx.fill();

        _specCtx.beginPath();
        for (let i = 0; i < n; i += 1) {
            const x = (i / (n - 1)) * width;
            const y = height - (bins[i] / 255) * height;
            if (i === 0) _specCtx.moveTo(x, y);
            else _specCtx.lineTo(x, y);
        }
        _specCtx.strokeStyle = 'rgba(110,188,255,0.85)';
        _specCtx.lineWidth = 1;
        _specCtx.stroke();

        if (_peakHold) {
            if (!_peakLine || _peakLine.length !== n) _peakLine = new Uint8Array(n);
            for (let i = 0; i < n; i += 1) {
                if (bins[i] > _peakLine[i]) _peakLine[i] = bins[i];
            }

            _specCtx.beginPath();
            for (let i = 0; i < n; i += 1) {
                const x = (i / (n - 1)) * width;
                const y = height - (_peakLine[i] / 255) * height;
                if (i === 0) _specCtx.moveTo(x, y);
                else _specCtx.lineTo(x, y);
            }
            _specCtx.strokeStyle = 'rgba(255,98,98,0.75)';
            _specCtx.lineWidth = 1;
            _specCtx.stroke();
        }

        _drawCenterLine(width, height);
    }

    function _scrollWaterfall(bins) {
        if (!_wfCtx || !_wfCanvas || !bins || bins.length === 0) return;

        const width = _wfCanvas.width;
        const height = _wfCanvas.height;
        if (width === 0 || height === 0) return;

        // Shift existing image down by 1px using GPU copy (avoids expensive readback).
        _wfCtx.drawImage(_wfCanvas, 0, 0, width, height - 1, 0, 1, width, height - 1);

        const lut = PALETTES[_palette] || PALETTES.turbo;
        const row = _wfCtx.createImageData(width, 1);
        const data = row.data;
        const n = bins.length;
        for (let x = 0; x < width; x += 1) {
            const idx = Math.round((x / (width - 1)) * (n - 1));
            const val = bins[idx] / 255;
            const [r, g, b] = _colorize(val, lut);
            const off = x * 4;
            data[off] = r;
            data[off + 1] = g;
            data[off + 2] = b;
            data[off + 3] = 255;
        }
        _wfCtx.putImageData(row, 0, 0);
    }

    function _drawFreqAxis() {
        const axis = document.getElementById('wfFreqAxis');
        if (!axis) return;
        axis.innerHTML = '';
        const ticks = 8;
        for (let i = 0; i <= ticks; i += 1) {
            const frac = i / ticks;
            const freq = _startMhz + frac * (_endMhz - _startMhz);
            const tick = document.createElement('div');
            tick.className = 'wf-freq-tick';
            tick.style.left = `${frac * 100}%`;
            tick.textContent = freq.toFixed(2);
            axis.appendChild(tick);
        }
        _updateFreqDisplay();
    }

    function _resizeCanvases() {
        const sc = document.getElementById('wfSpectrumCanvas');
        const wc = document.getElementById('wfWaterfallCanvas');

        if (sc) {
            sc.width = sc.parentElement ? sc.parentElement.offsetWidth : 800;
            sc.height = sc.parentElement ? sc.parentElement.offsetHeight : 110;
        }

        if (wc) {
            wc.width = wc.parentElement ? wc.parentElement.offsetWidth : 800;
            wc.height = wc.parentElement ? wc.parentElement.offsetHeight : 450;
        }

        _drawFreqAxis();
    }

    function _freqAtX(canvas, clientX) {
        const rect = canvas.getBoundingClientRect();
        const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
        return _startMhz + frac * (_endMhz - _startMhz);
    }

    function _showTooltip(canvas, event) {
        const tooltip = document.getElementById('wfTooltip');
        if (!tooltip) return;

        const freq = _freqAtX(canvas, event.clientX);
        const wrap = document.querySelector('.wf-waterfall-canvas-wrap');
        if (wrap) {
            const rect = wrap.getBoundingClientRect();
            tooltip.style.left = `${event.clientX - rect.left}px`;
            tooltip.style.transform = 'translateX(-50%)';
            tooltip.style.top = '4px';
        }
        tooltip.textContent = `${freq.toFixed(4)} MHz`;
        tooltip.style.display = 'block';
    }

    function _hideTooltip() {
        const tooltip = document.getElementById('wfTooltip');
        if (tooltip) tooltip.style.display = 'none';
    }

    function _queueRetune(delayMs, action = 'start') {
        clearTimeout(_retuneTimer);
        _retuneTimer = setTimeout(() => {
            if ((_ws && _ws.readyState === WebSocket.OPEN) || _transport === 'sse') {
                if (action === 'tune' && _transport === 'ws') {
                    _sendWsTuneCmd();
                } else {
                    _sendStartCmd();
                }
            }
        }, delayMs);
    }

    function _queueMonitorRetune(delayMs) {
        if (!_monitoring) return;
        clearTimeout(_monitorRetuneTimer);
        _monitorRetuneTimer = setTimeout(() => {
            _startMonitorInternal({ wasRunningWaterfall: false, retuneOnly: true }).catch(() => {});
        }, delayMs);
    }

    function _isSharedMonitorActive() {
        return (
            _monitoring
            && _monitorSource === 'waterfall'
            && _transport === 'ws'
            && _running
            && _ws
            && _ws.readyState === WebSocket.OPEN
        );
    }

    function _queueMonitorAdjust(delayMs, { allowSharedTune = true } = {}) {
        if (!_monitoring) return;
        if (allowSharedTune && _isSharedMonitorActive()) {
            _queueRetune(delayMs, 'tune');
            return;
        }
        _queueMonitorRetune(delayMs);
    }

    function _setAndTune(freqMhz, immediate = false) {
        const clamped = _clamp(freqMhz, 0.001, 6000.0);

        const input = document.getElementById('wfCenterFreq');
        if (input) input.value = clamped.toFixed(4);

        _monitorFreqMhz = clamped;
        const currentSpan = _endMhz - _startMhz;
        const configuredSpan = _clamp(_currentSpan(), 0.05, 30.0);
        const activeSpan = Number.isFinite(currentSpan) && currentSpan > 0 ? currentSpan : configuredSpan;
        const edgeMargin = activeSpan * 0.08;
        const withinCapture = clamped >= (_startMhz + edgeMargin) && clamped <= (_endMhz - edgeMargin);
        const needsRetune = !withinCapture;

        if (needsRetune) {
            _startMhz = clamped - configuredSpan / 2;
            _endMhz = clamped + configuredSpan / 2;
            _drawFreqAxis();
        } else {
            _updateFreqDisplay();
        }

        const sharedMonitor = _isSharedMonitorActive();
        if (_monitoring) {
            if (!sharedMonitor) {
                _queueMonitorRetune(immediate ? 35 : 140);
            } else if (needsRetune) {
                // Capture restart can clear shared monitor state; re-arm on 'started'.
                _pendingSharedMonitorRearm = true;
            }
        }

        if (!((_ws && _ws.readyState === WebSocket.OPEN) || _transport === 'sse')) {
            return;
        }

        if (_transport === 'ws') {
            if (needsRetune) {
                if (immediate) _sendStartCmd();
                else _queueRetune(160, 'start');
            } else {
                if (immediate) _sendWsTuneCmd();
                else _queueRetune(70, 'tune');
            }
            return;
        }

        if (immediate) _sendStartCmd();
        else _queueRetune(220, 'start');
    }

    function _recenterAndRestart() {
        _startMhz = _currentCenter() - _currentSpan() / 2;
        _endMhz = _currentCenter() + _currentSpan() / 2;
        _drawFreqAxis();
        _sendStartCmd();
    }

    function _onRetuneRequired(msg) {
        if (!msg || msg.status !== 'retune_required') return false;
        _setStatus(msg.message || 'Retuning SDR capture...');
        if (Number.isFinite(msg.vfo_freq_mhz)) {
            const input = document.getElementById('wfCenterFreq');
            if (input) input.value = Number(msg.vfo_freq_mhz).toFixed(4);
        }
        _recenterAndRestart();
        return true;
    }

    function _handleCanvasWheel(event) {
        event.preventDefault();

        if (event.ctrlKey || event.metaKey) {
            const spanEl = document.getElementById('wfSpanMhz');
            const current = _currentSpan();
            const factor = event.deltaY < 0 ? 1 / 1.2 : 1.2;
            const next = _clamp(current * factor, 0.05, 30.0);
            if (spanEl) spanEl.value = next.toFixed(3);
            _startMhz = _currentCenter() - next / 2;
            _endMhz = _currentCenter() + next / 2;
            _drawFreqAxis();

            if (_monitoring) {
                _queueMonitorAdjust(260, { allowSharedTune: false });
            } else if (_running) {
                _queueRetune(260);
            }
            return;
        }

        const step = _getNumber('wfStepSize', 0.1);
        const dir = event.deltaY < 0 ? 1 : -1;
        const center = _currentCenter();
        _setAndTune(center + dir * step, true);
    }

    function _clickTune(canvas, event) {
        const target = _freqAtX(canvas, event.clientX);
        _setAndTune(target, true);
    }

    function _setupCanvasInteraction() {
        if (_listenersAttached) return;
        _listenersAttached = true;

        const bindCanvas = (canvas) => {
            if (!canvas) return;
            canvas.style.cursor = 'crosshair';
            canvas.addEventListener('mousemove', (e) => _showTooltip(canvas, e));
            canvas.addEventListener('mouseleave', _hideTooltip);
            canvas.addEventListener('click', (e) => _clickTune(canvas, e));
            canvas.addEventListener('wheel', _handleCanvasWheel, { passive: false });
        };

        bindCanvas(_wfCanvas);
        bindCanvas(_specCanvas);
    }

    function _setupResizeHandle() {
        const handle = document.getElementById('wfResizeHandle');
        if (!handle || handle.dataset.rdy) return;
        handle.dataset.rdy = '1';

        let startY = 0;
        let startH = 0;

        const onMove = (event) => {
            const delta = event.clientY - startY;
            const next = _clamp(startH + delta, 55, 300);
            const wrap = document.querySelector('.wf-spectrum-canvas-wrap');
            if (wrap) wrap.style.height = `${next}px`;
            _resizeCanvases();
            if (_wfCtx && _wfCanvas) _wfCtx.clearRect(0, 0, _wfCanvas.width, _wfCanvas.height);
        };

        const onUp = () => {
            handle.classList.remove('dragging');
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        };

        handle.addEventListener('mousedown', (event) => {
            const wrap = document.querySelector('.wf-spectrum-canvas-wrap');
            startY = event.clientY;
            startH = wrap ? wrap.offsetHeight : 108;
            handle.classList.add('dragging');
            document.body.style.userSelect = 'none';
            document.body.style.cursor = 'ns-resize';
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
            event.preventDefault();
        });
    }

    function _setupFrequencyBarInteraction() {
        const display = document.getElementById('wfFreqCenterDisplay');
        if (!display || display.dataset.rdy) return;
        display.dataset.rdy = '1';

        display.addEventListener('focus', () => display.select());

        display.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                const value = parseFloat(display.value);
                if (Number.isFinite(value) && value > 0) _setAndTune(value, true);
                display.blur();
            } else if (event.key === 'Escape') {
                _updateFreqDisplay();
                display.blur();
            } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
                event.preventDefault();
                const step = _getNumber('wfStepSize', 0.1);
                const dir = event.key === 'ArrowUp' ? 1 : -1;
                const cur = parseFloat(display.value) || _currentCenter();
                _setAndTune(cur + dir * step, true);
            }
        });

        display.addEventListener('blur', () => {
            const value = parseFloat(display.value);
            if (Number.isFinite(value) && value > 0) _setAndTune(value, true);
        });

        display.addEventListener('wheel', (event) => {
            event.preventDefault();
            const step = _getNumber('wfStepSize', 0.1);
            const dir = event.deltaY < 0 ? 1 : -1;
            _setAndTune(_currentCenter() + dir * step, true);
        }, { passive: false });
    }

    function _setupControlListeners() {
        if (_controlListenersAttached) return;
        _controlListenersAttached = true;

        const centerEl = document.getElementById('wfCenterFreq');
        if (centerEl) {
            centerEl.addEventListener('change', () => {
                const value = parseFloat(centerEl.value);
                if (Number.isFinite(value) && value > 0) _setAndTune(value, true);
            });
        }

        const spanEl = document.getElementById('wfSpanMhz');
        if (spanEl) {
            spanEl.addEventListener('change', () => {
                const span = _clamp(_currentSpan(), 0.05, 30.0);
                spanEl.value = span.toFixed(3);
                _startMhz = _currentCenter() - span / 2;
                _endMhz = _currentCenter() + span / 2;
                _drawFreqAxis();

                if (_monitoring) _queueMonitorAdjust(250, { allowSharedTune: false });
                if (_running) _queueRetune(250);
            });
        }

        const stepEl = document.getElementById('wfStepSize');
        if (stepEl) {
            stepEl.addEventListener('change', () => _updateFreqDisplay());
        }

        ['wfFftSize', 'wfFps', 'wfAvgCount', 'wfGain', 'wfPpm', 'wfBiasT', 'wfDbMin', 'wfDbMax'].forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;
            const evt = el.tagName === 'INPUT' && el.type === 'text' ? 'blur' : 'change';
            el.addEventListener(evt, () => {
                if (_monitoring && (id === 'wfGain' || id === 'wfBiasT')) {
                    _queueMonitorAdjust(280, { allowSharedTune: false });
                }
                if (_running) _queueRetune(180);
            });
        });

        const monitorMode = document.getElementById('wfMonitorMode');
        if (monitorMode) {
            monitorMode.addEventListener('change', () => {
                _setMonitorMode(monitorMode.value);
                if (_monitoring) _queueMonitorAdjust(140);
            });
        }

        document.querySelectorAll('.wf-mode-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                const mode = btn.dataset.mode || 'wfm';
                _setMonitorMode(mode);
                if (_monitoring) _queueMonitorAdjust(140);
                _updateFreqDisplay();
            });
        });

        const sq = document.getElementById('wfMonitorSquelch');
        const sqValue = document.getElementById('wfMonitorSquelchValue');
        if (sq) {
            sq.addEventListener('input', () => {
                if (sqValue) sqValue.textContent = String(parseInt(sq.value, 10) || 0);
            });
            sq.addEventListener('change', () => {
                if (_monitoring) _queueMonitorAdjust(180);
            });
        }

        const gain = document.getElementById('wfMonitorGain');
        const gainValue = document.getElementById('wfMonitorGainValue');
        if (gain) {
            gain.addEventListener('input', () => {
                const g = parseInt(gain.value, 10) || 0;
                if (gainValue) gainValue.textContent = String(g);
            });
            gain.addEventListener('change', () => {
                if (_monitoring) _queueMonitorAdjust(180, { allowSharedTune: false });
            });
        }

        const vol = document.getElementById('wfMonitorVolume');
        const volValue = document.getElementById('wfMonitorVolumeValue');
        if (vol) {
            vol.addEventListener('input', () => {
                const v = parseInt(vol.value, 10) || 0;
                if (volValue) volValue.textContent = String(v);
                const player = document.getElementById('wfAudioPlayer');
                if (player) player.volume = v / 100;
            });
        }

        window.addEventListener('resize', _resizeCanvases);
    }

    function _selectedDevice() {
        const raw = document.getElementById('wfDevice')?.value || 'rtlsdr:0';
        const parts = raw.includes(':') ? raw.split(':') : ['rtlsdr', '0'];
        return {
            sdrType: parts[0] || 'rtlsdr',
            deviceIndex: parseInt(parts[1], 10) || 0,
        };
    }

    function _waterfallRequestConfig() {
        const centerMhz = _currentCenter();
        const spanMhz = _clamp(_currentSpan(), 0.05, 30.0);
        _startMhz = centerMhz - spanMhz / 2;
        _endMhz = centerMhz + spanMhz / 2;
        _monitorFreqMhz = centerMhz;
        _peakLine = null;
        _drawFreqAxis();

        const gainRaw = String(document.getElementById('wfGain')?.value || 'AUTO').trim();
        const gain = gainRaw.toUpperCase() === 'AUTO' ? 'auto' : parseFloat(gainRaw);
        const device = _selectedDevice();
        const fftSize = parseInt(document.getElementById('wfFftSize')?.value, 10) || 1024;
        const fps = parseInt(document.getElementById('wfFps')?.value, 10) || 20;
        const avgCount = parseInt(document.getElementById('wfAvgCount')?.value, 10) || 4;
        const ppm = parseInt(document.getElementById('wfPpm')?.value, 10) || 0;
        const biasT = !!document.getElementById('wfBiasT')?.checked;

        return {
            centerMhz,
            spanMhz,
            gain,
            device,
            fftSize,
            fps,
            avgCount,
            ppm,
            biasT,
        };
    }

    function _sendWsStartCmd() {
        if (!_ws || _ws.readyState !== WebSocket.OPEN) return;
        const cfg = _waterfallRequestConfig();

        const payload = {
            cmd: 'start',
            center_freq_mhz: cfg.centerMhz,
            center_freq: cfg.centerMhz,
            span_mhz: cfg.spanMhz,
            gain: cfg.gain,
            sdr_type: cfg.device.sdrType,
            device: cfg.device.deviceIndex,
            fft_size: cfg.fftSize,
            fps: cfg.fps,
            avg_count: cfg.avgCount,
            ppm: cfg.ppm,
            bias_t: cfg.biasT,
        };

        if (!_autoRange) {
            _dbMin = parseFloat(document.getElementById('wfDbMin')?.value) || -100;
            _dbMax = parseFloat(document.getElementById('wfDbMax')?.value) || -20;
            payload.db_min = _dbMin;
            payload.db_max = _dbMax;
        }

        try {
            _ws.send(JSON.stringify(payload));
            _setStatus(`Tuning ${cfg.centerMhz.toFixed(4)} MHz...`);
            _setVisualStatus('TUNING');
        } catch (err) {
            _setStatus(`Failed to send tune command: ${err}`);
            _setVisualStatus('ERROR');
        }
    }

    function _sendWsTuneCmd() {
        if (!_ws || _ws.readyState !== WebSocket.OPEN) return;

        const squelch = parseInt(document.getElementById('wfMonitorSquelch')?.value, 10) || 0;
        const mode = _getMonitorMode();
        const payload = {
            cmd: 'tune',
            vfo_freq_mhz: _monitorFreqMhz,
            modulation: mode,
            squelch,
        };

        try {
            _ws.send(JSON.stringify(payload));
            _setStatus(`Tuned ${_monitorFreqMhz.toFixed(4)} MHz`);
            if (!_monitoring) _setVisualStatus('RUNNING');
        } catch (err) {
            _setStatus(`Tune command failed: ${err}`);
            _setVisualStatus('ERROR');
        }
    }

    async function _sendSseStartCmd({ forceRestart = false } = {}) {
        const cfg = _waterfallRequestConfig();
        const spanHz = Math.max(1000, Math.round(cfg.spanMhz * 1e6));
        const targetBins = _clamp(cfg.fftSize, 128, 4096);
        const binSize = Math.max(1000, Math.round(spanHz / targetBins));
        const interval = _clamp(1 / Math.max(1, cfg.fps), 0.1, 2.0);
        const gain = Number.isFinite(cfg.gain) ? cfg.gain : 40;

        const payload = {
            start_freq: _startMhz,
            end_freq: _endMhz,
            bin_size: binSize,
            gain: Math.round(gain),
            device: cfg.device.deviceIndex,
            interval,
            max_bins: targetBins,
        };
        const payloadKey = _ssePayloadKey(payload);

        const startOnce = async () => {
            const response = await fetch('/listening/waterfall/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            let body = {};
            try {
                body = await response.json();
            } catch (_) {
                body = {};
            }
            return { response, body };
        };

        if (_sseStartPromise) {
            await _sseStartPromise.catch(() => {});
            if (!_active) return;
            if (!forceRestart && _running && _sseStartConfigKey === payloadKey) return;
        }

        const runStart = (async () => {
            const shouldRestart = forceRestart || (_running && _sseStartConfigKey && _sseStartConfigKey !== payloadKey);
            if (shouldRestart) {
                await fetch('/listening/waterfall/stop', { method: 'POST' }).catch(() => {});
                _running = false;
                _updateRunButtons();
                await _wait(140);
            }

            let { response, body } = await startOnce();

            if (_isWaterfallDeviceBusy(response, body)) {
                throw new Error(body.message || 'SDR device is busy');
            }

            // If we attached to an existing backend worker after a page refresh,
            // restart once so requested center/span is definitely applied.
            if (_isWaterfallAlreadyRunningConflict(response, body) && !_sseStartConfigKey) {
                await fetch('/listening/waterfall/stop', { method: 'POST' }).catch(() => {});
                await _wait(140);
                ({ response, body } = await startOnce());
                if (_isWaterfallDeviceBusy(response, body)) {
                    throw new Error(body.message || 'SDR device is busy');
                }
            }

            if (_isWaterfallAlreadyRunningConflict(response, body)) {
                body = { status: 'started', message: body.message || 'Waterfall already running' };
            } else if (!response.ok || (body.status && body.status !== 'started')) {
                throw new Error(body.message || `Waterfall start failed (${response.status})`);
            }

            _sseStartConfigKey = payloadKey;
            _running = true;
            _updateRunButtons();
            _setStatus(`Streaming ${_startMhz.toFixed(4)} - ${_endMhz.toFixed(4)} MHz`);
            _setVisualStatus('RUNNING');
        })();
        _sseStartPromise = runStart;

        try {
            await runStart;
        } finally {
            if (_sseStartPromise === runStart) {
                _sseStartPromise = null;
            }
        }
    }

    function _sendStartCmd() {
        if (_transport === 'sse') {
            _sendSseStartCmd().catch((err) => {
                _setStatus(`Waterfall start failed: ${err}`);
                _setVisualStatus('ERROR');
            });
            return;
        }
        _sendWsStartCmd();
    }

    function _handleSseMessage(msg) {
        if (!msg || typeof msg !== 'object') return;
        if (msg.type === 'keepalive') return;
        if (msg.type === 'waterfall_error') {
            const text = msg.message || 'Waterfall source error';
            _setStatus(text);
            if (!_monitoring) _setVisualStatus('ERROR');
            return;
        }
        if (msg.type !== 'waterfall_sweep') return;

        const startFreq = Number(msg.start_freq);
        const endFreq = Number(msg.end_freq);
        if (Number.isFinite(startFreq) && Number.isFinite(endFreq) && endFreq > startFreq) {
            _startMhz = startFreq;
            _endMhz = endFreq;
            _drawFreqAxis();
        }

        const bins = _normalizeSweepBins(msg.bins);
        if (!bins || bins.length === 0) return;
        _drawSpectrum(bins);
        _scrollWaterfall(bins);
    }

    function _openSseStream() {
        if (_es) return;
        const source = new EventSource(`/listening/waterfall/stream?t=${Date.now()}`);
        _es = source;
        source.onmessage = (event) => {
            let msg = null;
            try {
                msg = JSON.parse(event.data);
            } catch (_) {
                return;
            }
            _running = true;
            _updateRunButtons();
            if (!_monitoring) _setVisualStatus('RUNNING');
            _handleSseMessage(msg);
        };
        source.onerror = () => {
            if (!_active) return;
            _setStatus('Waterfall SSE stream interrupted; retrying...');
            if (!_monitoring) _setVisualStatus('DISCONNECTED');
        };
    }

    async function _activateSseFallback(reason = '') {
        _clearWsFallbackTimer();

        if (_ws) {
            try {
                _ws.close();
            } catch (_) {
                // Ignore close errors during fallback.
            }
            _ws = null;
        }

        _transport = 'sse';
        _openSseStream();
        if (reason) _setStatus(reason);
        await _sendSseStartCmd();
    }

    async function _handleBinary(data) {
        let buf = null;
        if (data instanceof ArrayBuffer) {
            buf = data;
        } else if (data && typeof data.arrayBuffer === 'function') {
            buf = await data.arrayBuffer();
        }

        if (!buf) return;
        const frame = _parseFrame(buf);
        if (!frame) return;

        if (frame.startMhz > 0 && frame.endMhz > frame.startMhz) {
            _startMhz = frame.startMhz;
            _endMhz = frame.endMhz;
            _drawFreqAxis();
        }

        _drawSpectrum(frame.bins);
        _scrollWaterfall(frame.bins);
    }

    function _onMessage(event) {
        if (typeof event.data === 'string') {
            try {
                const msg = JSON.parse(event.data);
                if (msg.status === 'started') {
                    _running = true;
                    _updateRunButtons();
                    if (Number.isFinite(msg.vfo_freq_mhz)) {
                        _monitorFreqMhz = Number(msg.vfo_freq_mhz);
                    }
                    if (Number.isFinite(msg.start_freq) && Number.isFinite(msg.end_freq)) {
                        _startMhz = msg.start_freq;
                        _endMhz = msg.end_freq;
                        _drawFreqAxis();
                    }
                    _setStatus(`Streaming ${_startMhz.toFixed(4)} - ${_endMhz.toFixed(4)} MHz`);
                    _setVisualStatus('RUNNING');
                    if (_pendingSharedMonitorRearm && _monitoring && _monitorSource === 'waterfall') {
                        _pendingSharedMonitorRearm = false;
                        _queueMonitorRetune(120);
                    }
                } else if (msg.status === 'tuned') {
                    if (_onRetuneRequired(msg)) return;
                    if (Number.isFinite(msg.vfo_freq_mhz)) {
                        _monitorFreqMhz = Number(msg.vfo_freq_mhz);
                    }
                    _updateFreqDisplay();
                    _setStatus(`Tuned ${_monitorFreqMhz.toFixed(4)} MHz`);
                    if (!_monitoring) _setVisualStatus('RUNNING');
                } else if (_onRetuneRequired(msg)) {
                    return;
                } else if (msg.status === 'stopped') {
                    _running = false;
                    _updateRunButtons();
                    _setStatus('Waterfall stopped');
                    _setVisualStatus('STOPPED');
                } else if (msg.status === 'error') {
                    _running = false;
                    _updateRunButtons();
                    _setStatus(msg.message || 'Waterfall error');
                    _setVisualStatus('ERROR');
                } else if (msg.status) {
                    _setStatus(msg.status);
                }
            } catch (_) {
                // Ignore malformed status payloads
            }
            return;
        }

        _handleBinary(event.data).catch(() => {});
    }

    async function _pauseMonitorAudioElement() {
        const player = document.getElementById('wfAudioPlayer');
        if (!player) return;
        try {
            player.pause();
        } catch (_) {
            // Ignore pause errors
        }
        player.removeAttribute('src');
        player.load();
    }

    async function _attachMonitorAudio(nonce) {
        const player = document.getElementById('wfAudioPlayer');
        if (!player) {
            return { ok: false, reason: 'player_missing', message: 'Audio player is unavailable.' };
        }

        player.autoplay = true;
        player.preload = 'auto';
        player.muted = _monitorMuted;
        const vol = parseInt(document.getElementById('wfMonitorVolume')?.value, 10) || 82;
        player.volume = vol / 100;

        const maxAttempts = 4;
        for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
            if (nonce !== _audioConnectNonce) {
                return { ok: false, reason: 'stale' };
            }

            await _pauseMonitorAudioElement();
            player.src = `/listening/audio/stream?fresh=1&t=${Date.now()}-${attempt}`;
            player.load();

            try {
                const playPromise = player.play();
                if (playPromise && typeof playPromise.then === 'function') {
                    await playPromise;
                }
            } catch (err) {
                if (_isAutoplayError(err)) {
                    _audioUnlockRequired = true;
                    _setUnlockVisible(true);
                    return {
                        ok: false,
                        reason: 'autoplay_blocked',
                        message: 'Browser blocked audio playback. Click Unlock Audio.',
                    };
                }

                if (attempt < maxAttempts) {
                    await _wait(180 * attempt);
                    continue;
                }

                return {
                    ok: false,
                    reason: 'play_failed',
                    message: `Audio playback failed: ${err && err.message ? err.message : 'unknown error'}`,
                };
            }

            const active = await _waitForPlayback(player, 3500);
            if (nonce !== _audioConnectNonce) {
                return { ok: false, reason: 'stale' };
            }

            if (active) {
                _audioUnlockRequired = false;
                _setUnlockVisible(false);
                return { ok: true, player };
            }

            if (attempt < maxAttempts) {
                await _wait(220 * attempt);
                continue;
            }
        }

        return {
            ok: false,
            reason: 'stream_timeout',
            message: 'No audio data reached the browser stream.',
        };
    }

    function _deviceKey(device) {
        if (!device) return '';
        return `${device.sdrType || ''}:${device.deviceIndex || 0}`;
    }

    function _findAlternateDevice(currentDevice) {
        const currentKey = _deviceKey(currentDevice);
        for (const d of _devices) {
            const candidate = {
                sdrType: String(d.sdr_type || 'rtlsdr'),
                deviceIndex: parseInt(d.index, 10) || 0,
            };
            if (_deviceKey(candidate) !== currentKey) {
                return candidate;
            }
        }
        return null;
    }

    async function _requestAudioStart({
        frequency,
        modulation,
        squelch,
        gain,
        device,
        biasT,
    }) {
        const response = await fetch('/listening/audio/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                frequency,
                modulation,
                squelch,
                gain,
                device: device.deviceIndex,
                sdr_type: device.sdrType,
                bias_t: biasT,
            }),
        });

        let payload = {};
        try {
            payload = await response.json();
        } catch (_) {
            payload = {};
        }
        return { response, payload };
    }

    function _syncMonitorButtons() {
        const monitorBtn = document.getElementById('wfMonitorBtn');
        const muteBtn = document.getElementById('wfMuteBtn');

        if (monitorBtn) {
            monitorBtn.textContent = _monitoring ? 'Stop Monitor' : 'Monitor';
            monitorBtn.classList.toggle('is-active', _monitoring);
            monitorBtn.disabled = _startingMonitor;
        }

        if (muteBtn) {
            muteBtn.textContent = _monitorMuted ? 'Unmute' : 'Mute';
            muteBtn.disabled = !_monitoring;
        }
    }

    async function _startMonitorInternal({ wasRunningWaterfall = false, retuneOnly = false } = {}) {
        if (_startingMonitor) return;
        _startingMonitor = true;
        _syncMonitorButtons();
        const nonce = ++_audioConnectNonce;

        try {
            if (!retuneOnly) {
                _resumeWaterfallAfterMonitor = !!wasRunningWaterfall;
            }

            const centerMhz = _currentCenter();
            const mode = document.getElementById('wfMonitorMode')?.value || 'wfm';
            const squelch = parseInt(document.getElementById('wfMonitorSquelch')?.value, 10) || 0;
            const sliderGain = parseInt(document.getElementById('wfMonitorGain')?.value, 10);
            const fallbackGain = parseFloat(String(document.getElementById('wfGain')?.value || '40'));
            const gain = Number.isFinite(sliderGain)
                ? sliderGain
                : (Number.isFinite(fallbackGain) ? Math.round(fallbackGain) : 40);
            const selectedDevice = _selectedDevice();
            const altDevice = _running ? _findAlternateDevice(selectedDevice) : null;
            let monitorDevice = altDevice || selectedDevice;
            const biasT = !!document.getElementById('wfBiasT')?.checked;
            const usingSecondaryDevice = !!altDevice;

            _monitorFreqMhz = centerMhz;
            _drawFreqAxis();
            _stopSmeter();
            _setUnlockVisible(false);
            _audioUnlockRequired = false;

            if (usingSecondaryDevice) {
                _setMonitorState(
                    `Starting ${centerMhz.toFixed(4)} MHz ${mode.toUpperCase()} on `
                    + `${monitorDevice.sdrType.toUpperCase()} #${monitorDevice.deviceIndex}...`
                );
            } else {
                _setMonitorState(`Starting ${centerMhz.toFixed(4)} MHz ${mode.toUpperCase()}...`);
            }

            let { response, payload } = await _requestAudioStart({
                frequency: centerMhz,
                modulation: mode,
                squelch,
                gain,
                device: monitorDevice,
                biasT,
            });
            if (nonce !== _audioConnectNonce) return;

            const busy = payload?.error_type === 'DEVICE_BUSY' || response.status === 409;
            if (
                busy
                && _running
                && !usingSecondaryDevice
                && !retuneOnly
            ) {
                _setMonitorState('Audio device busy, pausing waterfall and retrying monitor...');
                await stop({ keepStatus: true });
                _resumeWaterfallAfterMonitor = true;
                await _wait(220);
                monitorDevice = selectedDevice;
                ({ response, payload } = await _requestAudioStart({
                    frequency: centerMhz,
                    modulation: mode,
                    squelch,
                    gain,
                    device: monitorDevice,
                    biasT,
                }));
                if (nonce !== _audioConnectNonce) return;
            }

            if (!response.ok || payload.status !== 'started') {
                const msg = payload.message || `Monitor start failed (${response.status})`;
                _monitoring = false;
                _monitorSource = 'process';
                _pendingSharedMonitorRearm = false;
                _stopSmeter();
                _setMonitorState(msg);
                _setStatus(msg);
                _setVisualStatus('ERROR');
                _syncMonitorButtons();
                if (!retuneOnly && _resumeWaterfallAfterMonitor && _active) {
                    await start();
                }
                return;
            }

            const attach = await _attachMonitorAudio(nonce);
            if (nonce !== _audioConnectNonce) return;
            _monitorSource = payload?.source === 'waterfall' ? 'waterfall' : 'process';

            if (!attach.ok) {
                if (attach.reason === 'autoplay_blocked') {
                    _monitoring = true;
                    _syncMonitorButtons();
                    _setMonitorState(`Monitoring ${centerMhz.toFixed(4)} MHz ${mode.toUpperCase()} (audio locked)`);
                    _setStatus('Monitor started but browser blocked playback. Click Unlock Audio.');
                    _setVisualStatus('MONITOR');
                    return;
                }

                _monitoring = false;
                _monitorSource = 'process';
                _pendingSharedMonitorRearm = false;
                _stopSmeter();
                _setUnlockVisible(false);
                _setMonitorState(attach.message || 'Audio stream failed to start.');
                _setStatus(attach.message || 'Audio stream failed to start.');
                _setVisualStatus('ERROR');
                _syncMonitorButtons();
                try {
                    await fetch('/listening/audio/stop', { method: 'POST' });
                } catch (_) {
                    // Ignore cleanup stop failures
                }
                if (!retuneOnly && _resumeWaterfallAfterMonitor && _active) {
                    await start();
                }
                return;
            }

            _monitoring = true;
            _syncMonitorButtons();
            _startSmeter(attach.player);
            if (_monitorSource === 'waterfall') {
                _setMonitorState(
                    `Monitoring ${centerMhz.toFixed(4)} MHz ${mode.toUpperCase()} via shared IQ`
                );
            } else if (usingSecondaryDevice) {
                _setMonitorState(
                    `Monitoring ${centerMhz.toFixed(4)} MHz ${mode.toUpperCase()} `
                    + `via ${monitorDevice.sdrType.toUpperCase()} #${monitorDevice.deviceIndex}`
                );
            } else {
                _setMonitorState(`Monitoring ${centerMhz.toFixed(4)} MHz ${mode.toUpperCase()}`);
            }
            _setStatus(`Audio monitor active on ${centerMhz.toFixed(4)} MHz (${mode.toUpperCase()})`);
            _setVisualStatus('MONITOR');
        } catch (err) {
            if (nonce !== _audioConnectNonce) return;
            _monitoring = false;
            _monitorSource = 'process';
            _pendingSharedMonitorRearm = false;
            _stopSmeter();
            _setUnlockVisible(false);
            _syncMonitorButtons();
            _setMonitorState(`Monitor error: ${err}`);
            _setStatus(`Monitor error: ${err}`);
            _setVisualStatus('ERROR');
            if (!retuneOnly && _resumeWaterfallAfterMonitor && _active) {
                await start();
            }
        } finally {
            _startingMonitor = false;
            _syncMonitorButtons();
        }
    }

    async function stopMonitor({ resumeWaterfall = false } = {}) {
        clearTimeout(_monitorRetuneTimer);
        _audioConnectNonce += 1;

        try {
            await fetch('/listening/audio/stop', { method: 'POST' });
        } catch (_) {
            // Ignore backend stop errors
        }

        _stopSmeter();
        _setUnlockVisible(false);
        _audioUnlockRequired = false;
        await _pauseMonitorAudioElement();

        _monitoring = false;
        _monitorSource = 'process';
        _pendingSharedMonitorRearm = false;
        _syncMonitorButtons();
        _setMonitorState('No audio monitor');

        if (_running) {
            _setVisualStatus('RUNNING');
        } else {
            _setVisualStatus('READY');
        }

        if (resumeWaterfall && _active) {
            _resumeWaterfallAfterMonitor = false;
            await start();
        }
    }

    function _syncMonitorModeWithPreset(mode) {
        _setMonitorMode(mode);
    }

    function applyPreset(name) {
        const preset = PRESETS[name];
        if (!preset) return;

        const centerEl = document.getElementById('wfCenterFreq');
        const spanEl = document.getElementById('wfSpanMhz');
        const stepEl = document.getElementById('wfStepSize');

        if (centerEl) centerEl.value = preset.center.toFixed(4);
        if (spanEl) spanEl.value = preset.span.toFixed(3);
        if (stepEl) stepEl.value = String(preset.step);

        _syncMonitorModeWithPreset(preset.mode);
        _setAndTune(preset.center, true);
        _setStatus(`Preset applied: ${name.toUpperCase()}`);
    }

    async function toggleMonitor() {
        if (_monitoring) {
            await stopMonitor({ resumeWaterfall: _resumeWaterfallAfterMonitor });
            return;
        }

        await _startMonitorInternal({ wasRunningWaterfall: _running, retuneOnly: false });
    }

    function toggleMute() {
        _monitorMuted = !_monitorMuted;
        const player = document.getElementById('wfAudioPlayer');
        if (player) player.muted = _monitorMuted;
        _syncMonitorButtons();
    }

    async function unlockAudio() {
        if (!_monitoring || !_audioUnlockRequired) return;
        const player = document.getElementById('wfAudioPlayer');
        if (!player) return;

        try {
            if (_audioContext && _audioContext.state === 'suspended') {
                await _audioContext.resume();
            }
        } catch (_) {
            // Ignore context resume errors.
        }

        try {
            const playPromise = player.play();
            if (playPromise && typeof playPromise.then === 'function') {
                await playPromise;
            }
            _audioUnlockRequired = false;
            _setUnlockVisible(false);
            _startSmeter(player);
            _setMonitorState(`Monitoring ${_monitorFreqMhz.toFixed(4)} MHz ${_getMonitorMode().toUpperCase()}`);
            _setStatus('Audio monitor unlocked');
        } catch (_) {
            _audioUnlockRequired = true;
            _setUnlockVisible(true);
            _setMonitorState('Audio is still blocked by browser policy. Click Unlock Audio again.');
        }
    }

    async function start() {
        if (_monitoring) {
            await stopMonitor({ resumeWaterfall: false });
        }

        if (_ws && _ws.readyState === WebSocket.OPEN) {
            _sendStartCmd();
            return;
        }

        if (_ws && _ws.readyState === WebSocket.CONNECTING) return;

        _specCanvas = document.getElementById('wfSpectrumCanvas');
        _wfCanvas = document.getElementById('wfWaterfallCanvas');
        _specCtx = _ctx2d(_specCanvas);
        _wfCtx = _ctx2d(_wfCanvas, { willReadFrequently: false });

        _resizeCanvases();
        _setupCanvasInteraction();

        const center = _currentCenter();
        const span = _currentSpan();
        _startMhz = center - span / 2;
        _endMhz = center + span / 2;
        _monitorFreqMhz = center;
        _drawFreqAxis();

        if (typeof WebSocket === 'undefined') {
            await _activateSseFallback('WebSocket unavailable. Using fallback waterfall stream.');
            return;
        }

        _transport = 'ws';
        _wsOpened = false;
        _clearWsFallbackTimer();
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        let ws = null;
        try {
            ws = new WebSocket(`${proto}//${location.host}/ws/waterfall`);
        } catch (_) {
            await _activateSseFallback('WebSocket initialization failed. Using fallback waterfall stream.');
            return;
        }
        _ws = ws;
        _ws.binaryType = 'arraybuffer';
        _wsFallbackTimer = setTimeout(() => {
            if (!_wsOpened && _active && _transport === 'ws') {
                _activateSseFallback('WebSocket endpoint unavailable. Using fallback waterfall stream.').catch((err) => {
                    _setStatus(`Waterfall fallback failed: ${err}`);
                    _setVisualStatus('ERROR');
                });
            }
        }, WS_OPEN_FALLBACK_MS);

        _ws.onopen = () => {
            _wsOpened = true;
            _clearWsFallbackTimer();
            _sendStartCmd();
            _setStatus('Connected to waterfall stream');
        };

        _ws.onmessage = _onMessage;

        _ws.onerror = () => {
            if (!_wsOpened && _active) {
                // Let the open-timeout fallback decide; transient errors can recover.
                _setStatus('WebSocket handshake hiccup. Retrying...');
                return;
            }
            _setStatus('Waterfall connection error');
            if (!_monitoring) _setVisualStatus('ERROR');
        };

        _ws.onclose = () => {
            if (!_wsOpened && _active) {
                // Wait for timeout-based fallback; avoid flapping to SSE on brief close/retry.
                _setStatus('WebSocket closed before ready. Waiting to retry/fallback...');
                return;
            }
            _clearWsFallbackTimer();
            _running = false;
            _updateRunButtons();
            if (_active) {
                _setStatus('Waterfall disconnected');
                if (!_monitoring) {
                    _setVisualStatus('DISCONNECTED');
                }
            }
        };
    }

    async function stop({ keepStatus = false } = {}) {
        clearTimeout(_retuneTimer);
        _clearWsFallbackTimer();
        _wsOpened = false;
        _pendingSharedMonitorRearm = false;

        if (_ws) {
            try {
                _ws.send(JSON.stringify({ cmd: 'stop' }));
            } catch (_) {
                // Ignore command send failures during shutdown.
            }
            try {
                _ws.close();
            } catch (_) {
                // Ignore close errors.
            }
            _ws = null;
        }

        if (_es) {
            _closeSseStream();
            try {
                await fetch('/listening/waterfall/stop', { method: 'POST' });
            } catch (_) {
                // Ignore fallback stop errors.
            }
        }

        _sseStartConfigKey = '';
        _running = false;
        _updateRunButtons();
        if (!keepStatus) {
            _setStatus('Waterfall stopped');
            if (!_monitoring) _setVisualStatus('STOPPED');
        }
    }

    function setPalette(name) {
        _palette = name;
    }

    function togglePeakHold(value) {
        _peakHold = !!value;
        if (!_peakHold) _peakLine = null;
    }

    function toggleAnnotations(value) {
        _showAnnotations = !!value;
    }

    function toggleAutoRange(value) {
        _autoRange = !!value;
        const dbMinEl = document.getElementById('wfDbMin');
        const dbMaxEl = document.getElementById('wfDbMax');
        if (dbMinEl) dbMinEl.disabled = _autoRange;
        if (dbMaxEl) dbMaxEl.disabled = _autoRange;

        if (_running) {
            _queueRetune(50);
        }
    }

    function stepFreq(multiplier) {
        const step = _getNumber('wfStepSize', 0.1);
        _setAndTune(_currentCenter() + multiplier * step, true);
    }

    function _renderDeviceOptions(devices) {
        const sel = document.getElementById('wfDevice');
        if (!sel) return;

        if (!Array.isArray(devices) || devices.length === 0) {
            sel.innerHTML = '<option value="">No SDR devices detected</option>';
            return;
        }

        const previous = sel.value;
        sel.innerHTML = devices.map((d) => {
            const label = d.serial ? `${d.name} [${d.serial}]` : d.name;
            return `<option value="${d.sdr_type}:${d.index}">${label}</option>`;
        }).join('');

        if (previous && [...sel.options].some((opt) => opt.value === previous)) {
            sel.value = previous;
        }

        _updateDeviceInfo();
    }

    function _formatSampleRate(samples) {
        if (!Array.isArray(samples) || samples.length === 0) return '--';
        const max = Math.max(...samples.map((v) => parseInt(v, 10)).filter((v) => Number.isFinite(v)));
        if (!Number.isFinite(max) || max <= 0) return '--';
        return max >= 1e6 ? `${(max / 1e6).toFixed(2)} Msps` : `${Math.round(max / 1000)} ksps`;
    }

    function _updateDeviceInfo() {
        const sel = document.getElementById('wfDevice');
        const panel = document.getElementById('wfDeviceInfo');
        if (!sel || !panel) return;

        const value = sel.value;
        if (!value) {
            panel.style.display = 'none';
            return;
        }

        const [sdrType, idx] = value.split(':');
        const device = _devices.find((d) => d.sdr_type === sdrType && String(d.index) === idx);
        if (!device) {
            panel.style.display = 'none';
            return;
        }

        const caps = device.capabilities || {};
        const typeEl = document.getElementById('wfDeviceType');
        const rangeEl = document.getElementById('wfDeviceRange');
        const bwEl = document.getElementById('wfDeviceBw');

        if (typeEl) typeEl.textContent = String(device.sdr_type || '--').toUpperCase();
        if (rangeEl) {
            rangeEl.textContent = Number.isFinite(caps.freq_min_mhz) && Number.isFinite(caps.freq_max_mhz)
                ? `${caps.freq_min_mhz}-${caps.freq_max_mhz} MHz`
                : '--';
        }
        if (bwEl) bwEl.textContent = _formatSampleRate(caps.sample_rates);

        panel.style.display = 'block';
    }

    function onDeviceChange() {
        _updateDeviceInfo();
        if (_monitoring) _queueMonitorRetune(120);
        if (_running) _queueRetune(120);
    }

    function _loadDevices() {
        fetch('/devices')
            .then((r) => r.json())
            .then((devices) => {
                _devices = Array.isArray(devices) ? devices : [];
                _renderDeviceOptions(_devices);
            })
            .catch(() => {
                const sel = document.getElementById('wfDevice');
                if (sel) sel.innerHTML = '<option value="">Could not load devices</option>';
            });
    }

    function init() {
        if (_active) {
            if (!_running && !_sseStartPromise) {
                _setVisualStatus('CONNECTING');
                _setStatus('Connecting waterfall stream...');
                Promise.resolve(start()).catch((err) => {
                    _setStatus(`Waterfall start failed: ${err}`);
                    _setVisualStatus('ERROR');
                });
            }
            return;
        }
        _active = true;
        _buildPalettes();
        _peakLine = null;

        _specCanvas = document.getElementById('wfSpectrumCanvas');
        _wfCanvas = document.getElementById('wfWaterfallCanvas');
        _specCtx = _ctx2d(_specCanvas);
        _wfCtx = _ctx2d(_wfCanvas, { willReadFrequently: false });

        _setupCanvasInteraction();
        _setupResizeHandle();
        _setupFrequencyBarInteraction();
        _setupControlListeners();

        _loadDevices();

        const center = _currentCenter();
        const span = _currentSpan();
        _monitorFreqMhz = center;
        _startMhz = center - span / 2;
        _endMhz = center + span / 2;

        const vol = document.getElementById('wfMonitorVolume');
        const volValue = document.getElementById('wfMonitorVolumeValue');
        if (vol && volValue) volValue.textContent = String(parseInt(vol.value, 10) || 0);

        const sq = document.getElementById('wfMonitorSquelch');
        const sqValue = document.getElementById('wfMonitorSquelchValue');
        if (sq && sqValue) sqValue.textContent = String(parseInt(sq.value, 10) || 0);

        const gain = document.getElementById('wfMonitorGain');
        const gainValue = document.getElementById('wfMonitorGainValue');
        if (gain && gainValue) gainValue.textContent = String(parseInt(gain.value, 10) || 0);

        const dbMinEl = document.getElementById('wfDbMin');
        const dbMaxEl = document.getElementById('wfDbMax');
        if (dbMinEl) dbMinEl.disabled = true;
        if (dbMaxEl) dbMaxEl.disabled = true;

        _setMonitorMode(_getMonitorMode());
        _setUnlockVisible(false);
        _setSmeter(0, 'S0');
        _syncMonitorButtons();
        _updateRunButtons();
        _setVisualStatus('CONNECTING');
        _setStatus('Connecting waterfall stream...');

        setTimeout(_resizeCanvases, 60);
        _drawFreqAxis();
        Promise.resolve(start()).catch((err) => {
            _setStatus(`Waterfall start failed: ${err}`);
            _setVisualStatus('ERROR');
        });
    }

    async function destroy() {
        _active = false;
        clearTimeout(_retuneTimer);
        clearTimeout(_monitorRetuneTimer);

        if (_monitoring) {
            await stopMonitor({ resumeWaterfall: false });
        }

        await stop({ keepStatus: true });

        if (_specCtx && _specCanvas) _specCtx.clearRect(0, 0, _specCanvas.width, _specCanvas.height);
        if (_wfCtx && _wfCanvas) _wfCtx.clearRect(0, 0, _wfCanvas.width, _wfCanvas.height);

        _specCanvas = null;
        _wfCanvas = null;
        _specCtx = null;
        _wfCtx = null;

        _stopSmeter();
        _setUnlockVisible(false);
        _audioUnlockRequired = false;
        _pendingSharedMonitorRearm = false;
        _sseStartConfigKey = '';
        _sseStartPromise = null;
    }

    return {
        init,
        destroy,
        start,
        stop,
        stepFreq,
        setPalette,
        togglePeakHold,
        toggleAnnotations,
        toggleAutoRange,
        onDeviceChange,
        toggleMonitor,
        toggleMute,
        unlockAudio,
        applyPreset,
        stopMonitor,
    };
})();

window.Waterfall = Waterfall;
