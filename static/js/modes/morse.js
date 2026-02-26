/**
 * Morse Code (CW) decoder module.
 *
 * IIFE providing start/stop controls, SSE streaming, scope canvas,
 * decoded text display, and export capabilities.
 */
var MorseMode = (function () {
    'use strict';

    var state = {
        running: false,
        initialized: false,
        eventSource: null,
        charCount: 0,
        decodedLog: [],  // { timestamp, morse, char }
    };

    // Scope state
    var scopeCtx = null;
    var scopeAnim = null;
    var scopeHistory = [];
    var SCOPE_HISTORY_LEN = 300;
    var scopeThreshold = 0;
    var scopeToneOn = false;
    var scopeWaiting = false;
    var waitingStart = 0;  // timestamp when waiting began

    // ---- Initialization ----

    function init() {
        if (state.initialized) {
            checkStatus();
            return;
        }
        state.initialized = true;
        checkStatus();
    }

    function destroy() {
        disconnectSSE();
        stopScope();
    }

    // ---- Status ----

    function checkStatus() {
        fetch('/morse/status')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.running) {
                    state.running = true;
                    updateUI(true);
                    connectSSE();
                    startScope();
                } else {
                    state.running = false;
                    updateUI(false);
                }
            })
            .catch(function () {});
    }

    // ---- Start / Stop ----

    function start() {
        if (state.running) return;

        var payload = {
            frequency: document.getElementById('morseFrequency').value || '14.060',
            gain: document.getElementById('morseGain').value || '0',
            ppm: document.getElementById('morsePPM').value || '0',
            device: document.getElementById('deviceSelect')?.value || '0',
            sdr_type: document.getElementById('sdrTypeSelect')?.value || 'rtlsdr',
            tone_freq: document.getElementById('morseToneFreq').value || '700',
            wpm: document.getElementById('morseWpm').value || '15',
            bias_t: typeof getBiasTEnabled === 'function' ? getBiasTEnabled() : false,
        };

        fetch('/morse/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.status === 'started') {
                state.running = true;
                state.charCount = 0;
                state.decodedLog = [];
                updateUI(true);
                connectSSE();
                startScope();
                clearDecodedText();
            } else {
                alert('Error: ' + (data.message || 'Unknown error'));
            }
        })
        .catch(function (err) {
            alert('Failed to start Morse decoder: ' + err);
        });
    }

    function stop() {
        fetch('/morse/stop', { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function () {
                state.running = false;
                updateUI(false);
                disconnectSSE();
                stopScope();
            })
            .catch(function (err) {
                console.error('Morse stop request failed:', err);
                // Reset UI regardless so the user isn't stuck
                state.running = false;
                updateUI(false);
                disconnectSSE();
                stopScope();
            });
    }

    // ---- SSE ----

    function connectSSE() {
        disconnectSSE();
        var es = new EventSource('/morse/stream');

        es.onmessage = function (e) {
            try {
                var msg = JSON.parse(e.data);
                handleMessage(msg);
            } catch (_) {}
        };

        es.onerror = function () {
            // Reconnect handled by browser
        };

        state.eventSource = es;
    }

    function disconnectSSE() {
        if (state.eventSource) {
            state.eventSource.close();
            state.eventSource = null;
        }
    }

    function handleMessage(msg) {
        var type = msg.type;

        if (type === 'scope') {
            // Update scope data
            var amps = msg.amplitudes || [];
            if (msg.waiting && amps.length === 0 && scopeHistory.length === 0) {
                if (!scopeWaiting) {
                    scopeWaiting = true;
                    waitingStart = Date.now();
                }
            } else if (amps.length > 0) {
                scopeWaiting = false;
                waitingStart = 0;
            }
            for (var i = 0; i < amps.length; i++) {
                scopeHistory.push(amps[i]);
                if (scopeHistory.length > SCOPE_HISTORY_LEN) {
                    scopeHistory.shift();
                }
            }
            scopeThreshold = msg.threshold || 0;
            scopeToneOn = msg.tone_on || false;

        } else if (type === 'morse_char') {
            appendChar(msg.char, msg.morse, msg.timestamp);

        } else if (type === 'morse_space') {
            appendSpace();

        } else if (type === 'status') {
            if (msg.status === 'stopped') {
                state.running = false;
                updateUI(false);
                disconnectSSE();
                stopScope();
            }
        } else if (type === 'info') {
            appendDiagLine(msg.text);

        } else if (type === 'error') {
            console.error('Morse error:', msg.text);
        }
    }

    // ---- Decoded text ----

    function appendChar(ch, morse, timestamp) {
        state.charCount++;
        state.decodedLog.push({ timestamp: timestamp, morse: morse, char: ch });

        var panel = document.getElementById('morseDecodedText');
        if (!panel) return;

        var span = document.createElement('span');
        span.className = 'morse-char';
        span.textContent = ch;
        span.title = morse + ' (' + timestamp + ')';
        panel.appendChild(span);

        // Auto-scroll
        panel.scrollTop = panel.scrollHeight;

        // Update count
        var countEl = document.getElementById('morseCharCount');
        if (countEl) countEl.textContent = state.charCount + ' chars';
        var barChars = document.getElementById('morseStatusBarChars');
        if (barChars) barChars.textContent = state.charCount + ' chars decoded';
    }

    function appendSpace() {
        var panel = document.getElementById('morseDecodedText');
        if (!panel) return;

        var span = document.createElement('span');
        span.className = 'morse-word-space';
        span.textContent = ' ';
        panel.appendChild(span);
    }

    function clearDecodedText() {
        var panel = document.getElementById('morseDecodedText');
        if (panel) panel.innerHTML = '';
        state.charCount = 0;
        state.decodedLog = [];
        var countEl = document.getElementById('morseCharCount');
        if (countEl) countEl.textContent = '0 chars';
        var barChars = document.getElementById('morseStatusBarChars');
        if (barChars) barChars.textContent = '0 chars decoded';
    }

    // ---- Scope canvas ----

    function startScope() {
        var canvas = document.getElementById('morseScopeCanvas');
        if (!canvas) return;

        var dpr = window.devicePixelRatio || 1;
        var rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = 80 * dpr;
        canvas.style.height = '80px';

        scopeCtx = canvas.getContext('2d');
        scopeCtx.scale(dpr, dpr);
        scopeHistory = [];
        scopeWaiting = false;

        var toneLabel = document.getElementById('morseScopeToneLabel');
        var threshLabel = document.getElementById('morseScopeThreshLabel');

        function draw() {
            if (!scopeCtx) return;
            var w = rect.width;
            var h = 80;

            scopeCtx.fillStyle = '#050510';
            scopeCtx.fillRect(0, 0, w, h);

            // Update header labels
            if (toneLabel) toneLabel.textContent = scopeToneOn ? 'ON' : '--';
            if (threshLabel) threshLabel.textContent = scopeThreshold > 0 ? Math.round(scopeThreshold) : '--';

            if (scopeHistory.length === 0) {
                if (scopeWaiting) {
                    var elapsed = waitingStart ? (Date.now() - waitingStart) / 1000 : 0;
                    var waitText = elapsed > 10
                        ? 'No audio data \u2014 check SDR log below'
                        : 'Awaiting SDR data\u2026';
                    scopeCtx.fillStyle = elapsed > 10 ? '#887744' : '#556677';
                    scopeCtx.font = '12px monospace';
                    scopeCtx.textAlign = 'center';
                    scopeCtx.fillText(waitText, w / 2, h / 2);
                    scopeCtx.textAlign = 'start';
                }
                scopeAnim = requestAnimationFrame(draw);
                return;
            }

            // Find max for normalization
            var maxVal = 0;
            for (var i = 0; i < scopeHistory.length; i++) {
                if (scopeHistory[i] > maxVal) maxVal = scopeHistory[i];
            }
            if (maxVal === 0) maxVal = 1;

            var barW = w / SCOPE_HISTORY_LEN;
            var threshNorm = scopeThreshold / maxVal;

            // Draw amplitude bars
            for (var j = 0; j < scopeHistory.length; j++) {
                var norm = scopeHistory[j] / maxVal;
                var barH = norm * (h - 10);
                var x = j * barW;
                var y = h - barH;

                // Green if above threshold, gray if below
                if (scopeHistory[j] > scopeThreshold) {
                    scopeCtx.fillStyle = '#00ff88';
                } else {
                    scopeCtx.fillStyle = '#334455';
                }
                scopeCtx.fillRect(x, y, Math.max(barW - 1, 1), barH);
            }

            // Draw threshold line
            if (scopeThreshold > 0) {
                var threshY = h - (threshNorm * (h - 10));
                scopeCtx.strokeStyle = '#ff4444';
                scopeCtx.lineWidth = 1;
                scopeCtx.setLineDash([4, 4]);
                scopeCtx.beginPath();
                scopeCtx.moveTo(0, threshY);
                scopeCtx.lineTo(w, threshY);
                scopeCtx.stroke();
                scopeCtx.setLineDash([]);
            }

            // Tone indicator
            if (scopeToneOn) {
                scopeCtx.fillStyle = '#00ff88';
                scopeCtx.beginPath();
                scopeCtx.arc(w - 12, 12, 5, 0, Math.PI * 2);
                scopeCtx.fill();
            }

            scopeAnim = requestAnimationFrame(draw);
        }

        draw();
    }

    function stopScope() {
        if (scopeAnim) {
            cancelAnimationFrame(scopeAnim);
            scopeAnim = null;
        }
        scopeCtx = null;
    }

    // ---- Export ----

    function exportTxt() {
        var text = state.decodedLog.map(function (e) { return e.char; }).join('');
        downloadFile('morse_decoded.txt', text, 'text/plain');
    }

    function exportCsv() {
        var lines = ['timestamp,morse,character'];
        state.decodedLog.forEach(function (e) {
            lines.push(e.timestamp + ',"' + e.morse + '",' + e.char);
        });
        downloadFile('morse_decoded.csv', lines.join('\n'), 'text/csv');
    }

    function copyToClipboard() {
        var text = state.decodedLog.map(function (e) { return e.char; }).join('');
        navigator.clipboard.writeText(text).then(function () {
            var btn = document.getElementById('morseCopyBtn');
            if (btn) {
                var orig = btn.textContent;
                btn.textContent = 'Copied!';
                setTimeout(function () { btn.textContent = orig; }, 1500);
            }
        });
    }

    function downloadFile(filename, content, type) {
        var blob = new Blob([content], { type: type });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
    }

    // ---- Diagnostic log ----

    function appendDiagLine(text) {
        var log = document.getElementById('morseDiagLog');
        if (!log) return;
        log.style.display = 'block';
        var line = document.createElement('div');
        line.textContent = text;
        log.appendChild(line);
        // Limit to 20 entries
        while (log.children.length > 20) {
            log.removeChild(log.firstChild);
        }
        log.scrollTop = log.scrollHeight;
    }

    function clearDiagLog() {
        var log = document.getElementById('morseDiagLog');
        if (log) {
            log.innerHTML = '';
            log.style.display = 'none';
        }
    }

    // ---- UI ----

    function updateUI(running) {
        var startBtn = document.getElementById('morseStartBtn');
        var stopBtn = document.getElementById('morseStopBtn');
        var indicator = document.getElementById('morseStatusIndicator');
        var statusText = document.getElementById('morseStatusText');

        if (startBtn) startBtn.style.display = running ? 'none' : '';
        if (stopBtn) stopBtn.style.display = running ? '' : 'none';

        if (indicator) {
            indicator.style.background = running ? '#00ff88' : 'var(--text-dim)';
        }
        if (statusText) {
            statusText.textContent = running ? 'Listening' : 'Standby';
        }

        // Toggle scope and output panels (pager/sensor pattern)
        var scopePanel = document.getElementById('morseScopePanel');
        var outputPanel = document.getElementById('morseOutputPanel');
        if (scopePanel) scopePanel.style.display = running ? 'block' : 'none';
        if (outputPanel) outputPanel.style.display = running ? 'block' : 'none';

        var scopeStatus = document.getElementById('morseScopeStatusLabel');
        if (scopeStatus) scopeStatus.textContent = running ? 'ACTIVE' : 'IDLE';
        if (scopeStatus) scopeStatus.style.color = running ? '#0f0' : '#444';

        // Diagnostic log: clear on start, hide on stop
        if (running) {
            clearDiagLog();
        } else {
            var diagLog = document.getElementById('morseDiagLog');
            if (diagLog) diagLog.style.display = 'none';
        }
    }

    function setFreq(mhz) {
        var el = document.getElementById('morseFrequency');
        if (el) el.value = mhz;
    }

    // ---- Public API ----

    return {
        init: init,
        destroy: destroy,
        start: start,
        stop: stop,
        setFreq: setFreq,
        exportTxt: exportTxt,
        exportCsv: exportCsv,
        copyToClipboard: copyToClipboard,
        clearText: clearDecodedText,
    };
})();
