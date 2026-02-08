/**
 * Weather Satellite Mode
 * NOAA APT and Meteor LRPT decoder interface with auto-scheduler,
 * polar plot, ground track map, countdown, and timeline.
 */

const WeatherSat = (function() {
    // State
    let isRunning = false;
    let eventSource = null;
    let images = [];
    let passes = [];
    let selectedPassIndex = -1;
    let currentSatellite = null;
    let countdownInterval = null;
    let schedulerEnabled = false;
    let groundMap = null;
    let groundTrackLayer = null;
    let observerMarker = null;
    let consoleEntries = [];
    let consoleCollapsed = false;
    let currentPhase = 'idle';
    let consoleAutoHideTimer = null;
    let currentModalFilename = null;

    /**
     * Initialize the Weather Satellite mode
     */
    function init() {
        checkStatus();
        loadImages();
        loadLocationInputs();
        loadPasses();
        startCountdownTimer();
        checkSchedulerStatus();
        initGroundMap();
    }

    /**
     * Load observer location into input fields
     */
    function loadLocationInputs() {
        const latInput = document.getElementById('wxsatObsLat');
        const lonInput = document.getElementById('wxsatObsLon');

        let storedLat = localStorage.getItem('observerLat');
        let storedLon = localStorage.getItem('observerLon');
        if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
            const shared = ObserverLocation.getShared();
            storedLat = shared.lat.toString();
            storedLon = shared.lon.toString();
        }

        if (latInput && storedLat) latInput.value = storedLat;
        if (lonInput && storedLon) lonInput.value = storedLon;

        if (latInput) latInput.addEventListener('change', saveLocationFromInputs);
        if (lonInput) lonInput.addEventListener('change', saveLocationFromInputs);
    }

    /**
     * Save location from inputs and refresh passes
     */
    function saveLocationFromInputs() {
        const latInput = document.getElementById('wxsatObsLat');
        const lonInput = document.getElementById('wxsatObsLon');

        const lat = parseFloat(latInput?.value);
        const lon = parseFloat(lonInput?.value);

        if (!isNaN(lat) && lat >= -90 && lat <= 90 &&
            !isNaN(lon) && lon >= -180 && lon <= 180) {
            if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
                ObserverLocation.setShared({ lat, lon });
            } else {
                localStorage.setItem('observerLat', lat.toString());
                localStorage.setItem('observerLon', lon.toString());
            }
            loadPasses();
        }
    }

    /**
     * Use GPS for location
     */
    function useGPS(btn) {
        if (!navigator.geolocation) {
            showNotification('Weather Sat', 'GPS not available in this browser');
            return;
        }

        const originalText = btn.innerHTML;
        btn.innerHTML = '<span style="opacity: 0.7;">...</span>';
        btn.disabled = true;

        navigator.geolocation.getCurrentPosition(
            (pos) => {
                const latInput = document.getElementById('wxsatObsLat');
                const lonInput = document.getElementById('wxsatObsLon');

                const lat = pos.coords.latitude.toFixed(4);
                const lon = pos.coords.longitude.toFixed(4);

                if (latInput) latInput.value = lat;
                if (lonInput) lonInput.value = lon;

                if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
                    ObserverLocation.setShared({ lat: parseFloat(lat), lon: parseFloat(lon) });
                } else {
                    localStorage.setItem('observerLat', lat);
                    localStorage.setItem('observerLon', lon);
                }

                btn.innerHTML = originalText;
                btn.disabled = false;
                showNotification('Weather Sat', 'Location updated');
                loadPasses();
            },
            (err) => {
                btn.innerHTML = originalText;
                btn.disabled = false;
                showNotification('Weather Sat', 'Failed to get location');
            },
            { enableHighAccuracy: true, timeout: 10000 }
        );
    }

    /**
     * Check decoder status
     */
    async function checkStatus() {
        try {
            const response = await fetch('/weather-sat/status');
            const data = await response.json();

            if (!data.available) {
                updateStatusUI('unavailable', 'SatDump not installed');
                return;
            }

            if (data.running) {
                isRunning = true;
                currentSatellite = data.satellite;
                updateStatusUI('capturing', `Capturing ${data.satellite}...`);
                startStream();
            } else {
                updateStatusUI('idle', 'Idle');
            }
        } catch (err) {
            console.error('Failed to check weather sat status:', err);
        }
    }

    /**
     * Start capture
     */
    async function start() {
        const satSelect = document.getElementById('weatherSatSelect');
        const gainInput = document.getElementById('weatherSatGain');
        const biasTInput = document.getElementById('weatherSatBiasT');
        const deviceSelect = document.getElementById('deviceSelect');

        const satellite = satSelect?.value || 'NOAA-18';
        const gain = parseFloat(gainInput?.value || '40');
        const biasT = biasTInput?.checked || false;
        const device = parseInt(deviceSelect?.value || '0', 10);

        clearConsole();
        showConsole(true);
        updatePhaseIndicator('tuning');
        addConsoleEntry('Starting capture...', 'info');
        updateStatusUI('connecting', 'Starting...');

        try {
            const response = await fetch('/weather-sat/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    satellite,
                    device,
                    gain,
                    bias_t: biasT,
                })
            });

            const data = await response.json();

            if (data.status === 'started' || data.status === 'already_running') {
                isRunning = true;
                currentSatellite = data.satellite || satellite;
                updateStatusUI('capturing', `${data.satellite} ${data.frequency} MHz`);
                updateFreqDisplay(data.frequency, data.mode);
                startStream();
                showNotification('Weather Sat', `Capturing ${data.satellite} on ${data.frequency} MHz`);
            } else {
                updateStatusUI('idle', 'Start failed');
                showNotification('Weather Sat', data.message || 'Failed to start');
            }
        } catch (err) {
            console.error('Failed to start weather sat:', err);
            updateStatusUI('idle', 'Error');
            showNotification('Weather Sat', 'Connection error');
        }
    }

    /**
     * Start capture for a specific pass
     */
    function startPass(satellite) {
        const satSelect = document.getElementById('weatherSatSelect');
        if (satSelect) {
            satSelect.value = satellite;
        }
        start();
    }

    /**
     * Stop capture
     */
    async function stop() {
        try {
            await fetch('/weather-sat/stop', { method: 'POST' });
            isRunning = false;
            stopStream();
            updateStatusUI('idle', 'Stopped');
            showNotification('Weather Sat', 'Capture stopped');
        } catch (err) {
            console.error('Failed to stop weather sat:', err);
        }
    }

    /**
     * Start test decode from a pre-recorded file
     */
    async function testDecode() {
        const satSelect = document.getElementById('wxsatTestSatSelect');
        const fileInput = document.getElementById('wxsatTestFilePath');
        const rateSelect = document.getElementById('wxsatTestSampleRate');

        const satellite = satSelect?.value || 'NOAA-18';
        const inputFile = (fileInput?.value || '').trim();
        const sampleRate = parseInt(rateSelect?.value || '1000000', 10);

        if (!inputFile) {
            showNotification('Weather Sat', 'Enter a file path');
            return;
        }

        clearConsole();
        showConsole(true);
        updatePhaseIndicator('decoding');
        addConsoleEntry(`Test decode: ${inputFile}`, 'info');
        updateStatusUI('connecting', 'Starting file decode...');

        try {
            const response = await fetch('/weather-sat/test-decode', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    satellite,
                    input_file: inputFile,
                    sample_rate: sampleRate,
                })
            });

            const data = await response.json();

            if (data.status === 'started' || data.status === 'already_running') {
                isRunning = true;
                currentSatellite = data.satellite || satellite;
                updateStatusUI('decoding', `Decoding ${data.satellite} from file`);
                updateFreqDisplay(data.frequency, data.mode);
                startStream();
                showNotification('Weather Sat', `Decoding ${data.satellite} from file`);
            } else {
                updateStatusUI('idle', 'Decode failed');
                showNotification('Weather Sat', data.message || 'Failed to start decode');
                addConsoleEntry(data.message || 'Failed to start decode', 'error');
            }
        } catch (err) {
            console.error('Failed to start test decode:', err);
            updateStatusUI('idle', 'Error');
            showNotification('Weather Sat', 'Connection error');
        }
    }

    /**
     * Update status UI
     */
    function updateStatusUI(status, text) {
        const dot = document.getElementById('wxsatStripDot');
        const statusText = document.getElementById('wxsatStripStatus');
        const startBtn = document.getElementById('wxsatStartBtn');
        const stopBtn = document.getElementById('wxsatStopBtn');

        if (dot) {
            dot.className = 'wxsat-strip-dot';
            if (status === 'capturing') dot.classList.add('capturing');
            else if (status === 'decoding') dot.classList.add('decoding');
        }

        if (statusText) statusText.textContent = text || status;

        if (startBtn && stopBtn) {
            if (status === 'capturing' || status === 'decoding') {
                startBtn.style.display = 'none';
                stopBtn.style.display = 'inline-block';
            } else {
                startBtn.style.display = 'inline-block';
                stopBtn.style.display = 'none';
            }
        }
    }

    /**
     * Update frequency display in strip
     */
    function updateFreqDisplay(freq, mode) {
        const freqEl = document.getElementById('wxsatStripFreq');
        const modeEl = document.getElementById('wxsatStripMode');
        if (freqEl) freqEl.textContent = freq || '--';
        if (modeEl) modeEl.textContent = mode || '--';
    }

    /**
     * Start SSE stream
     */
    function startStream() {
        if (eventSource) eventSource.close();

        eventSource = new EventSource('/weather-sat/stream');

        eventSource.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                if (data.type === 'weather_sat_progress') {
                    handleProgress(data);
                } else if (data.type && data.type.startsWith('schedule_')) {
                    handleSchedulerSSE(data);
                }
            } catch (err) {
                console.error('Failed to parse SSE:', err);
            }
        };

        eventSource.onerror = () => {
            setTimeout(() => {
                if (isRunning || schedulerEnabled) startStream();
            }, 3000);
        };
    }

    /**
     * Stop SSE stream
     */
    function stopStream() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
    }

    /**
     * Handle progress update
     */
    function handleProgress(data) {
        const captureStatus = document.getElementById('wxsatCaptureStatus');
        const captureMsg = document.getElementById('wxsatCaptureMsg');
        const captureElapsed = document.getElementById('wxsatCaptureElapsed');
        const progressBar = document.getElementById('wxsatProgressFill');

        if (data.status === 'capturing' || data.status === 'decoding') {
            updateStatusUI(data.status, `${data.status === 'decoding' ? 'Decoding' : 'Capturing'} ${data.satellite}...`);

            if (captureStatus) captureStatus.classList.add('active');
            if (captureMsg) captureMsg.textContent = data.message || '';
            if (captureElapsed) captureElapsed.textContent = formatElapsed(data.elapsed_seconds || 0);
            if (progressBar) progressBar.style.width = (data.progress || 0) + '%';

            // Console updates
            showConsole(true);
            if (data.message) addConsoleEntry(data.message, data.log_type || 'info');
            if (data.capture_phase) updatePhaseIndicator(data.capture_phase);

        } else if (data.status === 'complete') {
            if (data.image) {
                images.unshift(data.image);
                updateImageCount(images.length);
                renderGallery();
                showNotification('Weather Sat', `New image: ${data.image.product || data.image.satellite}`);
            }

            if (!data.image) {
                // Capture ended
                isRunning = false;
                if (!schedulerEnabled) stopStream();
                updateStatusUI('idle', 'Capture complete');
                if (captureStatus) captureStatus.classList.remove('active');

                addConsoleEntry('Capture complete', 'signal');
                updatePhaseIndicator('complete');
                consoleAutoHideTimer = setTimeout(() => showConsole(false), 30000);
            }

        } else if (data.status === 'error') {
            updateStatusUI('idle', 'Error');
            showNotification('Weather Sat', data.message || 'Capture error');
            if (captureStatus) captureStatus.classList.remove('active');

            if (data.message) addConsoleEntry(data.message, 'error');
            updatePhaseIndicator('error');
            consoleAutoHideTimer = setTimeout(() => showConsole(false), 15000);
        }
    }

    /**
     * Handle scheduler SSE events
     */
    function handleSchedulerSSE(data) {
        if (data.type === 'schedule_capture_start') {
            isRunning = true;
            const p = data.pass || {};
            currentSatellite = p.satellite;
            updateStatusUI('capturing', `Auto: ${p.name || p.satellite} ${p.frequency} MHz`);
            showNotification('Weather Sat', `Auto-capture started: ${p.name || p.satellite}`);
        } else if (data.type === 'schedule_capture_complete') {
            showNotification('Weather Sat', `Auto-capture complete: ${(data.pass || {}).name || ''}`);
            loadImages();
        } else if (data.type === 'schedule_capture_skipped') {
            const reason = data.reason || 'unknown';
            const p = data.pass || {};
            showNotification('Weather Sat', `Pass skipped (${reason}): ${p.name || p.satellite}`);
        }
    }

    /**
     * Format elapsed seconds
     */
    function formatElapsed(seconds) {
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        return `${m}:${s.toString().padStart(2, '0')}`;
    }

    /**
     * Load pass predictions (with trajectory + ground track)
     */
    async function loadPasses() {
        const storedLat = localStorage.getItem('observerLat');
        const storedLon = localStorage.getItem('observerLon');

        if (!storedLat || !storedLon) {
            renderPasses([]);
            return;
        }

        try {
            const url = `/weather-sat/passes?latitude=${storedLat}&longitude=${storedLon}&hours=24&min_elevation=15&trajectory=true&ground_track=true`;
            const response = await fetch(url);
            const data = await response.json();

            if (data.status === 'ok') {
                passes = data.passes || [];
                renderPasses(passes);
                renderTimeline(passes);
                updateCountdownFromPasses();
                // Auto-select first pass
                if (passes.length > 0 && selectedPassIndex < 0) {
                    selectPass(0);
                }
            }
        } catch (err) {
            console.error('Failed to load passes:', err);
        }
    }

    /**
     * Select a pass to display in polar plot and map
     */
    function selectPass(index) {
        if (index < 0 || index >= passes.length) return;
        selectedPassIndex = index;
        const pass = passes[index];

        // Highlight active card
        document.querySelectorAll('.wxsat-pass-card').forEach((card, i) => {
            card.classList.toggle('selected', i === index);
        });

        // Update polar plot
        drawPolarPlot(pass);

        // Update ground track
        updateGroundTrack(pass);

        // Update polar panel subtitle
        const polarSat = document.getElementById('wxsatPolarSat');
        if (polarSat) polarSat.textContent = `${pass.name} ${pass.maxEl}\u00b0`;
    }

    /**
     * Render pass predictions list
     */
    function renderPasses(passList) {
        const container = document.getElementById('wxsatPassesList');
        const countEl = document.getElementById('wxsatPassesCount');

        if (countEl) countEl.textContent = passList.length;

        if (!container) return;

        if (passList.length === 0) {
            const hasLocation = localStorage.getItem('observerLat') !== null;
            container.innerHTML = `
                <div class="wxsat-gallery-empty">
                    <p>${hasLocation ? 'No passes in next 24h' : 'Set location to see pass predictions'}</p>
                </div>
            `;
            return;
        }

        container.innerHTML = passList.map((pass, idx) => {
            const modeClass = pass.mode === 'APT' ? 'apt' : 'lrpt';
            const timeStr = pass.startTime || '--';
            const now = new Date();
            const passStart = new Date(pass.startTimeISO);
            const diffMs = passStart - now;
            const diffMins = Math.floor(diffMs / 60000);
            const isSelected = idx === selectedPassIndex;

            let countdown = '';
            if (diffMs < 0) {
                countdown = 'NOW';
            } else if (diffMins < 60) {
                countdown = `in ${diffMins}m`;
            } else {
                const hrs = Math.floor(diffMins / 60);
                const mins = diffMins % 60;
                countdown = `in ${hrs}h${mins}m`;
            }

            return `
                <div class="wxsat-pass-card${isSelected ? ' selected' : ''}" onclick="WeatherSat.selectPass(${idx})">
                    <div class="wxsat-pass-sat">
                        <span class="wxsat-pass-sat-name">${escapeHtml(pass.name)}</span>
                        <span class="wxsat-pass-mode ${modeClass}">${escapeHtml(pass.mode)}</span>
                    </div>
                    <div class="wxsat-pass-details">
                        <span class="wxsat-pass-detail-label">Time</span>
                        <span class="wxsat-pass-detail-value">${escapeHtml(timeStr)}</span>
                        <span class="wxsat-pass-detail-label">Max El</span>
                        <span class="wxsat-pass-detail-value">${pass.maxEl}&deg;</span>
                        <span class="wxsat-pass-detail-label">Duration</span>
                        <span class="wxsat-pass-detail-value">${pass.duration} min</span>
                        <span class="wxsat-pass-detail-label">Freq</span>
                        <span class="wxsat-pass-detail-value">${pass.frequency} MHz</span>
                    </div>
                    <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 4px;">
                        <span class="wxsat-pass-quality ${pass.quality}">${pass.quality}</span>
                        <span style="font-size: 10px; color: var(--text-dim); font-family: 'JetBrains Mono', monospace;">${countdown}</span>
                    </div>
                    <div style="margin-top: 6px; text-align: right;">
                        <button class="wxsat-strip-btn" onclick="event.stopPropagation(); WeatherSat.startPass('${escapeHtml(pass.satellite)}')" style="font-size: 10px; padding: 2px 8px;">Capture</button>
                    </div>
                </div>
            `;
        }).join('');
    }

    // ========================
    // Polar Plot
    // ========================

    /**
     * Draw polar plot for a pass trajectory
     */
    function drawPolarPlot(pass) {
        const canvas = document.getElementById('wxsatPolarCanvas');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const w = canvas.width;
        const h = canvas.height;
        const cx = w / 2;
        const cy = h / 2;
        const r = Math.min(cx, cy) - 20;

        ctx.clearRect(0, 0, w, h);

        // Background
        ctx.fillStyle = '#0d1117';
        ctx.fillRect(0, 0, w, h);

        // Grid circles (30, 60, 90 deg elevation)
        ctx.strokeStyle = '#2a3040';
        ctx.lineWidth = 0.5;
        [90, 60, 30].forEach((el, i) => {
            const gr = r * (1 - el / 90);
            ctx.beginPath();
            ctx.arc(cx, cy, gr, 0, Math.PI * 2);
            ctx.stroke();
            // Label
            ctx.fillStyle = '#555';
            ctx.font = '9px JetBrains Mono, monospace';
            ctx.textAlign = 'left';
            ctx.fillText(el + '\u00b0', cx + gr + 3, cy - 2);
        });

        // Horizon circle
        ctx.strokeStyle = '#3a4050';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();

        // Cardinal directions
        ctx.fillStyle = '#666';
        ctx.font = '10px JetBrains Mono, monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('N', cx, cy - r - 10);
        ctx.fillText('S', cx, cy + r + 10);
        ctx.fillText('E', cx + r + 10, cy);
        ctx.fillText('W', cx - r - 10, cy);

        // Cross hairs
        ctx.strokeStyle = '#2a3040';
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.moveTo(cx, cy - r);
        ctx.lineTo(cx, cy + r);
        ctx.moveTo(cx - r, cy);
        ctx.lineTo(cx + r, cy);
        ctx.stroke();

        // Trajectory
        const trajectory = pass.trajectory;
        if (!trajectory || trajectory.length === 0) return;

        const color = pass.mode === 'LRPT' ? '#00ff88' : '#00d4ff';

        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;

        trajectory.forEach((pt, i) => {
            const elRad = (90 - pt.el) / 90;
            const azRad = (pt.az - 90) * Math.PI / 180; // offset: N is up
            const px = cx + r * elRad * Math.cos(azRad);
            const py = cy + r * elRad * Math.sin(azRad);

            if (i === 0) ctx.moveTo(px, py);
            else ctx.lineTo(px, py);
        });
        ctx.stroke();

        // Start point (green dot)
        const start = trajectory[0];
        const startR = (90 - start.el) / 90;
        const startAz = (start.az - 90) * Math.PI / 180;
        ctx.fillStyle = '#00ff88';
        ctx.beginPath();
        ctx.arc(cx + r * startR * Math.cos(startAz), cy + r * startR * Math.sin(startAz), 4, 0, Math.PI * 2);
        ctx.fill();

        // End point (red dot)
        const end = trajectory[trajectory.length - 1];
        const endR = (90 - end.el) / 90;
        const endAz = (end.az - 90) * Math.PI / 180;
        ctx.fillStyle = '#ff4444';
        ctx.beginPath();
        ctx.arc(cx + r * endR * Math.cos(endAz), cy + r * endR * Math.sin(endAz), 4, 0, Math.PI * 2);
        ctx.fill();

        // Max elevation marker
        let maxEl = 0;
        let maxPt = trajectory[0];
        trajectory.forEach(pt => { if (pt.el > maxEl) { maxEl = pt.el; maxPt = pt; } });
        const maxR = (90 - maxPt.el) / 90;
        const maxAz = (maxPt.az - 90) * Math.PI / 180;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(cx + r * maxR * Math.cos(maxAz), cy + r * maxR * Math.sin(maxAz), 3, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = color;
        ctx.font = '9px JetBrains Mono, monospace';
        ctx.textAlign = 'center';
        ctx.fillText(Math.round(maxEl) + '\u00b0', cx + r * maxR * Math.cos(maxAz), cy + r * maxR * Math.sin(maxAz) - 8);
    }

    // ========================
    // Ground Track Map
    // ========================

    /**
     * Initialize Leaflet ground track map
     */
    function initGroundMap() {
        const container = document.getElementById('wxsatGroundMap');
        if (!container || groundMap) return;
        if (typeof L === 'undefined') return;

        groundMap = L.map(container, {
            center: [20, 0],
            zoom: 2,
            zoomControl: false,
            attributionControl: false,
        });

        // Check tile provider from settings
        let tileUrl = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
        try {
            const provider = localStorage.getItem('tileProvider');
            if (provider === 'osm') {
                tileUrl = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
            }
        } catch (e) {}

        L.tileLayer(tileUrl, { maxZoom: 10 }).addTo(groundMap);

        groundTrackLayer = L.layerGroup().addTo(groundMap);

        // Delayed invalidation to fix sizing
        setTimeout(() => { if (groundMap) groundMap.invalidateSize(); }, 200);
    }

    /**
     * Update ground track on the map
     */
    function updateGroundTrack(pass) {
        if (!groundMap || !groundTrackLayer) return;

        groundTrackLayer.clearLayers();

        const track = pass.groundTrack;
        if (!track || track.length === 0) return;

        const color = pass.mode === 'LRPT' ? '#00ff88' : '#00d4ff';

        // Draw polyline
        const latlngs = track.map(p => [p.lat, p.lon]);
        L.polyline(latlngs, { color, weight: 2, opacity: 0.8 }).addTo(groundTrackLayer);

        // Start marker
        L.circleMarker(latlngs[0], {
            radius: 5, color: '#00ff88', fillColor: '#00ff88', fillOpacity: 1, weight: 0,
        }).addTo(groundTrackLayer);

        // End marker
        L.circleMarker(latlngs[latlngs.length - 1], {
            radius: 5, color: '#ff4444', fillColor: '#ff4444', fillOpacity: 1, weight: 0,
        }).addTo(groundTrackLayer);

        // Observer marker
        const lat = parseFloat(localStorage.getItem('observerLat'));
        const lon = parseFloat(localStorage.getItem('observerLon'));
        if (!isNaN(lat) && !isNaN(lon)) {
            L.circleMarker([lat, lon], {
                radius: 6, color: '#ffbb00', fillColor: '#ffbb00', fillOpacity: 0.8, weight: 1,
            }).addTo(groundTrackLayer);
        }

        // Fit bounds
        try {
            const bounds = L.latLngBounds(latlngs);
            if (!isNaN(lat) && !isNaN(lon)) bounds.extend([lat, lon]);
            groundMap.fitBounds(bounds, { padding: [20, 20] });
        } catch (e) {}
    }

    // ========================
    // Countdown
    // ========================

    /**
     * Start the countdown interval timer
     */
    function startCountdownTimer() {
        if (countdownInterval) clearInterval(countdownInterval);
        countdownInterval = setInterval(updateCountdownFromPasses, 1000);
    }

    /**
     * Update countdown display from passes array
     */
    function updateCountdownFromPasses() {
        const now = new Date();
        let nextPass = null;
        let isActive = false;

        for (const pass of passes) {
            const start = new Date(pass.startTimeISO);
            const end = new Date(pass.endTimeISO);
            if (end > now) {
                nextPass = pass;
                isActive = start <= now;
                break;
            }
        }

        const daysEl = document.getElementById('wxsatCdDays');
        const hoursEl = document.getElementById('wxsatCdHours');
        const minsEl = document.getElementById('wxsatCdMins');
        const secsEl = document.getElementById('wxsatCdSecs');
        const satEl = document.getElementById('wxsatCountdownSat');
        const detailEl = document.getElementById('wxsatCountdownDetail');
        const boxes = document.getElementById('wxsatCountdownBoxes');

        if (!nextPass) {
            if (daysEl) daysEl.textContent = '--';
            if (hoursEl) hoursEl.textContent = '--';
            if (minsEl) minsEl.textContent = '--';
            if (secsEl) secsEl.textContent = '--';
            if (satEl) satEl.textContent = '--';
            if (detailEl) detailEl.textContent = 'No passes predicted';
            if (boxes) boxes.querySelectorAll('.wxsat-countdown-box').forEach(b => {
                b.classList.remove('imminent', 'active');
            });
            return;
        }

        const target = new Date(nextPass.startTimeISO);
        let diffMs = target - now;

        if (isActive) {
            diffMs = 0;
        }

        const totalSec = Math.max(0, Math.floor(diffMs / 1000));
        const d = Math.floor(totalSec / 86400);
        const h = Math.floor((totalSec % 86400) / 3600);
        const m = Math.floor((totalSec % 3600) / 60);
        const s = totalSec % 60;

        if (daysEl) daysEl.textContent = d.toString().padStart(2, '0');
        if (hoursEl) hoursEl.textContent = h.toString().padStart(2, '0');
        if (minsEl) minsEl.textContent = m.toString().padStart(2, '0');
        if (secsEl) secsEl.textContent = s.toString().padStart(2, '0');
        if (satEl) satEl.textContent = `${nextPass.name} ${nextPass.frequency} MHz`;
        if (detailEl) {
            if (isActive) {
                detailEl.textContent = `ACTIVE - ${nextPass.maxEl}\u00b0 max el`;
            } else {
                detailEl.textContent = `${nextPass.maxEl}\u00b0 max el / ${nextPass.duration} min`;
            }
        }

        // Countdown box states
        if (boxes) {
            const isImminent = totalSec < 600 && totalSec > 0; // < 10 min
            boxes.querySelectorAll('.wxsat-countdown-box').forEach(b => {
                b.classList.toggle('imminent', isImminent);
                b.classList.toggle('active', isActive);
            });
        }
    }

    // ========================
    // Timeline
    // ========================

    /**
     * Render 24h timeline with pass markers
     */
    function renderTimeline(passList) {
        const track = document.getElementById('wxsatTimelineTrack');
        const cursor = document.getElementById('wxsatTimelineCursor');
        if (!track) return;

        // Clear existing pass markers
        track.querySelectorAll('.wxsat-timeline-pass').forEach(el => el.remove());

        const now = new Date();
        const dayStart = new Date(now);
        dayStart.setHours(0, 0, 0, 0);
        const dayMs = 24 * 60 * 60 * 1000;

        passList.forEach((pass, idx) => {
            const start = new Date(pass.startTimeISO);
            const end = new Date(pass.endTimeISO);

            const startPct = Math.max(0, Math.min(100, ((start - dayStart) / dayMs) * 100));
            const endPct = Math.max(0, Math.min(100, ((end - dayStart) / dayMs) * 100));
            const widthPct = Math.max(0.5, endPct - startPct);

            const marker = document.createElement('div');
            marker.className = `wxsat-timeline-pass ${pass.mode === 'LRPT' ? 'lrpt' : 'apt'}`;
            marker.style.left = startPct + '%';
            marker.style.width = widthPct + '%';
            marker.title = `${pass.name} ${pass.startTime} (${pass.maxEl}\u00b0)`;
            marker.onclick = () => selectPass(idx);
            track.appendChild(marker);
        });

        // Update cursor position
        updateTimelineCursor();
    }

    /**
     * Update timeline cursor to current time
     */
    function updateTimelineCursor() {
        const cursor = document.getElementById('wxsatTimelineCursor');
        if (!cursor) return;

        const now = new Date();
        const dayStart = new Date(now);
        dayStart.setHours(0, 0, 0, 0);
        const pct = ((now - dayStart) / (24 * 60 * 60 * 1000)) * 100;
        cursor.style.left = pct + '%';
    }

    // ========================
    // Auto-Scheduler
    // ========================

    /**
     * Toggle auto-scheduler
     */
    async function toggleScheduler() {
        const stripCheckbox = document.getElementById('wxsatAutoSchedule');
        const sidebarCheckbox = document.getElementById('wxsatSidebarAutoSchedule');
        const checked = stripCheckbox?.checked || sidebarCheckbox?.checked;

        // Sync both checkboxes
        if (stripCheckbox) stripCheckbox.checked = checked;
        if (sidebarCheckbox) sidebarCheckbox.checked = checked;

        if (checked) {
            await enableScheduler();
        } else {
            await disableScheduler();
        }
    }

    /**
     * Enable auto-scheduler
     */
    async function enableScheduler() {
        const lat = parseFloat(localStorage.getItem('observerLat'));
        const lon = parseFloat(localStorage.getItem('observerLon'));

        if (isNaN(lat) || isNaN(lon)) {
            showNotification('Weather Sat', 'Set observer location first');
            const stripCheckbox = document.getElementById('wxsatAutoSchedule');
            const sidebarCheckbox = document.getElementById('wxsatSidebarAutoSchedule');
            if (stripCheckbox) stripCheckbox.checked = false;
            if (sidebarCheckbox) sidebarCheckbox.checked = false;
            return;
        }

        const deviceSelect = document.getElementById('deviceSelect');
        const gainInput = document.getElementById('weatherSatGain');
        const biasTInput = document.getElementById('weatherSatBiasT');

        try {
            const response = await fetch('/weather-sat/schedule/enable', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    latitude: lat,
                    longitude: lon,
                    device: parseInt(deviceSelect?.value || '0', 10),
                    gain: parseFloat(gainInput?.value || '40'),
                    bias_t: biasTInput?.checked || false,
                }),
            });

            const data = await response.json();
            schedulerEnabled = true;
            updateSchedulerUI(data);
            startStream();
            showNotification('Weather Sat', `Auto-scheduler enabled (${data.scheduled_count || 0} passes)`);
        } catch (err) {
            console.error('Failed to enable scheduler:', err);
            showNotification('Weather Sat', 'Failed to enable auto-scheduler');
        }
    }

    /**
     * Disable auto-scheduler
     */
    async function disableScheduler() {
        try {
            await fetch('/weather-sat/schedule/disable', { method: 'POST' });
            schedulerEnabled = false;
            updateSchedulerUI({ enabled: false });
            if (!isRunning) stopStream();
            showNotification('Weather Sat', 'Auto-scheduler disabled');
        } catch (err) {
            console.error('Failed to disable scheduler:', err);
        }
    }

    /**
     * Check current scheduler status
     */
    async function checkSchedulerStatus() {
        try {
            const response = await fetch('/weather-sat/schedule/status');
            const data = await response.json();
            schedulerEnabled = data.enabled;
            updateSchedulerUI(data);
            if (schedulerEnabled) startStream();
        } catch (err) {
            // Scheduler endpoint may not exist yet
        }
    }

    /**
     * Update scheduler UI elements
     */
    function updateSchedulerUI(data) {
        const stripCheckbox = document.getElementById('wxsatAutoSchedule');
        const sidebarCheckbox = document.getElementById('wxsatSidebarAutoSchedule');
        const statusEl = document.getElementById('wxsatSchedulerStatus');

        if (stripCheckbox) stripCheckbox.checked = data.enabled;
        if (sidebarCheckbox) sidebarCheckbox.checked = data.enabled;
        if (statusEl) {
            if (data.enabled) {
                statusEl.textContent = `Active: ${data.scheduled_count || 0} passes queued`;
                statusEl.style.color = '#00ff88';
            } else {
                statusEl.textContent = 'Disabled';
                statusEl.style.color = '';
            }
        }
    }

    // ========================
    // Images
    // ========================

    /**
     * Load decoded images
     */
    async function loadImages() {
        try {
            const response = await fetch('/weather-sat/images');
            const data = await response.json();

            if (data.status === 'ok') {
                images = data.images || [];
                updateImageCount(images.length);
                renderGallery();
            }
        } catch (err) {
            console.error('Failed to load weather sat images:', err);
        }
    }

    /**
     * Update image count
     */
    function updateImageCount(count) {
        const countEl = document.getElementById('wxsatImageCount');
        const stripCount = document.getElementById('wxsatStripImageCount');
        if (countEl) countEl.textContent = count;
        if (stripCount) stripCount.textContent = count;
    }

    /**
     * Render image gallery grouped by date
     */
    function renderGallery() {
        const gallery = document.getElementById('wxsatGallery');
        if (!gallery) return;

        if (images.length === 0) {
            gallery.innerHTML = `
                <div class="wxsat-gallery-empty">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <circle cx="12" cy="12" r="10"/>
                        <path d="M2 12h20"/>
                        <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                    </svg>
                    <p>No images decoded yet</p>
                    <p style="margin-top: 4px; font-size: 11px;">Select a satellite pass and start capturing</p>
                </div>
            `;
            return;
        }

        // Sort by timestamp descending
        const sorted = [...images].sort((a, b) => {
            return new Date(b.timestamp || 0) - new Date(a.timestamp || 0);
        });

        // Group by date
        const groups = {};
        sorted.forEach(img => {
            const dateKey = img.timestamp
                ? new Date(img.timestamp).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
                : 'Unknown Date';
            if (!groups[dateKey]) groups[dateKey] = [];
            groups[dateKey].push(img);
        });

        let html = '';
        for (const [date, imgs] of Object.entries(groups)) {
            html += `<div class="wxsat-date-header">${escapeHtml(date)}</div>`;
            html += imgs.map(img => {
                const fn = escapeHtml(img.filename || img.url.split('/').pop());
                return `
                <div class="wxsat-image-card">
                    <div class="wxsat-image-clickable" onclick="WeatherSat.showImage('${escapeHtml(img.url)}', '${escapeHtml(img.satellite)}', '${escapeHtml(img.product)}', '${fn}')">
                        <img src="${escapeHtml(img.url)}" alt="${escapeHtml(img.satellite)} ${escapeHtml(img.product)}" class="wxsat-image-preview" loading="lazy">
                        <div class="wxsat-image-info">
                            <div class="wxsat-image-sat">${escapeHtml(img.satellite)}</div>
                            <div class="wxsat-image-product">${escapeHtml(img.product || img.mode)}</div>
                            <div class="wxsat-image-timestamp">${formatTimestamp(img.timestamp)}</div>
                        </div>
                    </div>
                    <div class="wxsat-image-actions">
                        <button onclick="event.stopPropagation(); WeatherSat.deleteImage('${fn}')" title="Delete image">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                            </svg>
                        </button>
                    </div>
                </div>`;
            }).join('');
        }

        gallery.innerHTML = html;
    }

    /**
     * Show full-size image
     */
    function showImage(url, satellite, product, filename) {
        currentModalFilename = filename || null;

        let modal = document.getElementById('wxsatImageModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'wxsatImageModal';
            modal.className = 'wxsat-image-modal';
            modal.innerHTML = `
                <div class="wxsat-modal-toolbar">
                    <button class="wxsat-modal-btn delete" onclick="WeatherSat.deleteImage(WeatherSat._getModalFilename())" title="Delete image">
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                        </svg>
                    </button>
                </div>
                <button class="wxsat-modal-close" onclick="WeatherSat.closeImage()">&times;</button>
                <img src="" alt="Weather Satellite Image">
                <div class="wxsat-modal-info"></div>
            `;
            modal.addEventListener('click', (e) => {
                if (e.target === modal) closeImage();
            });
            document.body.appendChild(modal);
        }

        modal.querySelector('img').src = url;
        const info = modal.querySelector('.wxsat-modal-info');
        if (info) {
            info.textContent = `${satellite || ''} ${product ? '// ' + product : ''}`;
        }
        modal.classList.add('show');
    }

    /**
     * Close image modal
     */
    function closeImage() {
        const modal = document.getElementById('wxsatImageModal');
        if (modal) modal.classList.remove('show');
    }

    /**
     * Delete a single image
     */
    async function deleteImage(filename) {
        if (!filename) return;
        if (!confirm(`Delete this image?`)) return;

        try {
            const response = await fetch(`/weather-sat/images/${encodeURIComponent(filename)}`, { method: 'DELETE' });
            const data = await response.json();

            if (data.status === 'deleted') {
                images = images.filter(img => {
                    const imgFn = img.filename || img.url.split('/').pop();
                    return imgFn !== filename;
                });
                updateImageCount(images.length);
                renderGallery();
                closeImage();
            } else {
                showNotification('Weather Sat', data.message || 'Failed to delete image');
            }
        } catch (err) {
            console.error('Failed to delete image:', err);
            showNotification('Weather Sat', 'Failed to delete image');
        }
    }

    /**
     * Delete all images
     */
    async function deleteAllImages() {
        if (images.length === 0) return;
        if (!confirm(`Delete all ${images.length} decoded images?`)) return;

        try {
            const response = await fetch('/weather-sat/images', { method: 'DELETE' });
            const data = await response.json();

            if (data.status === 'ok') {
                images = [];
                updateImageCount(0);
                renderGallery();
                showNotification('Weather Sat', `Deleted ${data.deleted} images`);
            } else {
                showNotification('Weather Sat', 'Failed to delete images');
            }
        } catch (err) {
            console.error('Failed to delete all images:', err);
            showNotification('Weather Sat', 'Failed to delete images');
        }
    }

    /**
     * Format timestamp
     */
    function formatTimestamp(isoString) {
        if (!isoString) return '--';
        try {
            return new Date(isoString).toLocaleString();
        } catch {
            return isoString;
        }
    }

    /**
     * Escape HTML
     */
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Invalidate ground map size (call after container becomes visible)
     */
    function invalidateMap() {
        if (groundMap) {
            setTimeout(() => groundMap.invalidateSize(), 100);
        }
    }

    // ========================
    // Decoder Console
    // ========================

    /**
     * Add an entry to the decoder console log
     */
    function addConsoleEntry(message, logType) {
        const log = document.getElementById('wxsatConsoleLog');
        if (!log) return;

        const entry = document.createElement('div');
        entry.className = `wxsat-console-entry wxsat-log-${logType || 'info'}`;
        entry.textContent = message;
        log.appendChild(entry);

        consoleEntries.push(entry);

        // Cap at 200 entries
        while (consoleEntries.length > 200) {
            const old = consoleEntries.shift();
            if (old.parentNode) old.parentNode.removeChild(old);
        }

        // Auto-scroll to bottom
        log.scrollTop = log.scrollHeight;
    }

    /**
     * Update the phase indicator steps
     */
    function updatePhaseIndicator(phase) {
        if (!phase || phase === currentPhase) return;
        currentPhase = phase;

        const phases = ['tuning', 'listening', 'signal_detected', 'decoding', 'complete'];
        const phaseIndex = phases.indexOf(phase);
        const isError = phase === 'error';

        document.querySelectorAll('#wxsatPhaseIndicator .wxsat-phase-step').forEach(step => {
            const stepPhase = step.dataset.phase;
            const stepIndex = phases.indexOf(stepPhase);

            step.classList.remove('active', 'completed', 'error');

            if (isError) {
                if (stepPhase === currentPhase || stepIndex === phaseIndex) {
                    step.classList.add('error');
                }
            } else if (stepIndex === phaseIndex) {
                step.classList.add('active');
            } else if (stepIndex < phaseIndex && phaseIndex >= 0) {
                step.classList.add('completed');
            }
        });
    }

    /**
     * Show or hide the decoder console
     */
    function showConsole(visible) {
        const el = document.getElementById('wxsatSignalConsole');
        if (el) el.classList.toggle('active', visible);

        if (consoleAutoHideTimer) {
            clearTimeout(consoleAutoHideTimer);
            consoleAutoHideTimer = null;
        }
    }

    /**
     * Toggle console body collapsed state
     */
    function toggleConsole() {
        const body = document.getElementById('wxsatConsoleBody');
        const btn = document.getElementById('wxsatConsoleToggle');
        if (!body) return;

        consoleCollapsed = !consoleCollapsed;
        body.classList.toggle('collapsed', consoleCollapsed);
        if (btn) btn.classList.toggle('collapsed', consoleCollapsed);
    }

    /**
     * Clear console entries and reset phase indicator
     */
    function clearConsole() {
        const log = document.getElementById('wxsatConsoleLog');
        if (log) log.innerHTML = '';
        consoleEntries = [];
        currentPhase = 'idle';

        document.querySelectorAll('#wxsatPhaseIndicator .wxsat-phase-step').forEach(step => {
            step.classList.remove('active', 'completed', 'error');
        });

        if (consoleAutoHideTimer) {
            clearTimeout(consoleAutoHideTimer);
            consoleAutoHideTimer = null;
        }
    }

    // Public API
    return {
        init,
        start,
        stop,
        startPass,
        selectPass,
        testDecode,
        loadImages,
        loadPasses,
        showImage,
        closeImage,
        deleteImage,
        deleteAllImages,
        useGPS,
        toggleScheduler,
        invalidateMap,
        toggleConsole,
        _getModalFilename: () => currentModalFilename,
    };
})();

document.addEventListener('DOMContentLoaded', function() {
    // Initialization happens via selectMode when weather-satellite mode is activated
});
