/**
 * SSTV Mode
 * ISS Slow-Scan Television decoder interface
 */

const SSTV = (function() {
    // State
    let isRunning = false;
    let eventSource = null;
    let images = [];
    let currentMode = null;
    let progress = 0;
    let issMap = null;
    let issMarker = null;
    let issTrackLine = null;
    let issPosition = null;
    let issUpdateInterval = null;

    // ISS frequency
    const ISS_FREQ = 145.800;

    /**
     * Initialize the SSTV mode
     */
    function init() {
        checkStatus();
        loadImages();
        loadLocationInputs();
        loadIssSchedule();
        initMap();
        startIssTracking();
    }

    /**
     * Load location into input fields
     */
    function loadLocationInputs() {
        const latInput = document.getElementById('sstvObsLat');
        const lonInput = document.getElementById('sstvObsLon');

        const storedLat = localStorage.getItem('observerLat');
        const storedLon = localStorage.getItem('observerLon');

        if (latInput && storedLat) latInput.value = storedLat;
        if (lonInput && storedLon) lonInput.value = storedLon;

        // Add change handlers to save and refresh
        if (latInput) latInput.addEventListener('change', saveLocationFromInputs);
        if (lonInput) lonInput.addEventListener('change', saveLocationFromInputs);
    }

    /**
     * Save location from input fields
     */
    function saveLocationFromInputs() {
        const latInput = document.getElementById('sstvObsLat');
        const lonInput = document.getElementById('sstvObsLon');

        const lat = parseFloat(latInput?.value);
        const lon = parseFloat(lonInput?.value);

        if (!isNaN(lat) && lat >= -90 && lat <= 90 &&
            !isNaN(lon) && lon >= -180 && lon <= 180) {
            localStorage.setItem('observerLat', lat.toString());
            localStorage.setItem('observerLon', lon.toString());
            loadIssSchedule(); // Refresh pass predictions
        }
    }

    /**
     * Use GPS to get location
     */
    function useGPS(btn) {
        if (!navigator.geolocation) {
            showNotification('SSTV', 'GPS not available in this browser');
            return;
        }

        const originalText = btn.innerHTML;
        btn.innerHTML = '<span style="opacity: 0.7;">...</span>';
        btn.disabled = true;

        navigator.geolocation.getCurrentPosition(
            (pos) => {
                const latInput = document.getElementById('sstvObsLat');
                const lonInput = document.getElementById('sstvObsLon');

                const lat = pos.coords.latitude.toFixed(4);
                const lon = pos.coords.longitude.toFixed(4);

                if (latInput) latInput.value = lat;
                if (lonInput) lonInput.value = lon;

                localStorage.setItem('observerLat', lat);
                localStorage.setItem('observerLon', lon);

                btn.innerHTML = originalText;
                btn.disabled = false;

                showNotification('SSTV', 'Location updated from GPS');
                loadIssSchedule();
            },
            (err) => {
                btn.innerHTML = originalText;
                btn.disabled = false;

                let msg = 'Failed to get location';
                if (err.code === 1) msg = 'Location access denied';
                else if (err.code === 2) msg = 'Location unavailable';
                showNotification('SSTV', msg);
            },
            { enableHighAccuracy: true, timeout: 10000 }
        );
    }

    /**
     * Update TLE data from CelesTrak
     */
    async function updateTLE(btn) {
        const originalText = btn.innerHTML;
        btn.innerHTML = '<span style="opacity: 0.7;">Updating...</span>';
        btn.disabled = true;

        try {
            const response = await fetch('/satellite/update-tle', { method: 'POST' });
            const data = await response.json();

            if (data.status === 'success') {
                showNotification('SSTV', `TLE updated: ${data.updated?.length || 0} satellites`);
                loadIssSchedule(); // Refresh predictions with new TLE
            } else {
                showNotification('SSTV', data.message || 'TLE update failed');
            }
        } catch (err) {
            console.error('TLE update error:', err);
            showNotification('SSTV', 'Failed to update TLE');
        }

        btn.innerHTML = originalText;
        btn.disabled = false;
    }

    /**
     * Initialize Leaflet map for ISS tracking
     */
    function initMap() {
        const mapContainer = document.getElementById('sstvIssMap');
        if (!mapContainer || issMap) return;

        // Create map
        issMap = L.map('sstvIssMap', {
            center: [0, 0],
            zoom: 1,
            minZoom: 1,
            maxZoom: 6,
            zoomControl: true,
            attributionControl: false,
            worldCopyJump: true
        });

        // Add tile layer using settings manager if available
        if (typeof Settings !== 'undefined' && Settings.createTileLayer) {
            Settings.createTileLayer().addTo(issMap);
        } else {
            // Fallback to dark theme tiles
            L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
                maxZoom: 19
            }).addTo(issMap);
        }

        // Create ISS icon
        const issIcon = L.divIcon({
            className: 'sstv-iss-marker',
            html: `<div class="sstv-iss-dot"></div><div class="sstv-iss-label">ISS</div>`,
            iconSize: [40, 40],
            iconAnchor: [20, 20]
        });

        // Create ISS marker (will be positioned when we get data)
        issMarker = L.marker([0, 0], { icon: issIcon }).addTo(issMap);

        // Create ground track line
        issTrackLine = L.polyline([], {
            color: '#00d4ff',
            weight: 2,
            opacity: 0.6,
            dashArray: '5, 5'
        }).addTo(issMap);
    }

    /**
     * Start ISS position tracking
     */
    function startIssTracking() {
        updateIssPosition();
        // Update every 5 seconds
        if (issUpdateInterval) clearInterval(issUpdateInterval);
        issUpdateInterval = setInterval(updateIssPosition, 5000);
    }

    /**
     * Stop ISS tracking
     */
    function stopIssTracking() {
        if (issUpdateInterval) {
            clearInterval(issUpdateInterval);
            issUpdateInterval = null;
        }
    }

    /**
     * Fetch current ISS position
     */
    async function updateIssPosition() {
        const storedLat = localStorage.getItem('observerLat') || '51.5074';
        const storedLon = localStorage.getItem('observerLon') || '-0.1278';

        try {
            const url = `/sstv/iss-position?latitude=${storedLat}&longitude=${storedLon}`;
            const response = await fetch(url);
            const data = await response.json();

            if (data.status === 'ok') {
                issPosition = data;
                updateIssDisplay();
                updateMap();
                console.log('ISS position updated:', data.lat.toFixed(1), data.lon.toFixed(1));
            } else {
                console.warn('ISS position error:', data.message);
            }
        } catch (err) {
            console.error('Failed to get ISS position:', err);
        }
    }

    /**
     * Update ISS position display
     */
    function updateIssDisplay() {
        if (!issPosition) return;

        const latEl = document.getElementById('sstvIssLat');
        const lonEl = document.getElementById('sstvIssLon');
        const altEl = document.getElementById('sstvIssAlt');

        if (latEl) latEl.textContent = issPosition.lat.toFixed(1) + '°';
        if (lonEl) lonEl.textContent = issPosition.lon.toFixed(1) + '°';
        if (altEl) altEl.textContent = Math.round(issPosition.altitude);
    }

    /**
     * Update map with ISS position
     */
    function updateMap() {
        if (!issMap || !issPosition) return;

        const lat = issPosition.lat;
        const lon = issPosition.lon;

        // Update marker position
        if (issMarker) {
            issMarker.setLatLng([lat, lon]);
        }

        // Calculate and draw ground track
        if (issTrackLine) {
            const trackPoints = [];
            const inclination = 51.6; // ISS orbital inclination in degrees

            // Generate orbit track points
            for (let offset = -180; offset <= 180; offset += 3) {
                let trackLon = lon + offset;

                // Normalize longitude
                while (trackLon > 180) trackLon -= 360;
                while (trackLon < -180) trackLon += 360;

                // Calculate latitude based on orbital inclination
                const phase = (offset / 360) * 2 * Math.PI;
                const currentPhase = Math.asin(Math.max(-1, Math.min(1, lat / inclination)));
                let trackLat = inclination * Math.sin(phase + currentPhase);

                // Clamp to valid range
                trackLat = Math.max(-inclination, Math.min(inclination, trackLat));

                trackPoints.push([trackLat, trackLon]);
            }

            // Split track at antimeridian to avoid line across map
            const segments = [];
            let currentSegment = [];

            for (let i = 0; i < trackPoints.length; i++) {
                if (i > 0) {
                    const prevLon = trackPoints[i - 1][1];
                    const currLon = trackPoints[i][1];
                    if (Math.abs(currLon - prevLon) > 180) {
                        // Crossed antimeridian
                        if (currentSegment.length > 0) {
                            segments.push(currentSegment);
                        }
                        currentSegment = [];
                    }
                }
                currentSegment.push(trackPoints[i]);
            }
            if (currentSegment.length > 0) {
                segments.push(currentSegment);
            }

            // Use only the longest segment or combine if needed
            issTrackLine.setLatLngs(segments.length > 0 ? segments : []);
        }

        // Pan map to follow ISS
        issMap.panTo([lat, lon], { animate: true, duration: 0.5 });
    }

    /**
     * Check current decoder status
     */
    async function checkStatus() {
        try {
            const response = await fetch('/sstv/status');
            const data = await response.json();

            if (!data.available) {
                updateStatusUI('unavailable', 'Decoder not installed');
                showStatusMessage('SSTV decoder not available. Install slowrx: apt install slowrx', 'warning');
                return;
            }

            if (data.running) {
                isRunning = true;
                updateStatusUI('listening', 'Listening...');
                startStream();
            } else {
                updateStatusUI('idle', 'Idle');
            }

            // Update image count
            updateImageCount(data.image_count || 0);
        } catch (err) {
            console.error('Failed to check SSTV status:', err);
        }
    }

    /**
     * Start SSTV decoder
     */
    async function start() {
        const freqInput = document.getElementById('sstvFrequency');
        const deviceSelect = document.getElementById('sstvDevice');

        const frequency = parseFloat(freqInput?.value || ISS_FREQ);
        const device = parseInt(deviceSelect?.value || '0', 10);

        updateStatusUI('connecting', 'Starting...');

        try {
            const response = await fetch('/sstv/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ frequency, device })
            });

            const data = await response.json();

            if (data.status === 'started' || data.status === 'already_running') {
                isRunning = true;
                updateStatusUI('listening', `${frequency} MHz`);
                startStream();
                showNotification('SSTV', `Listening on ${frequency} MHz`);
            } else {
                updateStatusUI('idle', 'Start failed');
                showStatusMessage(data.message || 'Failed to start decoder', 'error');
            }
        } catch (err) {
            console.error('Failed to start SSTV:', err);
            updateStatusUI('idle', 'Error');
            showStatusMessage('Connection error: ' + err.message, 'error');
        }
    }

    /**
     * Stop SSTV decoder
     */
    async function stop() {
        try {
            await fetch('/sstv/stop', { method: 'POST' });
            isRunning = false;
            stopStream();
            updateStatusUI('idle', 'Stopped');
            showNotification('SSTV', 'Decoder stopped');
        } catch (err) {
            console.error('Failed to stop SSTV:', err);
        }
    }

    /**
     * Update status UI elements
     */
    function updateStatusUI(status, text) {
        const dot = document.getElementById('sstvStripDot');
        const statusText = document.getElementById('sstvStripStatus');
        const startBtn = document.getElementById('sstvStartBtn');
        const stopBtn = document.getElementById('sstvStopBtn');

        if (dot) {
            dot.className = 'sstv-strip-dot';
            if (status === 'listening' || status === 'detecting') {
                dot.classList.add('listening');
            } else if (status === 'decoding') {
                dot.classList.add('decoding');
            } else {
                dot.classList.add('idle');
            }
        }

        if (statusText) {
            statusText.textContent = text || status;
        }

        if (startBtn && stopBtn) {
            if (status === 'listening' || status === 'decoding') {
                startBtn.style.display = 'none';
                stopBtn.style.display = 'inline-block';
            } else {
                startBtn.style.display = 'inline-block';
                stopBtn.style.display = 'none';
            }
        }

        // Update live content area
        const liveContent = document.getElementById('sstvLiveContent');
        if (liveContent) {
            if (status === 'idle' || status === 'unavailable') {
                liveContent.innerHTML = renderIdleState();
            }
        }
    }

    /**
     * Render idle state HTML
     */
    function renderIdleState() {
        return `
            <div class="sstv-idle-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <rect x="3" y="3" width="18" height="18" rx="2"/>
                    <circle cx="12" cy="12" r="3"/>
                    <path d="M3 9h2M19 9h2M3 15h2M19 15h2"/>
                </svg>
                <h4>ISS SSTV Decoder</h4>
                <p>Click Start to listen for SSTV transmissions on 145.800 MHz</p>
            </div>
        `;
    }

    /**
     * Start SSE stream
     */
    function startStream() {
        if (eventSource) {
            eventSource.close();
        }

        eventSource = new EventSource('/sstv/stream');

        eventSource.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                if (data.type === 'sstv_progress') {
                    handleProgress(data);
                }
            } catch (err) {
                console.error('Failed to parse SSE message:', err);
            }
        };

        eventSource.onerror = () => {
            console.warn('SSTV SSE error, will reconnect...');
            setTimeout(() => {
                if (isRunning) startStream();
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
        currentMode = data.mode || currentMode;
        progress = data.progress || 0;

        // Update status based on decode state
        if (data.status === 'decoding') {
            updateStatusUI('decoding', `Decoding ${currentMode || 'image'}...`);
            renderDecodeProgress(data);
        } else if (data.status === 'complete' && data.image) {
            // New image decoded
            images.unshift(data.image);
            updateImageCount(images.length);
            renderGallery();
            showNotification('SSTV', 'New image decoded!');
            updateStatusUI('listening', 'Listening...');
        } else if (data.status === 'detecting') {
            updateStatusUI('listening', data.message || 'Listening...');
        }
    }

    /**
     * Render decode progress in live area
     */
    function renderDecodeProgress(data) {
        const liveContent = document.getElementById('sstvLiveContent');
        if (!liveContent) return;

        liveContent.innerHTML = `
            <div class="sstv-canvas-container">
                <canvas id="sstvCanvas" width="320" height="256"></canvas>
            </div>
            <div class="sstv-decode-info">
                <div class="sstv-mode-label">${data.mode || 'Detecting mode...'}</div>
                <div class="sstv-progress-bar">
                    <div class="progress" style="width: ${data.progress || 0}%"></div>
                </div>
                <div class="sstv-status-message">${data.message || 'Decoding...'}</div>
            </div>
        `;
    }

    /**
     * Load decoded images
     */
    async function loadImages() {
        try {
            const response = await fetch('/sstv/images');
            const data = await response.json();

            if (data.status === 'ok') {
                images = data.images || [];
                updateImageCount(images.length);
                renderGallery();
            }
        } catch (err) {
            console.error('Failed to load SSTV images:', err);
        }
    }

    /**
     * Update image count display
     */
    function updateImageCount(count) {
        const countEl = document.getElementById('sstvImageCount');
        const stripCount = document.getElementById('sstvStripImageCount');

        if (countEl) countEl.textContent = count;
        if (stripCount) stripCount.textContent = count;
    }

    /**
     * Render image gallery
     */
    function renderGallery() {
        const gallery = document.getElementById('sstvGallery');
        if (!gallery) return;

        if (images.length === 0) {
            gallery.innerHTML = `
                <div class="sstv-gallery-empty">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="3" y="3" width="18" height="18" rx="2"/>
                        <circle cx="8.5" cy="8.5" r="1.5"/>
                        <polyline points="21 15 16 10 5 21"/>
                    </svg>
                    <p>No images decoded yet</p>
                </div>
            `;
            return;
        }

        gallery.innerHTML = images.map(img => `
            <div class="sstv-image-card" onclick="SSTV.showImage('${escapeHtml(img.url)}')">
                <img src="${escapeHtml(img.url)}" alt="SSTV Image" class="sstv-image-preview" loading="lazy">
                <div class="sstv-image-info">
                    <div class="sstv-image-mode">${escapeHtml(img.mode || 'Unknown')}</div>
                    <div class="sstv-image-timestamp">${formatTimestamp(img.timestamp)}</div>
                </div>
            </div>
        `).join('');
    }

    /**
     * Load ISS pass schedule
     */
    async function loadIssSchedule() {
        // Try to get user's location from settings
        const storedLat = localStorage.getItem('observerLat');
        const storedLon = localStorage.getItem('observerLon');

        // Check if location is actually set
        const hasLocation = storedLat !== null && storedLon !== null;
        const lat = storedLat || 51.5074;
        const lon = storedLon || -0.1278;

        try {
            const response = await fetch(`/sstv/iss-schedule?latitude=${lat}&longitude=${lon}&hours=48`);
            const data = await response.json();

            if (data.status === 'ok' && data.passes && data.passes.length > 0) {
                renderIssInfo(data.passes[0], hasLocation);
            } else {
                renderIssInfo(null, hasLocation);
            }
        } catch (err) {
            console.error('Failed to load ISS schedule:', err);
            renderIssInfo(null, hasLocation);
        }
    }

    /**
     * Render ISS pass info
     */
    function renderIssInfo(nextPass, hasLocation = true) {
        const passEl = document.getElementById('sstvNextPass');
        if (!passEl) return;

        if (!nextPass) {
            passEl.textContent = hasLocation
                ? 'No passes in 48h'
                : 'Set location above';
            return;
        }

        passEl.textContent = `${nextPass.startTime} (${nextPass.maxEl}° el, ${nextPass.duration}min)`;
    }

    /**
     * Show full-size image in modal
     */
    function showImage(url) {
        let modal = document.getElementById('sstvImageModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'sstvImageModal';
            modal.className = 'sstv-image-modal';
            modal.innerHTML = `
                <button class="sstv-modal-close" onclick="SSTV.closeImage()">&times;</button>
                <img src="" alt="SSTV Image">
            `;
            modal.addEventListener('click', (e) => {
                if (e.target === modal) closeImage();
            });
            document.body.appendChild(modal);
        }

        modal.querySelector('img').src = url;
        modal.classList.add('show');
    }

    /**
     * Close image modal
     */
    function closeImage() {
        const modal = document.getElementById('sstvImageModal');
        if (modal) modal.classList.remove('show');
    }

    /**
     * Format timestamp for display
     */
    function formatTimestamp(isoString) {
        if (!isoString) return '--';
        try {
            const date = new Date(isoString);
            return date.toLocaleString();
        } catch {
            return isoString;
        }
    }

    /**
     * Escape HTML for safe display
     */
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Show status message
     */
    function showStatusMessage(message, type) {
        if (typeof showNotification === 'function') {
            showNotification('SSTV', message);
        } else {
            console.log(`[SSTV ${type}] ${message}`);
        }
    }

    // Public API
    return {
        init,
        start,
        stop,
        loadImages,
        loadIssSchedule,
        showImage,
        closeImage,
        useGPS,
        updateTLE,
        stopIssTracking
    };
})();

// Initialize when DOM is ready (will be called by selectMode)
document.addEventListener('DOMContentLoaded', function() {
    // Initialization happens via selectMode when SSTV mode is activated
});
