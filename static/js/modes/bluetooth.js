/**
 * Bluetooth Mode Controller
 * Uses the new unified Bluetooth API at /api/bluetooth/
 */

const BluetoothMode = (function() {
    'use strict';

    // State
    let isScanning = false;
    let eventSource = null;
    let devices = new Map();
    let baselineSet = false;
    let baselineCount = 0;
    let selectedDeviceId = null;

    // DOM elements (cached)
    let startBtn, stopBtn, messageContainer, deviceContainer;
    let adapterSelect, scanModeSelect, transportSelect, durationInput, minRssiInput;
    let baselineStatusEl, capabilityStatusEl;

    // Stats tracking
    let deviceStats = {
        phones: 0,
        computers: 0,
        audio: 0,
        wearables: 0,
        other: 0,
        strong: 0,
        medium: 0,
        weak: 0,
        trackers: [],
        findmy: []
    };

    /**
     * Initialize the Bluetooth mode
     */
    function init() {
        console.log('[BT] Initializing BluetoothMode');

        // Cache DOM elements
        startBtn = document.getElementById('startBtBtn');
        stopBtn = document.getElementById('stopBtBtn');
        messageContainer = document.getElementById('btMessageContainer');
        deviceContainer = document.getElementById('btDeviceListContent');
        adapterSelect = document.getElementById('btAdapterSelect');
        scanModeSelect = document.getElementById('btScanMode');
        transportSelect = document.getElementById('btTransport');
        durationInput = document.getElementById('btScanDuration');
        minRssiInput = document.getElementById('btMinRssi');
        baselineStatusEl = document.getElementById('btBaselineStatus');
        capabilityStatusEl = document.getElementById('btCapabilityStatus');

        // Create modal if it doesn't exist
        createModal();

        // Check capabilities on load
        checkCapabilities();

        // Check scan status (in case page was reloaded during scan)
        checkScanStatus();

        // Initialize radar canvas
        initRadar();
    }

    /**
     * Create the device details modal
     */
    function createModal() {
        if (document.getElementById('btDeviceModal')) return;

        const modal = document.createElement('div');
        modal.id = 'btDeviceModal';
        modal.style.cssText = 'display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.8);z-index:10000;align-items:center;justify-content:center;';
        modal.innerHTML = `
            <div id="btDeviceModalContent" style="background:#1a1a2e;border:1px solid #444;border-radius:12px;max-width:600px;width:90%;max-height:85vh;overflow-y:auto;position:relative;">
                <div style="position:sticky;top:0;background:#1a1a2e;padding:16px 20px;border-bottom:1px solid #333;display:flex;justify-content:space-between;align-items:center;">
                    <h3 id="btModalTitle" style="margin:0;color:#e0e0e0;font-size:18px;">Device Details</h3>
                    <button onclick="BluetoothMode.closeModal()" style="background:none;border:none;color:#888;font-size:24px;cursor:pointer;padding:0;line-height:1;">&times;</button>
                </div>
                <div id="btModalBody" style="padding:20px;"></div>
            </div>
        `;
        modal.onclick = (e) => {
            if (e.target === modal) closeModal();
        };
        document.body.appendChild(modal);
    }

    /**
     * Show device details modal
     */
    function showModal(deviceId) {
        const device = devices.get(deviceId);
        if (!device) return;

        selectedDeviceId = deviceId;
        const modal = document.getElementById('btDeviceModal');
        const title = document.getElementById('btModalTitle');
        const body = document.getElementById('btModalBody');

        title.textContent = device.name || formatDeviceId(device.address);

        const rssi = device.rssi_current;
        const rssiColor = getRssiColor(rssi);
        const flags = device.heuristic_flags || [];

        body.innerHTML = `
            <!-- Header badges -->
            <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px;">
                <span style="display:inline-block;background:${device.protocol === 'ble' ? 'rgba(59,130,246,0.15)' : 'rgba(139,92,246,0.15)'};color:${device.protocol === 'ble' ? '#3b82f6' : '#8b5cf6'};border:1px solid ${device.protocol === 'ble' ? 'rgba(59,130,246,0.3)' : 'rgba(139,92,246,0.3)'};padding:4px 10px;border-radius:4px;font-size:11px;font-weight:600;">${(device.protocol || 'ble').toUpperCase()}</span>
                ${flags.map(f => `<span style="display:inline-block;background:rgba(107,114,128,0.15);color:#9ca3af;border:1px solid rgba(107,114,128,0.3);padding:4px 10px;border-radius:4px;font-size:11px;">${f.replace('_', ' ').toUpperCase()}</span>`).join('')}
                <span style="display:inline-block;background:${device.in_baseline ? 'rgba(34,197,94,0.15)' : 'rgba(59,130,246,0.15)'};color:${device.in_baseline ? '#22c55e' : '#3b82f6'};padding:4px 10px;border-radius:4px;font-size:11px;">${device.in_baseline ? '✓ In Baseline' : '● New Device'}</span>
            </div>

            <!-- Signal strength display -->
            <div style="background:#141428;border-radius:8px;padding:20px;margin-bottom:20px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
                    <div>
                        <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">Signal Strength</div>
                        <div style="font-family:monospace;font-size:36px;font-weight:700;color:${rssiColor};">${rssi !== null ? rssi : '--'}<span style="font-size:14px;color:#666;margin-left:4px;">dBm</span></div>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">Range</div>
                        <div style="font-size:18px;font-weight:600;color:#e0e0e0;text-transform:uppercase;">${device.range_band || 'Unknown'}</div>
                    </div>
                </div>
                <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
                    <div style="background:#1a1a2e;padding:10px;border-radius:6px;text-align:center;">
                        <div style="font-size:9px;color:#666;text-transform:uppercase;">Min</div>
                        <div style="font-family:monospace;font-size:14px;color:#e0e0e0;">${device.rssi_min !== null ? device.rssi_min : '--'}</div>
                    </div>
                    <div style="background:#1a1a2e;padding:10px;border-radius:6px;text-align:center;">
                        <div style="font-size:9px;color:#666;text-transform:uppercase;">Max</div>
                        <div style="font-family:monospace;font-size:14px;color:#e0e0e0;">${device.rssi_max !== null ? device.rssi_max : '--'}</div>
                    </div>
                    <div style="background:#1a1a2e;padding:10px;border-radius:6px;text-align:center;">
                        <div style="font-size:9px;color:#666;text-transform:uppercase;">Median</div>
                        <div style="font-family:monospace;font-size:14px;color:#e0e0e0;">${device.rssi_median !== null ? device.rssi_median : '--'}</div>
                    </div>
                    <div style="background:#1a1a2e;padding:10px;border-radius:6px;text-align:center;">
                        <div style="font-size:9px;color:#666;text-transform:uppercase;">Confidence</div>
                        <div style="font-family:monospace;font-size:14px;color:#e0e0e0;">${device.rssi_confidence ? Math.round(device.rssi_confidence * 100) + '%' : '--'}</div>
                    </div>
                </div>
            </div>

            <!-- Device info grid -->
            <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:20px;">
                <div style="background:#141428;padding:14px;border-radius:6px;">
                    <div style="font-size:10px;color:#666;text-transform:uppercase;margin-bottom:4px;">Address</div>
                    <div style="font-family:monospace;font-size:13px;color:#00d4ff;">${device.address}</div>
                </div>
                <div style="background:#141428;padding:14px;border-radius:6px;">
                    <div style="font-size:10px;color:#666;text-transform:uppercase;margin-bottom:4px;">Address Type</div>
                    <div style="font-size:13px;color:#e0e0e0;">${device.address_type || 'Unknown'}</div>
                </div>
                <div style="background:#141428;padding:14px;border-radius:6px;">
                    <div style="font-size:10px;color:#666;text-transform:uppercase;margin-bottom:4px;">Manufacturer</div>
                    <div style="font-size:13px;color:#e0e0e0;">${device.manufacturer_name || 'Unknown'}</div>
                </div>
                <div style="background:#141428;padding:14px;border-radius:6px;">
                    <div style="font-size:10px;color:#666;text-transform:uppercase;margin-bottom:4px;">Manufacturer ID</div>
                    <div style="font-family:monospace;font-size:13px;color:#e0e0e0;">${device.manufacturer_id !== null ? '0x' + device.manufacturer_id.toString(16).toUpperCase().padStart(4, '0') : '--'}</div>
                </div>
            </div>

            <!-- Observation stats -->
            <div style="background:#141428;padding:14px;border-radius:6px;margin-bottom:20px;">
                <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;">Observation Stats</div>
                <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;">
                    <div>
                        <div style="font-size:10px;color:#666;">First Seen</div>
                        <div style="font-size:12px;color:#e0e0e0;">${device.first_seen ? new Date(device.first_seen).toLocaleTimeString() : '--'}</div>
                    </div>
                    <div>
                        <div style="font-size:10px;color:#666;">Last Seen</div>
                        <div style="font-size:12px;color:#e0e0e0;">${device.last_seen ? new Date(device.last_seen).toLocaleTimeString() : '--'}</div>
                    </div>
                    <div>
                        <div style="font-size:10px;color:#666;">Seen Count</div>
                        <div style="font-size:12px;color:#e0e0e0;">${device.seen_count || 0} times</div>
                    </div>
                </div>
            </div>

            <!-- Service UUIDs -->
            ${device.service_uuids && device.service_uuids.length > 0 ? `
            <div style="background:#141428;padding:14px;border-radius:6px;margin-bottom:20px;">
                <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;">Service UUIDs</div>
                <div style="display:flex;flex-wrap:wrap;gap:6px;">
                    ${device.service_uuids.map(uuid => `<span style="font-family:monospace;font-size:10px;background:#1a1a2e;padding:4px 8px;border-radius:4px;color:#888;">${uuid}</span>`).join('')}
                </div>
            </div>
            ` : ''}

            <!-- Actions -->
            <div style="display:flex;gap:10px;margin-top:20px;">
                <button onclick="BluetoothMode.copyAddress('${device.address}')" style="flex:1;background:#252538;border:1px solid #444;color:#e0e0e0;padding:10px;border-radius:6px;cursor:pointer;font-size:12px;">Copy Address</button>
                <button onclick="BluetoothMode.closeModal()" style="flex:1;background:#3b82f6;border:none;color:#fff;padding:10px;border-radius:6px;cursor:pointer;font-size:12px;">Close</button>
            </div>
        `;

        modal.style.display = 'flex';
    }

    /**
     * Close device details modal
     */
    function closeModal() {
        const modal = document.getElementById('btDeviceModal');
        if (modal) modal.style.display = 'none';
        selectedDeviceId = null;
    }

    /**
     * Copy address to clipboard
     */
    function copyAddress(address) {
        navigator.clipboard.writeText(address).then(() => {
            // Brief visual feedback
            const btn = event.target;
            const originalText = btn.textContent;
            btn.textContent = 'Copied!';
            btn.style.background = '#22c55e';
            setTimeout(() => {
                btn.textContent = originalText;
                btn.style.background = '#252538';
            }, 1500);
        });
    }

    /**
     * Format device ID for display (when no name available)
     */
    function formatDeviceId(address) {
        if (!address) return 'Unknown Device';
        // Return shortened format: first 2 and last 2 octets
        const parts = address.split(':');
        if (parts.length === 6) {
            return parts[0] + ':' + parts[1] + ':...:' + parts[4] + ':' + parts[5];
        }
        return address;
    }

    /**
     * Check system capabilities
     */
    async function checkCapabilities() {
        try {
            const response = await fetch('/api/bluetooth/capabilities');
            const data = await response.json();

            if (!data.available) {
                showCapabilityWarning(['Bluetooth not available on this system']);
                return;
            }

            // Update adapter select
            if (adapterSelect && data.adapters && data.adapters.length > 0) {
                adapterSelect.innerHTML = data.adapters.map(a => {
                    const status = a.powered ? 'UP' : 'DOWN';
                    return `<option value="${a.id}">${a.id} - ${a.name || 'Bluetooth Adapter'} [${status}]</option>`;
                }).join('');
            } else if (adapterSelect) {
                adapterSelect.innerHTML = '<option value="">No adapters found</option>';
            }

            // Show any issues
            if (data.issues && data.issues.length > 0) {
                showCapabilityWarning(data.issues);
            } else {
                hideCapabilityWarning();
            }

            // Update scan mode based on preferred backend
            if (scanModeSelect && data.preferred_backend) {
                const option = scanModeSelect.querySelector(`option[value="${data.preferred_backend}"]`);
                if (option) option.selected = true;
            }

        } catch (err) {
            console.error('Failed to check capabilities:', err);
            showCapabilityWarning(['Failed to check Bluetooth capabilities']);
        }
    }

    /**
     * Show capability warning
     */
    function showCapabilityWarning(issues) {
        if (!capabilityStatusEl) return;

        capabilityStatusEl.style.display = 'block';
        capabilityStatusEl.innerHTML = `
            <div style="color: #f59e0b; padding: 10px; background: rgba(245,158,11,0.1); border-radius: 6px; font-size: 12px;">
                ${issues.map(i => `<div>⚠ ${i}</div>`).join('')}
            </div>
        `;
    }

    /**
     * Hide capability warning
     */
    function hideCapabilityWarning() {
        if (capabilityStatusEl) {
            capabilityStatusEl.style.display = 'none';
            capabilityStatusEl.innerHTML = '';
        }
    }

    /**
     * Check current scan status
     */
    async function checkScanStatus() {
        try {
            const response = await fetch('/api/bluetooth/scan/status');
            const data = await response.json();

            if (data.is_scanning) {
                setScanning(true);
                startEventStream();
            }

            // Update baseline status
            if (data.baseline_count > 0) {
                baselineSet = true;
                baselineCount = data.baseline_count;
                updateBaselineStatus();
            }

        } catch (err) {
            console.error('Failed to check scan status:', err);
        }
    }

    /**
     * Start scanning
     */
    async function startScan() {
        const adapter = adapterSelect?.value || '';
        const mode = scanModeSelect?.value || 'auto';
        const transport = transportSelect?.value || 'auto';
        const duration = parseInt(durationInput?.value || '0', 10);
        const minRssi = parseInt(minRssiInput?.value || '-100', 10);

        try {
            const response = await fetch('/api/bluetooth/scan/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    mode: mode,
                    adapter_id: adapter || undefined,
                    duration_s: duration > 0 ? duration : undefined,
                    transport: transport,
                    rssi_threshold: minRssi
                })
            });

            const data = await response.json();

            if (data.status === 'started' || data.status === 'already_scanning') {
                setScanning(true);
                startEventStream();
            } else {
                showErrorMessage(data.message || 'Failed to start scan');
            }

        } catch (err) {
            console.error('Failed to start scan:', err);
            showErrorMessage('Failed to start scan: ' + err.message);
        }
    }

    /**
     * Stop scanning
     */
    async function stopScan() {
        try {
            await fetch('/api/bluetooth/scan/stop', { method: 'POST' });
            setScanning(false);
            stopEventStream();
        } catch (err) {
            console.error('Failed to stop scan:', err);
        }
    }

    /**
     * Set scanning state
     */
    function setScanning(scanning) {
        isScanning = scanning;

        if (startBtn) startBtn.style.display = scanning ? 'none' : 'block';
        if (stopBtn) stopBtn.style.display = scanning ? 'block' : 'none';

        // Clear container when starting scan
        if (scanning && deviceContainer) {
            deviceContainer.innerHTML = '';
            devices.clear();
            resetStats();
        }

        // Update global status if available
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        if (statusDot) statusDot.classList.toggle('running', scanning);
        if (statusText) statusText.textContent = scanning ? 'Scanning...' : 'Idle';
    }

    /**
     * Reset stats
     */
    function resetStats() {
        deviceStats = {
            phones: 0,
            computers: 0,
            audio: 0,
            wearables: 0,
            other: 0,
            strong: 0,
            medium: 0,
            weak: 0,
            trackers: [],
            findmy: []
        };
        updateVisualizationPanels();
    }

    /**
     * Start SSE event stream
     */
    function startEventStream() {
        if (eventSource) eventSource.close();

        eventSource = new EventSource('/api/bluetooth/stream');

        eventSource.addEventListener('device_update', (e) => {
            try {
                const device = JSON.parse(e.data);
                handleDeviceUpdate(device);
            } catch (err) {
                console.error('Failed to parse device update:', err);
            }
        });

        eventSource.addEventListener('scan_started', (e) => {
            const data = JSON.parse(e.data);
            setScanning(true);
        });

        eventSource.addEventListener('scan_stopped', (e) => {
            setScanning(false);
            const data = JSON.parse(e.data);
        });

        eventSource.onerror = () => {
            console.warn('Bluetooth SSE connection error');
        };
    }

    /**
     * Stop SSE event stream
     */
    function stopEventStream() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
    }

    /**
     * Handle device update from SSE
     */
    function handleDeviceUpdate(device) {
        devices.set(device.device_id, device);
        renderDevice(device);
        updateDeviceCount();
        updateStatsFromDevice(device);
        updateVisualizationPanels();
        updateRadar();
    }

    /**
     * Update stats from device
     */
    function updateStatsFromDevice(device) {
        // Categorize by manufacturer/type
        const mfr = (device.manufacturer_name || '').toLowerCase();
        const name = (device.name || '').toLowerCase();

        // Reset counts and recalculate from all devices
        deviceStats.phones = 0;
        deviceStats.computers = 0;
        deviceStats.audio = 0;
        deviceStats.wearables = 0;
        deviceStats.other = 0;
        deviceStats.strong = 0;
        deviceStats.medium = 0;
        deviceStats.weak = 0;
        deviceStats.trackers = [];
        deviceStats.findmy = [];

        devices.forEach(d => {
            const m = (d.manufacturer_name || '').toLowerCase();
            const n = (d.name || '').toLowerCase();
            const rssi = d.rssi_current;

            // Device type classification
            if (n.includes('iphone') || n.includes('phone') || n.includes('pixel') || n.includes('galaxy') || n.includes('android')) {
                deviceStats.phones++;
            } else if (n.includes('macbook') || n.includes('laptop') || n.includes('pc') || n.includes('computer') || n.includes('imac')) {
                deviceStats.computers++;
            } else if (n.includes('airpod') || n.includes('headphone') || n.includes('speaker') || n.includes('buds') || n.includes('audio') || n.includes('beats')) {
                deviceStats.audio++;
            } else if (n.includes('watch') || n.includes('band') || n.includes('fitbit') || n.includes('garmin')) {
                deviceStats.wearables++;
            } else {
                deviceStats.other++;
            }

            // Signal strength classification
            if (rssi !== null && rssi !== undefined) {
                if (rssi >= -50) deviceStats.strong++;
                else if (rssi >= -70) deviceStats.medium++;
                else deviceStats.weak++;
            }

            // Tracker detection (Apple, Tile, etc.)
            if (m.includes('apple') && (d.heuristic_flags || []).includes('beacon_like')) {
                if (!deviceStats.findmy.find(t => t.address === d.address)) {
                    deviceStats.findmy.push(d);
                }
            }
            if (n.includes('tile') || n.includes('airtag') || n.includes('smarttag')) {
                if (!deviceStats.trackers.find(t => t.address === d.address)) {
                    deviceStats.trackers.push(d);
                }
            }
        });
    }

    /**
     * Update visualization panels
     */
    function updateVisualizationPanels() {
        // Device Types
        const phoneCount = document.getElementById('btPhoneCount');
        const computerCount = document.getElementById('btComputerCount');
        const audioCount = document.getElementById('btAudioCount');
        const wearableCount = document.getElementById('btWearableCount');
        const otherCount = document.getElementById('btOtherCount');

        if (phoneCount) phoneCount.textContent = deviceStats.phones;
        if (computerCount) computerCount.textContent = deviceStats.computers;
        if (audioCount) audioCount.textContent = deviceStats.audio;
        if (wearableCount) wearableCount.textContent = deviceStats.wearables;
        if (otherCount) otherCount.textContent = deviceStats.other;

        // Signal Distribution
        const total = devices.size || 1;
        const strongBar = document.getElementById('btSignalStrong');
        const mediumBar = document.getElementById('btSignalMedium');
        const weakBar = document.getElementById('btSignalWeak');
        const strongCount = document.getElementById('btSignalStrongCount');
        const mediumCount = document.getElementById('btSignalMediumCount');
        const weakCount = document.getElementById('btSignalWeakCount');

        if (strongBar) strongBar.style.width = (deviceStats.strong / total * 100) + '%';
        if (mediumBar) mediumBar.style.width = (deviceStats.medium / total * 100) + '%';
        if (weakBar) weakBar.style.width = (deviceStats.weak / total * 100) + '%';
        if (strongCount) strongCount.textContent = deviceStats.strong;
        if (mediumCount) mediumCount.textContent = deviceStats.medium;
        if (weakCount) weakCount.textContent = deviceStats.weak;

        // Tracker Detection
        const trackerList = document.getElementById('btTrackerList');
        if (trackerList) {
            if (deviceStats.trackers.length === 0) {
                trackerList.innerHTML = '<div style="color:#666;padding:10px;text-align:center;">No trackers detected</div>';
            } else {
                trackerList.innerHTML = deviceStats.trackers.map(t => `
                    <div style="padding:8px;border-bottom:1px solid rgba(255,255,255,0.05);cursor:pointer;" onclick="BluetoothMode.showModal('${t.device_id}')">
                        <div style="display:flex;justify-content:space-between;">
                            <span style="color:#f97316;">${t.name || formatDeviceId(t.address)}</span>
                            <span style="color:#666;">${t.rssi_current || '--'} dBm</span>
                        </div>
                        <div style="font-size:10px;color:#666;">${t.address}</div>
                    </div>
                `).join('');
            }
        }

        // FindMy Detection
        const findmyList = document.getElementById('btFindMyList');
        if (findmyList) {
            if (deviceStats.findmy.length === 0) {
                findmyList.innerHTML = '<div style="color:#666;padding:10px;text-align:center;">No FindMy devices detected</div>';
            } else {
                findmyList.innerHTML = deviceStats.findmy.map(t => `
                    <div style="padding:8px;border-bottom:1px solid rgba(255,255,255,0.05);cursor:pointer;" onclick="BluetoothMode.showModal('${t.device_id}')">
                        <div style="display:flex;justify-content:space-between;">
                            <span style="color:#007aff;">${t.name || 'Apple Device'}</span>
                            <span style="color:#666;">${t.rssi_current || '--'} dBm</span>
                        </div>
                        <div style="font-size:10px;color:#666;">${t.address}</div>
                    </div>
                `).join('');
            }
        }
    }

    /**
     * Initialize radar canvas
     */
    function initRadar() {
        const canvas = document.getElementById('btRadarCanvas');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        drawRadarBackground(ctx, canvas.width, canvas.height);
    }

    /**
     * Draw radar background
     */
    function drawRadarBackground(ctx, width, height) {
        const centerX = width / 2;
        const centerY = height / 2;
        const maxRadius = Math.min(width, height) / 2 - 10;

        ctx.clearRect(0, 0, width, height);

        // Draw concentric circles
        ctx.strokeStyle = 'rgba(0, 212, 255, 0.2)';
        ctx.lineWidth = 1;
        for (let i = 1; i <= 4; i++) {
            ctx.beginPath();
            ctx.arc(centerX, centerY, maxRadius * i / 4, 0, Math.PI * 2);
            ctx.stroke();
        }

        // Draw cross lines
        ctx.beginPath();
        ctx.moveTo(centerX, 10);
        ctx.lineTo(centerX, height - 10);
        ctx.moveTo(10, centerY);
        ctx.lineTo(width - 10, centerY);
        ctx.stroke();

        // Center dot
        ctx.fillStyle = '#00d4ff';
        ctx.beginPath();
        ctx.arc(centerX, centerY, 3, 0, Math.PI * 2);
        ctx.fill();
    }

    /**
     * Update radar with device positions
     */
    function updateRadar() {
        const canvas = document.getElementById('btRadarCanvas');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const width = canvas.width;
        const height = canvas.height;
        const centerX = width / 2;
        const centerY = height / 2;
        const maxRadius = Math.min(width, height) / 2 - 15;

        // Redraw background
        drawRadarBackground(ctx, width, height);

        // Plot devices
        let angle = 0;
        const angleStep = (Math.PI * 2) / Math.max(devices.size, 1);

        devices.forEach(device => {
            const rssi = device.rssi_current;
            if (rssi === null || rssi === undefined) return;

            // Convert RSSI to distance (closer = smaller radius)
            // RSSI typically ranges from -30 (very close) to -100 (far)
            const normalizedRssi = Math.max(0, Math.min(1, (rssi + 100) / 70));
            const radius = maxRadius * (1 - normalizedRssi);

            const x = centerX + Math.cos(angle) * radius;
            const y = centerY + Math.sin(angle) * radius;

            // Color based on signal strength
            const color = getRssiColor(rssi);

            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(x, y, 4, 0, Math.PI * 2);
            ctx.fill();

            // Glow effect
            ctx.fillStyle = color.replace(')', ', 0.3)').replace('rgb', 'rgba');
            ctx.beginPath();
            ctx.arc(x, y, 8, 0, Math.PI * 2);
            ctx.fill();

            angle += angleStep;
        });
    }

    /**
     * Update device count display
     */
    function updateDeviceCount() {
        const countEl = document.getElementById('btDeviceListCount');
        if (countEl) {
            countEl.textContent = devices.size;
        }
    }

    /**
     * Render a device card
     */
    function renderDevice(device) {
        if (!deviceContainer) {
            deviceContainer = document.getElementById('btDeviceListContent');
            if (!deviceContainer) return;
        }

        const escapedId = CSS.escape(device.device_id);
        const existingCard = deviceContainer.querySelector('[data-bt-device-id="' + escapedId + '"]');
        const cardHtml = createSimpleDeviceCard(device);

        if (existingCard) {
            existingCard.outerHTML = cardHtml;
        } else {
            deviceContainer.insertAdjacentHTML('afterbegin', cardHtml);
        }
    }

    /**
     * Simple device card with click handler
     */
    function createSimpleDeviceCard(device) {
        const protocol = device.protocol || 'ble';
        const protoBadge = protocol === 'ble'
            ? '<span style="display:inline-block;background:rgba(59,130,246,0.15);color:#3b82f6;border:1px solid rgba(59,130,246,0.3);padding:2px 6px;border-radius:3px;font-size:10px;font-weight:600;">BLE</span>'
            : '<span style="display:inline-block;background:rgba(139,92,246,0.15);color:#8b5cf6;border:1px solid rgba(139,92,246,0.3);padding:2px 6px;border-radius:3px;font-size:10px;font-weight:600;">CLASSIC</span>';

        const flags = device.heuristic_flags || [];
        let badgesHtml = '';
        if (flags.includes('random_address')) {
            badgesHtml += '<span style="display:inline-block;background:rgba(107,114,128,0.15);color:#6b7280;border:1px solid rgba(107,114,128,0.3);padding:2px 6px;border-radius:3px;font-size:9px;margin-left:4px;">RANDOM</span>';
        }
        if (flags.includes('persistent')) {
            badgesHtml += '<span style="display:inline-block;background:rgba(34,197,94,0.15);color:#22c55e;border:1px solid rgba(34,197,94,0.3);padding:2px 6px;border-radius:3px;font-size:9px;margin-left:4px;">PERSISTENT</span>';
        }

        // Use device name if available, otherwise format the address nicely
        const displayName = device.name || formatDeviceId(device.address);
        const name = escapeHtml(displayName);
        const addr = escapeHtml(device.address || 'Unknown');
        const addrType = escapeHtml(device.address_type || 'unknown');
        const rssi = device.rssi_current;
        const rssiStr = (rssi !== null && rssi !== undefined) ? rssi + ' dBm' : '--';
        const rssiColor = getRssiColor(rssi);
        const mfr = device.manufacturer_name ? escapeHtml(device.manufacturer_name) : '';
        const seenCount = device.seen_count || 0;
        const rangeBand = device.range_band || 'unknown';
        const inBaseline = device.in_baseline || false;

        const cardStyle = 'display:block;background:#1a1a2e;border:1px solid #444;border-radius:8px;padding:14px;margin-bottom:10px;cursor:pointer;transition:border-color 0.2s;';
        const headerStyle = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;';
        const nameStyle = 'font-size:14px;font-weight:600;color:#e0e0e0;margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
        const addrStyle = 'font-family:monospace;font-size:11px;color:#00d4ff;';
        const rssiRowStyle = 'display:flex;justify-content:space-between;align-items:center;background:#141428;padding:10px;border-radius:6px;margin:10px 0;';
        const rssiValueStyle = 'font-family:monospace;font-size:16px;font-weight:700;color:' + rssiColor + ';';
        const rangeBandStyle = 'font-size:10px;color:#888;text-transform:uppercase;letter-spacing:0.5px;';
        const mfrStyle = 'font-size:11px;color:#888;margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
        const metaStyle = 'display:flex;justify-content:space-between;font-size:10px;color:#666;';
        const statusPillStyle = 'background:' + (inBaseline ? 'rgba(34,197,94,0.15)' : 'rgba(59,130,246,0.15)') + ';color:' + (inBaseline ? '#22c55e' : '#3b82f6') + ';padding:3px 10px;border-radius:12px;font-size:10px;font-weight:500;';

        const deviceIdEscaped = escapeHtml(device.device_id).replace(/'/g, "\\'");

        return '<div data-bt-device-id="' + escapeHtml(device.device_id) + '" style="' + cardStyle + '" onclick="BluetoothMode.showModal(\'' + deviceIdEscaped + '\')" onmouseover="this.style.borderColor=\'#00d4ff\'" onmouseout="this.style.borderColor=\'#444\'">' +
            '<div style="' + headerStyle + '">' +
                '<div>' + protoBadge + badgesHtml + '</div>' +
                '<span style="' + statusPillStyle + '">' + (inBaseline ? '✓ Known' : '● New') + '</span>' +
            '</div>' +
            '<div style="margin-bottom:10px;">' +
                '<div style="' + nameStyle + '">' + name + '</div>' +
                '<div style="' + addrStyle + '">' + addr + ' <span style="color:#666;font-size:10px;">(' + addrType + ')</span></div>' +
            '</div>' +
            '<div style="' + rssiRowStyle + '">' +
                '<span style="' + rssiValueStyle + '">' + rssiStr + '</span>' +
                '<span style="' + rangeBandStyle + '">' + rangeBand + '</span>' +
            '</div>' +
            (mfr ? '<div style="' + mfrStyle + '">' + mfr + '</div>' : '') +
            '<div style="' + metaStyle + '">' +
                '<span>Seen ' + seenCount + '×</span>' +
                '<span>Just now</span>' +
            '</div>' +
        '</div>';
    }

    /**
     * Get RSSI color
     */
    function getRssiColor(rssi) {
        if (rssi === null || rssi === undefined) return '#666';
        if (rssi >= -50) return '#22c55e';
        if (rssi >= -60) return '#84cc16';
        if (rssi >= -70) return '#eab308';
        if (rssi >= -80) return '#f97316';
        return '#ef4444';
    }

    /**
     * Escape HTML
     */
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

    /**
     * Set baseline
     */
    async function setBaseline() {
        try {
            const response = await fetch('/api/bluetooth/baseline/set', { method: 'POST' });
            const data = await response.json();

            if (data.status === 'success') {
                baselineSet = true;
                baselineCount = data.device_count;
                updateBaselineStatus();
            }
        } catch (err) {
            console.error('Failed to set baseline:', err);
        }
    }

    /**
     * Clear baseline
     */
    async function clearBaseline() {
        try {
            const response = await fetch('/api/bluetooth/baseline/clear', { method: 'POST' });
            const data = await response.json();

            if (data.status === 'success') {
                baselineSet = false;
                baselineCount = 0;
                updateBaselineStatus();
            }
        } catch (err) {
            console.error('Failed to clear baseline:', err);
        }
    }

    /**
     * Update baseline status display
     */
    function updateBaselineStatus() {
        if (!baselineStatusEl) return;

        if (baselineSet) {
            baselineStatusEl.textContent = `Baseline: ${baselineCount} devices`;
            baselineStatusEl.style.color = '#22c55e';
        } else {
            baselineStatusEl.textContent = 'No baseline';
            baselineStatusEl.style.color = '';
        }
    }

    /**
     * Export data
     */
    function exportData(format) {
        window.open(`/api/bluetooth/export?format=${format}`, '_blank');
    }

    /**
     * Show error message
     */
    function showErrorMessage(message) {
        console.error('[BT] Error:', message);
        // Could show a toast notification here
    }

    // Public API
    return {
        init,
        startScan,
        stopScan,
        checkCapabilities,
        setBaseline,
        clearBaseline,
        exportData,
        showModal,
        closeModal,
        copyAddress,
        getDevices: () => Array.from(devices.values()),
        isScanning: () => isScanning
    };
})();

// Global functions for onclick handlers in HTML
function btStartScan() { BluetoothMode.startScan(); }
function btStopScan() { BluetoothMode.stopScan(); }
function btCheckCapabilities() { BluetoothMode.checkCapabilities(); }
function btSetBaseline() { BluetoothMode.setBaseline(); }
function btClearBaseline() { BluetoothMode.clearBaseline(); }
function btExport(format) { BluetoothMode.exportData(format); }

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        if (document.getElementById('bluetoothMode')) {
            BluetoothMode.init();
        }
    });
} else {
    if (document.getElementById('bluetoothMode')) {
        BluetoothMode.init();
    }
}

// Make globally available
window.BluetoothMode = BluetoothMode;
