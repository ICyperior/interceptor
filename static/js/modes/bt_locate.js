/**
 * BT Locate — Bluetooth SAR Device Location Mode
 * GPS-tagged signal trail mapping with proximity audio alerts.
 */
const BtLocate = (function() {
    'use strict';

    let eventSource = null;
    let map = null;
    let mapMarkers = [];
    let trailLine = null;
    let rssiHistory = [];
    const MAX_RSSI_POINTS = 60;
    let chartCanvas = null;
    let chartCtx = null;
    let currentEnvironment = 'OUTDOOR';
    let audioCtx = null;
    let audioEnabled = false;
    let beepTimer = null;
    let initialized = false;
    let handoffData = null;
    let pollTimer = null;
    let durationTimer = null;
    let sessionStartedAt = null;
    let lastDetectionCount = 0;

    function init() {
        if (initialized) {
            // Re-invalidate map on re-entry and ensure tiles are present
            if (map) {
                setTimeout(() => {
                    map.invalidateSize();
                    // Re-apply user's tile layer if tiles were lost
                    let hasTiles = false;
                    map.eachLayer(layer => {
                        if (layer instanceof L.TileLayer) hasTiles = true;
                    });
                    if (!hasTiles && typeof Settings !== 'undefined' && Settings.createTileLayer) {
                        Settings.createTileLayer().addTo(map);
                    }
                }, 150);
            }
            checkStatus();
            return;
        }

        // Init map
        const mapEl = document.getElementById('btLocateMap');
        if (mapEl && typeof L !== 'undefined') {
            map = L.map('btLocateMap', {
                center: [0, 0],
                zoom: 2,
                zoomControl: true,
            });
            // Use tile provider from user settings
            if (typeof Settings !== 'undefined' && Settings.createTileLayer) {
                Settings.createTileLayer().addTo(map);
                Settings.registerMap(map);
            } else {
                L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
                    maxZoom: 19,
                    attribution: '&copy; OSM &copy; CARTO'
                }).addTo(map);
            }
            setTimeout(() => map.invalidateSize(), 100);
        }

        // Init RSSI chart canvas
        chartCanvas = document.getElementById('btLocateRssiChart');
        if (chartCanvas) {
            chartCtx = chartCanvas.getContext('2d');
        }

        checkStatus();
        initialized = true;
    }

    function checkStatus() {
        fetch('/bt_locate/status')
            .then(r => r.json())
            .then(data => {
                if (data.active) {
                    sessionStartedAt = data.started_at ? new Date(data.started_at).getTime() : Date.now();
                    showActiveUI();
                    updateScanStatus(data);
                    if (!eventSource) connectSSE();
                    // Restore trail from server
                    fetch('/bt_locate/trail')
                        .then(r => r.json())
                        .then(trail => {
                            if (trail.gps_trail) {
                                trail.gps_trail.forEach(p => addMapMarker(p));
                            }
                            updateStats(data.detection_count, data.gps_trail_count);
                        });
                }
            })
            .catch(() => {});
    }

    function start() {
        const mac = document.getElementById('btLocateMac')?.value.trim();
        const namePattern = document.getElementById('btLocateNamePattern')?.value.trim();
        const irk = document.getElementById('btLocateIrk')?.value.trim();

        const body = { environment: currentEnvironment };
        if (mac) body.mac_address = mac;
        if (namePattern) body.name_pattern = namePattern;
        if (irk) body.irk_hex = irk;
        if (handoffData?.device_id) body.device_id = handoffData.device_id;
        if (handoffData?.known_name) body.known_name = handoffData.known_name;
        if (handoffData?.known_manufacturer) body.known_manufacturer = handoffData.known_manufacturer;
        if (handoffData?.last_known_rssi) body.last_known_rssi = handoffData.last_known_rssi;

        // Include user location as fallback when GPS unavailable
        const userLat = localStorage.getItem('observerLat');
        const userLon = localStorage.getItem('observerLon');
        if (userLat && userLon) {
            body.fallback_lat = parseFloat(userLat);
            body.fallback_lon = parseFloat(userLon);
        }

        console.log('[BtLocate] Starting with body:', body);

        if (!body.mac_address && !body.name_pattern && !body.irk_hex && !body.device_id) {
            alert('Please provide at least a MAC address, name pattern, IRK, or use hand-off from Bluetooth mode.');
            return;
        }

        fetch('/bt_locate/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'started') {
                    sessionStartedAt = data.session?.started_at ? new Date(data.session.started_at).getTime() : Date.now();
                    showActiveUI();
                    connectSSE();
                    rssiHistory = [];
                    updateScanStatus(data.session);
                    // Restore any existing trail (e.g. from a stop/start cycle)
                    restoreTrail();
                }
            })
            .catch(err => console.error('[BtLocate] Start error:', err));
    }

    function stop() {
        fetch('/bt_locate/stop', { method: 'POST' })
            .then(r => r.json())
            .then(() => {
                showIdleUI();
                disconnectSSE();
                stopAudio();
            })
            .catch(err => console.error('[BtLocate] Stop error:', err));
    }

    function showActiveUI() {
        const startBtn = document.getElementById('btLocateStartBtn');
        const stopBtn = document.getElementById('btLocateStopBtn');
        if (startBtn) startBtn.style.display = 'none';
        if (stopBtn) stopBtn.style.display = 'inline-block';
        show('btLocateHud');
    }

    function showIdleUI() {
        const startBtn = document.getElementById('btLocateStartBtn');
        const stopBtn = document.getElementById('btLocateStopBtn');
        if (startBtn) startBtn.style.display = 'inline-block';
        if (stopBtn) stopBtn.style.display = 'none';
        hide('btLocateHud');
        hide('btLocateScanStatus');
    }

    function updateScanStatus(statusData) {
        const el = document.getElementById('btLocateScanStatus');
        const dot = document.getElementById('btLocateScanDot');
        const text = document.getElementById('btLocateScanText');
        if (!el) return;

        el.style.display = '';
        if (statusData && statusData.scanner_running) {
            if (dot) dot.style.background = '#22c55e';
            if (text) text.textContent = 'BT scanner active';
        } else {
            if (dot) dot.style.background = '#f97316';
            if (text) text.textContent = 'BT scanner not running — waiting...';
        }
    }

    function show(id) { const el = document.getElementById(id); if (el) el.style.display = ''; }
    function hide(id) { const el = document.getElementById(id); if (el) el.style.display = 'none'; }

    function connectSSE() {
        if (eventSource) eventSource.close();
        console.log('[BtLocate] Connecting SSE stream');
        eventSource = new EventSource('/bt_locate/stream');

        eventSource.addEventListener('detection', function(e) {
            try {
                const event = JSON.parse(e.data);
                console.log('[BtLocate] Detection event:', event);
                handleDetection(event);
            } catch (err) {
                console.error('[BtLocate] Parse error:', err);
            }
        });

        eventSource.addEventListener('session_ended', function() {
            showIdleUI();
            disconnectSSE();
        });

        eventSource.onerror = function() {
            console.warn('[BtLocate] SSE error, polling fallback active');
        };

        // Start polling fallback (catches data even if SSE fails)
        startPolling();
    }

    function disconnectSSE() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
        stopPolling();
    }

    function startPolling() {
        stopPolling();
        lastDetectionCount = 0;
        pollTimer = setInterval(pollStatus, 3000);
        startDurationTimer();
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
        stopDurationTimer();
    }

    function startDurationTimer() {
        stopDurationTimer();
        durationTimer = setInterval(updateDuration, 1000);
    }

    function stopDurationTimer() {
        if (durationTimer) {
            clearInterval(durationTimer);
            durationTimer = null;
        }
    }

    function updateDuration() {
        if (!sessionStartedAt) return;
        const elapsed = Math.round((Date.now() - sessionStartedAt) / 1000);
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        const timeEl = document.getElementById('btLocateSessionTime');
        if (timeEl) timeEl.textContent = mins + ':' + String(secs).padStart(2, '0');
    }

    function pollStatus() {
        fetch('/bt_locate/status')
            .then(r => r.json())
            .then(data => {
                if (!data.active) {
                    showIdleUI();
                    disconnectSSE();
                    return;
                }

                updateScanStatus(data);
                updateHudInfo(data);

                // Show diagnostics
                const diagEl = document.getElementById('btLocateDiag');
                if (diagEl) {
                    let diag = 'Polls: ' + (data.poll_count || 0) +
                        (data.poll_thread_alive === false ? ' DEAD' : '') +
                        ' | Scan: ' + (data.scanner_running ? 'Y' : 'N') +
                        ' | Devices: ' + (data.scanner_device_count || 0) +
                        ' | Det: ' + (data.detection_count || 0);
                    // Show debug device sample if no detections
                    if (data.detection_count === 0 && data.debug_devices && data.debug_devices.length > 0) {
                        const matched = data.debug_devices.filter(d => d.match);
                        const sample = data.debug_devices.slice(0, 3).map(d =>
                            (d.name || '?') + '|' + (d.id || '').substring(0, 12) + ':' + (d.match ? 'Y' : 'N')
                        ).join(', ');
                        diag += ' | Match:' + matched.length + '/' + data.debug_devices.length + ' [' + sample + ']';
                    }
                    diagEl.textContent = diag;
                }

                // If detection count increased, fetch new trail points
                if (data.detection_count > lastDetectionCount) {
                    lastDetectionCount = data.detection_count;
                    fetch('/bt_locate/trail')
                        .then(r => r.json())
                        .then(trail => {
                            if (trail.trail && trail.trail.length > 0) {
                                const latest = trail.trail[trail.trail.length - 1];
                                handleDetection({ data: latest });
                            }
                            updateStats(data.detection_count, data.gps_trail_count);
                        });
                }
            })
            .catch(() => {});
    }

    function updateHudInfo(data) {
        // Target info
        const targetEl = document.getElementById('btLocateTargetInfo');
        if (targetEl && data.target) {
            const t = data.target;
            const name = t.known_name || t.name_pattern || '';
            const addr = t.mac_address || t.device_id || '';
            const addrDisplay = formatAddr(addr);
            targetEl.textContent = name ? (name + (addrDisplay ? ' (' + addrDisplay + ')' : '')) : addrDisplay || '--';
        }

        // Environment info
        const envEl = document.getElementById('btLocateEnvInfo');
        if (envEl) {
            const envNames = { FREE_SPACE: 'Open Field', OUTDOOR: 'Outdoor', INDOOR: 'Indoor', CUSTOM: 'Custom' };
            envEl.textContent = (envNames[data.environment] || data.environment) + ' n=' + (data.path_loss_exponent || '?');
        }

        // GPS status
        const gpsEl = document.getElementById('btLocateGpsStatus');
        if (gpsEl) {
            const src = data.gps_source || 'none';
            if (src === 'live') gpsEl.textContent = 'GPS: Live';
            else if (src === 'manual') gpsEl.textContent = 'GPS: Manual';
            else gpsEl.textContent = 'GPS: None';
        }

        // Last seen
        const lastEl = document.getElementById('btLocateLastSeen');
        if (lastEl) {
            if (data.last_detection) {
                const ago = Math.round((Date.now() - new Date(data.last_detection).getTime()) / 1000);
                lastEl.textContent = 'Last: ' + (ago < 60 ? ago + 's ago' : Math.floor(ago / 60) + 'm ago');
            } else {
                lastEl.textContent = 'Last: --';
            }
        }

        // Session start time (duration handled by 1s timer)
        if (data.started_at && !sessionStartedAt) {
            sessionStartedAt = new Date(data.started_at).getTime();
        }
    }

    function handleDetection(event) {
        const d = event.data;
        if (!d) return;

        // Update proximity UI
        const bandEl = document.getElementById('btLocateBand');
        const distEl = document.getElementById('btLocateDistance');
        const rssiEl = document.getElementById('btLocateRssi');
        const rssiEmaEl = document.getElementById('btLocateRssiEma');

        if (bandEl) {
            bandEl.textContent = d.proximity_band;
            bandEl.className = 'btl-hud-band ' + d.proximity_band.toLowerCase();
        }
        if (distEl) distEl.textContent = d.estimated_distance.toFixed(1);
        if (rssiEl) rssiEl.textContent = d.rssi;
        if (rssiEmaEl) rssiEmaEl.textContent = d.rssi_ema.toFixed(1);

        // RSSI sparkline
        rssiHistory.push(d.rssi);
        if (rssiHistory.length > MAX_RSSI_POINTS) rssiHistory.shift();
        drawRssiChart();

        // Map marker
        if (d.lat != null && d.lon != null) {
            addMapMarker(d);
        }

        // Update stats
        const detCountEl = document.getElementById('btLocateDetectionCount');
        const gpsCountEl = document.getElementById('btLocateGpsCount');
        if (detCountEl) {
            const cur = parseInt(detCountEl.textContent) || 0;
            detCountEl.textContent = cur + 1;
        }
        if (gpsCountEl && d.lat != null) {
            const cur = parseInt(gpsCountEl.textContent) || 0;
            gpsCountEl.textContent = cur + 1;
        }

        // Audio
        if (audioEnabled) playProximityTone(d.rssi);
    }

    function updateStats(detections, gpsPoints) {
        const detCountEl = document.getElementById('btLocateDetectionCount');
        const gpsCountEl = document.getElementById('btLocateGpsCount');
        if (detCountEl) detCountEl.textContent = detections || 0;
        if (gpsCountEl) gpsCountEl.textContent = gpsPoints || 0;
    }

    function addMapMarker(point) {
        if (!map || point.lat == null || point.lon == null) return;

        const band = (point.proximity_band || 'FAR').toLowerCase();
        const colors = { immediate: '#ef4444', near: '#f97316', far: '#eab308' };
        const sizes = { immediate: 8, near: 6, far: 5 };
        const color = colors[band] || '#eab308';
        const radius = sizes[band] || 5;

        const marker = L.circleMarker([point.lat, point.lon], {
            radius: radius,
            fillColor: color,
            color: '#fff',
            weight: 1,
            opacity: 0.9,
            fillOpacity: 0.8,
        }).addTo(map);

        marker.bindPopup(
            '<div style="font-family:monospace;font-size:11px;">' +
            '<b>' + point.proximity_band + '</b><br>' +
            'RSSI: ' + point.rssi + ' dBm<br>' +
            'Distance: ~' + point.estimated_distance.toFixed(1) + ' m<br>' +
            'Time: ' + new Date(point.timestamp).toLocaleTimeString() +
            '</div>'
        );

        mapMarkers.push(marker);
        map.panTo([point.lat, point.lon]);

        // Update trail line
        const latlngs = mapMarkers.map(m => m.getLatLng());
        if (trailLine) {
            trailLine.setLatLngs(latlngs);
        } else if (latlngs.length >= 2) {
            trailLine = L.polyline(latlngs, {
                color: 'rgba(0,255,136,0.5)',
                weight: 2,
                dashArray: '4 4',
            }).addTo(map);
        }
    }

    function restoreTrail() {
        fetch('/bt_locate/trail')
            .then(r => r.json())
            .then(trail => {
                if (trail.gps_trail && trail.gps_trail.length > 0) {
                    clearMapMarkers();
                    trail.gps_trail.forEach(p => addMapMarker(p));
                }
                if (trail.trail && trail.trail.length > 0) {
                    // Restore RSSI history from trail
                    rssiHistory = trail.trail.map(p => p.rssi).slice(-MAX_RSSI_POINTS);
                    drawRssiChart();
                    // Update HUD with latest detection
                    const latest = trail.trail[trail.trail.length - 1];
                    handleDetection({ data: latest });
                }
            })
            .catch(() => {});
    }

    function clearMapMarkers() {
        mapMarkers.forEach(m => map?.removeLayer(m));
        mapMarkers = [];
        if (trailLine) {
            map?.removeLayer(trailLine);
            trailLine = null;
        }
    }

    function drawRssiChart() {
        if (!chartCtx || !chartCanvas) return;

        const w = chartCanvas.width = chartCanvas.parentElement.clientWidth - 16;
        const h = chartCanvas.height = chartCanvas.parentElement.clientHeight - 24;
        chartCtx.clearRect(0, 0, w, h);

        if (rssiHistory.length < 2) return;

        // RSSI range: -100 to -20
        const minR = -100, maxR = -20;
        const range = maxR - minR;

        // Grid lines
        chartCtx.strokeStyle = 'rgba(255,255,255,0.05)';
        chartCtx.lineWidth = 1;
        [-30, -50, -70, -90].forEach(v => {
            const y = h - ((v - minR) / range) * h;
            chartCtx.beginPath();
            chartCtx.moveTo(0, y);
            chartCtx.lineTo(w, y);
            chartCtx.stroke();
        });

        // Draw RSSI line
        const step = w / (MAX_RSSI_POINTS - 1);
        chartCtx.beginPath();
        chartCtx.strokeStyle = '#00ff88';
        chartCtx.lineWidth = 2;

        rssiHistory.forEach((rssi, i) => {
            const x = i * step;
            const y = h - ((rssi - minR) / range) * h;
            if (i === 0) chartCtx.moveTo(x, y);
            else chartCtx.lineTo(x, y);
        });
        chartCtx.stroke();

        // Fill under
        const lastIdx = rssiHistory.length - 1;
        chartCtx.lineTo(lastIdx * step, h);
        chartCtx.lineTo(0, h);
        chartCtx.closePath();
        chartCtx.fillStyle = 'rgba(0,255,136,0.08)';
        chartCtx.fill();
    }

    // Audio proximity tone (Web Audio API)
    function playTone(freq, duration) {
        if (!audioCtx || audioCtx.state !== 'running') return;
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.frequency.value = freq;
        osc.type = 'sine';
        gain.gain.value = 0.2;
        gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);
        osc.start();
        osc.stop(audioCtx.currentTime + duration);
    }

    function playProximityTone(rssi) {
        if (!audioCtx || audioCtx.state !== 'running') return;
        // Stronger signal = higher pitch and shorter beep
        const strength = Math.max(0, Math.min(1, (rssi + 100) / 70));
        const freq = 400 + strength * 800;  // 400-1200 Hz
        const duration = 0.06 + (1 - strength) * 0.12;
        playTone(freq, duration);
    }

    function toggleAudio() {
        const cb = document.getElementById('btLocateAudioEnable');
        audioEnabled = cb?.checked || false;
        if (audioEnabled) {
            // Create AudioContext on user gesture (required by browser policy)
            if (!audioCtx) {
                try {
                    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                } catch (e) {
                    console.error('[BtLocate] AudioContext creation failed:', e);
                    return;
                }
            }
            // Resume must happen within a user gesture handler
            const ctx = audioCtx;
            ctx.resume().then(() => {
                console.log('[BtLocate] AudioContext state:', ctx.state);
                // Confirmation beep so user knows audio is working
                playTone(600, 0.08);
            });
        } else {
            stopAudio();
        }
    }

    function stopAudio() {
        audioEnabled = false;
        const cb = document.getElementById('btLocateAudioEnable');
        if (cb) cb.checked = false;
    }

    function setEnvironment(env) {
        currentEnvironment = env;
        document.querySelectorAll('.btl-env-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.env === env);
        });
        // Push to running session if active
        fetch('/bt_locate/status').then(r => r.json()).then(data => {
            if (data.active) {
                fetch('/bt_locate/environment', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ environment: env }),
                }).then(r => r.json()).then(res => {
                    console.log('[BtLocate] Environment updated:', res);
                });
            }
        }).catch(() => {});
    }

    function isUuid(addr) {
        return addr && /^[0-9A-F]{8}-[0-9A-F]{4}-/i.test(addr);
    }

    function formatAddr(addr) {
        if (!addr) return '';
        if (isUuid(addr)) return addr.substring(0, 8) + '-...' + addr.slice(-4);
        return addr;
    }

    function handoff(deviceInfo) {
        console.log('[BtLocate] Handoff received:', deviceInfo);
        handoffData = deviceInfo;

        // Populate fields
        if (deviceInfo.mac_address) {
            const macInput = document.getElementById('btLocateMac');
            if (macInput) macInput.value = deviceInfo.mac_address;
        }

        // Show handoff card
        const card = document.getElementById('btLocateHandoffCard');
        const nameEl = document.getElementById('btLocateHandoffName');
        const metaEl = document.getElementById('btLocateHandoffMeta');
        if (card) card.style.display = '';
        if (nameEl) nameEl.textContent = deviceInfo.known_name || formatAddr(deviceInfo.mac_address) || 'Unknown';
        if (metaEl) {
            const parts = [];
            if (deviceInfo.mac_address) parts.push(formatAddr(deviceInfo.mac_address));
            if (deviceInfo.known_manufacturer) parts.push(deviceInfo.known_manufacturer);
            if (deviceInfo.last_known_rssi != null) parts.push(deviceInfo.last_known_rssi + ' dBm');
            metaEl.textContent = parts.join(' \u00b7 ');
        }

        // Auto-fill IRK if available from scanner
        if (deviceInfo.irk_hex) {
            const irkInput = document.getElementById('btLocateIrk');
            if (irkInput) irkInput.value = deviceInfo.irk_hex;
        }

        // Switch to bt_locate mode
        if (typeof switchMode === 'function') {
            switchMode('bt_locate');
        }
    }

    function clearHandoff() {
        handoffData = null;
        const card = document.getElementById('btLocateHandoffCard');
        if (card) card.style.display = 'none';
    }

    function fetchPairedIrks() {
        const picker = document.getElementById('btLocateIrkPicker');
        const status = document.getElementById('btLocateIrkPickerStatus');
        const list = document.getElementById('btLocateIrkPickerList');
        const btn = document.getElementById('btLocateDetectIrkBtn');
        if (!picker || !status || !list) return;

        // Toggle off if already visible
        if (picker.style.display !== 'none') {
            picker.style.display = 'none';
            return;
        }

        picker.style.display = '';
        list.innerHTML = '';
        status.textContent = 'Scanning paired devices...';
        status.style.display = '';
        if (btn) btn.disabled = true;

        fetch('/bt_locate/paired_irks')
            .then(r => r.json())
            .then(data => {
                if (btn) btn.disabled = false;
                const devices = data.devices || [];

                if (devices.length === 0) {
                    status.textContent = 'No paired devices with IRKs found';
                    return;
                }

                status.style.display = 'none';
                list.innerHTML = '';

                devices.forEach(dev => {
                    const item = document.createElement('div');
                    item.className = 'btl-irk-picker-item';
                    item.innerHTML =
                        '<div class="btl-irk-picker-name">' + (dev.name || 'Unknown Device') + '</div>' +
                        '<div class="btl-irk-picker-meta">' + dev.address + ' \u00b7 ' + (dev.address_type || '') + '</div>';
                    item.addEventListener('click', function() {
                        selectPairedIrk(dev);
                    });
                    list.appendChild(item);
                });
            })
            .catch(err => {
                if (btn) btn.disabled = false;
                console.error('[BtLocate] Failed to fetch paired IRKs:', err);
                status.textContent = 'Failed to read paired devices';
            });
    }

    function selectPairedIrk(dev) {
        const irkInput = document.getElementById('btLocateIrk');
        const nameInput = document.getElementById('btLocateNamePattern');
        const picker = document.getElementById('btLocateIrkPicker');

        if (irkInput) irkInput.value = dev.irk_hex;
        if (nameInput && dev.name && !nameInput.value) nameInput.value = dev.name;
        if (picker) picker.style.display = 'none';
    }

    function clearTrail() {
        fetch('/bt_locate/clear_trail', { method: 'POST' })
            .then(r => r.json())
            .then(() => {
                clearMapMarkers();
                rssiHistory = [];
                drawRssiChart();
                updateStats(0, 0);
            })
            .catch(err => console.error('[BtLocate] Clear trail error:', err));
    }

    function invalidateMap() {
        if (map) map.invalidateSize();
    }

    return {
        init,
        start,
        stop,
        handoff,
        clearHandoff,
        setEnvironment,
        toggleAudio,
        clearTrail,
        handleDetection,
        invalidateMap,
        fetchPairedIrks,
    };
})();
