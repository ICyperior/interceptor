/**
 * Analytics Dashboard Module
 * Cross-mode summary, sparklines, alerts, correlations, geofence management, export.
 */
const Analytics = (function () {
    'use strict';

    let refreshTimer = null;

    function init() {
        refresh();
        if (!refreshTimer) {
            refreshTimer = setInterval(refresh, 5000);
        }
    }

    function destroy() {
        if (refreshTimer) {
            clearInterval(refreshTimer);
            refreshTimer = null;
        }
    }

    function refresh() {
        Promise.all([
            fetch('/analytics/summary').then(r => r.json()).catch(() => null),
            fetch('/analytics/activity').then(r => r.json()).catch(() => null),
            fetch('/alerts/events?limit=20').then(r => r.json()).catch(() => null),
            fetch('/correlation').then(r => r.json()).catch(() => null),
            fetch('/analytics/geofences').then(r => r.json()).catch(() => null),
        ]).then(([summary, activity, alerts, correlations, geofences]) => {
            if (summary) renderSummary(summary);
            if (activity) renderSparklines(activity.sparklines || {});
            if (alerts) renderAlerts(alerts.events || []);
            if (correlations) renderCorrelations(correlations);
            if (geofences) renderGeofences(geofences.zones || []);
        });
    }

    function renderSummary(data) {
        const counts = data.counts || {};
        _setText('analyticsCountAdsb', counts.adsb || 0);
        _setText('analyticsCountAis', counts.ais || 0);
        _setText('analyticsCountWifi', counts.wifi || 0);
        _setText('analyticsCountBt', counts.bluetooth || 0);
        _setText('analyticsCountDsc', counts.dsc || 0);
        _setText('analyticsCountAcars', counts.acars || 0);
        _setText('analyticsCountVdl2', counts.vdl2 || 0);
        _setText('analyticsCountAprs', counts.aprs || 0);
        _setText('analyticsCountMesh', counts.meshtastic || 0);

        // Health
        const health = data.health || {};
        const container = document.getElementById('analyticsHealth');
        if (container) {
            let html = '';
            const modeLabels = {
                pager: 'Pager', sensor: '433MHz', adsb: 'ADS-B', ais: 'AIS',
                acars: 'ACARS', vdl2: 'VDL2', aprs: 'APRS', wifi: 'WiFi',
                bluetooth: 'BT', dsc: 'DSC'
            };
            for (const [mode, info] of Object.entries(health)) {
                if (mode === 'sdr_devices') continue;
                const running = info && info.running;
                const label = modeLabels[mode] || mode;
                html += '<div class="health-item"><span class="health-dot' + (running ? ' running' : '') + '"></span>' + _esc(label) + '</div>';
            }
            container.innerHTML = html;
        }

        // Squawks
        const squawks = data.squawks || [];
        const sqSection = document.getElementById('analyticsSquawkSection');
        const sqList = document.getElementById('analyticsSquawkList');
        if (sqSection && sqList) {
            if (squawks.length > 0) {
                sqSection.style.display = '';
                sqList.innerHTML = squawks.map(s =>
                    '<div class="squawk-item"><strong>' + _esc(s.squawk) + '</strong> ' +
                    _esc(s.meaning) + ' — ' + _esc(s.callsign || s.icao) + '</div>'
                ).join('');
            } else {
                sqSection.style.display = 'none';
            }
        }
    }

    function renderSparklines(sparklines) {
        const map = {
            adsb: 'analyticsSparkAdsb',
            ais: 'analyticsSparkAis',
            wifi: 'analyticsSparkWifi',
            bluetooth: 'analyticsSparkBt',
            dsc: 'analyticsSparkDsc',
        };

        for (const [mode, elId] of Object.entries(map)) {
            const el = document.getElementById(elId);
            if (!el) continue;
            const data = sparklines[mode] || [];
            if (data.length < 2) {
                el.innerHTML = '';
                continue;
            }
            const max = Math.max(...data, 1);
            const w = 100;
            const h = 24;
            const step = w / (data.length - 1);
            const points = data.map((v, i) =>
                (i * step).toFixed(1) + ',' + (h - (v / max) * (h - 2)).toFixed(1)
            ).join(' ');
            el.innerHTML = '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none"><polyline points="' + points + '"/></svg>';
        }
    }

    function renderAlerts(events) {
        const container = document.getElementById('analyticsAlertFeed');
        if (!container) return;
        if (!events || events.length === 0) {
            container.innerHTML = '<div class="analytics-empty">No recent alerts</div>';
            return;
        }
        container.innerHTML = events.slice(0, 20).map(e => {
            const sev = e.severity || 'medium';
            const title = e.title || e.event_type || 'Alert';
            const time = e.created_at ? new Date(e.created_at).toLocaleTimeString() : '';
            return '<div class="analytics-alert-item">' +
                '<span class="alert-severity ' + _esc(sev) + '">' + _esc(sev) + '</span>' +
                '<span>' + _esc(title) + '</span>' +
                '<span style="margin-left:auto;color:var(--text-dim)">' + _esc(time) + '</span>' +
                '</div>';
        }).join('');
    }

    function renderCorrelations(data) {
        const container = document.getElementById('analyticsCorrelations');
        if (!container) return;
        const pairs = (data && data.correlations) || [];
        if (pairs.length === 0) {
            container.innerHTML = '<div class="analytics-empty">No correlations detected</div>';
            return;
        }
        container.innerHTML = pairs.slice(0, 20).map(p => {
            const conf = Math.round((p.confidence || 0) * 100);
            return '<div class="analytics-correlation-pair">' +
                '<span>' + _esc(p.wifi_mac || '') + '</span>' +
                '<span style="color:var(--text-dim)">&#8596;</span>' +
                '<span>' + _esc(p.bt_mac || '') + '</span>' +
                '<div class="confidence-bar"><div class="confidence-fill" style="width:' + conf + '%"></div></div>' +
                '<span style="color:var(--text-dim)">' + conf + '%</span>' +
                '</div>';
        }).join('');
    }

    function renderGeofences(zones) {
        const container = document.getElementById('analyticsGeofenceList');
        if (!container) return;
        if (!zones || zones.length === 0) {
            container.innerHTML = '<div class="analytics-empty">No geofence zones defined</div>';
            return;
        }
        container.innerHTML = zones.map(z =>
            '<div class="geofence-zone-item">' +
            '<span class="zone-name">' + _esc(z.name) + '</span>' +
            '<span class="zone-radius">' + z.radius_m + 'm</span>' +
            '<button class="zone-delete" onclick="Analytics.deleteGeofence(' + z.id + ')">DEL</button>' +
            '</div>'
        ).join('');
    }

    function addGeofence() {
        const name = prompt('Zone name:');
        if (!name) return;
        const lat = parseFloat(prompt('Latitude:', '0'));
        const lon = parseFloat(prompt('Longitude:', '0'));
        const radius = parseFloat(prompt('Radius (meters):', '1000'));
        if (isNaN(lat) || isNaN(lon) || isNaN(radius)) {
            alert('Invalid input');
            return;
        }
        fetch('/analytics/geofences', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, lat, lon, radius_m: radius }),
        })
            .then(r => r.json())
            .then(() => refresh());
    }

    function deleteGeofence(id) {
        if (!confirm('Delete this geofence zone?')) return;
        fetch('/analytics/geofences/' + id, { method: 'DELETE' })
            .then(r => r.json())
            .then(() => refresh());
    }

    function exportData(mode) {
        const m = mode || (document.getElementById('exportMode') || {}).value || 'adsb';
        const f = (document.getElementById('exportFormat') || {}).value || 'json';
        window.open('/analytics/export/' + encodeURIComponent(m) + '?format=' + encodeURIComponent(f), '_blank');
    }

    // Helpers
    function _setText(id, val) {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    function _esc(s) {
        if (typeof s !== 'string') s = String(s == null ? '' : s);
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    return { init, destroy, refresh, addGeofence, deleteGeofence, exportData };
})();
