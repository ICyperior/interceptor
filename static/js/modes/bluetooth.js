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

        console.log('[BT] DOM elements:', {
            startBtn: !!startBtn,
            stopBtn: !!stopBtn,
            deviceContainer: !!deviceContainer,
            adapterSelect: !!adapterSelect
        });

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

        // Clear container when starting scan (removes legacy cards and placeholder)
        if (scanning && deviceContainer) {
            deviceContainer.innerHTML = '';
            devices.clear();  // Also clear our device map
        }

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
        console.log('[BT] SSE stream connected');

        eventSource.addEventListener('device_update', (e) => {
            console.log('[BT] SSE device_update event:', e.data);
            try {
                const device = JSON.parse(e.data);
                handleDeviceUpdate(device);
            } catch (err) {
                console.error('Failed to parse device update:', err);
            }
        });

        // Also listen for generic messages as fallback
        eventSource.onmessage = (e) => {
            console.log('[BT] SSE generic message:', e.data);
        };

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
        console.log('[BT] Device update received:', device);
        devices.set(device.device_id, device);
        renderDevice(device);
        updateDeviceCount();
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
        console.log('[BT] Rendering device:', device.device_id, device);
        if (!deviceContainer) {
            deviceContainer = document.getElementById('btDeviceListContent');
            if (!deviceContainer) {
                console.error('[BT] No container - cannot render');
                return;
            }
        }

        // Use simple inline rendering with NO CSS classes to avoid any interference
        const escapedId = CSS.escape(device.device_id);
        const existingCard = deviceContainer.querySelector('[data-bt-device-id="' + escapedId + '"]');
        const cardHtml = createSimpleDeviceCard(device);

        console.log('[BT] Card HTML length:', cardHtml.length, 'existing:', !!existingCard);

        if (existingCard) {
            existingCard.outerHTML = cardHtml;
        } else {
            deviceContainer.insertAdjacentHTML('afterbegin', cardHtml);
        }

        // Log container state
        console.log('[BT] Container now has', deviceContainer.children.length, 'children');
    }

    /**
     * Simple device card - pure inline rendering with NO CSS classes
     * This avoids any CSS conflicts by using only inline styles
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

        const name = escapeHtml(device.name || device.device_id || 'Unknown');
        const addr = escapeHtml(device.address || 'Unknown');
        const addrType = escapeHtml(device.address_type || 'unknown');
        const rssi = device.rssi_current;
        const rssiStr = (rssi !== null && rssi !== undefined) ? rssi + ' dBm' : '--';
        const rssiColor = getRssiColor(rssi);
        const mfr = device.manufacturer_name ? escapeHtml(device.manufacturer_name) : '';
        const seenCount = device.seen_count || 0;
        const rangeBand = device.range_band || 'unknown';
        const inBaseline = device.in_baseline || false;

        // Use a div with NO classes at all - pure inline styles to avoid any CSS interference
        const cardStyle = 'display:block;background:#1a1a2e;border:1px solid #444;border-radius:8px;padding:14px;margin-bottom:10px;box-sizing:border-box;overflow:visible;';
        const headerStyle = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;';
        const nameStyle = 'font-size:15px;font-weight:600;color:#e0e0e0;margin-bottom:4px;';
        const addrStyle = 'font-family:monospace;font-size:12px;color:#00d4ff;';
        const rssiRowStyle = 'display:flex;justify-content:space-between;align-items:center;background:#141428;padding:12px;border-radius:6px;margin:10px 0;';
        const rssiValueStyle = 'font-family:monospace;font-size:18px;font-weight:700;color:' + rssiColor + ';';
        const rangeBandStyle = 'font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.5px;';
        const mfrStyle = 'font-size:11px;color:#888;margin-bottom:8px;';
        const metaStyle = 'display:flex;justify-content:space-between;font-size:10px;color:#666;';
        const statusPillStyle = 'background:' + (inBaseline ? 'rgba(34,197,94,0.15)' : 'rgba(59,130,246,0.15)') + ';color:' + (inBaseline ? '#22c55e' : '#3b82f6') + ';padding:3px 10px;border-radius:12px;font-size:10px;font-weight:500;';

        return '<div data-bt-device-id="' + escapeHtml(device.device_id) + '" style="' + cardStyle + '">' +
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
            (mfr ? '<div style="' + mfrStyle + '">Manufacturer: ' + mfr + '</div>' : '') +
            '<div style="' + metaStyle + '">' +
                '<span>Seen: ' + seenCount + ' times</span>' +
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
