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

    // Proximity visualization state
    let devicePositions = new Map(); // Persistent positions for smooth visualization
    let lastVisualizationUpdate = 0;
    let visualizationTimer = null;
    const VISUALIZATION_UPDATE_INTERVAL = 100; // ms
    const VISUALIZATION_REFRESH_INTERVAL = 1000; // 1 second refresh for fading
    const DEVICE_STALE_THRESHOLD = 30000; // 30 seconds before device fades

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

        // Check capabilities on load
        checkCapabilities();

        // Check scan status (in case page was reloaded during scan)
        checkScanStatus();

        // Initialize proximity visualization
        initHeatmap();

        // Start visualization refresh timer
        startVisualizationTimer();

        // Initialize timeline as collapsed
        initTimeline();

        // Set initial panel states
        updateVisualizationPanels();
    }

    /**
     * Initialize the proximity visualization
     */
    function initHeatmap() {
        const canvas = document.getElementById('btRadarCanvas');
        if (!canvas) return;

        // Set canvas size for crisp rendering
        canvas.width = 180;
        canvas.height = 180;

        drawProximityVisualization();
    }

    /**
     * Draw clean zone-based proximity visualization
     */
    function drawProximityVisualization() {
        const canvas = document.getElementById('btRadarCanvas');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const width = canvas.width;
        const height = canvas.height;
        const centerX = width / 2;
        const centerY = height / 2;
        const maxRadius = Math.min(width, height) / 2 - 10;

        // Clear canvas
        ctx.clearRect(0, 0, width, height);

        // Define zones with colors
        const zones = [
            { name: 'VERY CLOSE', minRssi: -40, radius: 0.25, color: 'rgba(34, 197, 94, 0.08)', borderColor: 'rgba(34, 197, 94, 0.4)' },
            { name: 'CLOSE', minRssi: -55, radius: 0.5, color: 'rgba(132, 204, 22, 0.06)', borderColor: 'rgba(132, 204, 22, 0.3)' },
            { name: 'NEARBY', minRssi: -70, radius: 0.75, color: 'rgba(234, 179, 8, 0.05)', borderColor: 'rgba(234, 179, 8, 0.25)' },
            { name: 'FAR', minRssi: -100, radius: 1.0, color: 'rgba(239, 68, 68, 0.04)', borderColor: 'rgba(239, 68, 68, 0.2)' }
        ];

        // Draw zones from outside in (so inner zones overlay)
        for (let i = zones.length - 1; i >= 0; i--) {
            const zone = zones[i];
            const r = maxRadius * zone.radius;

            // Fill zone
            ctx.beginPath();
            ctx.arc(centerX, centerY, r, 0, Math.PI * 2);
            ctx.fillStyle = zone.color;
            ctx.fill();

            // Draw zone border
            ctx.beginPath();
            ctx.arc(centerX, centerY, r, 0, Math.PI * 2);
            ctx.strokeStyle = zone.borderColor;
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        // Count devices in each zone
        const zoneCounts = [0, 0, 0, 0]; // very close, close, nearby, far
        const now = Date.now();

        // Update device positions and count per zone
        devices.forEach((device, deviceId) => {
            const rssi = device.rssi_current;
            if (rssi == null) return;

            // Determine zone
            let zoneIndex = 3; // far by default
            if (rssi >= -40) zoneIndex = 0;
            else if (rssi >= -55) zoneIndex = 1;
            else if (rssi >= -70) zoneIndex = 2;

            zoneCounts[zoneIndex]++;

            // Get or create position for this device
            let pos = devicePositions.get(deviceId);
            if (!pos) {
                // Assign new position with random angle
                const angle = Math.random() * Math.PI * 2;
                pos = { angle, lastSeen: now, rssi };
                devicePositions.set(deviceId, pos);
            } else {
                pos.lastSeen = now;
                pos.rssi = rssi;
            }
        });

        // Clean up stale device positions
        devicePositions.forEach((pos, deviceId) => {
            if (now - pos.lastSeen > DEVICE_STALE_THRESHOLD) {
                devicePositions.delete(deviceId);
            }
        });

        // Draw device dots
        devicePositions.forEach((pos, deviceId) => {
            const device = devices.get(deviceId);
            const rssi = pos.rssi;
            if (rssi == null) return;

            // Calculate radius based on RSSI (stronger = closer to center)
            const normalizedRssi = Math.max(0, Math.min(1, (rssi + 100) / 70));
            const radius = maxRadius * (1 - normalizedRssi * 0.85 + 0.1); // Keep some margin from center

            // Calculate position
            const x = centerX + Math.cos(pos.angle) * radius;
            const y = centerY + Math.sin(pos.angle) * radius;

            // Calculate opacity based on staleness
            const age = now - pos.lastSeen;
            const opacity = age < 5000 ? 1.0 : Math.max(0.3, 1 - (age - 5000) / DEVICE_STALE_THRESHOLD);

            // Get color
            const color = getRssiColorRgb(rssi);

            // Draw glow
            const gradient = ctx.createRadialGradient(x, y, 0, x, y, 12);
            gradient.addColorStop(0, `rgba(${color.r}, ${color.g}, ${color.b}, ${0.4 * opacity})`);
            gradient.addColorStop(1, `rgba(${color.r}, ${color.g}, ${color.b}, 0)`);
            ctx.fillStyle = gradient;
            ctx.beginPath();
            ctx.arc(x, y, 12, 0, Math.PI * 2);
            ctx.fill();

            // Draw dot
            ctx.fillStyle = `rgba(${color.r}, ${color.g}, ${color.b}, ${opacity})`;
            ctx.beginPath();
            ctx.arc(x, y, 4, 0, Math.PI * 2);
            ctx.fill();

            // Draw border
            ctx.strokeStyle = `rgba(255, 255, 255, ${0.5 * opacity})`;
            ctx.lineWidth = 1;
            ctx.stroke();
        });

        // Draw center point (user position)
        ctx.fillStyle = '#00d4ff';
        ctx.shadowColor = '#00d4ff';
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.arc(centerX, centerY, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;

        // Draw "YOU" label
        ctx.fillStyle = 'rgba(0, 212, 255, 0.8)';
        ctx.font = '8px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('YOU', centerX, centerY + 14);

        // Draw zone counts on the right side
        const countX = width - 8;
        ctx.textAlign = 'right';
        ctx.font = '9px monospace';

        const countLabels = [
            { count: zoneCounts[0], color: '#22c55e', y: centerY - 45 },
            { count: zoneCounts[1], color: '#84cc16', y: centerY - 15 },
            { count: zoneCounts[2], color: '#eab308', y: centerY + 15 },
            { count: zoneCounts[3], color: '#ef4444', y: centerY + 45 }
        ];

        countLabels.forEach(item => {
            if (item.count > 0) {
                ctx.fillStyle = item.color;
                ctx.fillText(item.count.toString(), countX, item.y);
            }
        });

        // Draw total count at bottom
        ctx.fillStyle = 'rgba(255, 255, 255, 0.5)';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(`${devices.size} devices`, centerX, height - 4);

        // If no devices and not scanning, show message
        if (devices.size === 0 && !isScanning) {
            ctx.fillStyle = 'rgba(255, 255, 255, 0.4)';
            ctx.font = '11px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('Start scan to', centerX, centerY - 8);
            ctx.fillText('detect devices', centerX, centerY + 8);
        }
    }

    /**
     * Schedule visualization update (throttled)
     */
    function scheduleVisualizationUpdate() {
        const now = Date.now();
        if (now - lastVisualizationUpdate >= VISUALIZATION_UPDATE_INTERVAL) {
            lastVisualizationUpdate = now;
            drawProximityVisualization();
        }
    }

    /**
     * Start periodic visualization refresh for smooth fading
     */
    function startVisualizationTimer() {
        if (visualizationTimer) clearInterval(visualizationTimer);
        visualizationTimer = setInterval(() => {
            if (devicePositions.size > 0 || isScanning) {
                drawProximityVisualization();
            }
        }, VISUALIZATION_REFRESH_INTERVAL);
    }

    /**
     * Stop visualization timer
     */
    function stopVisualizationTimer() {
        if (visualizationTimer) {
            clearInterval(visualizationTimer);
            visualizationTimer = null;
        }
    }

    /**
     * Get RSSI color as RGB object
     */
    function getRssiColorRgb(rssi) {
        if (rssi === null || rssi === undefined) return { r: 102, g: 102, b: 102 };
        if (rssi >= -40) return { r: 34, g: 197, b: 94 };   // Green - very close
        if (rssi >= -55) return { r: 132, g: 204, b: 22 };  // Lime - close
        if (rssi >= -70) return { r: 234, g: 179, b: 8 };   // Yellow - nearby
        if (rssi >= -85) return { r: 249, g: 115, b: 22 };  // Orange
        return { r: 239, g: 68, b: 68 };                     // Red - far
    }

    /**
     * Initialize timeline as collapsed
     */
    function initTimeline() {
        const timelineContainer = document.getElementById('bluetoothTimelineContainer');
        if (!timelineContainer) return;

        // Check if ActivityTimeline exists and initialize it collapsed
        if (typeof ActivityTimeline !== 'undefined') {
            // Timeline will be initialized by the main app, but we'll collapse it
            setTimeout(() => {
                const timeline = timelineContainer.querySelector('.activity-timeline');
                if (timeline) {
                    const content = timeline.querySelector('.activity-timeline-content');
                    const toggleBtn = timeline.querySelector('.activity-timeline-toggle');
                    if (content) content.style.display = 'none';
                    if (toggleBtn) toggleBtn.textContent = '▶';
                }
            }, 500);
        } else {
            // Create a simple placeholder
            timelineContainer.innerHTML = `
                <div style="background: var(--bg-tertiary, #1a1a1a); border: 1px solid var(--border-color, #333); border-radius: 6px; overflow: hidden;">
                    <div style="padding: 10px 12px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; background: var(--bg-secondary, #252525);" onclick="this.nextElementSibling.style.display = this.nextElementSibling.style.display === 'none' ? 'block' : 'none'; this.querySelector('span:last-child').textContent = this.nextElementSibling.style.display === 'none' ? '▶' : '▼';">
                        <span style="font-size: 12px; color: var(--text-primary, #e0e0e0);">Device Activity</span>
                        <span style="font-size: 10px; color: var(--text-dim, #666);">▶</span>
                    </div>
                    <div id="btActivityContent" style="display: none; padding: 12px; color: var(--text-dim, #666); font-size: 11px; text-align: center;">
                        Activity timeline will appear here during scanning
                    </div>
                </div>
            `;
        }
    }

    /**
     * Select a device and show in Selected Device panel
     */
    function selectDevice(deviceId) {
        const device = devices.get(deviceId);
        if (!device) return;

        selectedDeviceId = deviceId;

        // Update selected device panel
        const panel = document.getElementById('btSelectedDevice');
        if (!panel) return;

        const rssi = device.rssi_current;
        const rssiColor = getRssiColor(rssi);
        const flags = device.heuristic_flags || [];

        panel.innerHTML = `
            <div style="padding: 10px;">
                <!-- Device header -->
                <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
                    <div>
                        <div style="font-size: 16px; font-weight: 600; color: #e0e0e0; margin-bottom: 4px;">
                            ${escapeHtml(device.name || formatDeviceId(device.address))}
                        </div>
                        <div style="font-family: monospace; font-size: 12px; color: #00d4ff;">
                            ${escapeHtml(device.address)}
                            <span style="color: #666; font-size: 10px;">(${device.address_type || 'unknown'})</span>
                        </div>
                    </div>
                    <div style="text-align: right;">
                        <div style="font-family: monospace; font-size: 24px; font-weight: 700; color: ${rssiColor};">
                            ${rssi != null ? rssi : '--'}
                            <span style="font-size: 11px; color: #666;">dBm</span>
                        </div>
                        <div style="font-size: 10px; color: #888; text-transform: uppercase;">${device.range_band || 'unknown'}</div>
                    </div>
                </div>

                <!-- Badges -->
                <div style="display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px;">
                    <span style="background: ${device.protocol === 'ble' ? 'rgba(59,130,246,0.15)' : 'rgba(139,92,246,0.15)'}; color: ${device.protocol === 'ble' ? '#3b82f6' : '#8b5cf6'}; padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: 600;">
                        ${(device.protocol || 'BLE').toUpperCase()}
                    </span>
                    ${flags.map(f => `<span style="background: rgba(107,114,128,0.15); color: #9ca3af; padding: 3px 8px; border-radius: 4px; font-size: 10px;">${f.replace('_', ' ').toUpperCase()}</span>`).join('')}
                    <span style="background: ${device.in_baseline ? 'rgba(34,197,94,0.15)' : 'rgba(59,130,246,0.15)'}; color: ${device.in_baseline ? '#22c55e' : '#3b82f6'}; padding: 3px 8px; border-radius: 4px; font-size: 10px;">
                        ${device.in_baseline ? '✓ BASELINE' : '● NEW'}
                    </span>
                </div>

                <!-- Info grid -->
                <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 12px;">
                    <div style="background: #141428; padding: 8px 10px; border-radius: 4px;">
                        <div style="font-size: 9px; color: #666; text-transform: uppercase;">Manufacturer</div>
                        <div style="font-size: 11px; color: #e0e0e0; margin-top: 2px;">${device.manufacturer_name || 'Unknown'}</div>
                    </div>
                    <div style="background: #141428; padding: 8px 10px; border-radius: 4px;">
                        <div style="font-size: 9px; color: #666; text-transform: uppercase;">Mfr ID</div>
                        <div style="font-family: monospace; font-size: 11px; color: #e0e0e0; margin-top: 2px;">
                            ${device.manufacturer_id != null ? '0x' + device.manufacturer_id.toString(16).toUpperCase().padStart(4, '0') : '--'}
                        </div>
                    </div>
                    <div style="background: #141428; padding: 8px 10px; border-radius: 4px;">
                        <div style="font-size: 9px; color: #666; text-transform: uppercase;">Seen</div>
                        <div style="font-size: 11px; color: #e0e0e0; margin-top: 2px;">${device.seen_count || 0} times</div>
                    </div>
                    <div style="background: #141428; padding: 8px 10px; border-radius: 4px;">
                        <div style="font-size: 9px; color: #666; text-transform: uppercase;">Confidence</div>
                        <div style="font-size: 11px; color: #e0e0e0; margin-top: 2px;">${device.rssi_confidence ? Math.round(device.rssi_confidence * 100) + '%' : '--'}</div>
                    </div>
                </div>

                <!-- Signal stats -->
                <div style="background: #141428; padding: 10px; border-radius: 4px; margin-bottom: 12px;">
                    <div style="font-size: 9px; color: #666; text-transform: uppercase; margin-bottom: 8px;">Signal Statistics</div>
                    <div style="display: flex; justify-content: space-between;">
                        <div style="text-align: center;">
                            <div style="font-size: 9px; color: #666;">MIN</div>
                            <div style="font-family: monospace; font-size: 12px; color: #ef4444;">${device.rssi_min != null ? device.rssi_min : '--'}</div>
                        </div>
                        <div style="text-align: center;">
                            <div style="font-size: 9px; color: #666;">MEDIAN</div>
                            <div style="font-family: monospace; font-size: 12px; color: #eab308;">${device.rssi_median != null ? Math.round(device.rssi_median) : '--'}</div>
                        </div>
                        <div style="text-align: center;">
                            <div style="font-size: 9px; color: #666;">MAX</div>
                            <div style="font-family: monospace; font-size: 12px; color: #22c55e;">${device.rssi_max != null ? device.rssi_max : '--'}</div>
                        </div>
                        <div style="text-align: center;">
                            <div style="font-size: 9px; color: #666;">CURRENT</div>
                            <div style="font-family: monospace; font-size: 12px; color: ${rssiColor};">${rssi != null ? rssi : '--'}</div>
                        </div>
                    </div>
                </div>

                <!-- Service UUIDs -->
                ${device.service_uuids && device.service_uuids.length > 0 ? `
                <div style="background: #141428; padding: 10px; border-radius: 4px; margin-bottom: 12px;">
                    <div style="font-size: 9px; color: #666; text-transform: uppercase; margin-bottom: 8px;">Service UUIDs (${device.service_uuids.length})</div>
                    <div style="display: flex; flex-wrap: wrap; gap: 4px;">
                        ${device.service_uuids.slice(0, 6).map(uuid => `<span style="font-family: monospace; font-size: 9px; background: #1a1a2e; padding: 3px 6px; border-radius: 3px; color: #888;">${uuid.substring(0, 8)}...</span>`).join('')}
                        ${device.service_uuids.length > 6 ? `<span style="font-size: 9px; color: #666;">+${device.service_uuids.length - 6} more</span>` : ''}
                    </div>
                </div>
                ` : ''}

                <!-- Timestamps -->
                <div style="display: flex; justify-content: space-between; font-size: 10px; color: #666;">
                    <span>First: ${device.first_seen ? new Date(device.first_seen).toLocaleTimeString() : '--'}</span>
                    <span>Last: ${device.last_seen ? new Date(device.last_seen).toLocaleTimeString() : '--'}</span>
                </div>

                <!-- Action buttons -->
                <div style="display: flex; gap: 8px; margin-top: 12px;">
                    <button onclick="BluetoothMode.copyAddress('${device.address}')" style="flex: 1; background: #252538; border: 1px solid #444; color: #e0e0e0; padding: 8px; border-radius: 4px; cursor: pointer; font-size: 11px;">
                        Copy Address
                    </button>
                </div>
            </div>
        `;

        // Highlight selected card
        const cards = deviceContainer?.querySelectorAll('[data-bt-device-id]');
        cards?.forEach(card => {
            if (card.dataset.btDeviceId === deviceId) {
                card.style.borderColor = '#00d4ff';
                card.style.boxShadow = '0 0 0 1px rgba(0, 212, 255, 0.3)';
            } else {
                card.style.borderColor = '#444';
                card.style.boxShadow = 'none';
            }
        });
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

            if (adapterSelect && data.adapters && data.adapters.length > 0) {
                adapterSelect.innerHTML = data.adapters.map(a => {
                    const status = a.powered ? 'UP' : 'DOWN';
                    return `<option value="${a.id}">${a.id} - ${a.name || 'Bluetooth Adapter'} [${status}]</option>`;
                }).join('');
            } else if (adapterSelect) {
                adapterSelect.innerHTML = '<option value="">No adapters found</option>';
            }

            if (data.issues && data.issues.length > 0) {
                showCapabilityWarning(data.issues);
            } else {
                hideCapabilityWarning();
            }

            if (scanModeSelect && data.preferred_backend) {
                const option = scanModeSelect.querySelector(`option[value="${data.preferred_backend}"]`);
                if (option) option.selected = true;
            }

        } catch (err) {
            console.error('Failed to check capabilities:', err);
            showCapabilityWarning(['Failed to check Bluetooth capabilities']);
        }
    }

    function showCapabilityWarning(issues) {
        if (!capabilityStatusEl) return;
        capabilityStatusEl.style.display = 'block';
        capabilityStatusEl.innerHTML = `
            <div style="color: #f59e0b; padding: 10px; background: rgba(245,158,11,0.1); border-radius: 6px; font-size: 12px;">
                ${issues.map(i => `<div>⚠ ${i}</div>`).join('')}
            </div>
        `;
    }

    function hideCapabilityWarning() {
        if (capabilityStatusEl) {
            capabilityStatusEl.style.display = 'none';
            capabilityStatusEl.innerHTML = '';
        }
    }

    async function checkScanStatus() {
        try {
            const response = await fetch('/api/bluetooth/scan/status');
            const data = await response.json();

            if (data.is_scanning) {
                setScanning(true);
                startEventStream();
            }

            if (data.baseline_count > 0) {
                baselineSet = true;
                baselineCount = data.baseline_count;
                updateBaselineStatus();
            }

        } catch (err) {
            console.error('Failed to check scan status:', err);
        }
    }

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

    async function stopScan() {
        try {
            await fetch('/api/bluetooth/scan/stop', { method: 'POST' });
            setScanning(false);
            stopEventStream();
        } catch (err) {
            console.error('Failed to stop scan:', err);
        }
    }

    function setScanning(scanning) {
        isScanning = scanning;

        if (startBtn) startBtn.style.display = scanning ? 'none' : 'block';
        if (stopBtn) stopBtn.style.display = scanning ? 'block' : 'none';

        if (scanning && deviceContainer) {
            deviceContainer.innerHTML = '';
            devices.clear();
            resetStats();

            // Reset selected device panel
            const selectedPanel = document.getElementById('btSelectedDevice');
            if (selectedPanel) {
                selectedPanel.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">Click a device to view details</div>';
            }
        }

        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        if (statusDot) statusDot.classList.toggle('running', scanning);
        if (statusText) statusText.textContent = scanning ? 'Scanning...' : 'Idle';
    }

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
        devicePositions.clear(); // Clear visualization positions
        updateVisualizationPanels();
        drawProximityVisualization();
    }

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
            setScanning(true);
        });

        eventSource.addEventListener('scan_stopped', (e) => {
            setScanning(false);
        });

        eventSource.onerror = () => {
            console.warn('Bluetooth SSE connection error');
        };
    }

    function stopEventStream() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
    }

    function handleDeviceUpdate(device) {
        devices.set(device.device_id, device);
        renderDevice(device);
        updateDeviceCount();
        updateStatsFromDevices();
        updateVisualizationPanels();
        scheduleVisualizationUpdate(); // Throttled visualization update

        // Update selected device panel if this device is selected
        if (selectedDeviceId === device.device_id) {
            selectDevice(device.device_id);
        }

        // Feed to activity timeline
        addToTimeline(device);
    }

    /**
     * Add device event to timeline
     */
    function addToTimeline(device) {
        if (typeof addTimelineEvent === 'function') {
            const normalized = {
                id: device.device_id,
                label: device.name || formatDeviceId(device.address),
                strength: device.rssi_current ? Math.min(5, Math.max(1, Math.ceil((device.rssi_current + 100) / 20))) : 3,
                duration: 1500,
                type: 'bluetooth'
            };
            addTimelineEvent('bluetooth', normalized);
        }

        // Also update our simple timeline if it exists
        const activityContent = document.getElementById('btActivityContent');
        if (activityContent) {
            const time = new Date().toLocaleTimeString();
            const existing = activityContent.querySelector('.bt-activity-list');

            if (!existing) {
                activityContent.innerHTML = '<div class="bt-activity-list" style="max-height: 150px; overflow-y: auto;"></div>';
            }

            const list = activityContent.querySelector('.bt-activity-list');
            const entry = document.createElement('div');
            entry.style.cssText = 'padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 10px;';
            entry.innerHTML = `
                <span style="color: #666;">${time}</span>
                <span style="color: #00d4ff; margin-left: 8px;">${escapeHtml(device.name || formatDeviceId(device.address))}</span>
                <span style="color: ${getRssiColor(device.rssi_current)}; margin-left: 8px;">${device.rssi_current || '--'} dBm</span>
            `;
            list.insertBefore(entry, list.firstChild);

            // Keep only last 50 entries
            while (list.children.length > 50) {
                list.removeChild(list.lastChild);
            }
        }
    }

    /**
     * Update stats from all devices
     */
    function updateStatsFromDevices() {
        // Reset counts
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
            const mfr = (d.manufacturer_name || '').toLowerCase();
            const name = (d.name || '').toLowerCase();
            const rssi = d.rssi_current;
            const flags = d.heuristic_flags || [];

            // Device type classification - more lenient matching
            let classified = false;

            // Phones
            if (name.includes('iphone') || name.includes('phone') || name.includes('pixel') ||
                name.includes('galaxy') || name.includes('android') || name.includes('samsung') ||
                name.includes('oneplus') || name.includes('huawei') || name.includes('xiaomi')) {
                deviceStats.phones++;
                classified = true;
            }
            // Computers
            else if (name.includes('macbook') || name.includes('laptop') || name.includes('pc') ||
                     name.includes('computer') || name.includes('imac') || name.includes('mac mini') ||
                     name.includes('thinkpad') || name.includes('surface') || name.includes('dell') ||
                     name.includes('hp ') || name.includes('lenovo')) {
                deviceStats.computers++;
                classified = true;
            }
            // Audio devices
            else if (name.includes('airpod') || name.includes('headphone') || name.includes('speaker') ||
                     name.includes('buds') || name.includes('audio') || name.includes('beats') ||
                     name.includes('bose') || name.includes('sony wh') || name.includes('sony wf') ||
                     name.includes('jbl') || name.includes('soundbar') || name.includes('earbuds') ||
                     name.includes('jabra') || name.includes('soundcore')) {
                deviceStats.audio++;
                classified = true;
            }
            // Wearables
            else if (name.includes('watch') || name.includes('band') || name.includes('fitbit') ||
                     name.includes('garmin') || name.includes('whoop') || name.includes('oura') ||
                     name.includes('mi band') || name.includes('amazfit')) {
                deviceStats.wearables++;
                classified = true;
            }

            // If not classified by name, try manufacturer
            if (!classified) {
                if (mfr.includes('apple')) {
                    // Could be various Apple devices - count as other
                    deviceStats.other++;
                } else {
                    deviceStats.other++;
                }
            }

            // Signal strength classification
            if (rssi != null) {
                if (rssi >= -50) deviceStats.strong++;
                else if (rssi >= -70) deviceStats.medium++;
                else deviceStats.weak++;
            }

            // Tracker detection - check for known tracker patterns
            const isTracker = name.includes('tile') || name.includes('airtag') ||
                             name.includes('smarttag') || name.includes('chipolo') ||
                             name.includes('tracker') || name.includes('tag');

            if (isTracker) {
                if (!deviceStats.trackers.find(t => t.address === d.address)) {
                    deviceStats.trackers.push(d);
                }
            }

            // FindMy detection - Apple devices with specific characteristics
            // Apple manufacturer ID is 0x004C (76)
            const isApple = mfr.includes('apple') || d.manufacturer_id === 76;
            const hasBeaconBehavior = flags.includes('beacon_like') || flags.includes('persistent');

            if (isApple && hasBeaconBehavior) {
                if (!deviceStats.findmy.find(t => t.address === d.address)) {
                    deviceStats.findmy.push(d);
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
            if (devices.size === 0) {
                trackerList.innerHTML = '<div style="color:#666;padding:10px;text-align:center;font-size:11px;">Start scanning to detect trackers</div>';
            } else if (deviceStats.trackers.length === 0) {
                trackerList.innerHTML = '<div style="color:#22c55e;padding:10px;text-align:center;font-size:11px;">✓ No known trackers detected</div>';
            } else {
                trackerList.innerHTML = deviceStats.trackers.map(t => `
                    <div style="padding:8px;border-bottom:1px solid rgba(255,255,255,0.05);cursor:pointer;" onclick="BluetoothMode.selectDevice('${t.device_id}')">
                        <div style="display:flex;justify-content:space-between;">
                            <span style="color:#f97316;font-size:11px;">${escapeHtml(t.name || formatDeviceId(t.address))}</span>
                            <span style="color:#666;font-size:10px;">${t.rssi_current || '--'} dBm</span>
                        </div>
                        <div style="font-size:9px;color:#666;font-family:monospace;">${t.address}</div>
                    </div>
                `).join('');
            }
        }

        // FindMy Detection
        const findmyList = document.getElementById('btFindMyList');
        if (findmyList) {
            if (devices.size === 0) {
                findmyList.innerHTML = '<div style="color:#666;padding:10px;text-align:center;font-size:11px;">Start scanning to detect FindMy devices</div>';
            } else if (deviceStats.findmy.length === 0) {
                findmyList.innerHTML = '<div style="color:#666;padding:10px;text-align:center;font-size:11px;">No FindMy-compatible devices detected</div>';
            } else {
                findmyList.innerHTML = deviceStats.findmy.map(t => `
                    <div style="padding:8px;border-bottom:1px solid rgba(255,255,255,0.05);cursor:pointer;" onclick="BluetoothMode.selectDevice('${t.device_id}')">
                        <div style="display:flex;justify-content:space-between;">
                            <span style="color:#007aff;font-size:11px;">${escapeHtml(t.name || 'Apple Device')}</span>
                            <span style="color:#666;font-size:10px;">${t.rssi_current || '--'} dBm</span>
                        </div>
                        <div style="font-size:9px;color:#666;font-family:monospace;">${t.address}</div>
                    </div>
                `).join('');
            }
        }
    }

    function updateDeviceCount() {
        const countEl = document.getElementById('btDeviceListCount');
        if (countEl) {
            countEl.textContent = devices.size;
        }
    }

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

        const displayName = device.name || formatDeviceId(device.address);
        const name = escapeHtml(displayName);
        const addr = escapeHtml(device.address || 'Unknown');
        const addrType = escapeHtml(device.address_type || 'unknown');
        const rssi = device.rssi_current;
        const rssiStr = (rssi != null) ? rssi + ' dBm' : '--';
        const rssiColor = getRssiColor(rssi);
        const mfr = device.manufacturer_name ? escapeHtml(device.manufacturer_name) : '';
        const seenCount = device.seen_count || 0;
        const rangeBand = device.range_band || 'unknown';
        const inBaseline = device.in_baseline || false;
        const isSelected = selectedDeviceId === device.device_id;

        const cardStyle = 'display:block;background:#1a1a2e;border:1px solid ' + (isSelected ? '#00d4ff' : '#444') + ';border-radius:8px;padding:14px;margin-bottom:10px;cursor:pointer;transition:border-color 0.2s;' + (isSelected ? 'box-shadow:0 0 0 1px rgba(0,212,255,0.3);' : '');
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

        return '<div data-bt-device-id="' + escapeHtml(device.device_id) + '" style="' + cardStyle + '" onclick="BluetoothMode.selectDevice(\'' + deviceIdEscaped + '\')" onmouseover="this.style.borderColor=\'#00d4ff\'" onmouseout="this.style.borderColor=\'' + (isSelected ? '#00d4ff' : '#444') + '\'">' +
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

    function getRssiColor(rssi) {
        if (rssi == null) return '#666';
        if (rssi >= -50) return '#22c55e';
        if (rssi >= -60) return '#84cc16';
        if (rssi >= -70) return '#eab308';
        if (rssi >= -80) return '#f97316';
        return '#ef4444';
    }

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

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

    function exportData(format) {
        window.open(`/api/bluetooth/export?format=${format}`, '_blank');
    }

    function showErrorMessage(message) {
        console.error('[BT] Error:', message);
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
        selectDevice,
        copyAddress,
        getDevices: () => Array.from(devices.values()),
        isScanning: () => isScanning
    };
})();

// Global functions for onclick handlers
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

window.BluetoothMode = BluetoothMode;
