/**
 * Analytics Dashboard Module
 * Cross-mode summary, sparklines, alerts, correlations, target view, and replay.
 */
const Analytics = (function () {
    'use strict';

    let refreshTimer = null;
    let replayTimer = null;
    let replaySessions = [];
    let replayEvents = [];
    let replayIndex = 0;

    function init() {
        refresh();
        loadReplaySessions();
        if (!refreshTimer) {
            refreshTimer = setInterval(refresh, 5000);
        }
    }

    function destroy() {
        if (refreshTimer) {
            clearInterval(refreshTimer);
            refreshTimer = null;
        }
        pauseReplay();
    }

    function refresh() {
        Promise.all([
            fetch('/analytics/summary').then(r => r.json()).catch(() => null),
            fetch('/analytics/activity').then(r => r.json()).catch(() => null),
            fetch('/analytics/insights').then(r => r.json()).catch(() => null),
            fetch('/analytics/patterns').then(r => r.json()).catch(() => null),
            fetch('/alerts/events?limit=20').then(r => r.json()).catch(() => null),
            fetch('/correlation').then(r => r.json()).catch(() => null),
            fetch('/analytics/geofences').then(r => r.json()).catch(() => null),
        ]).then(([summary, activity, insights, patterns, alerts, correlations, geofences]) => {
            if (summary) renderSummary(summary);
            if (activity) renderSparklines(activity.sparklines || {});
            if (insights) renderInsights(insights);
            if (patterns) renderPatterns(patterns.patterns || []);
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

        const health = data.health || {};
        const container = document.getElementById('analyticsHealth');
        if (container) {
            let html = '';
            const modeLabels = {
                pager: 'Pager', sensor: '433MHz', adsb: 'ADS-B', ais: 'AIS',
                acars: 'ACARS', vdl2: 'VDL2', aprs: 'APRS', wifi: 'WiFi',
                bluetooth: 'BT', dsc: 'DSC', meshtastic: 'Mesh'
            };
            for (const [mode, info] of Object.entries(health)) {
                if (mode === 'sdr_devices') continue;
                const running = info && info.running;
                const label = modeLabels[mode] || mode;
                html += '<div class="health-item"><span class="health-dot' + (running ? ' running' : '') + '"></span>' + _esc(label) + '</div>';
            }
            container.innerHTML = html;
        }

        const squawks = data.squawks || [];
        const sqSection = document.getElementById('analyticsSquawkSection');
        const sqList = document.getElementById('analyticsSquawkList');
        if (sqSection && sqList) {
            if (squawks.length > 0) {
                sqSection.style.display = '';
                sqList.innerHTML = squawks.map(s =>
                    '<div class="squawk-item"><strong>' + _esc(s.squawk) + '</strong> ' +
                    _esc(s.meaning) + ' - ' + _esc(s.callsign || s.icao) + '</div>'
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
            acars: 'analyticsSparkAcars',
            vdl2: 'analyticsSparkVdl2',
            aprs: 'analyticsSparkAprs',
            meshtastic: 'analyticsSparkMesh',
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

    function renderInsights(data) {
        const cards = data.cards || [];
        const topChanges = data.top_changes || [];
        const cardsEl = document.getElementById('analyticsInsights');
        const changesEl = document.getElementById('analyticsTopChanges');

        if (cardsEl) {
            if (!cards.length) {
                cardsEl.innerHTML = '<div class="analytics-empty">No insight data available</div>';
            } else {
                cardsEl.innerHTML = cards.map(c => {
                    const sev = _esc(c.severity || 'low');
                    const title = _esc(c.title || 'Insight');
                    const value = _esc(c.value || '--');
                    const label = _esc(c.label || '');
                    const detail = _esc(c.detail || '');
                    return '<div class="analytics-insight-card ' + sev + '">' +
                        '<div class="insight-title">' + title + '</div>' +
                        '<div class="insight-value">' + value + '</div>' +
                        '<div class="insight-label">' + label + '</div>' +
                        '<div class="insight-detail">' + detail + '</div>' +
                        '</div>';
                }).join('');
            }
        }

        if (changesEl) {
            if (!topChanges.length) {
                changesEl.innerHTML = '<div class="analytics-empty">No change signals yet</div>';
            } else {
                changesEl.innerHTML = topChanges.map(item => {
                    const mode = _esc(item.mode_label || item.mode || '');
                    const deltaRaw = Number(item.delta || 0);
                    const trendClass = deltaRaw > 0 ? 'up' : (deltaRaw < 0 ? 'down' : 'flat');
                    const delta = _esc(item.signed_delta || String(deltaRaw));
                    const recentAvg = _esc(item.recent_avg);
                    const prevAvg = _esc(item.previous_avg);
                    return '<div class="analytics-change-row">' +
                        '<span class="mode">' + mode + '</span>' +
                        '<span class="delta ' + trendClass + '">' + delta + '</span>' +
                        '<span class="avg">avg ' + recentAvg + ' vs ' + prevAvg + '</span>' +
                        '</div>';
                }).join('');
            }
        }
    }

    function renderPatterns(patterns) {
        const container = document.getElementById('analyticsPatternList');
        if (!container) return;
        if (!patterns || patterns.length === 0) {
            container.innerHTML = '<div class="analytics-empty">No recurring patterns detected</div>';
            return;
        }

        const modeLabels = {
            adsb: 'ADS-B', ais: 'AIS', wifi: 'WiFi', bluetooth: 'Bluetooth',
            dsc: 'DSC', acars: 'ACARS', vdl2: 'VDL2', aprs: 'APRS', meshtastic: 'Meshtastic',
        };

        const sorted = patterns
            .slice()
            .sort((a, b) => (b.confidence || 0) - (a.confidence || 0))
            .slice(0, 20);

        container.innerHTML = sorted.map(p => {
            const confidencePct = Math.round((Number(p.confidence || 0)) * 100);
            const mode = modeLabels[p.mode] || (p.mode || '--').toUpperCase();
            const period = _humanPeriod(Number(p.period_seconds || 0));
            const occurrences = Number(p.occurrences || 0);
            const deviceId = _shortId(p.device_id || '--');
            return '<div class="analytics-pattern-item">' +
                '<div class="pattern-main">' +
                '<span class="pattern-mode">' + _esc(mode) + '</span>' +
                '<span class="pattern-device">' + _esc(deviceId) + '</span>' +
                '</div>' +
                '<div class="pattern-meta">' +
                '<span>Period: ' + _esc(period) + '</span>' +
                '<span>Hits: ' + _esc(occurrences) + '</span>' +
                '<span class="pattern-confidence">' + _esc(confidencePct) + '%</span>' +
                '</div>' +
                '</div>';
        }).join('');
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

    function searchTarget() {
        const input = document.getElementById('analyticsTargetQuery');
        const summaryEl = document.getElementById('analyticsTargetSummary');
        const q = (input && input.value || '').trim();
        if (!q) {
            if (summaryEl) summaryEl.textContent = 'Enter a search value to correlate entities';
            renderTargetResults([]);
            return;
        }

        fetch('/analytics/target?q=' + encodeURIComponent(q) + '&limit=120')
            .then((r) => r.json())
            .then((data) => {
                const results = data.results || [];
                if (summaryEl) {
                    const modeCounts = data.mode_counts || {};
                    const bits = Object.entries(modeCounts).map(([mode, count]) => `${mode}: ${count}`).join(' | ');
                    summaryEl.textContent = `${results.length} results${bits ? ' | ' + bits : ''}`;
                }
                renderTargetResults(results);
            })
            .catch((err) => {
                if (summaryEl) summaryEl.textContent = 'Search failed';
                if (typeof reportActionableError === 'function') {
                    reportActionableError('Target View Search', err, { onRetry: searchTarget });
                }
            });
    }

    function renderTargetResults(results) {
        const container = document.getElementById('analyticsTargetResults');
        if (!container) return;

        if (!results || !results.length) {
            container.innerHTML = '<div class="analytics-empty">No matching entities</div>';
            return;
        }

        container.innerHTML = results.map((item) => {
            const title = _esc(item.title || item.id || 'Entity');
            const subtitle = _esc(item.subtitle || '');
            const mode = _esc(item.mode || 'unknown');
            const confidence = item.confidence != null ? `Confidence ${_esc(Math.round(Number(item.confidence) * 100))}%` : '';
            const lastSeen = _esc(item.last_seen || '');
            return '<div class="analytics-target-item">' +
                '<div class="title"><span class="mode">' + mode + '</span><span>' + title + '</span></div>' +
                '<div class="meta"><span>' + subtitle + '</span>' +
                (lastSeen ? '<span>Last seen ' + lastSeen + '</span>' : '') +
                (confidence ? '<span>' + confidence + '</span>' : '') +
                '</div>' +
                '</div>';
        }).join('');
    }

    function loadReplaySessions() {
        const select = document.getElementById('analyticsReplaySelect');
        if (!select) return;

        fetch('/recordings?limit=60')
            .then((r) => r.json())
            .then((data) => {
                replaySessions = (data.recordings || []).filter((rec) => Number(rec.event_count || 0) > 0);

                if (!replaySessions.length) {
                    select.innerHTML = '<option value="">No recordings</option>';
                    return;
                }

                select.innerHTML = replaySessions.map((rec) => {
                    const label = `${rec.mode} | ${(rec.label || 'session')} | ${new Date(rec.started_at).toLocaleString()}`;
                    return `<option value="${_esc(rec.id)}">${_esc(label)}</option>`;
                }).join('');

                const pendingReplay = localStorage.getItem('analyticsReplaySession');
                if (pendingReplay && replaySessions.some((rec) => rec.id === pendingReplay)) {
                    select.value = pendingReplay;
                    localStorage.removeItem('analyticsReplaySession');
                    loadReplay();
                }
            })
            .catch((err) => {
                if (typeof reportActionableError === 'function') {
                    reportActionableError('Load Replay Sessions', err, { onRetry: loadReplaySessions });
                }
            });
    }

    function loadReplay() {
        pauseReplay();
        replayEvents = [];
        replayIndex = 0;

        const select = document.getElementById('analyticsReplaySelect');
        const meta = document.getElementById('analyticsReplayMeta');
        const timeline = document.getElementById('analyticsReplayTimeline');
        if (!select || !meta || !timeline) return;

        const id = select.value;
        if (!id) {
            meta.textContent = 'Select a recording';
            timeline.innerHTML = '<div class="analytics-empty">No recording selected</div>';
            return;
        }

        meta.textContent = 'Loading replay events...';

        fetch('/recordings/' + encodeURIComponent(id) + '/events?limit=600')
            .then((r) => r.json())
            .then((data) => {
                replayEvents = data.events || [];
                replayIndex = 0;
                if (!replayEvents.length) {
                    meta.textContent = 'No events found in selected recording';
                    timeline.innerHTML = '<div class="analytics-empty">No events to replay</div>';
                    return;
                }

                const rec = replaySessions.find((s) => s.id === id);
                const mode = rec ? rec.mode : (data.recording && data.recording.mode) || 'unknown';
                meta.textContent = `${replayEvents.length} events loaded | mode ${mode}`;
                renderReplayWindow();
            })
            .catch((err) => {
                meta.textContent = 'Replay load failed';
                if (typeof reportActionableError === 'function') {
                    reportActionableError('Load Replay', err, { onRetry: loadReplay });
                }
            });
    }

    function playReplay() {
        if (!replayEvents.length) {
            loadReplay();
            return;
        }

        if (replayTimer) return;

        replayTimer = setInterval(() => {
            if (replayIndex >= replayEvents.length - 1) {
                pauseReplay();
                return;
            }
            replayIndex += 1;
            renderReplayWindow();
        }, 260);
    }

    function pauseReplay() {
        if (replayTimer) {
            clearInterval(replayTimer);
            replayTimer = null;
        }
    }

    function stepReplay() {
        if (!replayEvents.length) {
            loadReplay();
            return;
        }

        pauseReplay();
        replayIndex = Math.min(replayIndex + 1, replayEvents.length - 1);
        renderReplayWindow();
    }

    function renderReplayWindow() {
        const timeline = document.getElementById('analyticsReplayTimeline');
        const meta = document.getElementById('analyticsReplayMeta');
        if (!timeline || !meta) return;

        const total = replayEvents.length;
        if (!total) {
            timeline.innerHTML = '<div class="analytics-empty">No events to replay</div>';
            return;
        }

        const start = Math.max(0, replayIndex - 15);
        const end = Math.min(total, replayIndex + 20);
        const windowed = replayEvents.slice(start, end);

        timeline.innerHTML = windowed.map((row, i) => {
            const absolute = start + i;
            const active = absolute === replayIndex;
            const eventType = _esc(row.event_type || 'event');
            const mode = _esc(row.mode || '--');
            const ts = _esc(row.timestamp ? new Date(row.timestamp).toLocaleTimeString() : '--');
            const detail = summarizeReplayEvent(row.event || {});
            return '<div class="analytics-replay-item" style="opacity:' + (active ? '1' : '0.65') + ';">' +
                '<div class="title"><span class="mode">' + mode + '</span><span>' + eventType + '</span></div>' +
                '<div class="meta"><span>' + ts + '</span><span>' + _esc(detail) + '</span></div>' +
                '</div>';
        }).join('');

        meta.textContent = `Event ${replayIndex + 1}/${total}`;
    }

    function summarizeReplayEvent(event) {
        if (!event || typeof event !== 'object') return 'No details';
        if (event.callsign) return `Callsign ${event.callsign}`;
        if (event.icao) return `ICAO ${event.icao}`;
        if (event.ssid) return `SSID ${event.ssid}`;
        if (event.bssid) return `BSSID ${event.bssid}`;
        if (event.address) return `Address ${event.address}`;
        if (event.name) return `Name ${event.name}`;
        const keys = Object.keys(event);
        if (!keys.length) return 'No fields';
        return `${keys[0]}=${String(event[keys[0]]).slice(0, 40)}`;
    }

    function _setText(id, val) {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    function _esc(s) {
        if (typeof s !== 'string') s = String(s == null ? '' : s);
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function _shortId(value) {
        const text = String(value || '');
        if (text.length <= 18) return text;
        return text.slice(0, 8) + '...' + text.slice(-6);
    }

    function _humanPeriod(seconds) {
        if (!isFinite(seconds) || seconds <= 0) return '--';
        if (seconds < 60) return Math.round(seconds) + 's';
        const mins = seconds / 60;
        if (mins < 60) return mins.toFixed(mins < 10 ? 1 : 0) + 'm';
        const hours = mins / 60;
        return hours.toFixed(hours < 10 ? 1 : 0) + 'h';
    }

    return {
        init,
        destroy,
        refresh,
        addGeofence,
        deleteGeofence,
        exportData,
        searchTarget,
        loadReplay,
        playReplay,
        pauseReplay,
        stepReplay,
        loadReplaySessions,
    };
})();
