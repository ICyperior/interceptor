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

    // DOM elements (cached)
    let startBtn, stopBtn, messageContainer, deviceContainer;
    let adapterSelect, scanModeSelect, transportSelect, durationInput, minRssiInput;
    let baselineStatusEl, capabilityStatusEl;

    /**
     * Initialize the Bluetooth mode
     */
    function init() {
        // Cache DOM elements
        startBtn = document.getElementById('startBtBtn');
        stopBtn = document.getElementById('stopBtBtn');
        messageContainer = document.getElementById('btMessageContainer');
        deviceContainer = document.getElementById('output');
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
        if (!capabilityStatusEl || !messageContainer) return;

        capabilityStatusEl.style.display = 'block';

        if (typeof MessageCard !== 'undefined') {
            const card = MessageCard.createCapabilityWarning(issues);
            if (card) {
                capabilityStatusEl.innerHTML = '';
                capabilityStatusEl.appendChild(card);
            }
        } else {
            capabilityStatusEl.innerHTML = `
                <div class="warning-text" style="color: #f59e0b;">
                    ${issues.map(i => `<div>${i}</div>`).join('')}
                </div>
            `;
        }
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
                showScanningMessage(mode);
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
            removeScanningMessage();
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

        // Update global status if available
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        if (statusDot) statusDot.classList.toggle('running', scanning);
        if (statusText) statusText.textContent = scanning ? 'Scanning...' : 'Idle';
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
            showScanningMessage(data.mode);
        });

        eventSource.addEventListener('scan_stopped', (e) => {
            setScanning(false);
            removeScanningMessage();
            const data = JSON.parse(e.data);
            showScanCompleteMessage(data.device_count, data.duration);
        });

        eventSource.addEventListener('error', (e) => {
            try {
                const data = JSON.parse(e.data);
                showErrorMessage(data.message);
            } catch {
                // Connection error
            }
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
    }

    /**
     * Render a device card
     */
    function renderDevice(device) {
        if (!deviceContainer) return;

        const existingCard = deviceContainer.querySelector(`[data-device-id="${device.device_id}"]`);

        if (typeof DeviceCard !== 'undefined') {
            const cardHtml = DeviceCard.createDeviceCard(device);

            if (existingCard) {
                existingCard.outerHTML = cardHtml;
            } else {
                deviceContainer.insertAdjacentHTML('afterbegin', cardHtml);
            }

            // Re-attach click handler
            const newCard = deviceContainer.querySelector(`[data-device-id="${device.device_id}"]`);
            if (newCard) {
                newCard.addEventListener('click', () => showDeviceDetails(device.device_id));
            }
        } else {
            // Fallback simple rendering
            const cardHtml = createSimpleDeviceCard(device);

            if (existingCard) {
                existingCard.outerHTML = cardHtml;
            } else {
                deviceContainer.insertAdjacentHTML('afterbegin', cardHtml);
            }
        }
    }

    /**
     * Simple device card fallback
     */
    function createSimpleDeviceCard(device) {
        const protoBadge = device.protocol === 'ble'
            ? '<span class="signal-proto-badge" style="background: rgba(59, 130, 246, 0.15); color: #3b82f6; border: 1px solid rgba(59, 130, 246, 0.3);">BLE</span>'
            : '<span class="signal-proto-badge" style="background: rgba(139, 92, 246, 0.15); color: #8b5cf6; border: 1px solid rgba(139, 92, 246, 0.3);">CLASSIC</span>';

        const badges = [];
        if (device.is_new) badges.push('<span class="device-heuristic-badge new">New</span>');
        if (device.is_persistent) badges.push('<span class="device-heuristic-badge persistent">Persistent</span>');
        if (device.is_beacon_like) badges.push('<span class="device-heuristic-badge beacon">Beacon-like</span>');

        const rssiColor = getRssiColor(device.rssi_current);

        return `
            <div class="signal-card device-card" data-device-id="${device.device_id}">
                <div class="signal-card-header">
                    <div class="signal-card-badges">
                        ${protoBadge}
                        ${badges.join('')}
                    </div>
                </div>
                <div class="signal-card-body">
                    <div class="device-name">${escapeHtml(device.name || 'Unknown Device')}</div>
                    <div class="device-address">${escapeHtml(device.address)} (${device.address_type || 'unknown'})</div>
                    <div class="rssi-display">
                        <span class="rssi-current" style="color: ${rssiColor}">${device.rssi_current !== null ? device.rssi_current + ' dBm' : '--'}</span>
                    </div>
                    ${device.manufacturer_name ? `<div class="device-manufacturer">${escapeHtml(device.manufacturer_name)}</div>` : ''}
                </div>
            </div>
        `;
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
     * Show device details
     */
    async function showDeviceDetails(deviceId) {
        try {
            const response = await fetch(`/api/bluetooth/devices/${encodeURIComponent(deviceId)}`);
            const device = await response.json();

            // Toggle advanced panel or show modal
            const card = deviceContainer?.querySelector(`[data-device-id="${deviceId}"]`);
            if (card) {
                const panel = card.querySelector('.signal-advanced-panel');
                if (panel) {
                    panel.classList.toggle('show');
                    if (panel.classList.contains('show')) {
                        panel.innerHTML = `<pre style="font-size: 10px; overflow: auto;">${JSON.stringify(device, null, 2)}</pre>`;
                    }
                }
            }
        } catch (err) {
            console.error('Failed to get device details:', err);
        }
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
                showBaselineSetMessage(data.device_count);
            } else {
                showErrorMessage(data.message || 'Failed to set baseline');
            }
        } catch (err) {
            console.error('Failed to set baseline:', err);
            showErrorMessage('Failed to set baseline');
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
            baselineStatusEl.textContent = `Baseline set: ${baselineCount} device${baselineCount !== 1 ? 's' : ''}`;
            baselineStatusEl.style.color = '#22c55e';
        } else {
            baselineStatusEl.textContent = 'No baseline set';
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
     * Show scanning message
     */
    function showScanningMessage(mode) {
        if (!messageContainer || typeof MessageCard === 'undefined') return;

        removeScanningMessage();
        const card = MessageCard.createScanningCard({
            backend: mode,
            deviceCount: devices.size
        });
        messageContainer.appendChild(card);
    }

    /**
     * Remove scanning message
     */
    function removeScanningMessage() {
        MessageCard?.removeMessage?.('btScanningStatus');
    }

    /**
     * Show scan complete message
     */
    function showScanCompleteMessage(deviceCount, duration) {
        if (!messageContainer || typeof MessageCard === 'undefined') return;

        const card = MessageCard.createScanCompleteCard(deviceCount, duration || 0);
        messageContainer.appendChild(card);
    }

    /**
     * Show baseline set message
     */
    function showBaselineSetMessage(count) {
        if (!messageContainer || typeof MessageCard === 'undefined') return;

        const card = MessageCard.createBaselineCard(count, true);
        messageContainer.appendChild(card);
    }

    /**
     * Show error message
     */
    function showErrorMessage(message) {
        if (!messageContainer || typeof MessageCard === 'undefined') return;

        const card = MessageCard.createErrorCard(message, () => startScan());
        messageContainer.appendChild(card);
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
        // Only init if we're on a page with Bluetooth mode
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
