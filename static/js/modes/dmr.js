/**
 * Intercept - DMR / Digital Voice Mode
 * Decoding DMR, P25, NXDN, D-STAR digital voice protocols
 */

// ============== STATE ==============
let isDmrRunning = false;
let dmrEventSource = null;
let dmrCallCount = 0;
let dmrSyncCount = 0;
let dmrCallHistory = [];
let dmrCurrentProtocol = '--';

// ============== TOOLS CHECK ==============

function checkDmrTools() {
    fetch('/dmr/tools')
        .then(r => r.json())
        .then(data => {
            const warning = document.getElementById('dmrToolsWarning');
            const warningText = document.getElementById('dmrToolsWarningText');
            if (!warning) return;

            const missing = [];
            if (!data.dsd) missing.push('dsd (Digital Speech Decoder)');
            if (!data.rtl_fm) missing.push('rtl_fm (RTL-SDR)');

            if (missing.length > 0) {
                warning.style.display = 'block';
                if (warningText) warningText.textContent = missing.join(', ');
            } else {
                warning.style.display = 'none';
            }
        })
        .catch(() => {});
}

// ============== START / STOP ==============

function startDmr() {
    const frequency = parseFloat(document.getElementById('dmrFrequency')?.value || 462.5625);
    const protocol = document.getElementById('dmrProtocol')?.value || 'auto';
    const gain = parseInt(document.getElementById('dmrGain')?.value || 40);
    const device = typeof getSelectedDevice === 'function' ? getSelectedDevice() : 0;

    fetch('/dmr/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ frequency, protocol, gain, device })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'started') {
            isDmrRunning = true;
            dmrCallCount = 0;
            dmrSyncCount = 0;
            dmrCallHistory = [];
            updateDmrUI();
            connectDmrSSE();
            const statusEl = document.getElementById('dmrStatus');
            if (statusEl) statusEl.textContent = 'DECODING';
            if (typeof showNotification === 'function') {
                showNotification('DMR', `Decoding ${frequency} MHz (${protocol.toUpperCase()})`);
            }
        } else {
            if (typeof showNotification === 'function') {
                showNotification('Error', data.message || 'Failed to start DMR');
            }
        }
    })
    .catch(err => console.error('[DMR] Start error:', err));
}

function stopDmr() {
    fetch('/dmr/stop', { method: 'POST' })
    .then(r => r.json())
    .then(() => {
        isDmrRunning = false;
        if (dmrEventSource) { dmrEventSource.close(); dmrEventSource = null; }
        updateDmrUI();
        const statusEl = document.getElementById('dmrStatus');
        if (statusEl) statusEl.textContent = 'IDLE';
    })
    .catch(err => console.error('[DMR] Stop error:', err));
}

// ============== SSE STREAMING ==============

function connectDmrSSE() {
    if (dmrEventSource) dmrEventSource.close();
    dmrEventSource = new EventSource('/dmr/stream');

    dmrEventSource.onmessage = function(event) {
        const msg = JSON.parse(event.data);
        handleDmrMessage(msg);
    };

    dmrEventSource.onerror = function() {
        if (isDmrRunning) {
            setTimeout(connectDmrSSE, 2000);
        }
    };
}

function handleDmrMessage(msg) {
    if (msg.type === 'sync') {
        dmrCurrentProtocol = msg.protocol || '--';
        const protocolEl = document.getElementById('dmrActiveProtocol');
        if (protocolEl) protocolEl.textContent = dmrCurrentProtocol;
        const mainProtocolEl = document.getElementById('dmrMainProtocol');
        if (mainProtocolEl) mainProtocolEl.textContent = dmrCurrentProtocol;
        dmrSyncCount++;
        const syncCountEl = document.getElementById('dmrSyncCount');
        if (syncCountEl) syncCountEl.textContent = dmrSyncCount;
    } else if (msg.type === 'call') {
        dmrCallCount++;
        const countEl = document.getElementById('dmrCallCount');
        if (countEl) countEl.textContent = dmrCallCount;
        const mainCountEl = document.getElementById('dmrMainCallCount');
        if (mainCountEl) mainCountEl.textContent = dmrCallCount;

        // Update current call display
        const callEl = document.getElementById('dmrCurrentCall');
        if (callEl) {
            callEl.innerHTML = `
                <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                    <span style="color: var(--text-muted);">Talkgroup</span>
                    <span style="color: var(--accent-green); font-weight: bold; font-family: var(--font-mono);">${msg.talkgroup}</span>
                </div>
                <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                    <span style="color: var(--text-muted);">Source ID</span>
                    <span style="color: var(--accent-cyan); font-family: var(--font-mono);">${msg.source_id}</span>
                </div>
                <div style="display: flex; justify-content: space-between;">
                    <span style="color: var(--text-muted);">Time</span>
                    <span style="color: var(--text-primary);">${msg.timestamp}</span>
                </div>
            `;
        }

        // Add to history
        dmrCallHistory.unshift({
            talkgroup: msg.talkgroup,
            source_id: msg.source_id,
            protocol: dmrCurrentProtocol,
            time: msg.timestamp,
        });
        if (dmrCallHistory.length > 50) dmrCallHistory.length = 50;
        renderDmrHistory();

    } else if (msg.type === 'slot') {
        // Update slot info in current call
    } else if (msg.type === 'status') {
        const statusEl = document.getElementById('dmrStatus');
        if (statusEl) {
            statusEl.textContent = msg.text === 'started' ? 'DECODING' : 'IDLE';
        }
        if (msg.text === 'stopped') {
            isDmrRunning = false;
            updateDmrUI();
        }
    }
}

// ============== UI ==============

function updateDmrUI() {
    const startBtn = document.getElementById('startDmrBtn');
    const stopBtn = document.getElementById('stopDmrBtn');
    if (startBtn) startBtn.style.display = isDmrRunning ? 'none' : 'block';
    if (stopBtn) stopBtn.style.display = isDmrRunning ? 'block' : 'none';
}

function renderDmrHistory() {
    const container = document.getElementById('dmrHistoryBody');
    if (!container) return;

    const historyCountEl = document.getElementById('dmrHistoryCount');
    if (historyCountEl) historyCountEl.textContent = `${dmrCallHistory.length} calls`;

    if (dmrCallHistory.length === 0) {
        container.innerHTML = '<tr><td colspan="4" style="padding: 10px; text-align: center; color: var(--text-muted);">No calls recorded</td></tr>';
        return;
    }

    container.innerHTML = dmrCallHistory.slice(0, 20).map(call => `
        <tr>
            <td style="padding: 3px 6px; font-family: var(--font-mono);">${call.time}</td>
            <td style="padding: 3px 6px; color: var(--accent-green);">${call.talkgroup}</td>
            <td style="padding: 3px 6px; color: var(--accent-cyan);">${call.source_id}</td>
            <td style="padding: 3px 6px;">${call.protocol}</td>
        </tr>
    `).join('');
}

// ============== EXPORTS ==============

window.startDmr = startDmr;
window.stopDmr = stopDmr;
window.checkDmrTools = checkDmrTools;
