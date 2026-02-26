/**
 * System Health – IIFE module
 *
 * Always-on monitoring that auto-connects when the mode is entered.
 * Streams real-time system metrics via SSE and provides SDR device enumeration.
 */
const SystemHealth = (function () {
    'use strict';

    let eventSource = null;
    let connected = false;
    let lastMetrics = null;

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    function formatBytes(bytes) {
        if (bytes == null) return '--';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let i = 0;
        let val = bytes;
        while (val >= 1024 && i < units.length - 1) { val /= 1024; i++; }
        return val.toFixed(1) + ' ' + units[i];
    }

    function barClass(pct) {
        if (pct >= 85) return 'crit';
        if (pct >= 60) return 'warn';
        return 'ok';
    }

    function barHtml(pct, label) {
        if (pct == null) return '<span class="sys-metric-na">N/A</span>';
        const cls = barClass(pct);
        const rounded = Math.round(pct);
        return '<div class="sys-metric-bar-wrap">' +
            (label ? '<span class="sys-metric-bar-label">' + label + '</span>' : '') +
            '<div class="sys-metric-bar"><div class="sys-metric-bar-fill ' + cls + '" style="width:' + rounded + '%"></div></div>' +
            '<span class="sys-metric-bar-value">' + rounded + '%</span>' +
            '</div>';
    }

    // -----------------------------------------------------------------------
    // Rendering
    // -----------------------------------------------------------------------

    function renderCpuCard(m) {
        const el = document.getElementById('sysCardCpu');
        if (!el) return;
        const cpu = m.cpu;
        if (!cpu) { el.innerHTML = '<div class="sys-card-body"><span class="sys-metric-na">psutil not available</span></div>'; return; }
        el.innerHTML =
            '<div class="sys-card-header">CPU</div>' +
            '<div class="sys-card-body">' +
            barHtml(cpu.percent, '') +
            '<div class="sys-card-detail">Load: ' + cpu.load_1 + ' / ' + cpu.load_5 + ' / ' + cpu.load_15 + '</div>' +
            '<div class="sys-card-detail">Cores: ' + cpu.count + '</div>' +
            '</div>';
    }

    function renderMemoryCard(m) {
        const el = document.getElementById('sysCardMemory');
        if (!el) return;
        const mem = m.memory;
        if (!mem) { el.innerHTML = '<div class="sys-card-body"><span class="sys-metric-na">N/A</span></div>'; return; }
        const swap = m.swap || {};
        el.innerHTML =
            '<div class="sys-card-header">Memory</div>' +
            '<div class="sys-card-body">' +
            barHtml(mem.percent, '') +
            '<div class="sys-card-detail">' + formatBytes(mem.used) + ' / ' + formatBytes(mem.total) + '</div>' +
            '<div class="sys-card-detail">Swap: ' + formatBytes(swap.used) + ' / ' + formatBytes(swap.total) + '</div>' +
            '</div>';
    }

    function renderDiskCard(m) {
        const el = document.getElementById('sysCardDisk');
        if (!el) return;
        const disk = m.disk;
        if (!disk) { el.innerHTML = '<div class="sys-card-body"><span class="sys-metric-na">N/A</span></div>'; return; }
        el.innerHTML =
            '<div class="sys-card-header">Disk</div>' +
            '<div class="sys-card-body">' +
            barHtml(disk.percent, '') +
            '<div class="sys-card-detail">' + formatBytes(disk.used) + ' / ' + formatBytes(disk.total) + '</div>' +
            '<div class="sys-card-detail">Path: ' + (disk.path || '/') + '</div>' +
            '</div>';
    }

    function _extractPrimaryTemp(temps) {
        if (!temps) return null;
        // Prefer common chip names
        const preferred = ['cpu_thermal', 'coretemp', 'k10temp', 'acpitz', 'soc_thermal'];
        for (const name of preferred) {
            if (temps[name] && temps[name].length) return temps[name][0];
        }
        // Fall back to first available
        for (const key of Object.keys(temps)) {
            if (temps[key] && temps[key].length) return temps[key][0];
        }
        return null;
    }

    function renderSdrCard(devices) {
        const el = document.getElementById('sysCardSdr');
        if (!el) return;
        let html = '<div class="sys-card-header">SDR Devices <button class="sys-rescan-btn" onclick="SystemHealth.refreshSdr()">Rescan</button></div>';
        html += '<div class="sys-card-body">';
        if (!devices || !devices.length) {
            html += '<span class="sys-metric-na">No devices found</span>';
        } else {
            devices.forEach(function (d) {
                html += '<div class="sys-sdr-device">' +
                    '<span class="sys-process-dot running"></span> ' +
                    '<strong>' + d.type + ' #' + d.index + '</strong>' +
                    '<div class="sys-card-detail">' + (d.name || 'Unknown') + '</div>' +
                    (d.serial ? '<div class="sys-card-detail">S/N: ' + d.serial + '</div>' : '') +
                    '</div>';
            });
        }
        html += '</div>';
        el.innerHTML = html;
    }

    function renderProcessCard(m) {
        const el = document.getElementById('sysCardProcesses');
        if (!el) return;
        const procs = m.processes || {};
        const keys = Object.keys(procs).sort();
        let html = '<div class="sys-card-header">Processes</div><div class="sys-card-body">';
        if (!keys.length) {
            html += '<span class="sys-metric-na">No data</span>';
        } else {
            keys.forEach(function (k) {
                const running = procs[k];
                const dotCls = running ? 'running' : 'stopped';
                const label = k.charAt(0).toUpperCase() + k.slice(1);
                html += '<div class="sys-process-item">' +
                    '<span class="sys-process-dot ' + dotCls + '"></span> ' +
                    '<span class="sys-process-name">' + label + '</span>' +
                    '</div>';
            });
        }
        html += '</div>';
        el.innerHTML = html;
    }

    function renderSystemInfoCard(m) {
        const el = document.getElementById('sysCardInfo');
        if (!el) return;
        const sys = m.system || {};
        const temp = _extractPrimaryTemp(m.temperatures);
        let html = '<div class="sys-card-header">System Info</div><div class="sys-card-body">';
        html += '<div class="sys-card-detail">Host: ' + (sys.hostname || '--') + '</div>';
        html += '<div class="sys-card-detail">OS: ' + (sys.platform || '--') + '</div>';
        html += '<div class="sys-card-detail">Python: ' + (sys.python || '--') + '</div>';
        html += '<div class="sys-card-detail">App: v' + (sys.version || '--') + '</div>';
        html += '<div class="sys-card-detail">Uptime: ' + (sys.uptime_human || '--') + '</div>';
        if (temp) {
            html += '<div class="sys-card-detail">Temp: ' + Math.round(temp.current) + '&deg;C';
            if (temp.high) html += ' / ' + Math.round(temp.high) + '&deg;C max';
            html += '</div>';
        }
        html += '</div>';
        el.innerHTML = html;
    }

    function updateSidebarQuickStats(m) {
        const cpuEl = document.getElementById('sysQuickCpu');
        const tempEl = document.getElementById('sysQuickTemp');
        const ramEl = document.getElementById('sysQuickRam');
        const diskEl = document.getElementById('sysQuickDisk');

        if (cpuEl) cpuEl.textContent = m.cpu ? Math.round(m.cpu.percent) + '%' : '--';
        if (ramEl) ramEl.textContent = m.memory ? Math.round(m.memory.percent) + '%' : '--';
        if (diskEl) diskEl.textContent = m.disk ? Math.round(m.disk.percent) + '%' : '--';

        const temp = _extractPrimaryTemp(m.temperatures);
        if (tempEl) tempEl.innerHTML = temp ? Math.round(temp.current) + '&deg;C' : '--';

        // Color-code values
        [cpuEl, ramEl, diskEl].forEach(function (el) {
            if (!el) return;
            const val = parseInt(el.textContent);
            el.classList.remove('sys-val-ok', 'sys-val-warn', 'sys-val-crit');
            if (!isNaN(val)) el.classList.add('sys-val-' + barClass(val));
        });
    }

    function updateSidebarProcesses(m) {
        const el = document.getElementById('sysProcessList');
        if (!el) return;
        const procs = m.processes || {};
        const keys = Object.keys(procs).sort();
        if (!keys.length) { el.textContent = 'No data'; return; }
        const running = keys.filter(function (k) { return procs[k]; });
        const stopped = keys.filter(function (k) { return !procs[k]; });
        el.innerHTML =
            (running.length ? '<span style="color: var(--accent-green, #00ff88);">' + running.length + ' running</span>' : '') +
            (running.length && stopped.length ? ' &middot; ' : '') +
            (stopped.length ? '<span style="color: var(--text-dim);">' + stopped.length + ' stopped</span>' : '');
    }

    function renderAll(m) {
        renderCpuCard(m);
        renderMemoryCard(m);
        renderDiskCard(m);
        renderProcessCard(m);
        renderSystemInfoCard(m);
        updateSidebarQuickStats(m);
        updateSidebarProcesses(m);
    }

    // -----------------------------------------------------------------------
    // SSE Connection
    // -----------------------------------------------------------------------

    function connect() {
        if (eventSource) return;
        eventSource = new EventSource('/system/stream');
        eventSource.onmessage = function (e) {
            try {
                var data = JSON.parse(e.data);
                if (data.type === 'keepalive') return;
                lastMetrics = data;
                renderAll(data);
            } catch (_) { /* ignore parse errors */ }
        };
        eventSource.onopen = function () {
            connected = true;
        };
        eventSource.onerror = function () {
            connected = false;
        };
    }

    function disconnect() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
        connected = false;
    }

    // -----------------------------------------------------------------------
    // SDR Devices
    // -----------------------------------------------------------------------

    function refreshSdr() {
        var sidebarEl = document.getElementById('sysSdrList');
        if (sidebarEl) sidebarEl.innerHTML = 'Scanning&hellip;';

        var cardEl = document.getElementById('sysCardSdr');
        if (cardEl) cardEl.innerHTML = '<div class="sys-card-header">SDR Devices</div><div class="sys-card-body">Scanning&hellip;</div>';

        fetch('/system/sdr_devices')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var devices = data.devices || [];
                renderSdrCard(devices);
                // Update sidebar
                if (sidebarEl) {
                    if (!devices.length) {
                        sidebarEl.innerHTML = '<span style="color: var(--text-dim);">No SDR devices found</span>';
                    } else {
                        var html = '';
                        devices.forEach(function (d) {
                            html += '<div style="margin-bottom: 4px;"><span class="sys-process-dot running"></span> ' +
                                d.type + ' #' + d.index + ' &mdash; ' + (d.name || 'Unknown') + '</div>';
                        });
                        sidebarEl.innerHTML = html;
                    }
                }
            })
            .catch(function () {
                if (sidebarEl) sidebarEl.innerHTML = '<span style="color: var(--accent-red, #ff3366);">Detection failed</span>';
                renderSdrCard([]);
            });
    }

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    function init() {
        connect();
        refreshSdr();
    }

    function destroy() {
        disconnect();
    }

    return {
        init: init,
        destroy: destroy,
        refreshSdr: refreshSdr,
    };
})();
