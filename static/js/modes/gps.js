/**
 * GPS Mode
 * Live GPS data display with satellite sky view, signal strength bars,
 * position/velocity/DOP readout. Connects to gpsd via backend SSE stream.
 */

const GPS = (function() {
    let connected = false;
    let lastPosition = null;
    let lastSky = null;
    let skyPollTimer = null;

    // Constellation color map
    const CONST_COLORS = {
        'GPS':     '#00d4ff',
        'GLONASS': '#00ff88',
        'Galileo': '#ff8800',
        'BeiDou':  '#ff4466',
        'SBAS':    '#ffdd00',
        'QZSS':    '#cc66ff',
    };

    function init() {
        drawEmptySkyView();
        connect();
    }

    function connect() {
        updateConnectionUI(false, false, 'connecting');
        fetch('/gps/auto-connect', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'connected') {
                    connected = true;
                    updateConnectionUI(true, data.has_fix);
                    if (data.position) {
                        lastPosition = data.position;
                        updatePositionUI(data.position);
                    }
                    if (data.sky) {
                        lastSky = data.sky;
                        updateSkyUI(data.sky);
                    }
                    subscribeToStream();
                    startSkyPolling();
                    // Ensure the global GPS stream is running
                    if (typeof startGpsStream === 'function' && !gpsEventSource) {
                        startGpsStream();
                    }
                } else {
                    connected = false;
                    updateConnectionUI(false, false, 'error', data.message || 'gpsd not available');
                }
            })
            .catch(() => {
                connected = false;
                updateConnectionUI(false, false, 'error', 'Connection failed — is the server running?');
            });
    }

    function disconnect() {
        unsubscribeFromStream();
        stopSkyPolling();
        fetch('/gps/stop', { method: 'POST' })
            .then(() => {
                connected = false;
                updateConnectionUI(false);
            });
    }

    function onGpsStreamData(data) {
        if (!connected) return;
        if (data.type === 'position') {
            lastPosition = data;
            updatePositionUI(data);
            updateConnectionUI(true, true);
        } else if (data.type === 'sky') {
            lastSky = data;
            updateSkyUI(data);
        }
    }

    function startSkyPolling() {
        stopSkyPolling();
        // Poll satellite data every 5 seconds as a reliable fallback
        // SSE stream may miss sky updates due to queue contention with position messages
        pollSatellites();
        skyPollTimer = setInterval(pollSatellites, 5000);
    }

    function stopSkyPolling() {
        if (skyPollTimer) {
            clearInterval(skyPollTimer);
            skyPollTimer = null;
        }
    }

    function pollSatellites() {
        if (!connected) return;
        fetch('/gps/satellites')
            .then(r => r.json())
            .then(data => {
                if (data.status === 'ok' && data.sky) {
                    lastSky = data.sky;
                    updateSkyUI(data.sky);
                }
            })
            .catch(() => {});
    }

    function subscribeToStream() {
        // Subscribe to the global GPS stream instead of opening a separate SSE connection
        if (typeof addGpsStreamSubscriber === 'function') {
            addGpsStreamSubscriber(onGpsStreamData);
        }
    }

    function unsubscribeFromStream() {
        if (typeof removeGpsStreamSubscriber === 'function') {
            removeGpsStreamSubscriber(onGpsStreamData);
        }
    }

    // ========================
    // UI Updates
    // ========================

    function updateConnectionUI(isConnected, hasFix, state, message) {
        const dot = document.getElementById('gpsStatusDot');
        const text = document.getElementById('gpsStatusText');
        const connectBtn = document.getElementById('gpsConnectBtn');
        const disconnectBtn = document.getElementById('gpsDisconnectBtn');
        const devicePath = document.getElementById('gpsDevicePath');

        if (dot) {
            dot.className = 'gps-status-dot';
            if (state === 'connecting') dot.classList.add('waiting');
            else if (state === 'error') dot.classList.add('error');
            else if (isConnected && hasFix) dot.classList.add('connected');
            else if (isConnected) dot.classList.add('waiting');
        }
        if (text) {
            if (state === 'connecting') text.textContent = 'Connecting...';
            else if (state === 'error') text.textContent = message || 'Connection failed';
            else if (isConnected && hasFix) text.textContent = 'Connected (Fix)';
            else if (isConnected) text.textContent = 'Connected (No Fix)';
            else text.textContent = 'Disconnected';
        }
        if (connectBtn) {
            connectBtn.style.display = isConnected ? 'none' : '';
            connectBtn.disabled = state === 'connecting';
        }
        if (disconnectBtn) disconnectBtn.style.display = isConnected ? '' : 'none';
        if (devicePath) devicePath.textContent = isConnected ? 'gpsd://localhost:2947' : '';
    }

    function updatePositionUI(pos) {
        // Sidebar fields
        setText('gpsLat', pos.latitude != null ? pos.latitude.toFixed(6) + '\u00b0' : '---');
        setText('gpsLon', pos.longitude != null ? pos.longitude.toFixed(6) + '\u00b0' : '---');
        setText('gpsAlt', pos.altitude != null ? pos.altitude.toFixed(1) + ' m' : '---');
        setText('gpsSpeed', pos.speed != null ? (pos.speed * 3.6).toFixed(1) + ' km/h' : '---');
        setText('gpsHeading', pos.heading != null ? pos.heading.toFixed(1) + '\u00b0' : '---');
        setText('gpsClimb', pos.climb != null ? pos.climb.toFixed(2) + ' m/s' : '---');

        // Fix type
        const fixEl = document.getElementById('gpsFixType');
        if (fixEl) {
            const fq = pos.fix_quality;
            if (fq === 3) fixEl.innerHTML = '<span class="gps-fix-badge fix-3d">3D FIX</span>';
            else if (fq === 2) fixEl.innerHTML = '<span class="gps-fix-badge fix-2d">2D FIX</span>';
            else fixEl.innerHTML = '<span class="gps-fix-badge no-fix">NO FIX</span>';
        }

        // Error estimates
        const eph = (pos.epx != null && pos.epy != null) ? Math.sqrt(pos.epx * pos.epx + pos.epy * pos.epy) : null;
        setText('gpsEph', eph != null ? eph.toFixed(1) + ' m' : '---');
        setText('gpsEpv', pos.epv != null ? pos.epv.toFixed(1) + ' m' : '---');
        setText('gpsEps', pos.eps != null ? pos.eps.toFixed(2) + ' m/s' : '---');

        // GPS time
        if (pos.timestamp) {
            const t = new Date(pos.timestamp);
            setText('gpsTime', t.toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC'));
        }

        // Visuals: position panel
        setText('gpsVisPosLat', pos.latitude != null ? pos.latitude.toFixed(6) + '\u00b0' : '---');
        setText('gpsVisPosLon', pos.longitude != null ? pos.longitude.toFixed(6) + '\u00b0' : '---');
        setText('gpsVisPosAlt', pos.altitude != null ? pos.altitude.toFixed(1) + ' m' : '---');
        setText('gpsVisPosSpeed', pos.speed != null ? (pos.speed * 3.6).toFixed(1) + ' km/h' : '---');
        setText('gpsVisPosHeading', pos.heading != null ? pos.heading.toFixed(1) + '\u00b0' : '---');
        setText('gpsVisPosClimb', pos.climb != null ? pos.climb.toFixed(2) + ' m/s' : '---');

        // Visuals: fix badge
        const visFixEl = document.getElementById('gpsVisFixBadge');
        if (visFixEl) {
            const fq = pos.fix_quality;
            if (fq === 3) { visFixEl.textContent = '3D FIX'; visFixEl.className = 'gps-fix-badge fix-3d'; }
            else if (fq === 2) { visFixEl.textContent = '2D FIX'; visFixEl.className = 'gps-fix-badge fix-2d'; }
            else { visFixEl.textContent = 'NO FIX'; visFixEl.className = 'gps-fix-badge no-fix'; }
        }

        // Visuals: GPS time
        if (pos.timestamp) {
            const t = new Date(pos.timestamp);
            setText('gpsVisTime', t.toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC'));
        }
    }

    function updateSkyUI(sky) {
        // Sidebar sat counts
        setText('gpsSatUsed', sky.usat != null ? sky.usat : '-');
        setText('gpsSatTotal', sky.nsat != null ? sky.nsat : '-');

        // DOP values
        setDop('gpsHdop', sky.hdop);
        setDop('gpsVdop', sky.vdop);
        setDop('gpsPdop', sky.pdop);
        setDop('gpsTdop', sky.tdop);
        setDop('gpsGdop', sky.gdop);

        // Visuals
        drawSkyView(sky.satellites || []);
        drawSignalBars(sky.satellites || []);
    }

    function setDop(id, val) {
        const el = document.getElementById(id);
        if (!el) return;
        if (val == null) { el.textContent = '---'; el.className = 'gps-info-value gps-mono'; return; }
        el.textContent = val.toFixed(1);
        let cls = 'gps-info-value gps-mono ';
        if (val <= 2) cls += 'gps-dop-good';
        else if (val <= 5) cls += 'gps-dop-moderate';
        else cls += 'gps-dop-poor';
        el.className = cls;
    }

    function setText(id, val) {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    // ========================
    // Sky View Polar Plot
    // ========================

    function drawEmptySkyView() {
        const canvas = document.getElementById('gpsSkyCanvas');
        if (!canvas) return;
        drawSkyViewBase(canvas);
    }

    function drawSkyView(satellites) {
        const canvas = document.getElementById('gpsSkyCanvas');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const w = canvas.width;
        const h = canvas.height;
        const cx = w / 2;
        const cy = h / 2;
        const r = Math.min(cx, cy) - 24;

        drawSkyViewBase(canvas);

        // Plot satellites
        satellites.forEach(sat => {
            if (sat.elevation == null || sat.azimuth == null) return;

            const elRad = (90 - sat.elevation) / 90;
            const azRad = (sat.azimuth - 90) * Math.PI / 180; // N = up
            const px = cx + r * elRad * Math.cos(azRad);
            const py = cy + r * elRad * Math.sin(azRad);

            const color = CONST_COLORS[sat.constellation] || CONST_COLORS['GPS'];
            const dotSize = sat.used ? 6 : 4;

            // Draw dot
            ctx.beginPath();
            ctx.arc(px, py, dotSize, 0, Math.PI * 2);
            if (sat.used) {
                ctx.fillStyle = color;
                ctx.fill();
            } else {
                ctx.strokeStyle = color;
                ctx.lineWidth = 1.5;
                ctx.stroke();
            }

            // PRN label
            ctx.fillStyle = color;
            ctx.font = '8px Roboto Condensed, monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';
            ctx.fillText(sat.prn, px, py - dotSize - 2);

            // SNR value
            if (sat.snr != null) {
                ctx.fillStyle = 'rgba(255,255,255,0.4)';
                ctx.font = '7px Roboto Condensed, monospace';
                ctx.textBaseline = 'top';
                ctx.fillText(Math.round(sat.snr), px, py + dotSize + 1);
            }
        });
    }

    function drawSkyViewBase(canvas) {
        const ctx = canvas.getContext('2d');
        const w = canvas.width;
        const h = canvas.height;
        const cx = w / 2;
        const cy = h / 2;
        const r = Math.min(cx, cy) - 24;

        ctx.clearRect(0, 0, w, h);

        // Background
        const bgStyle = getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim();
        ctx.fillStyle = bgStyle || '#0d1117';
        ctx.fillRect(0, 0, w, h);

        // Elevation rings (0, 30, 60, 90)
        ctx.strokeStyle = '#2a3040';
        ctx.lineWidth = 0.5;
        [90, 60, 30].forEach(el => {
            const gr = r * (1 - el / 90);
            ctx.beginPath();
            ctx.arc(cx, cy, gr, 0, Math.PI * 2);
            ctx.stroke();
            // Label
            ctx.fillStyle = '#555';
            ctx.font = '9px Roboto Condensed, monospace';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(el + '\u00b0', cx + gr + 3, cy - 2);
        });

        // Horizon circle
        ctx.strokeStyle = '#3a4050';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();

        // Cardinal directions
        ctx.fillStyle = '#888';
        ctx.font = 'bold 11px Roboto Condensed, monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('N', cx, cy - r - 12);
        ctx.fillText('S', cx, cy + r + 12);
        ctx.fillText('E', cx + r + 12, cy);
        ctx.fillText('W', cx - r - 12, cy);

        // Crosshairs
        ctx.strokeStyle = '#2a3040';
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.moveTo(cx, cy - r);
        ctx.lineTo(cx, cy + r);
        ctx.moveTo(cx - r, cy);
        ctx.lineTo(cx + r, cy);
        ctx.stroke();

        // Zenith dot
        ctx.fillStyle = '#333';
        ctx.beginPath();
        ctx.arc(cx, cy, 2, 0, Math.PI * 2);
        ctx.fill();
    }

    // ========================
    // Signal Strength Bars
    // ========================

    function drawSignalBars(satellites) {
        const container = document.getElementById('gpsSignalBars');
        if (!container) return;

        container.innerHTML = '';

        if (satellites.length === 0) return;

        // Sort: used first, then by PRN
        const sorted = [...satellites].sort((a, b) => {
            if (a.used !== b.used) return a.used ? -1 : 1;
            return a.prn - b.prn;
        });

        const maxSnr = 50; // dB-Hz typical max for display

        sorted.forEach(sat => {
            const snr = sat.snr || 0;
            const heightPct = Math.min(snr / maxSnr * 100, 100);
            const color = CONST_COLORS[sat.constellation] || CONST_COLORS['GPS'];
            const constClass = 'gps-const-' + (sat.constellation || 'GPS').toLowerCase();

            const wrap = document.createElement('div');
            wrap.className = 'gps-signal-bar-wrap';

            const snrLabel = document.createElement('span');
            snrLabel.className = 'gps-signal-snr';
            snrLabel.textContent = snr > 0 ? Math.round(snr) : '';

            const bar = document.createElement('div');
            bar.className = 'gps-signal-bar ' + constClass + (sat.used ? '' : ' unused');
            bar.style.height = Math.max(heightPct, 2) + '%';
            bar.title = `PRN ${sat.prn} (${sat.constellation}) - ${Math.round(snr)} dB-Hz${sat.used ? ' [USED]' : ''}`;

            const prn = document.createElement('span');
            prn.className = 'gps-signal-prn';
            prn.textContent = sat.prn;

            wrap.appendChild(snrLabel);
            wrap.appendChild(bar);
            wrap.appendChild(prn);
            container.appendChild(wrap);
        });
    }

    // ========================
    // Cleanup
    // ========================

    function destroy() {
        unsubscribeFromStream();
        stopSkyPolling();
    }

    return {
        init: init,
        connect: connect,
        disconnect: disconnect,
        destroy: destroy,
    };
})();
