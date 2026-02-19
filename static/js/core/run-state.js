const RunState = (function() {
    'use strict';

    const REFRESH_MS = 5000;
    const CHIP_MODES = ['pager', 'sensor', 'wifi', 'bluetooth', 'adsb', 'ais', 'acars', 'vdl2', 'aprs', 'dsc', 'dmr', 'subghz'];

    const modeLabels = {
        pager: 'Pager',
        sensor: '433',
        wifi: 'WiFi',
        bluetooth: 'BT',
        adsb: 'ADS-B',
        ais: 'AIS',
        acars: 'ACARS',
        vdl2: 'VDL2',
        aprs: 'APRS',
        dsc: 'DSC',
        dmr: 'DMR',
        subghz: 'SubGHz',
    };

    let refreshTimer = null;
    let activeMode = null;
    let lastHealth = null;
    let lastErrorToastAt = 0;

    function init() {
        const root = document.getElementById('runStateStrip');
        if (!root) return;

        wireActions();
        wrapModeSwitch();
        activeMode = inferCurrentMode();
        renderHealth(null);
        refresh();

        if (!refreshTimer) {
            refreshTimer = window.setInterval(refresh, REFRESH_MS);
        }

        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) refresh();
        });
    }

    function wireActions() {
        const refreshBtn = document.getElementById('runStateRefreshBtn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => refresh());
        }

        const settingsBtn = document.getElementById('runStateSettingsBtn');
        if (settingsBtn) {
            settingsBtn.addEventListener('click', () => {
                if (typeof showSettings === 'function') {
                    showSettings();
                    if (typeof switchSettingsTab === 'function') {
                        switchSettingsTab('tools');
                    }
                }
            });
        }
    }

    function wrapModeSwitch() {
        if (typeof window.switchMode !== 'function') return;
        if (window.switchMode.__runStateWrapped) return;

        const original = window.switchMode;
        const wrapped = function(mode) {
            if (mode) {
                activeMode = String(mode);
            }
            const result = original.apply(this, arguments);
            markActiveChip();
            return result;
        };
        wrapped.__runStateWrapped = true;
        window.switchMode = wrapped;
    }

    async function refresh() {
        try {
            const response = await fetch('/health');
            const data = await response.json();
            lastHealth = data;
            renderHealth(data);
        } catch (err) {
            renderHealth(null, err);
            const now = Date.now();
            if (typeof reportActionableError === 'function' && (now - lastErrorToastAt) > 30000) {
                lastErrorToastAt = now;
                reportActionableError('Run State', err, { persistent: false });
            }
        }
    }

    function renderHealth(data, err) {
        const chipsContainer = document.getElementById('runStateChips');
        const summaryEl = document.getElementById('runStateSummary');
        if (!chipsContainer || !summaryEl) return;

        chipsContainer.innerHTML = '';

        if (!data || data.status !== 'healthy') {
            const offline = buildChip('API', false);
            offline.classList.add('active');
            chipsContainer.appendChild(offline);
            summaryEl.textContent = err ? `Health unavailable: ${extractMessage(err)}` : 'Health unavailable';
            return;
        }

        const processes = data.processes || {};
        for (const mode of CHIP_MODES) {
            const isRunning = Boolean(processes[mode]);
            chipsContainer.appendChild(buildChip(modeLabels[mode] || mode.toUpperCase(), isRunning, mode));
        }

        const counts = data.data || {};
        summaryEl.textContent = `Aircraft ${counts.aircraft_count || 0} | Vessels ${counts.vessel_count || 0} | WiFi ${counts.wifi_networks_count || 0} | BT ${counts.bt_devices_count || 0}`;
        markActiveChip();
    }

    function buildChip(label, running, mode) {
        const chip = document.createElement('span');
        chip.className = `run-state-chip${running ? ' running' : ''}`;
        if (mode) {
            chip.dataset.mode = mode;
        }

        const dot = document.createElement('span');
        dot.className = 'dot';
        chip.appendChild(dot);

        const text = document.createElement('span');
        text.textContent = label;
        chip.appendChild(text);

        return chip;
    }

    function markActiveChip() {
        if (!activeMode) {
            activeMode = inferCurrentMode();
        }

        document.querySelectorAll('#runStateChips .run-state-chip').forEach((chip) => {
            chip.classList.remove('active');
            if (chip.dataset.mode && chip.dataset.mode === activeMode) {
                chip.classList.add('active');
            }
        });
    }

    function inferCurrentMode() {
        const modeParam = new URLSearchParams(window.location.search).get('mode');
        if (modeParam) return modeParam;

        const indicator = document.getElementById('activeModeIndicator');
        if (!indicator) return 'pager';

        const text = indicator.textContent || '';
        const normalized = text.toLowerCase();
        if (normalized.includes('wifi')) return 'wifi';
        if (normalized.includes('bluetooth')) return 'bluetooth';
        if (normalized.includes('ads-b')) return 'adsb';
        if (normalized.includes('ais')) return 'ais';
        if (normalized.includes('acars')) return 'acars';
        if (normalized.includes('vdl2')) return 'vdl2';
        if (normalized.includes('aprs')) return 'aprs';
        if (normalized.includes('dsc')) return 'dsc';
        if (normalized.includes('subghz')) return 'subghz';
        if (normalized.includes('dmr')) return 'dmr';
        if (normalized.includes('433')) return 'sensor';
        return 'pager';
    }

    function extractMessage(err) {
        if (!err) return 'Unknown error';
        if (typeof err === 'string') return err;
        if (err.message) return err.message;
        return String(err);
    }

    function getLastHealth() {
        return lastHealth;
    }

    function destroy() {
        if (refreshTimer) {
            clearInterval(refreshTimer);
            refreshTimer = null;
        }
    }

    return {
        init,
        refresh,
        destroy,
        getLastHealth,
    };
})();

document.addEventListener('DOMContentLoaded', () => {
    RunState.init();
});
