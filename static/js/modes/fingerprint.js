/* Signal Fingerprinting — RF baseline recorder + anomaly comparator */
const Fingerprint = (function () {
    'use strict';

    let _active = false;
    let _recording = false;
    let _scannerSource = null;
    let _pendingObs = [];
    let _flushTimer = null;
    let _currentTab = 'record';
    let _chartInstance = null;
    let _ownedScanner = false;
    let _obsCount = 0;

    function _flushObservations() {
        if (!_recording || _pendingObs.length === 0) return;
        const batch = _pendingObs.splice(0);
        fetch('/fingerprint/observation', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ observations: batch }),
        }).catch(() => {});
    }

    function _startScannerStream() {
        if (_scannerSource) { _scannerSource.close(); _scannerSource = null; }
        _scannerSource = new EventSource('/listening/scanner/stream');
        _scannerSource.onmessage = (ev) => {
            try {
                const d = JSON.parse(ev.data);
                // Only collect meaningful signal events (signal_found has SNR)
                if (d.type && d.type !== 'signal_found' && d.type !== 'scan_update') return;

                const freq = d.frequency ?? d.freq_mhz ?? null;
                if (freq === null) return;

                // Prefer SNR (dB) from signal_found events; fall back to level for scan_update
                let power = null;
                if (d.snr !== undefined && d.snr !== null) {
                    power = d.snr;
                } else if (d.level !== undefined && d.level !== null) {
                    // level is RMS audio — skip scan_update noise floor readings
                    if (d.type === 'signal_found') {
                        power = d.level;
                    } else {
                        return; // scan_update with no SNR — skip
                    }
                } else if (d.power_dbm !== undefined) {
                    power = d.power_dbm;
                }

                if (power === null) return;

                if (_recording) {
                    _pendingObs.push({ freq_mhz: parseFloat(freq), power_dbm: parseFloat(power) });
                    _obsCount++;
                    _updateObsCounter();
                }
            } catch (_) {}
        };
    }

    function _updateObsCounter() {
        const el = document.getElementById('fpObsCount');
        if (el) el.textContent = _obsCount;
    }

    function _setStatus(msg) {
        const el = document.getElementById('fpRecordStatus');
        if (el) el.textContent = msg;
    }

    // ── Scanner lifecycle (standalone control) ─────────────────────────

    async function _checkScannerStatus() {
        try {
            const r = await fetch('/listening/scanner/status');
            if (r.ok) {
                const d = await r.json();
                return !!d.running;
            }
        } catch (_) {}
        return false;
    }

    async function _updateScannerStatusUI() {
        const running = await _checkScannerStatus();
        const dotEl   = document.getElementById('fpScannerDot');
        const textEl  = document.getElementById('fpScannerStatusText');
        const startB  = document.getElementById('fpScannerStartBtn');
        const stopB   = document.getElementById('fpScannerStopBtn');

        if (dotEl)  dotEl.style.background = running ? 'var(--accent-green, #00ff88)' : 'rgba(255,255,255,0.2)';
        if (textEl) textEl.textContent = running ? 'Scanner running' : 'Scanner not running';
        if (startB) startB.style.display = running ? 'none' : '';
        if (stopB)  stopB.style.display = (running && _ownedScanner) ? '' : 'none';

        // Auto-connect to stream if scanner is running
        if (running && !_scannerSource) _startScannerStream();
    }

    async function startScanner() {
        const deviceVal = document.getElementById('fpDevice')?.value || 'rtlsdr:0';
        const [sdrType, idxStr] = deviceVal.includes(':') ? deviceVal.split(':') : ['rtlsdr', '0'];
        const startB = document.getElementById('fpScannerStartBtn');
        if (startB) { startB.disabled = true; startB.textContent = 'Starting…'; }

        try {
            const res = await fetch('/listening/scanner/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ start_freq: 24, end_freq: 1700, sdr_type: sdrType, device: parseInt(idxStr) || 0 }),
            });
            if (res.ok) {
                _ownedScanner = true;
                _startScannerStream();
            }
        } catch (_) {}

        if (startB) { startB.disabled = false; startB.textContent = 'Start Scanner'; }
        await _updateScannerStatusUI();
    }

    async function stopScanner() {
        if (!_ownedScanner) return;
        try {
            await fetch('/listening/scanner/stop', { method: 'POST' });
        } catch (_) {}
        _ownedScanner = false;
        if (_scannerSource) { _scannerSource.close(); _scannerSource = null; }
        await _updateScannerStatusUI();
    }

    // ── Recording ──────────────────────────────────────────────────────

    async function startRecording() {
        // Check scanner is running first
        const running = await _checkScannerStatus();
        if (!running) {
            _setStatus('Scanner not running — start it first (Step 2)');
            return;
        }

        const name     = document.getElementById('fpSessionName')?.value.trim()     || 'Session ' + new Date().toLocaleString();
        const location = document.getElementById('fpSessionLocation')?.value.trim() || null;
        try {
            const res  = await fetch('/fingerprint/start', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ name, location }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Start failed');
            _recording = true;
            _pendingObs = [];
            _obsCount = 0;
            _updateObsCounter();
            _flushTimer = setInterval(_flushObservations, 5000);
            if (!_scannerSource) _startScannerStream();
            const startBtn = document.getElementById('fpStartBtn');
            const stopBtn  = document.getElementById('fpStopBtn');
            if (startBtn) startBtn.style.display = 'none';
            if (stopBtn)  stopBtn.style.display  = '';
            _setStatus('Recording… session #' + data.session_id);
        } catch (e) {
            _setStatus('Error: ' + e.message);
        }
    }

    async function stopRecording() {
        _recording = false;
        _flushObservations();
        if (_flushTimer) { clearInterval(_flushTimer); _flushTimer = null; }
        if (_scannerSource) { _scannerSource.close(); _scannerSource = null; }
        try {
            const res  = await fetch('/fingerprint/stop', { method: 'POST' });
            const data = await res.json();
            _setStatus(`Saved: ${data.bands_recorded} bands recorded (${_obsCount} observations)`);
        } catch (e) {
            _setStatus('Error saving: ' + e.message);
        }
        const startBtn = document.getElementById('fpStartBtn');
        const stopBtn  = document.getElementById('fpStopBtn');
        if (startBtn) startBtn.style.display = '';
        if (stopBtn)  stopBtn.style.display  = 'none';
        _loadSessions();
    }

    async function _loadSessions() {
        try {
            const res  = await fetch('/fingerprint/list');
            const data = await res.json();
            const sel  = document.getElementById('fpBaselineSelect');
            if (!sel) return;
            const sessions = (data.sessions || []).filter(s => s.finalized_at);
            sel.innerHTML = sessions.length
                ? sessions.map(s => `<option value="${s.id}">[${s.id}] ${s.name} (${s.band_count || 0} bands)</option>`).join('')
                : '<option value="">No saved baselines</option>';
        } catch (_) {}
    }

    // ── Compare ────────────────────────────────────────────────────────

    async function compareNow() {
        const baselineId = document.getElementById('fpBaselineSelect')?.value;
        if (!baselineId) return;

        // Check scanner is running
        const running = await _checkScannerStatus();
        if (!running) {
            const statusEl = document.getElementById('fpCompareStatus');
            if (statusEl) statusEl.textContent = 'Scanner not running — start it first';
            return;
        }

        const statusEl = document.getElementById('fpCompareStatus');
        const compareBtn = document.querySelector('#fpComparePanel .run-btn');
        if (statusEl) statusEl.textContent = 'Collecting observations…';
        if (compareBtn) { compareBtn.disabled = true; compareBtn.textContent = 'Scanning…'; }

        // Collect live observations for ~3 seconds
        const obs = [];
        const tmpSrc  = new EventSource('/listening/scanner/stream');
        const deadline = Date.now() + 3000;

        await new Promise(resolve => {
            tmpSrc.onmessage = (ev) => {
                if (Date.now() > deadline) { tmpSrc.close(); resolve(); return; }
                try {
                    const d = JSON.parse(ev.data);
                    if (d.type && d.type !== 'signal_found' && d.type !== 'scan_update') return;
                    const freq = d.frequency ?? d.freq_mhz ?? null;
                    let power = null;
                    if (d.snr !== undefined && d.snr !== null) power = d.snr;
                    else if (d.type === 'signal_found' && d.level !== undefined) power = d.level;
                    else if (d.power_dbm !== undefined) power = d.power_dbm;
                    if (freq !== null && power !== null) obs.push({ freq_mhz: parseFloat(freq), power_dbm: parseFloat(power) });
                    if (statusEl) statusEl.textContent = `Collecting… ${obs.length} observations`;
                } catch (_) {}
            };
            tmpSrc.onerror = () => { tmpSrc.close(); resolve(); };
            setTimeout(() => { tmpSrc.close(); resolve(); }, 3500);
        });

        if (statusEl) statusEl.textContent = `Comparing ${obs.length} observations against baseline…`;

        try {
            const res  = await fetch('/fingerprint/compare', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ baseline_id: parseInt(baselineId), observations: obs }),
            });
            const data = await res.json();
            _renderAnomalies(data.anomalies || []);
            _renderChart(data.baseline_bands || [], data.anomalies || []);
            if (statusEl) statusEl.textContent = `Done — ${obs.length} observations, ${(data.anomalies || []).length} anomalies`;
        } catch (e) {
            console.error('Compare failed:', e);
            if (statusEl) statusEl.textContent = 'Compare failed: ' + e.message;
        }

        if (compareBtn) { compareBtn.disabled = false; compareBtn.textContent = 'Compare Now'; }
    }

    function _renderAnomalies(anomalies) {
        const panel = document.getElementById('fpAnomalyList');
        const items = document.getElementById('fpAnomalyItems');
        if (!panel || !items) return;

        if (anomalies.length === 0) {
            items.innerHTML = '<div style="font-size:11px; color:var(--text-dim); padding:8px;">No significant anomalies detected.</div>';
            panel.style.display = 'block';
            return;
        }

        items.innerHTML = anomalies.map(a => {
            const z = a.z_score !== null ? Math.abs(a.z_score) : 999;
            let cls = 'severity-warn', badge = 'POWER';
            if (a.anomaly_type === 'new')     { cls = 'severity-new';  badge = 'NEW'; }
            else if (a.anomaly_type === 'missing') { cls = 'severity-warn'; badge = 'MISSING'; }
            else if (z >= 3)                  { cls = 'severity-alert'; }

            const zText     = a.z_score !== null ? `z=${a.z_score.toFixed(1)}` : '';
            const powerText = a.current_power !== null ? `${a.current_power.toFixed(1)} dBm` : 'absent';
            const baseText  = a.baseline_mean !== null ? `baseline: ${a.baseline_mean.toFixed(1)} dBm` : '';

            return `<div class="fp-anomaly-item ${cls}">
  <div style="display:flex; align-items:center; gap:6px;">
    <span class="fp-anomaly-band">${a.band_label}</span>
    <span class="fp-anomaly-type-badge" style="background:rgba(255,255,255,0.1);">${badge}</span>
    ${z >= 3 ? '<span style="color:#ef4444; font-size:9px; font-weight:700;">ALERT</span>' : ''}
  </div>
  <div style="color:var(--text-secondary);">${powerText} ${baseText} ${zText}</div>
</div>`;
        }).join('');
        panel.style.display = 'block';

        // Voice alert for high-severity anomalies
        const highZ = anomalies.find(a => (a.z_score !== null && Math.abs(a.z_score) >= 3) || a.anomaly_type === 'new');
        if (highZ && window.VoiceAlerts) {
            VoiceAlerts.speak(`RF anomaly detected: ${highZ.band_label} — ${highZ.anomaly_type}`, 2);
        }
    }

    function _renderChart(baselineBands, anomalies) {
        const canvas = document.getElementById('fpChartCanvas');
        if (!canvas || typeof Chart === 'undefined') return;

        const anomalyMap = {};
        anomalies.forEach(a => { anomalyMap[a.band_center_mhz] = a; });

        const bands  = baselineBands.slice(0, 40);
        const labels = bands.map(b => b.band_center_mhz.toFixed(1));
        const means  = bands.map(b => b.mean_dbm);
        const currentPowers = bands.map(b => {
            const a = anomalyMap[b.band_center_mhz];
            return a ? a.current_power : b.mean_dbm;
        });
        const barColors = bands.map(b => {
            const a = anomalyMap[b.band_center_mhz];
            if (!a) return 'rgba(74,163,255,0.6)';
            if (a.anomaly_type === 'new') return 'rgba(168,85,247,0.8)';
            if (a.z_score !== null && Math.abs(a.z_score) >= 3) return 'rgba(239,68,68,0.8)';
            return 'rgba(251,191,36,0.7)';
        });

        if (_chartInstance) { _chartInstance.destroy(); _chartInstance = null; }

        _chartInstance = new Chart(canvas, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    { label: 'Baseline Mean', data: means,         backgroundColor: 'rgba(74,163,255,0.3)', borderColor: 'rgba(74,163,255,0.8)', borderWidth: 1 },
                    { label: 'Current',       data: currentPowers, backgroundColor: barColors, borderColor: barColors, borderWidth: 1 },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { labels: { color: '#aaa', font: { size: 10 } } } },
                scales: {
                    x: { ticks: { color: '#666', font: { size: 9 }, maxRotation: 90 }, grid: { color: 'rgba(255,255,255,0.05)' } },
                    y: { ticks: { color: '#666', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.05)' }, title: { display: true, text: 'Power (dBm)', color: '#666' } },
                },
            },
        });
    }

    function showTab(tab) {
        _currentTab = tab;
        const recordPanel  = document.getElementById('fpRecordPanel');
        const comparePanel = document.getElementById('fpComparePanel');
        if (recordPanel)  recordPanel.style.display  = tab === 'record'  ? '' : 'none';
        if (comparePanel) comparePanel.style.display = tab === 'compare' ? '' : 'none';
        document.querySelectorAll('.fp-tab-btn').forEach(b => b.classList.remove('active'));
        const activeBtn = tab === 'record'
            ? document.getElementById('fpTabRecord')
            : document.getElementById('fpTabCompare');
        if (activeBtn) activeBtn.classList.add('active');
        const hintEl = document.getElementById('fpTabHint');
        if (hintEl) hintEl.innerHTML = TAB_HINTS[tab] || '';
        if (tab === 'compare') _loadSessions();
    }

    function _loadDevices() {
        const sel = document.getElementById('fpDevice');
        if (!sel) return;
        fetch('/devices').then(r => r.json()).then(devices => {
            if (!devices || devices.length === 0) {
                sel.innerHTML = '<option value="">No SDR devices detected</option>';
                return;
            }
            sel.innerHTML = devices.map(d => {
                const label = d.serial ? `${d.name} [${d.serial}]` : d.name;
                return `<option value="${d.sdr_type}:${d.index}">${label}</option>`;
            }).join('');
        }).catch(() => { sel.innerHTML = '<option value="">Could not load devices</option>'; });
    }

    const TAB_HINTS = {
        record:  'Record a <strong style="color:var(--text-secondary);">baseline</strong> in a known-clean RF environment, then use <strong style="color:var(--text-secondary);">Compare</strong> later to detect new or anomalous signals.',
        compare: 'Select a saved baseline and click <strong style="color:var(--text-secondary);">Compare Now</strong> to scan for deviations. Anomalies are flagged by statistical z-score.',
    };

    function init() {
        _active = true;
        _loadDevices();
        _loadSessions();
        _updateScannerStatusUI();
    }

    function destroy() {
        _active = false;
        if (_recording) stopRecording();
        if (_scannerSource) { _scannerSource.close(); _scannerSource = null; }
        if (_chartInstance) { _chartInstance.destroy(); _chartInstance = null; }
        if (_ownedScanner) stopScanner();
    }

    return { init, destroy, showTab, startRecording, stopRecording, compareNow, startScanner, stopScanner };
})();

window.Fingerprint = Fingerprint;
