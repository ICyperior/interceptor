/* RF Heatmap — GPS + signal strength Leaflet heatmap */
const RFHeatmap = (function () {
    'use strict';

    let _map = null;
    let _heatLayer = null;
    let _gpsSource = null;
    let _sigSource = null;
    let _heatPoints = [];
    let _isRecording = false;
    let _lastLat = null, _lastLng = null;
    let _minDist = 5;
    let _source = 'wifi';
    let _gpsPos = null;
    let _lastSignal = null;
    let _active = false;
    let _ownedSource = false;   // true if heatmap started the source itself

    const RSSI_RANGES = {
        wifi:      { min: -90,  max: -30 },
        bluetooth: { min: -100, max: -40 },
        scanner:   { min: -120, max: -20 },
    };

    function _norm(val, src) {
        const r = RSSI_RANGES[src] || RSSI_RANGES.wifi;
        return Math.max(0, Math.min(1, (val - r.min) / (r.max - r.min)));
    }

    function _haversineM(lat1, lng1, lat2, lng2) {
        const R = 6371000;
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLng = (lng2 - lng1) * Math.PI / 180;
        const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLng / 2) ** 2;
        return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    function _ensureLeafletHeat(cb) {
        if (window.L && L.heatLayer) { cb(); return; }
        const s = document.createElement('script');
        s.src = '/static/js/vendor/leaflet-heat.js';
        s.onload = cb;
        s.onerror = () => console.warn('RF Heatmap: leaflet-heat.js failed to load');
        document.head.appendChild(s);
    }

    function _initMap() {
        if (_map) return;
        const el = document.getElementById('rfheatmapMapEl');
        if (!el) return;

        // Defer map creation until container has non-zero dimensions (prevents leaflet-heat IndexSizeError)
        if (el.offsetWidth === 0 || el.offsetHeight === 0) {
            setTimeout(_initMap, 200);
            return;
        }

        const fallback = _getFallbackPos();
        const lat = _gpsPos ? _gpsPos.lat : (fallback ? fallback.lat : 37.7749);
        const lng = _gpsPos ? _gpsPos.lng : (fallback ? fallback.lng : -122.4194);

        _map = L.map(el, { zoomControl: true }).setView([lat, lng], 16);
        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            attribution: '© OpenStreetMap contributors © CARTO',
            subdomains: 'abcd',
            maxZoom: 20,
        }).addTo(_map);

        _heatLayer = L.heatLayer([], { radius: 25, blur: 15, maxZoom: 17 }).addTo(_map);
    }

    function _startGPS() {
        if (_gpsSource) { _gpsSource.close(); _gpsSource = null; }
        _gpsSource = new EventSource('/gps/stream');
        _gpsSource.onmessage = (ev) => {
            try {
                const d = JSON.parse(ev.data);
                if (d.lat && d.lng && d.fix) {
                    _gpsPos = { lat: parseFloat(d.lat), lng: parseFloat(d.lng) };
                    _updateGpsPill(true, _gpsPos.lat, _gpsPos.lng);
                    if (_map) _map.setView([_gpsPos.lat, _gpsPos.lng], _map.getZoom(), { animate: false });
                } else {
                    _updateGpsPill(false);
                }
            } catch (_) {}
        };
        _gpsSource.onerror = () => _updateGpsPill(false);
    }

    function _updateGpsPill(fix, lat, lng) {
        const pill = document.getElementById('rfhmGpsPill');
        if (!pill) return;
        if (fix && lat !== undefined) {
            pill.textContent = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
            pill.style.color = 'var(--accent-green, #00ff88)';
        } else {
            const fallback = _getFallbackPos();
            pill.textContent = fallback ? 'No Fix (using fallback)' : 'No Fix';
            pill.style.color = fallback ? 'var(--accent-yellow, #f59e0b)' : 'var(--text-dim, #555)';
        }
    }

    function _startSignalStream() {
        if (_sigSource) { _sigSource.close(); _sigSource = null; }
        let url;
        if (_source === 'wifi')      url = '/wifi/stream';
        else if (_source === 'bluetooth') url = '/api/bluetooth/stream';
        else                         url = '/listening/scanner/stream';

        _sigSource = new EventSource(url);
        _sigSource.onmessage = (ev) => {
            try {
                const d = JSON.parse(ev.data);
                let rssi = null;
                if (_source === 'wifi')           rssi = d.signal_level ?? d.signal ?? null;
                else if (_source === 'bluetooth') rssi = d.rssi ?? null;
                else                              rssi = d.power_level ?? d.power ?? null;
                if (rssi !== null) {
                    _lastSignal = parseFloat(rssi);
                    _updateSignalDisplay(_lastSignal);
                }
                _maybeSample();
            } catch (_) {}
        };
    }

    function _maybeSample() {
        if (!_isRecording || _lastSignal === null) return;
        if (!_gpsPos) {
            const fb = _getFallbackPos();
            if (fb) _gpsPos = fb;
            else return;
        }

        const { lat, lng } = _gpsPos;
        if (_lastLat !== null) {
            const dist = _haversineM(_lastLat, _lastLng, lat, lng);
            if (dist < _minDist) return;
        }

        const intensity = _norm(_lastSignal, _source);
        _heatPoints.push([lat, lng, intensity]);
        _lastLat = lat;
        _lastLng = lng;

        if (_heatLayer) {
            const el = document.getElementById('rfheatmapMapEl');
            if (el && el.offsetWidth > 0 && el.offsetHeight > 0) _heatLayer.setLatLngs(_heatPoints);
        }
        _updateCount();
    }

    function _updateCount() {
        const el = document.getElementById('rfhmPointCount');
        if (el) el.textContent = _heatPoints.length;
    }

    function _updateSignalDisplay(rssi) {
        const valEl    = document.getElementById('rfhmLiveSignal');
        const barEl    = document.getElementById('rfhmSignalBar');
        const statusEl = document.getElementById('rfhmSignalStatus');
        if (!valEl) return;

        valEl.textContent = rssi !== null ? `${rssi.toFixed(1)} dBm` : '— dBm';

        if (rssi !== null) {
            // Normalise to 0–100% for the bar
            const pct = Math.round(_norm(rssi, _source) * 100);
            if (barEl) barEl.style.width = pct + '%';

            // Colour the value by strength
            let color, label;
            if (pct >= 66)      { color = 'var(--accent-green, #00ff88)'; label = 'Strong'; }
            else if (pct >= 33) { color = 'var(--accent-cyan, #4aa3ff)';  label = 'Moderate'; }
            else                { color = '#f59e0b';                        label = 'Weak'; }
            valEl.style.color = color;
            if (barEl) barEl.style.background = color;

            if (statusEl) {
                statusEl.textContent = _isRecording
                    ? `${label} — recording point every ${_minDist}m`
                    : `${label} — press Start Recording to begin`;
            }
        } else {
            if (barEl) barEl.style.width = '0%';
            valEl.style.color = 'var(--text-dim)';
            if (statusEl) statusEl.textContent = 'No signal data received yet';
        }
    }

    function setSource(src) {
        _source = src;
        if (_active) _startSignalStream();
    }

    function setMinDist(m) {
        _minDist = m;
    }

    function startRecording() {
        _isRecording = true;
        _lastLat = null; _lastLng = null;
        const startBtn = document.getElementById('rfhmRecordBtn');
        const stopBtn  = document.getElementById('rfhmStopBtn');
        if (startBtn) startBtn.style.display = 'none';
        if (stopBtn)  { stopBtn.style.display = ''; stopBtn.classList.add('rfhm-recording-pulse'); }
    }

    function stopRecording() {
        _isRecording = false;
        const startBtn = document.getElementById('rfhmRecordBtn');
        const stopBtn  = document.getElementById('rfhmStopBtn');
        if (startBtn) startBtn.style.display = '';
        if (stopBtn)  { stopBtn.style.display = 'none'; stopBtn.classList.remove('rfhm-recording-pulse'); }
    }

    function clearPoints() {
        _heatPoints = [];
        if (_heatLayer) {
            const el = document.getElementById('rfheatmapMapEl');
            if (el && el.offsetWidth > 0 && el.offsetHeight > 0) _heatLayer.setLatLngs([]);
        }
        _updateCount();
    }

    function exportGeoJSON() {
        const features = _heatPoints.map(([lat, lng, intensity]) => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [lng, lat] },
            properties: { intensity, source: _source },
        }));
        const geojson = { type: 'FeatureCollection', features };
        const blob = new Blob([JSON.stringify(geojson, null, 2)], { type: 'application/json' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `rf_heatmap_${Date.now()}.geojson`;
        a.click();
    }

    function invalidateMap() {
        if (!_map) return;
        const el = document.getElementById('rfheatmapMapEl');
        if (el && el.offsetWidth > 0 && el.offsetHeight > 0) {
            _map.invalidateSize();
        }
    }

    // ── Source lifecycle (start / stop / status) ──────────────────────

    async function _checkSourceStatus() {
        const src = _source;
        let running = false;
        let detail  = null;
        try {
            if (src === 'wifi') {
                const r = await fetch('/wifi/v2/scan/status');
                if (r.ok) { const d = await r.json(); running = !!d.is_scanning; detail = d.interface || null; }
            } else if (src === 'bluetooth') {
                const r = await fetch('/api/bluetooth/scan/status');
                if (r.ok) { const d = await r.json(); running = !!d.is_scanning; }
            } else if (src === 'scanner') {
                const r = await fetch('/listening/scanner/status');
                if (r.ok) { const d = await r.json(); running = !!d.running; }
            }
        } catch (_) {}
        return { running, detail };
    }

    async function startSource() {
        const src    = _source;
        const btn    = document.getElementById('rfhmSourceStartBtn');
        const status = document.getElementById('rfhmSourceStatus');
        if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }

        try {
            let res;
            if (src === 'wifi') {
                // Try to find a monitor interface from the WiFi status first
                let iface = null;
                try {
                    const st = await fetch('/wifi/v2/scan/status');
                    if (st.ok) { const d = await st.json(); iface = d.interface || null; }
                } catch (_) {}
                if (!iface) {
                    // Ask the user to enter an interface name
                    const entered = prompt('Enter your monitor-mode WiFi interface name (e.g. wlan0mon):');
                    if (!entered) { _updateSourceStatusUI(); return; }
                    iface = entered.trim();
                }
                res = await fetch('/wifi/v2/scan/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ interface: iface }) });
            } else if (src === 'bluetooth') {
                res = await fetch('/api/bluetooth/scan/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: 'auto' }) });
            } else if (src === 'scanner') {
                const deviceVal = document.getElementById('rfhmDevice')?.value || 'rtlsdr:0';
                const [sdrType, idxStr] = deviceVal.includes(':') ? deviceVal.split(':') : ['rtlsdr', '0'];
                res = await fetch('/listening/scanner/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ start_freq: 88, end_freq: 108, sdr_type: sdrType, device: parseInt(idxStr) || 0 }) });
            }
            if (res && res.ok) {
                _ownedSource = true;
                _startSignalStream();
            }
        } catch (_) {}

        await _updateSourceStatusUI();
    }

    async function stopSource() {
        if (!_ownedSource) return;
        try {
            if (_source === 'wifi')      await fetch('/wifi/v2/scan/stop',          { method: 'POST' });
            else if (_source === 'bluetooth') await fetch('/api/bluetooth/scan/stop', { method: 'POST' });
            else if (_source === 'scanner')   await fetch('/listening/scanner/stop',  { method: 'POST' });
        } catch (_) {}
        _ownedSource = false;
        await _updateSourceStatusUI();
    }

    async function _updateSourceStatusUI() {
        const { running, detail } = await _checkSourceStatus();
        const row    = document.getElementById('rfhmSourceStatusRow');
        const dotEl  = document.getElementById('rfhmSourceDot');
        const textEl = document.getElementById('rfhmSourceStatusText');
        const startB = document.getElementById('rfhmSourceStartBtn');
        const stopB  = document.getElementById('rfhmSourceStopBtn');
        if (!row) return;

        const SOURCE_NAMES = { wifi: 'WiFi Scanner', bluetooth: 'Bluetooth Scanner', scanner: 'SDR Scanner' };
        const name = SOURCE_NAMES[_source] || _source;

        if (dotEl)  dotEl.style.background  = running ? 'var(--accent-green)' : 'rgba(255,255,255,0.2)';
        if (textEl) textEl.textContent = running
            ? `${name} running${detail ? ' · ' + detail : ''}`
            : `${name} not running`;
        if (startB) { startB.style.display = running ? 'none' : ''; startB.disabled = false; startB.textContent = `Start ${name}`; }
        if (stopB)  stopB.style.display = (running && _ownedSource) ? '' : 'none';

        // Auto-subscribe to stream if source just became running
        if (running && !_sigSource) _startSignalStream();
    }

    const SOURCE_HINTS = {
        wifi:      'Walk with your device — stronger WiFi signals are plotted brighter on the map.',
        bluetooth: 'Walk near Bluetooth devices — signal strength is mapped by RSSI.',
        scanner:   'SDR scanner power levels are mapped by GPS position. Start the Listening Post scanner first.',
    };

    function onSourceChange() {
        const src  = document.getElementById('rfhmSource')?.value || 'wifi';
        const hint = document.getElementById('rfhmSourceHint');
        const dg   = document.getElementById('rfhmDeviceGroup');
        if (hint) hint.textContent = SOURCE_HINTS[src] || '';
        if (dg)   dg.style.display = src === 'scanner' ? '' : 'none';
        _lastSignal = null;
        _ownedSource = false;
        _updateSignalDisplay(null);
        _updateSourceStatusUI();
        // Re-subscribe to correct stream
        if (_sigSource) { _sigSource.close(); _sigSource = null; }
        _startSignalStream();
    }

    function _loadDevices() {
        const sel = document.getElementById('rfhmDevice');
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

    function _getFallbackPos() {
        // Try observer location from localStorage (shared across all map modes)
        try {
            const stored = localStorage.getItem('observerLocation');
            if (stored) {
                const p = JSON.parse(stored);
                if (p && typeof p.lat === 'number' && typeof p.lon === 'number') {
                    return { lat: p.lat, lng: p.lon };
                }
            }
        } catch (_) {}
        // Try manual coord inputs
        const lat = parseFloat(document.getElementById('rfhmManualLat')?.value);
        const lng = parseFloat(document.getElementById('rfhmManualLon')?.value);
        if (!isNaN(lat) && !isNaN(lng)) return { lat, lng };
        return null;
    }

    function setManualCoords() {
        const lat = parseFloat(document.getElementById('rfhmManualLat')?.value);
        const lng = parseFloat(document.getElementById('rfhmManualLon')?.value);
        if (!isNaN(lat) && !isNaN(lng) && !_gpsPos && _map) {
            _map.setView([lat, lng], _map.getZoom(), { animate: false });
        }
    }

    function useObserverLocation() {
        try {
            const stored = localStorage.getItem('observerLocation');
            if (stored) {
                const p = JSON.parse(stored);
                if (p && typeof p.lat === 'number' && typeof p.lon === 'number') {
                    const latEl = document.getElementById('rfhmManualLat');
                    const lonEl = document.getElementById('rfhmManualLon');
                    if (latEl) latEl.value = p.lat.toFixed(5);
                    if (lonEl) lonEl.value = p.lon.toFixed(5);
                    if (_map) _map.setView([p.lat, p.lon], _map.getZoom(), { animate: true });
                    return;
                }
            }
        } catch (_) {}
    }

    function init() {
        _active = true;
        _loadDevices();
        onSourceChange();

        // Pre-fill manual coords from observer location if available
        const fallback = _getFallbackPos();
        if (fallback) {
            const latEl = document.getElementById('rfhmManualLat');
            const lonEl = document.getElementById('rfhmManualLon');
            if (latEl && !latEl.value) latEl.value = fallback.lat.toFixed(5);
            if (lonEl && !lonEl.value) lonEl.value = fallback.lng.toFixed(5);
        }

        _updateSignalDisplay(null);
        _updateSourceStatusUI();
        _ensureLeafletHeat(() => {
            setTimeout(() => {
                _initMap();
                _startGPS();
                _startSignalStream();
            }, 50);
        });
    }

    function destroy() {
        _active = false;
        if (_isRecording) stopRecording();
        if (_ownedSource) stopSource();
        if (_gpsSource) { _gpsSource.close(); _gpsSource = null; }
        if (_sigSource) { _sigSource.close(); _sigSource = null; }
    }

    return { init, destroy, setSource, setMinDist, startRecording, stopRecording, clearPoints, exportGeoJSON, invalidateMap, onSourceChange, setManualCoords, useObserverLocation, startSource, stopSource };
})();

window.RFHeatmap = RFHeatmap;
