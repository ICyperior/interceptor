/* ISS dashboard — live position, ground track, footprint, day/night and passes.
 * Data comes from /satellite/iss/live (computed locally from the ISS TLE via
 * skyfield), so this works regardless of the NASA telemetry feed's AOS/LOS. */
(function () {
    'use strict';

    const POLL_MS = 5000;
    const TELEMETRY_MS = 4000;
    const EARTH_R_KM = 6371;

    // Solar arrays: telemetry key -> mount x on the 280-wide truss schematic.
    const PANELS = [
        { key: 'bga_2b', x: 105 }, { key: 'bga_4b', x: 85 }, { key: 'bga_4a', x: 65 }, { key: 'bga_2a', x: 45 },
        { key: 'bga_1a', x: 175 }, { key: 'bga_3a', x: 195 }, { key: 'bga_3b', x: 215 }, { key: 'bga_1b', x: 235 },
    ];
    let telemetryTimer = null;
    let map = null;
    let issMarker = null;
    let footprint = null;
    let nightCircle = null;
    let observerMarker = null;
    const trackSegments = [];
    let pollTimer = null;

    function $(id) { return document.getElementById(id); }
    function setText(id, txt) { const el = $(id); if (el) el.textContent = txt; }

    function getObserver() {
        if (window.ObserverLocation && ObserverLocation.getShared) {
            const s = ObserverLocation.getShared();
            if (s && isFinite(s.lat) && isFinite(s.lon)) return { lat: s.lat, lon: s.lon };
        }
        return { lat: 51.5074, lon: -0.1278 };
    }

    function issIcon() {
        return L.divIcon({ className: 'iss-marker', html: '\u{1F6F0}️', iconSize: [24, 24], iconAnchor: [12, 12] });
    }

    // ── Sun / terminator ────────────────────────────────────────────────
    // Subsolar point: declination from day-of-year, longitude from UTC time.
    function subsolarPoint(now) {
        const start = Date.UTC(now.getUTCFullYear(), 0, 0);
        const dayOfYear = Math.floor((now - start) / 86400000);
        const decl = -23.44 * Math.cos((2 * Math.PI / 365) * (dayOfYear + 10));
        const utcHours = now.getUTCHours() + now.getUTCMinutes() / 60 + now.getUTCSeconds() / 3600;
        let lon = -15 * (utcHours - 12);
        while (lon > 180) lon -= 360;
        while (lon < -180) lon += 360;
        return { lat: decl, lon: lon };
    }

    // Angular distance (deg) between two lat/lon points.
    function angularDistance(a, b) {
        const la1 = a.lat * Math.PI / 180, la2 = b.lat * Math.PI / 180;
        const dlon = (b.lon - a.lon) * Math.PI / 180;
        const c = Math.sin(la1) * Math.sin(la2) + Math.cos(la1) * Math.cos(la2) * Math.cos(dlon);
        return Math.acos(Math.max(-1, Math.min(1, c))) * 180 / Math.PI;
    }

    function footprintRadiusMeters(altKm) {
        const lambda = Math.acos(EARTH_R_KM / (EARTH_R_KM + altKm)); // central angle (rad)
        return lambda * EARTH_R_KM * 1000;
    }

    // ── Ground track (split at the antimeridian) ────────────────────────
    function drawTrack(points) {
        trackSegments.forEach(seg => map.removeLayer(seg));
        trackSegments.length = 0;
        let current = [];
        for (let i = 0; i < points.length; i++) {
            if (i > 0 && Math.abs(points[i][1] - points[i - 1][1]) > 180) {
                if (current.length > 1) trackSegments.push(L.polyline(current, { color: '#00d4ff', weight: 1.5, opacity: 0.7, dashArray: '4 4' }).addTo(map));
                current = [];
            }
            current.push(points[i]);
        }
        if (current.length > 1) trackSegments.push(L.polyline(current, { color: '#00d4ff', weight: 1.5, opacity: 0.7, dashArray: '4 4' }).addTo(map));
    }

    function drawNight() {
        const sun = subsolarPoint(new Date());
        const antisolar = [-sun.lat, sun.lon > 0 ? sun.lon - 180 : sun.lon + 180];
        if (nightCircle) map.removeLayer(nightCircle);
        // Night hemisphere ≈ circle of ~quarter Earth circumference around antisolar point.
        nightCircle = L.circle(antisolar, {
            radius: 10018000, stroke: false, fillColor: '#000', fillOpacity: 0.28, interactive: false,
        }).addTo(map);
        return sun;
    }

    function fmtCountdown(iso) {
        const ms = new Date(iso).getTime() - Date.now();
        if (ms <= 0) return 'now';
        const m = Math.floor(ms / 60000);
        if (m < 60) return `in ${m}m`;
        return `in ${Math.floor(m / 60)}h ${m % 60}m`;
    }

    function renderPasses(passes) {
        const el = $('issPasses');
        if (!el) return;
        if (!passes || passes.length === 0) {
            el.innerHTML = '<div class="iss-empty">No passes above 10° in the next 24 h.</div>';
            return;
        }
        el.innerHTML = passes.map(p => {
            const start = p.startTimeISO || p.startTime || p.aos;
            const maxEl = p.maxElevation != null ? p.maxElevation : (p.max_el != null ? p.max_el : '—');
            const dur = p.duration != null ? `${Math.round(p.duration / 60)}m` : (p.durationSeconds ? `${Math.round(p.durationSeconds / 60)}m` : '');
            const when = start ? new Date(start).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—';
            const cd = start ? fmtCountdown(start) : '';
            return `<div class="iss-pass">
                <div class="iss-pass-when">${when} <span style="color:var(--text-dim);font-size:10px;">${cd}</span></div>
                <div class="iss-pass-meta"><span>max el ${typeof maxEl === 'number' ? maxEl.toFixed(0) + '°' : maxEl}</span><span>${dur}</span></div>
            </div>`;
        }).join('');
    }

    function update(data) {
        const p = data.position;
        setText('issLat', p.lat.toFixed(2) + '°');
        setText('issLon', p.lon.toFixed(2) + '°');
        setText('issAlt', p.altitude_km.toFixed(0));
        setText('issVel', Math.round(p.velocity_kmh).toLocaleString());

        const latlng = [p.lat, p.lon];
        if (!issMarker) {
            issMarker = L.marker(latlng, { icon: issIcon(), zIndexOffset: 1000 }).addTo(map);
        } else {
            issMarker.setLatLng(latlng);
        }

        if (footprint) map.removeLayer(footprint);
        footprint = L.circle(latlng, {
            radius: footprintRadiusMeters(p.altitude_km),
            color: '#00d4ff', weight: 1, opacity: 0.5, fillColor: '#00d4ff', fillOpacity: 0.06, interactive: false,
        }).addTo(map);

        if (data.ground_track) drawTrack(data.ground_track);

        const sun = drawNight();
        const dayDist = angularDistance({ lat: p.lat, lon: p.lon }, sun);
        const sunlit = dayDist < 90;
        setText('issSun', sunlit ? 'DAY' : 'NIGHT');
        // Glow the solar wings when the station is in sunlight (always-live,
        // from tracking) even while telemetry angles are in LOS.
        const arraysEl = $('issArrays');
        if (arraysEl) arraysEl.classList.toggle('sunlit', sunlit);

        const o = data.observer;
        if (o) {
            setText('issElev', o.elevation.toFixed(1) + '°');
            setText('issAz', o.azimuth.toFixed(1) + '°');
            setText('issRange', Math.round(o.range_km).toLocaleString() + ' km');
            const vis = $('issVisible');
            if (vis) { vis.textContent = o.visible ? 'YES' : 'no'; vis.classList.toggle('visible', !!o.visible); }
        }
        renderPasses(data.next_passes);

        setText('issFeedState', 'TLE');
        const src = $('issFeedState');
        if (src) src.classList.remove('los');
    }

    function markStale() {
        const src = $('issFeedState');
        if (src) { src.textContent = 'STALE'; src.classList.add('los'); }
    }

    // ── Live telemetry ───────────────────────────────────────────────────
    function buildPanels() {
        const g = $('issPanels');
        if (!g) return;
        const NS = 'http://www.w3.org/2000/svg';
        PANELS.forEach(p => {
            const mount = document.createElementNS(NS, 'g');
            mount.setAttribute('transform', `translate(${p.x},60)`);
            const rotor = document.createElementNS(NS, 'g');
            rotor.setAttribute('data-key', p.key);
            const rect = document.createElementNS(NS, 'rect');
            rect.setAttribute('class', 'iss-panel-cell');
            rect.setAttribute('x', '-2.5'); rect.setAttribute('y', '-18');
            rect.setAttribute('width', '5'); rect.setAttribute('height', '36');
            rect.setAttribute('rx', '1');
            rotor.appendChild(rect);
            mount.appendChild(rotor);
            g.appendChild(mount);
        });
    }

    function fmt(v, digits, unit) {
        if (v == null) return '--';
        return v.toFixed(digits) + (unit ? ' ' + unit : '');
    }

    function updateTelemetry(d) {
        const badge = $('issTelemetryState');
        const arrays = $('issArrays');
        const foot = $('telFoot');
        const t = d.telemetry || {};
        const val = k => (t[k] ? t[k].value : null);

        // Rotate each solar array to its BGA angle (freeze on LOS).
        if (d.aos) {
            document.querySelectorAll('#issPanels [data-key]').forEach(rotor => {
                const v = val(rotor.getAttribute('data-key'));
                if (v != null) rotor.setAttribute('transform', `rotate(${v})`);
            });
        }
        if (arrays) arrays.classList.toggle('los', !d.aos);

        setText('telCabin', fmt(val('cabin_pressure'), 2, 'psi'));
        setText('telPpo2', fmt(val('ppo2'), 2, 'psia'));
        setText('telPpco2', fmt(val('ppco2'), 3, 'psia'));
        setText('telSarjP', fmt(val('sarj_port'), 1, '°'));
        setText('telSarjS', fmt(val('sarj_starboard'), 1, '°'));
        const rx = val('rate_x'), ry = val('rate_y'), rz = val('rate_z');
        if (rx != null && ry != null && rz != null) {
            setText('telRate', Math.sqrt(rx * rx + ry * ry + rz * rz).toFixed(3) + ' °/s');
        }

        if (badge) {
            if (!d.connected) { badge.textContent = 'STANDBY'; badge.className = 'iss-los-badge standby'; }
            else if (d.aos) { badge.textContent = 'AOS'; badge.className = 'iss-los-badge aos'; }
            else { badge.textContent = 'LOS'; badge.className = 'iss-los-badge los'; }
        }
        if (foot) {
            if (!d.connected) foot.textContent = 'Connecting to NASA telemetry feed…';
            else if (d.aos) foot.textContent = `Live · updated ${d.age_seconds != null ? d.age_seconds + 's' : ''} ago`;
            else foot.textContent = 'Loss of signal — telemetry paused (normal, several times per orbit).';
        }
    }

    async function pollTelemetry() {
        try {
            const r = await fetch('/satellite/iss/telemetry');
            const d = await r.json();
            if (d && d.status === 'success') updateTelemetry(d);
        } catch (e) { /* leave last state */ }
    }

    async function poll() {
        const o = getObserver();
        try {
            const r = await fetch(`/satellite/iss/live?lat=${o.lat}&lon=${o.lon}`);
            const d = await r.json();
            if (d && d.status === 'success') update(d);
            else markStale();
        } catch (e) {
            markStale();
        }
    }

    function start() {
        if (typeof L === 'undefined' || typeof MapUtils === 'undefined') { setTimeout(start, 200); return; }
        map = MapUtils.init('issMap', { center: [20, 0], zoom: 3, minZoom: 2, maxZoom: 8 });
        if (!map) return;
        MapUtils.addTacticalOverlays(map, { scaleBar: true });

        const obs = getObserver();
        observerMarker = L.circleMarker([obs.lat, obs.lon], {
            radius: 4, color: '#00ff88', fillColor: '#00ff88', fillOpacity: 0.9, weight: 1,
        }).addTo(map).bindTooltip('Observer', { direction: 'top' });

        buildPanels();
        poll();
        pollTimer = setInterval(poll, POLL_MS);
        pollTelemetry();
        telemetryTimer = setInterval(pollTelemetry, TELEMETRY_MS);
    }

    window.addEventListener('beforeunload', () => {
        if (pollTimer) clearInterval(pollTimer);
        if (telemetryTimer) clearInterval(telemetryTimer);
    });
    document.addEventListener('DOMContentLoaded', start);
})();
