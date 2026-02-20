/**
 * Drone Ops mode frontend.
 */
const DroneOps = (function () {
    'use strict';

    let initialized = false;
    let refreshTimer = null;
    let stream = null;
    let latestDetections = [];
    let latestTracks = [];
    let latestCorrelations = [];
    let correlationAccess = 'unknown';
    let correlationRefreshCount = 0;
    let map = null;
    let mapMarkers = null;
    let mapTracks = null;
    let mapHeat = null;
    let mapNeedsAutoFit = true;
    let lastCorrelationError = '';
    const DETECTION_START_WAIT_MS = 1500;
    const SOURCE_COLORS = {
        wifi: '#00d4ff',
        bluetooth: '#00ff88',
        rf: '#ff9f43',
        remote_id: '#f04dff',
    };

    function esc(value) {
        return String(value === undefined || value === null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function notify(message, isError = false) {
        if (typeof SignalCards !== 'undefined' && SignalCards.showToast) {
            SignalCards.showToast(message, isError ? 'error' : 'success');
            return;
        }
        if (typeof showNotification === 'function') {
            showNotification(isError ? 'Drone Ops Error' : 'Drone Ops', message);
            return;
        }
        if (isError) {
            console.error(message);
        } else {
            console.log(message);
        }
    }

    async function api(path, options = {}) {
        const response = await fetch(path, {
            headers: { 'Content-Type': 'application/json' },
            ...options,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.status === 'error') {
            const error = new Error(data.message || `Request failed (${response.status})`);
            error.status = response.status;
            throw error;
        }
        return data;
    }

    async function fetchJson(path, options = {}) {
        const response = await fetch(path, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.message || data.error || `Request failed (${response.status})`);
        }
        return data;
    }

    async function apiOptional(path, options = {}) {
        try {
            return await api(path, options);
        } catch (error) {
            return { __error: error };
        }
    }

    function confidenceClass(conf) {
        if (conf >= 0.8) return 'ok';
        if (conf >= 0.6) return 'warn';
        return 'bad';
    }

    function toNumber(value) {
        const n = Number(value);
        return Number.isFinite(n) ? n : null;
    }

    function hasCoords(lat, lon) {
        return lat !== null && lon !== null && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180;
    }

    function formatCoord(value) {
        const num = toNumber(value);
        return num === null ? '--' : num.toFixed(5);
    }

    function formatMetric(value, decimals = 1, suffix = '') {
        const num = toNumber(value);
        if (num === null) return '--';
        return `${num.toFixed(decimals)}${suffix}`;
    }

    function sourceColor(source) {
        return SOURCE_COLORS[String(source || '').toLowerCase()] || '#7f8ea3';
    }

    function defaultMapCenter() {
        if (typeof ObserverLocation !== 'undefined' && typeof ObserverLocation.getForModule === 'function') {
            const location = ObserverLocation.getForModule('droneops_observerLocation', { fallbackToLatLon: true });
            const lat = toNumber(location?.lat);
            const lon = toNumber(location?.lon);
            if (hasCoords(lat, lon)) return [lat, lon];
        }
        const fallbackLat = toNumber(window.INTERCEPT_DEFAULT_LAT);
        const fallbackLon = toNumber(window.INTERCEPT_DEFAULT_LON);
        if (hasCoords(fallbackLat, fallbackLon)) return [fallbackLat, fallbackLon];
        return [37.0902, -95.7129];
    }

    function sortedTracksByDetection() {
        const grouped = new Map();
        for (const raw of latestTracks) {
            const detectionId = Number(raw?.detection_id);
            if (!detectionId) continue;
            if (!grouped.has(detectionId)) grouped.set(detectionId, []);
            grouped.get(detectionId).push(raw);
        }
        for (const rows of grouped.values()) {
            rows.sort((a, b) => String(a?.timestamp || '').localeCompare(String(b?.timestamp || '')));
        }
        return grouped;
    }

    function detectionTelemetry(detection, tracksByDetection) {
        const rows = tracksByDetection.get(Number(detection?.id)) || [];
        const latestTrack = rows.length ? rows[rows.length - 1] : null;
        const remote = detection && typeof detection.remote_id === 'object' ? detection.remote_id : {};
        const lat = toNumber(latestTrack?.lat ?? remote.lat);
        const lon = toNumber(latestTrack?.lon ?? remote.lon);
        return {
            lat,
            lon,
            hasPosition: hasCoords(lat, lon),
            altitude_m: toNumber(latestTrack?.altitude_m ?? remote.altitude_m),
            speed_mps: toNumber(latestTrack?.speed_mps ?? remote.speed_mps),
            heading_deg: toNumber(latestTrack?.heading_deg ?? remote.heading_deg),
            quality: toNumber(latestTrack?.quality ?? remote.confidence ?? detection?.confidence),
            source: latestTrack?.source || detection?.source,
            timestamp: latestTrack?.timestamp || detection?.last_seen || '',
            uas_id: remote?.uas_id || null,
            operator_id: remote?.operator_id || null,
            trackRows: rows,
        };
    }

    function connectStream() {
        if (stream || !initialized) return;
        stream = new EventSource('/drone-ops/stream');

        const handler = (event) => {
            let payload = null;
            try {
                payload = JSON.parse(event.data);
            } catch (_) {
                return;
            }
            if (!payload || payload.type === 'keepalive') return;

            if (payload.type === 'detection') {
                refreshDetections();
                refreshTracks();
                refreshCorrelations();
                refreshStatus();
                return;
            }

            if (payload.type.startsWith('incident_')) {
                refreshIncidents();
                refreshStatus();
                return;
            }

            if (payload.type.startsWith('action_') || payload.type.startsWith('policy_')) {
                refreshActions();
                refreshStatus();
                return;
            }

            if (payload.type.startsWith('evidence_')) {
                refreshManifests();
                return;
            }
        };

        stream.onmessage = handler;
        stream.onerror = () => {
            if (stream) {
                stream.close();
                stream = null;
            }
            setTimeout(() => {
                if (initialized) connectStream();
            }, 2000);
        };
    }

    function disconnectStream() {
        if (stream) {
            stream.close();
            stream = null;
        }
    }

    function setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    function sleep(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    function isAgentMode() {
        return typeof currentAgent !== 'undefined' && currentAgent !== 'local';
    }

    function wifiRunning() {
        if (typeof WiFiMode !== 'undefined' && typeof WiFiMode.isScanning === 'function' && WiFiMode.isScanning()) {
            return true;
        }
        if (typeof isWifiRunning !== 'undefined' && isWifiRunning) {
            return true;
        }
        return false;
    }

    function bluetoothRunning() {
        if (typeof BluetoothMode !== 'undefined' && typeof BluetoothMode.isScanning === 'function' && BluetoothMode.isScanning()) {
            return true;
        }
        if (typeof isBtRunning !== 'undefined' && isBtRunning) {
            return true;
        }
        return false;
    }

    function updateSensorsState() {
        const active = [];
        if (wifiRunning()) active.push('WiFi');
        if (bluetoothRunning()) active.push('Bluetooth');
        setText('droneOpsSensorsState', active.length ? `Running (${active.join(' + ')})` : 'Idle');
    }

    function applySelectOptions(selectId, rows, autoLabel) {
        const select = document.getElementById(selectId);
        if (!select) return;
        const previous = String(select.value || '');
        const seen = new Set();
        const options = [`<option value="">${esc(autoLabel)}</option>`];

        for (const row of rows) {
            const value = String(row?.value || '').trim();
            if (!value || seen.has(value)) continue;
            seen.add(value);
            const label = String(row?.label || value);
            options.push(`<option value="${esc(value)}">${esc(label)}</option>`);
        }

        select.innerHTML = options.join('');
        if (previous && seen.has(previous)) {
            select.value = previous;
        }
    }

    function readExistingSelectOptions(selectId) {
        const select = document.getElementById(selectId);
        if (!select) return [];
        return Array.from(select.options || [])
            .map((opt) => ({
                value: String(opt?.value || '').trim(),
                label: String(opt?.textContent || opt?.label || '').trim(),
            }))
            .filter((opt) => opt.value);
    }

    function selectHasChoices(selectId) {
        return readExistingSelectOptions(selectId).length > 0;
    }

    function ensureSelectValue(select, value, label = '') {
        if (!select || !value) return;
        const target = String(value);
        const existing = Array.from(select.options || []).find((opt) => String(opt.value) === target);
        if (!existing) {
            const option = document.createElement('option');
            option.value = target;
            option.textContent = label || target;
            select.appendChild(option);
        }
        select.value = target;
    }

    async function fetchWifiSourceOptions() {
        if (isAgentMode()) {
            const agentId = typeof currentAgent !== 'undefined' ? currentAgent : null;
            if (!agentId || agentId === 'local') return [];
            const data = await fetchJson(`/controller/agents/${agentId}?refresh=true`);
            const rows = data?.agent?.interfaces?.wifi_interfaces || [];
            return rows.map((item) => {
                if (typeof item === 'string') {
                    return { value: item, label: item };
                }
                const value = String(item?.name || item?.id || '').trim();
                if (!value) return null;
                let label = String(item?.display_name || value);
                if (!item?.display_name && item?.type) label += ` (${item.type})`;
                if (item?.monitor_capable || item?.supports_monitor) label += ' [Monitor OK]';
                return { value, label };
            }).filter(Boolean);
        }

        let rows = [];
        try {
            const data = await fetchJson('/wifi/interfaces');
            rows = data?.interfaces || [];
        } catch (_) {
            rows = [];
        }

        const mapped = rows.map((item) => {
            const value = String(item?.name || item?.id || '').trim();
            if (!value) return null;
            let label = value;
            const details = [];
            if (item?.chipset) details.push(item.chipset);
            else if (item?.driver) details.push(item.driver);
            if (details.length) label += ` - ${details.join(' | ')}`;
            if (item?.type) label += ` (${item.type})`;
            if (item?.monitor_capable || item?.supports_monitor) label += ' [Monitor OK]';
            return { value, label };
        }).filter(Boolean);

        if (mapped.length) return mapped;

        if (typeof refreshWifiInterfaces === 'function') {
            try {
                await Promise.resolve(refreshWifiInterfaces());
                await sleep(250);
            } catch (_) {
                // Fall back to currently populated options.
            }
        }

        return readExistingSelectOptions('wifiInterfaceSelect');
    }

    async function fetchBluetoothSourceOptions() {
        if (isAgentMode()) {
            const agentId = typeof currentAgent !== 'undefined' ? currentAgent : null;
            if (!agentId || agentId === 'local') return [];
            const data = await fetchJson(`/controller/agents/${agentId}?refresh=true`);
            const rows = data?.agent?.interfaces?.bt_adapters || [];
            return rows.map((item) => {
                if (typeof item === 'string') {
                    return { value: item, label: item };
                }
                const value = String(item?.id || item?.name || '').trim();
                if (!value) return null;
                let label = item?.name && item.name !== value ? `${value} - ${item.name}` : value;
                if (item?.powered === false) label += ' [DOWN]';
                else if (item?.powered === true) label += ' [UP]';
                return { value, label };
            }).filter(Boolean);
        }

        let rows = [];
        try {
            const data = await fetchJson('/api/bluetooth/capabilities');
            rows = data?.adapters || [];
        } catch (_) {
            rows = [];
        }

        const mapped = rows.map((item) => {
            const value = String(item?.id || item?.name || '').trim();
            if (!value) return null;
            let label = item?.name && item.name !== value ? `${value} - ${item.name}` : value;
            if (item?.powered === false) label += ' [DOWN]';
            else if (item?.powered === true) label += ' [UP]';
            return { value, label };
        }).filter(Boolean);

        if (mapped.length) return mapped;

        if (typeof refreshBtInterfaces === 'function') {
            try {
                await Promise.resolve(refreshBtInterfaces());
                await sleep(250);
            } catch (_) {
                // Fall back to currently populated options.
            }
        }

        return readExistingSelectOptions('btAdapterSelect');
    }

    function applySelectedSourceToModeSelectors() {
        const wifiChosen = String(document.getElementById('droneOpsWifiInterfaceSelect')?.value || '').trim();
        if (wifiChosen) {
            const wifiSelect = document.getElementById('wifiInterfaceSelect');
            ensureSelectValue(wifiSelect, wifiChosen, wifiChosen);
            // Force fresh monitor-interface derivation for the selected adapter.
            if (typeof monitorInterface !== 'undefined' && monitorInterface && monitorInterface !== wifiChosen) {
                monitorInterface = null;
            }
        }

        const btChosen = String(document.getElementById('droneOpsBtAdapterSelect')?.value || '').trim();
        if (btChosen) {
            const btSelect = document.getElementById('btAdapterSelect');
            ensureSelectValue(btSelect, btChosen, btChosen);
        }
    }

    async function refreshDetectionSources(silent = false) {
        const wifiSelect = document.getElementById('droneOpsWifiInterfaceSelect');
        const btSelect = document.getElementById('droneOpsBtAdapterSelect');
        if (wifiSelect) wifiSelect.innerHTML = '<option value="">Loading WiFi sources...</option>';
        if (btSelect) btSelect.innerHTML = '<option value="">Loading Bluetooth sources...</option>';

        const [wifiResult, btResult] = await Promise.allSettled([
            fetchWifiSourceOptions(),
            fetchBluetoothSourceOptions(),
        ]);

        if (wifiResult.status === 'fulfilled') {
            applySelectOptions('droneOpsWifiInterfaceSelect', wifiResult.value, 'Auto WiFi source');
        } else {
            applySelectOptions('droneOpsWifiInterfaceSelect', [], 'Auto WiFi source');
            if (!silent) notify(`WiFi source refresh failed: ${wifiResult.reason?.message || 'unknown error'}`, true);
        }

        if (btResult.status === 'fulfilled') {
            applySelectOptions('droneOpsBtAdapterSelect', btResult.value, 'Auto Bluetooth source');
        } else {
            applySelectOptions('droneOpsBtAdapterSelect', [], 'Auto Bluetooth source');
            if (!silent) notify(`Bluetooth source refresh failed: ${btResult.reason?.message || 'unknown error'}`, true);
        }

        applySelectedSourceToModeSelectors();
        if (!silent && wifiResult.status === 'fulfilled' && btResult.status === 'fulfilled') {
            notify('Detection sources refreshed');
        }
    }

    function addFallbackMapLayer(targetMap) {
        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
            subdomains: 'abcd',
            maxZoom: 19,
            className: 'tile-layer-cyan',
        }).addTo(targetMap);
    }

    async function ensureMap() {
        if (map || typeof L === 'undefined') return;
        const mapEl = document.getElementById('droneOpsMap');
        if (!mapEl) return;

        map = L.map(mapEl, {
            center: defaultMapCenter(),
            zoom: 12,
            minZoom: 2,
            maxZoom: 19,
            zoomControl: true,
        });

        if (typeof Settings !== 'undefined' && typeof Settings.createTileLayer === 'function') {
            try {
                await Settings.init();
                Settings.createTileLayer().addTo(map);
                if (typeof Settings.registerMap === 'function') {
                    Settings.registerMap(map);
                }
            } catch (_) {
                addFallbackMapLayer(map);
            }
        } else {
            addFallbackMapLayer(map);
        }

        mapTracks = L.layerGroup().addTo(map);
        mapMarkers = L.layerGroup().addTo(map);
        if (typeof L.heatLayer === 'function') {
            mapHeat = L.heatLayer([], {
                radius: 18,
                blur: 16,
                minOpacity: 0.28,
                maxZoom: 18,
                gradient: {
                    0.2: '#00b7ff',
                    0.45: '#00ff88',
                    0.7: '#ffb400',
                    1.0: '#ff355e',
                },
            }).addTo(map);
        }
        setTimeout(() => {
            if (map) map.invalidateSize();
        }, 120);
    }

    function invalidateMap() {
        mapNeedsAutoFit = true;
        if (!map) {
            ensureMap();
            return;
        }
        [80, 240, 500].forEach((delay) => {
            setTimeout(() => {
                if (map) map.invalidateSize();
            }, delay);
        });
    }

    function renderMainSummary(detections, tracksByDetection, filteredTrackCount) {
        const sources = new Set();
        let telemetryCount = 0;

        for (const d of detections) {
            const source = String(d?.source || '').trim().toLowerCase();
            if (source) sources.add(source);
            const telemetry = detectionTelemetry(d, tracksByDetection);
            if (telemetry.hasPosition || telemetry.altitude_m !== null || telemetry.speed_mps !== null || telemetry.heading_deg !== null) {
                telemetryCount += 1;
            }
        }

        setText('droneOpsMainSummaryDetections', String(detections.length));
        setText('droneOpsMainSummarySources', String(sources.size));
        setText('droneOpsMainSummaryTracks', String(filteredTrackCount));
        setText('droneOpsMainSummaryTelemetry', String(telemetryCount));
        if (correlationAccess === 'restricted') {
            setText('droneOpsMainSummaryCorrelations', 'Role');
        } else {
            setText('droneOpsMainSummaryCorrelations', String(latestCorrelations.length));
        }
        setText('droneOpsMainLastUpdate', new Date().toLocaleTimeString());
    }

    function renderMainDetections(detections, tracksByDetection) {
        const container = document.getElementById('droneOpsMainDetections');
        if (!container) return;

        if (!detections.length) {
            container.innerHTML = '<div class="droneops-empty">No detections yet</div>';
            return;
        }

        container.innerHTML = detections.slice(0, 150).map((d) => {
            const conf = Number(d.confidence || 0);
            const cls = confidenceClass(conf);
            const telemetry = detectionTelemetry(d, tracksByDetection);
            const ridSummary = [];
            if (telemetry.uas_id) ridSummary.push(`uas: ${esc(telemetry.uas_id)}`);
            if (telemetry.operator_id) ridSummary.push(`operator: ${esc(telemetry.operator_id)}`);

            return `<div class="droneops-main-item">
                <div class="droneops-main-item-head">
                    <span class="droneops-main-item-id">${esc(d.identifier)}</span>
                    <span class="droneops-pill ${cls}">${Math.round(conf * 100)}%</span>
                </div>
                <div class="droneops-main-item-meta">
                    <span>source: ${esc(d.source || 'unknown')}</span>
                    <span>class: ${esc(d.classification || 'unknown')}</span>
                    <span>last: ${esc(d.last_seen || '')}</span>
                </div>
                <div class="droneops-main-item-meta">
                    <span>lat: ${formatCoord(telemetry.lat)}</span>
                    <span>lon: ${formatCoord(telemetry.lon)}</span>
                    <span>alt: ${formatMetric(telemetry.altitude_m, 1, ' m')}</span>
                    <span>spd: ${formatMetric(telemetry.speed_mps, 1, ' m/s')}</span>
                    <span>hdg: ${formatMetric(telemetry.heading_deg, 0, '°')}</span>
                </div>
                ${ridSummary.length ? `<div class="droneops-main-item-meta"><span>${ridSummary.join(' • ')}</span></div>` : ''}
            </div>`;
        }).join('');
    }

    function renderMainTelemetry(detections, filteredTracks) {
        const container = document.getElementById('droneOpsMainTelemetry');
        if (!container) return;

        const detectionById = new Map(detections.map((d) => [Number(d.id), d]));

        if (filteredTracks.length) {
            const rows = filteredTracks
                .slice()
                .sort((a, b) => String(b?.timestamp || '').localeCompare(String(a?.timestamp || '')))
                .slice(0, 180);

            container.innerHTML = rows.map((t) => {
                const detection = detectionById.get(Number(t.detection_id));
                const label = detection?.identifier || `#${Number(t.detection_id)}`;
                const quality = toNumber(t.quality);
                return `<div class="droneops-main-telemetry-row">
                    <span><strong>${esc(label)}</strong> ${esc(t.timestamp || '')}</span>
                    <span>${formatCoord(t.lat)}, ${formatCoord(t.lon)}</span>
                    <span>alt ${formatMetric(t.altitude_m, 1, 'm')}</span>
                    <span>spd ${formatMetric(t.speed_mps, 1, 'm/s')}</span>
                    <span>hdg ${formatMetric(t.heading_deg, 0, '°')}</span>
                    <span>${esc(t.source || detection?.source || 'unknown')} • q ${quality === null ? '--' : quality.toFixed(2)}</span>
                </div>`;
            }).join('');
            return;
        }

        const tracksByDetection = sortedTracksByDetection();
        const telemetryRows = detections
            .map((d) => ({ detection: d, telemetry: detectionTelemetry(d, tracksByDetection) }))
            .filter((entry) => entry.telemetry.hasPosition || entry.telemetry.altitude_m !== null || entry.telemetry.speed_mps !== null || entry.telemetry.heading_deg !== null);

        if (!telemetryRows.length) {
            container.innerHTML = '<div class="droneops-empty">No telemetry yet</div>';
            return;
        }

        container.innerHTML = telemetryRows.slice(0, 120).map((entry) => {
            const d = entry.detection;
            const telemetry = entry.telemetry;
            return `<div class="droneops-main-telemetry-row">
                <span><strong>${esc(d.identifier)}</strong> ${esc(telemetry.timestamp || '')}</span>
                <span>${formatCoord(telemetry.lat)}, ${formatCoord(telemetry.lon)}</span>
                <span>alt ${formatMetric(telemetry.altitude_m, 1, 'm')}</span>
                <span>spd ${formatMetric(telemetry.speed_mps, 1, 'm/s')}</span>
                <span>hdg ${formatMetric(telemetry.heading_deg, 0, '°')}</span>
                <span>${esc(d.source || 'unknown')}</span>
            </div>`;
        }).join('');
    }

    function renderMainCorrelations() {
        const container = document.getElementById('droneOpsMainCorrelations');
        if (!container) return;

        if (correlationAccess === 'restricted') {
            container.innerHTML = '<div class="droneops-empty">Correlation data requires analyst role</div>';
            return;
        }

        if (correlationAccess === 'error') {
            container.innerHTML = '<div class="droneops-empty">Correlation data unavailable</div>';
            return;
        }

        if (!latestCorrelations.length) {
            container.innerHTML = '<div class="droneops-empty">No correlations yet</div>';
            return;
        }

        container.innerHTML = latestCorrelations.slice(0, 80).map((row) => {
            const confidence = Number(row?.confidence || 0);
            const cls = confidenceClass(confidence);
            return `<div class="droneops-main-correlation-row">
                <strong>${esc(row.drone_identifier || 'unknown')} → ${esc(row.operator_identifier || 'unknown')}</strong>
                <span>method: ${esc(row.method || 'n/a')} • ${esc(row.created_at || '')}</span>
                <span><span class="droneops-pill ${cls}">${Math.round(confidence * 100)}%</span></span>
            </div>`;
        }).join('');
    }

    function renderMapDetections(detections, tracksByDetection) {
        if (!map) {
            if (typeof L === 'undefined' || !document.getElementById('droneOpsMap')) return;
            ensureMap().then(() => {
                if (map) renderMapDetections(detections, tracksByDetection);
            }).catch(() => {});
            return;
        }
        if (!mapMarkers || !mapTracks) return;

        mapMarkers.clearLayers();
        mapTracks.clearLayers();

        const heatPoints = [];
        const boundsPoints = [];

        for (const d of detections) {
            const telemetry = detectionTelemetry(d, tracksByDetection);
            const color = sourceColor(telemetry.source || d.source);
            const pathPoints = [];
            for (const row of telemetry.trackRows) {
                const lat = toNumber(row?.lat);
                const lon = toNumber(row?.lon);
                if (!hasCoords(lat, lon)) continue;
                const latLng = [lat, lon];
                pathPoints.push(latLng);
                boundsPoints.push(latLng);
                const intensity = Math.max(0.2, Math.min(1, toNumber(row?.quality ?? d.confidence) ?? 0.5));
                heatPoints.push([lat, lon, intensity]);
            }

            if (pathPoints.length > 1) {
                L.polyline(pathPoints, {
                    color,
                    weight: 2,
                    opacity: 0.75,
                    lineJoin: 'round',
                }).addTo(mapTracks);
            }

            if (telemetry.hasPosition) {
                const latLng = [telemetry.lat, telemetry.lon];
                boundsPoints.push(latLng);
                const intensity = Math.max(0.2, Math.min(1, telemetry.quality ?? toNumber(d.confidence) ?? 0.5));
                heatPoints.push([telemetry.lat, telemetry.lon, intensity]);

                L.circleMarker(latLng, {
                    radius: 6,
                    color,
                    fillColor: color,
                    fillOpacity: 0.88,
                    weight: 2,
                }).bindPopup(`
                    <div style="font-size:11px;min-width:180px;">
                        <div style="font-weight:700;margin-bottom:4px;">${esc(d.identifier)}</div>
                        <div>Source: ${esc(d.source || 'unknown')}</div>
                        <div>Confidence: ${Math.round(Number(d.confidence || 0) * 100)}%</div>
                        <div>Lat/Lon: ${formatCoord(telemetry.lat)}, ${formatCoord(telemetry.lon)}</div>
                        <div>Alt: ${formatMetric(telemetry.altitude_m, 1, ' m')} • Spd: ${formatMetric(telemetry.speed_mps, 1, ' m/s')}</div>
                        <div>Heading: ${formatMetric(telemetry.heading_deg, 0, '°')}</div>
                    </div>
                `).addTo(mapMarkers);
            }
        }

        if (mapHeat && typeof mapHeat.setLatLngs === 'function') {
            mapHeat.setLatLngs(heatPoints);
        }

        if (boundsPoints.length && mapNeedsAutoFit) {
            map.fitBounds(L.latLngBounds(boundsPoints), { padding: [24, 24], maxZoom: 16 });
            mapNeedsAutoFit = false;
        }

        if (!boundsPoints.length) {
            setText('droneOpsMapMeta', 'No geospatial telemetry yet');
        } else {
            setText('droneOpsMapMeta', `${boundsPoints.length} geo points • ${heatPoints.length} heat samples`);
        }
    }

    function renderMainPane() {
        const pane = document.getElementById('droneOpsMainPane');
        if (!pane) return;

        const detections = Array.isArray(latestDetections) ? latestDetections : [];
        const detectionIds = new Set(detections.map((d) => Number(d.id)).filter(Boolean));
        const filteredTracks = (Array.isArray(latestTracks) ? latestTracks : [])
            .filter((track) => detectionIds.has(Number(track?.detection_id)));
        const tracksByDetection = sortedTracksByDetection();

        renderMainSummary(detections, tracksByDetection, filteredTracks.length);
        renderMainDetections(detections, tracksByDetection);
        renderMainTelemetry(detections, filteredTracks);
        renderMainCorrelations();
        renderMapDetections(detections, tracksByDetection);
    }

    async function ensureSessionForDetection() {
        try {
            const status = await api('/drone-ops/status');
            if (!status.active_session) {
                await api('/drone-ops/session/start', {
                    method: 'POST',
                    body: JSON.stringify({ mode: 'passive' }),
                });
            }
        } catch (_) {
            // Detection can still run without an explicit Drone Ops session.
        }
    }

    async function startWifiDetection() {
        if (isAgentMode()) {
            if (typeof WiFiMode !== 'undefined') {
                if (WiFiMode.init) WiFiMode.init();
                if (WiFiMode.startDeepScan) {
                    await WiFiMode.startDeepScan();
                    await sleep(DETECTION_START_WAIT_MS);
                    if (wifiRunning()) return;
                }
            }
            throw new Error('Unable to start WiFi detection in agent mode');
        }

        if (typeof WiFiMode !== 'undefined') {
            if (WiFiMode.init) WiFiMode.init();
            if (WiFiMode.startDeepScan) {
                await WiFiMode.startDeepScan();
                await sleep(DETECTION_START_WAIT_MS);
                if (wifiRunning()) return;
            }
        }

        if (typeof startWifiScan === 'function') {
            await Promise.resolve(startWifiScan());
            await sleep(DETECTION_START_WAIT_MS);
            if (wifiRunning()) return;
        }

        throw new Error('WiFi scan did not start');
    }

    async function startBluetoothDetection() {
        if (typeof startBtScan === 'function') {
            await Promise.resolve(startBtScan());
            await sleep(DETECTION_START_WAIT_MS);
            if (bluetoothRunning()) return;
        }

        if (typeof BluetoothMode !== 'undefined' && typeof BluetoothMode.startScan === 'function') {
            await BluetoothMode.startScan();
            await sleep(DETECTION_START_WAIT_MS);
            if (bluetoothRunning()) return;
        }

        throw new Error('Bluetooth scan did not start');
    }

    async function stopWifiDetection() {
        if (isAgentMode() && typeof WiFiMode !== 'undefined' && typeof WiFiMode.stopScan === 'function') {
            await WiFiMode.stopScan();
            return;
        }

        if (typeof stopWifiScan === 'function') {
            await Promise.resolve(stopWifiScan());
            return;
        }

        if (typeof WiFiMode !== 'undefined' && typeof WiFiMode.stopScan === 'function') {
            await WiFiMode.stopScan();
            return;
        }
    }

    async function stopBluetoothDetection() {
        if (typeof stopBtScan === 'function') {
            await Promise.resolve(stopBtScan());
            return;
        }

        if (typeof BluetoothMode !== 'undefined' && typeof BluetoothMode.stopScan === 'function') {
            await BluetoothMode.stopScan();
            return;
        }
    }

    async function refreshStatus() {
        try {
            const data = await api('/drone-ops/status');
            const active = data.active_session;
            const policy = data.policy || {};
            const counts = data.counts || {};

            setText('droneOpsSessionValue', active ? `${active.mode.toUpperCase()} #${active.id}` : 'Idle');
            setText('droneOpsArmedValue', policy.armed ? 'Yes' : 'No');
            setText('droneOpsDetectionCount', String(counts.detections || 0));
            setText('droneOpsIncidentCount', String(counts.incidents_open || 0));
            updateSensorsState();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function refreshDetections() {
        const source = document.getElementById('droneOpsSourceFilter')?.value || '';
        const min = parseFloat(document.getElementById('droneOpsConfidenceFilter')?.value || '0.5');

        try {
            const data = await api(`/drone-ops/detections?limit=500&source=${encodeURIComponent(source)}&min_confidence=${encodeURIComponent(isNaN(min) ? 0.5 : min)}`);
            const detections = data.detections || [];
            latestDetections = detections;
            const container = document.getElementById('droneOpsDetections');
            if (!container) {
                renderMainPane();
                return;
            }

            if (!detections.length) {
                container.innerHTML = '<div class="droneops-empty">No detections yet</div>';
                renderMainPane();
                return;
            }

            container.innerHTML = detections.map((d) => {
                const conf = Number(d.confidence || 0);
                const confPct = Math.round(conf * 100);
                const cls = confidenceClass(conf);
                return `<div class="droneops-item">
                    <div class="droneops-item-title">${esc(d.identifier)} <span class="droneops-pill ${cls}">${confPct}%</span></div>
                    <div class="droneops-item-meta">
                        <span>source: ${esc(d.source)}</span>
                        <span>class: ${esc(d.classification || 'unknown')}</span>
                        <span>last seen: ${esc(d.last_seen || '')}</span>
                    </div>
                    <div class="droneops-item-actions">
                        <button class="preset-btn" onclick="DroneOps.openIncidentFromDetection(${Number(d.id)})">Open Incident</button>
                    </div>
                </div>`;
            }).join('');
            renderMainPane();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function refreshTracks() {
        try {
            const data = await api('/drone-ops/tracks?limit=3000');
            latestTracks = Array.isArray(data.tracks) ? data.tracks : [];
            renderMainPane();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function refreshCorrelations() {
        const refreshFlag = correlationRefreshCount % 4 === 0;
        correlationRefreshCount += 1;
        const result = await apiOptional(`/drone-ops/correlations?min_confidence=0.5&limit=120&refresh=${refreshFlag ? 'true' : 'false'}`);
        if (result.__error) {
            if (result.__error?.status === 403) {
                correlationAccess = 'restricted';
                latestCorrelations = [];
                renderMainPane();
                return;
            }
            correlationAccess = 'error';
            latestCorrelations = [];
            const message = String(result.__error?.message || 'Unable to load correlations');
            if (message !== lastCorrelationError) {
                lastCorrelationError = message;
                notify(message, true);
            }
            renderMainPane();
            return;
        }

        correlationAccess = 'ok';
        lastCorrelationError = '';
        latestCorrelations = Array.isArray(result.correlations) ? result.correlations : [];
        renderMainPane();
    }

    async function refreshIncidents() {
        try {
            const data = await api('/drone-ops/incidents?limit=100');
            const incidents = data.incidents || [];
            const container = document.getElementById('droneOpsIncidents');
            if (!container) return;

            if (!incidents.length) {
                container.innerHTML = '<div class="droneops-empty">No incidents</div>';
                return;
            }

            container.innerHTML = incidents.map((i) => `
                <div class="droneops-item">
                    <div class="droneops-item-title">#${Number(i.id)} ${esc(i.title)}
                        <span class="droneops-pill ${i.status === 'open' ? 'warn' : 'ok'}">${esc(i.status)}</span>
                    </div>
                    <div class="droneops-item-meta">
                        <span>severity: ${esc(i.severity)}</span>
                        <span>opened: ${esc(i.opened_at || '')}</span>
                    </div>
                    <div class="droneops-item-actions">
                        <button class="preset-btn" onclick="DroneOps.closeIncident(${Number(i.id)})">Close</button>
                        <button class="preset-btn" onclick="DroneOps.attachLatestDetections(${Number(i.id)})">Attach Detections</button>
                    </div>
                </div>
            `).join('');
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function refreshActions() {
        try {
            const data = await api('/drone-ops/actions/requests?limit=100');
            const rows = data.requests || [];
            const container = document.getElementById('droneOpsActions');
            if (!container) return;

            if (!rows.length) {
                container.innerHTML = '<div class="droneops-empty">No action requests</div>';
                return;
            }

            container.innerHTML = rows.map((r) => {
                const statusClass = r.status === 'executed' ? 'ok' : (r.status === 'approved' ? 'warn' : 'bad');
                return `<div class="droneops-item">
                    <div class="droneops-item-title">Request #${Number(r.id)}
                        <span class="droneops-pill ${statusClass}">${esc(r.status)}</span>
                    </div>
                    <div class="droneops-item-meta">
                        <span>incident: ${Number(r.incident_id)}</span>
                        <span>action: ${esc(r.action_type)}</span>
                        <span>requested by: ${esc(r.requested_by)}</span>
                    </div>
                    <div class="droneops-item-actions">
                        <button class="preset-btn" onclick="DroneOps.approveAction(${Number(r.id)})">Approve</button>
                        <button class="preset-btn" onclick="DroneOps.executeAction(${Number(r.id)})">Execute</button>
                    </div>
                </div>`;
            }).join('');
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function refreshManifests() {
        const incident = parseInt(document.getElementById('droneOpsManifestIncident')?.value || '0', 10);
        const container = document.getElementById('droneOpsManifests');
        if (!container) return;

        if (!incident) {
            container.innerHTML = '<div class="droneops-empty">Enter incident ID to list manifests</div>';
            return;
        }

        try {
            const data = await api(`/drone-ops/evidence/${incident}/manifests?limit=50`);
            const rows = data.manifests || [];
            if (!rows.length) {
                container.innerHTML = '<div class="droneops-empty">No manifests</div>';
                return;
            }
            container.innerHTML = rows.map((m) => `<div class="droneops-item">
                <div class="droneops-item-title">Manifest #${Number(m.id)}</div>
                <div class="droneops-item-meta">
                    <span>algo: ${esc(m.hash_algo)}</span>
                    <span>created: ${esc(m.created_at || '')}</span>
                </div>
            </div>`).join('');
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function refreshAll() {
        await Promise.all([
            refreshStatus(),
            refreshDetections(),
            refreshTracks(),
            refreshCorrelations(),
            refreshIncidents(),
            refreshActions(),
            refreshManifests(),
        ]);
        renderMainPane();
    }

    async function startSession(mode) {
        try {
            await api('/drone-ops/session/start', {
                method: 'POST',
                body: JSON.stringify({ mode }),
            });
            notify(`Started ${mode} session`);
            refreshStatus();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function stopSession() {
        try {
            await api('/drone-ops/session/stop', { method: 'POST', body: JSON.stringify({}) });
            notify('Session stopped');
            refreshStatus();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function arm() {
        const incident = parseInt(document.getElementById('droneOpsArmIncident')?.value || '0', 10);
        const reason = String(document.getElementById('droneOpsArmReason')?.value || '').trim();
        if (!incident || !reason) {
            notify('Incident ID and arming reason are required', true);
            return;
        }
        try {
            await api('/drone-ops/actions/arm', {
                method: 'POST',
                body: JSON.stringify({ incident_id: incident, reason }),
            });
            notify('Action plane armed');
            refreshStatus();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function disarm() {
        try {
            await api('/drone-ops/actions/disarm', { method: 'POST', body: JSON.stringify({}) });
            notify('Action plane disarmed');
            refreshStatus();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function startDetection() {
        const useWifi = Boolean(document.getElementById('droneOpsDetectWifi')?.checked);
        const useBluetooth = Boolean(document.getElementById('droneOpsDetectBluetooth')?.checked);

        if (!useWifi && !useBluetooth) {
            notify('Select at least one source (WiFi or Bluetooth)', true);
            return;
        }

        const needsWifiSources = useWifi && !selectHasChoices('droneOpsWifiInterfaceSelect');
        const needsBtSources = useBluetooth && !selectHasChoices('droneOpsBtAdapterSelect');
        if (needsWifiSources || needsBtSources) {
            await refreshDetectionSources(true);
        }

        applySelectedSourceToModeSelectors();
        await ensureSessionForDetection();

        const started = [];
        const failed = [];

        if (useWifi) {
            try {
                await startWifiDetection();
                started.push('WiFi');
            } catch (e) {
                failed.push(`WiFi: ${e.message}`);
            }
        }

        if (useBluetooth) {
            try {
                await startBluetoothDetection();
                started.push('Bluetooth');
            } catch (e) {
                failed.push(`Bluetooth: ${e.message}`);
            }
        }

        updateSensorsState();
        await refreshStatus();
        await refreshDetections();
        await refreshTracks();
        await refreshCorrelations();

        if (!started.length) {
            notify(`Detection start failed (${failed.join(' | ')})`, true);
            return;
        }

        if (failed.length) {
            notify(`Started: ${started.join(', ')} | Errors: ${failed.join(' | ')}`, true);
            return;
        }

        notify(`Detection started: ${started.join(', ')}`);
    }

    async function stopDetection() {
        const errors = [];

        if (wifiRunning()) {
            try {
                await stopWifiDetection();
            } catch (e) {
                errors.push(`WiFi: ${e.message}`);
            }
        }

        if (bluetoothRunning()) {
            try {
                await stopBluetoothDetection();
            } catch (e) {
                errors.push(`Bluetooth: ${e.message}`);
            }
        }

        await sleep(300);
        updateSensorsState();
        await refreshStatus();
        await refreshTracks();

        if (errors.length) {
            notify(`Detection stop issues: ${errors.join(' | ')}`, true);
            return;
        }
        notify('Detection stopped');
    }

    async function createIncident() {
        const title = String(document.getElementById('droneOpsIncidentTitle')?.value || '').trim();
        const severity = String(document.getElementById('droneOpsIncidentSeverity')?.value || 'medium');
        if (!title) {
            notify('Incident title is required', true);
            return;
        }

        try {
            const data = await api('/drone-ops/incidents', {
                method: 'POST',
                body: JSON.stringify({ title, severity }),
            });
            notify(`Incident #${data.incident?.id || ''} created`);
            refreshIncidents();
            refreshStatus();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function closeIncident(incidentId) {
        try {
            await api(`/drone-ops/incidents/${incidentId}`, {
                method: 'PUT',
                body: JSON.stringify({ status: 'closed' }),
            });
            notify(`Incident #${incidentId} closed`);
            refreshIncidents();
            refreshStatus();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function openIncidentFromDetection(detectionId) {
        const title = `Drone detection #${detectionId}`;
        try {
            const created = await api('/drone-ops/incidents', {
                method: 'POST',
                body: JSON.stringify({ title, severity: 'medium' }),
            });
            const incidentId = created.incident.id;
            await api(`/drone-ops/incidents/${incidentId}/artifacts`, {
                method: 'POST',
                body: JSON.stringify({
                    artifact_type: 'detection',
                    artifact_ref: String(detectionId),
                    metadata: { auto_linked: true },
                }),
            });
            notify(`Incident #${incidentId} opened for detection #${detectionId}`);
            refreshIncidents();
            refreshStatus();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function attachLatestDetections(incidentId) {
        try {
            const data = await api('/drone-ops/detections?limit=10&min_confidence=0.6');
            const detections = data.detections || [];
            if (!detections.length) {
                notify('No high-confidence detections to attach', true);
                return;
            }
            for (const d of detections) {
                await api(`/drone-ops/incidents/${incidentId}/artifacts`, {
                    method: 'POST',
                    body: JSON.stringify({
                        artifact_type: 'detection',
                        artifact_ref: String(d.id),
                        metadata: { source: d.source, identifier: d.identifier },
                    }),
                });
            }
            notify(`Attached ${detections.length} detections to incident #${incidentId}`);
            refreshIncidents();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function requestAction() {
        const incident = parseInt(document.getElementById('droneOpsActionIncident')?.value || '0', 10);
        const actionType = String(document.getElementById('droneOpsActionType')?.value || '').trim();

        if (!incident || !actionType) {
            notify('Incident ID and action type are required', true);
            return;
        }

        try {
            await api('/drone-ops/actions/request', {
                method: 'POST',
                body: JSON.stringify({
                    incident_id: incident,
                    action_type: actionType,
                    payload: {},
                }),
            });
            notify('Action request submitted');
            refreshActions();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function approveAction(requestId) {
        try {
            await api(`/drone-ops/actions/approve/${requestId}`, {
                method: 'POST',
                body: JSON.stringify({ decision: 'approved' }),
            });
            notify(`Request #${requestId} approved`);
            refreshActions();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function executeAction(requestId) {
        try {
            await api(`/drone-ops/actions/execute/${requestId}`, {
                method: 'POST',
                body: JSON.stringify({}),
            });
            notify(`Request #${requestId} executed`);
            refreshActions();
        } catch (e) {
            notify(e.message, true);
        }
    }

    async function generateManifest() {
        const incident = parseInt(document.getElementById('droneOpsManifestIncident')?.value || '0', 10);
        if (!incident) {
            notify('Incident ID is required to generate manifest', true);
            return;
        }
        try {
            await api(`/drone-ops/evidence/${incident}/manifest`, {
                method: 'POST',
                body: JSON.stringify({}),
            });
            notify(`Manifest generated for incident #${incident}`);
            refreshManifests();
        } catch (e) {
            notify(e.message, true);
        }
    }

    function init() {
        if (initialized) {
            refreshDetectionSources(true);
            refreshAll();
            invalidateMap();
            return;
        }
        initialized = true;
        mapNeedsAutoFit = true;
        ensureMap();
        refreshDetectionSources(true);
        refreshAll();
        connectStream();
        refreshTimer = setInterval(refreshAll, 15000);
    }

    function destroy() {
        initialized = false;
        mapNeedsAutoFit = true;
        if (refreshTimer) {
            clearInterval(refreshTimer);
            refreshTimer = null;
        }
        disconnectStream();
    }

    return {
        init,
        destroy,
        refreshStatus,
        refreshDetections,
        refreshTracks,
        refreshDetectionSources,
        refreshIncidents,
        refreshActions,
        startDetection,
        stopDetection,
        invalidateMap,
        startSession,
        stopSession,
        arm,
        disarm,
        createIncident,
        closeIncident,
        openIncidentFromDetection,
        attachLatestDetections,
        requestAction,
        approveAction,
        executeAction,
        generateManifest,
    };
})();
