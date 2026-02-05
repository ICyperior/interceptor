/**
 * Weather Satellite Mode
 * NOAA APT and Meteor LRPT decoder interface
 */

const WeatherSat = (function() {
    // State
    let isRunning = false;
    let eventSource = null;
    let images = [];
    let passes = [];
    let currentSatellite = null;

    /**
     * Initialize the Weather Satellite mode
     */
    function init() {
        checkStatus();
        loadImages();
        loadLocationInputs();
        loadPasses();
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
                }
            } catch (err) {
                console.error('Failed to parse SSE:', err);
            }
        };

        eventSource.onerror = () => {
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
                stopStream();
                updateStatusUI('idle', 'Capture complete');
                if (captureStatus) captureStatus.classList.remove('active');
            }

        } else if (data.status === 'error') {
            updateStatusUI('idle', 'Error');
            showNotification('Weather Sat', data.message || 'Capture error');
            if (captureStatus) captureStatus.classList.remove('active');
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
     * Load pass predictions
     */
    async function loadPasses() {
        const storedLat = localStorage.getItem('observerLat');
        const storedLon = localStorage.getItem('observerLon');

        if (!storedLat || !storedLon) {
            renderPasses([]);
            return;
        }

        try {
            const url = `/weather-sat/passes?latitude=${storedLat}&longitude=${storedLon}&hours=24&min_elevation=15`;
            const response = await fetch(url);
            const data = await response.json();

            if (data.status === 'ok') {
                passes = data.passes || [];
                renderPasses(passes);
            }
        } catch (err) {
            console.error('Failed to load passes:', err);
        }
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
                <div class="wxsat-pass-card" onclick="WeatherSat.startPass('${escapeHtml(pass.satellite)}')">
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
                </div>
            `;
        }).join('');
    }

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
     * Render image gallery
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

        gallery.innerHTML = images.map(img => `
            <div class="wxsat-image-card" onclick="WeatherSat.showImage('${escapeHtml(img.url)}', '${escapeHtml(img.satellite)}', '${escapeHtml(img.product)}')">
                <img src="${escapeHtml(img.url)}" alt="${escapeHtml(img.satellite)} ${escapeHtml(img.product)}" class="wxsat-image-preview" loading="lazy">
                <div class="wxsat-image-info">
                    <div class="wxsat-image-sat">${escapeHtml(img.satellite)}</div>
                    <div class="wxsat-image-product">${escapeHtml(img.product || img.mode)}</div>
                    <div class="wxsat-image-timestamp">${formatTimestamp(img.timestamp)}</div>
                </div>
            </div>
        `).join('');
    }

    /**
     * Show full-size image
     */
    function showImage(url, satellite, product) {
        let modal = document.getElementById('wxsatImageModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'wxsatImageModal';
            modal.className = 'wxsat-image-modal';
            modal.innerHTML = `
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

    // Public API
    return {
        init,
        start,
        stop,
        startPass,
        loadImages,
        loadPasses,
        showImage,
        closeImage,
        useGPS,
    };
})();

document.addEventListener('DOMContentLoaded', function() {
    // Initialization happens via selectMode when weather-satellite mode is activated
});
