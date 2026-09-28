/* ADS-B pop-out instrument panel.
 * Renders parametric SVG gauges driven by the live ADS-B feed
 * (/adsb/aircraft). All flight data is derived from ADS-B (SBS): ground
 * speed, barometric altitude, ground track and vertical rate. */
(function () {
    'use strict';

    const NS = 'http://www.w3.org/2000/svg';
    const ICAO = (window.INSTRUMENT_ICAO || '').toUpperCase();
    const POLL_MS = 1000;
    const C = 100, R = 88;

    function el(name, attrs) {
        const e = document.createElementNS(NS, name);
        for (const k in attrs) e.setAttribute(k, attrs[k]);
        return e;
    }
    function polar(cx, cy, r, deg) {
        const a = (deg - 90) * Math.PI / 180;
        return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    }
    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
    function baseSvg() {
        const svg = el('svg', { viewBox: '0 0 200 200', class: 'inst-svg' });
        svg.appendChild(el('circle', { cx: C, cy: C, r: R, class: 'inst-bezel' }));
        svg.appendChild(el('circle', { cx: C, cy: C, r: R - 2, class: 'inst-face' }));
        return svg;
    }

    // ── Round needle gauge ───────────────────────────────────────────────
    function makeGauge(id, cfg) {
        const box = document.getElementById(id);
        if (!box) return { set() {} };
        const svg = baseSvg();
        const range = cfg.max - cfg.min;
        const ang = v => cfg.a0 + (v - cfg.min) / range * (cfg.a1 - cfg.a0);
        const ticks = el('g', {});
        if (cfg.minorStep) {
            for (let v = cfg.min; v <= cfg.max + 1e-6; v += cfg.minorStep) {
                const [x1, y1] = polar(C, C, R - 4, ang(v));
                const [x2, y2] = polar(C, C, R - 9, ang(v));
                ticks.appendChild(el('line', { x1, y1, x2, y2, class: 'inst-tick-minor' }));
            }
        }
        cfg.major.forEach(v => {
            const [x1, y1] = polar(C, C, R - 4, ang(v));
            const [x2, y2] = polar(C, C, R - 14, ang(v));
            ticks.appendChild(el('line', { x1, y1, x2, y2, class: 'inst-tick-major' }));
            const [lx, ly] = polar(C, C, R - 27, ang(v));
            const t = el('text', { x: lx, y: ly, class: 'inst-tick-label', 'text-anchor': 'middle', 'dominant-baseline': 'central' });
            t.textContent = cfg.labelFmt ? cfg.labelFmt(v) : v;
            ticks.appendChild(t);
        });
        svg.appendChild(ticks);
        const needle = el('line', { x1: C, y1: C + 14, x2: C, y2: C - R + 20, class: 'inst-needle' });
        needle.style.transformOrigin = C + 'px ' + C + 'px';
        svg.appendChild(needle);
        svg.appendChild(el('circle', { cx: C, cy: C, r: 5, class: 'inst-hub' }));
        const val = el('text', { x: C, y: C + 42, class: 'inst-digital', 'text-anchor': 'middle' });
        val.textContent = '--';
        svg.appendChild(val);
        const unit = el('text', { x: C, y: C + 56, class: 'inst-unit', 'text-anchor': 'middle' });
        unit.textContent = cfg.unit || '';
        svg.appendChild(unit);
        box.appendChild(svg);
        return {
            set(v) {
                if (v == null || isNaN(v)) { val.textContent = '--'; return; }
                needle.style.transform = 'rotate(' + ang(clamp(v, cfg.min, cfg.max)) + 'deg)';
                val.textContent = cfg.valueFmt ? cfg.valueFmt(v) : Math.round(v);
            },
        };
    }

    // ── Compass / heading card ───────────────────────────────────────────
    function makeCompass(id) {
        const box = document.getElementById(id);
        if (!box) return { set() {} };
        const svg = baseSvg();
        const card = el('g', {});
        card.style.transformOrigin = C + 'px ' + C + 'px';
        for (let d = 0; d < 360; d += 10) {
            const major = d % 30 === 0;
            const [x1, y1] = polar(C, C, R - 4, d);
            const [x2, y2] = polar(C, C, R - (major ? 13 : 8), d);
            card.appendChild(el('line', { x1, y1, x2, y2, class: major ? 'inst-tick-major' : 'inst-tick-minor' }));
            if (major) {
                const [lx, ly] = polar(C, C, R - 25, d);
                const t = el('text', { x: lx, y: ly, class: 'inst-tick-label', 'text-anchor': 'middle', 'dominant-baseline': 'central' });
                t.textContent = { 0: 'N', 90: 'E', 180: 'S', 270: 'W' }[d] || (d / 10);
                card.appendChild(t);
            }
        }
        svg.appendChild(card);
        svg.appendChild(el('polygon', { points: C + ',12 ' + (C - 5) + ',23 ' + (C + 5) + ',23', class: 'compass-lubber' }));
        svg.appendChild(el('line', { x1: C, y1: C - 26, x2: C, y2: C + 30, class: 'compass-ac' }));
        svg.appendChild(el('line', { x1: C - 24, y1: C + 2, x2: C + 24, y2: C + 2, class: 'compass-ac' }));
        svg.appendChild(el('line', { x1: C - 10, y1: C + 22, x2: C + 10, y2: C + 22, class: 'compass-ac' }));
        const val = el('text', { x: C, y: C + 52, class: 'inst-digital', 'text-anchor': 'middle' });
        val.textContent = '---';
        svg.appendChild(val);
        box.appendChild(svg);
        return {
            set(h) {
                if (h == null || isNaN(h)) { val.textContent = '---'; return; }
                const hh = ((h % 360) + 360) % 360;
                card.style.transform = 'rotate(' + (-hh) + 'deg)';
                val.textContent = String(Math.round(hh)).padStart(3, '0') + '°';
            },
        };
    }

    // ── Turn coordinator (rate of turn, derived from track change) ───────
    function makeTurn(id) {
        const box = document.getElementById(id);
        if (!box) return { set() {} };
        const svg = baseSvg();
        [-30, 30].forEach(a => {
            const [x1, y1] = polar(C, C, R - 4, a + 90);
            const [x2, y2] = polar(C, C, R - 16, a + 90);
            svg.appendChild(el('line', { x1, y1, x2, y2, class: 'inst-tick-major' }));
            const [x3, y3] = polar(C, C, R - 4, -a + 270);
            const [x4, y4] = polar(C, C, R - 16, -a + 270);
            svg.appendChild(el('line', { x1: x3, y1: y3, x2: x4, y2: y4, class: 'inst-tick-major' }));
        });
        const ll = el('text', { x: 30, y: C - 30, class: 'inst-unit', 'text-anchor': 'middle' }); ll.textContent = 'L';
        const rl = el('text', { x: 170, y: C - 30, class: 'inst-unit', 'text-anchor': 'middle' }); rl.textContent = 'R';
        svg.appendChild(ll); svg.appendChild(rl);
        const ac = el('g', {});
        ac.style.transformOrigin = C + 'px ' + C + 'px';
        ac.style.transition = 'transform 0.5s ease-out';
        ac.appendChild(el('line', { x1: C - 46, y1: C, x2: C + 46, y2: C, class: 'turn-ac' }));
        ac.appendChild(el('line', { x1: C, y1: C - 10, x2: C, y2: C + 16, class: 'turn-ac' }));
        ac.appendChild(el('circle', { cx: C, cy: C, r: 4, class: 'turn-ac-hub' }));
        svg.appendChild(ac);
        const val = el('text', { x: C, y: C + 52, class: 'inst-digital', 'text-anchor': 'middle' });
        val.textContent = '--';
        svg.appendChild(val);
        const unit = el('text', { x: C, y: C + 66, class: 'inst-unit', 'text-anchor': 'middle' }); unit.textContent = '°/s';
        svg.appendChild(unit);
        box.appendChild(svg);
        return {
            // Derived rate of turn from track change (no EHS data).
            set(rate) {
                unit.textContent = '°/s';
                if (rate == null || isNaN(rate)) { val.textContent = '--'; ac.style.transform = 'rotate(0deg)'; return; }
                // Standard rate (3 deg/s) tilts the wings to the index marks (~20deg).
                ac.style.transform = 'rotate(' + clamp(rate / 3 * 20, -35, 35) + 'deg)';
                val.textContent = (rate >= 0 ? '+' : '') + rate.toFixed(1);
            },
            // Actual bank angle from Enhanced Mode-S roll.
            setBank(roll) {
                unit.textContent = 'BANK';
                if (roll == null || isNaN(roll)) { val.textContent = '--'; ac.style.transform = 'rotate(0deg)'; return; }
                ac.style.transform = 'rotate(' + clamp(roll, -60, 60) + 'deg)';
                val.textContent = (roll >= 0 ? 'R' : 'L') + Math.abs(Math.round(roll)) + '°';
            },
        };
    }

    // ── Build gauges ─────────────────────────────────────────────────────
    const asi = makeGauge('instAsi', { min: 0, max: 600, a0: -150, a1: 150, minorStep: 20, major: [0, 100, 200, 300, 400, 500, 600], unit: 'kt' });
    const alt = makeGauge('instAlt', { min: 0, max: 40000, a0: -150, a1: 150, minorStep: 2500, major: [0, 10000, 20000, 30000, 40000], unit: 'ft', labelFmt: v => v / 1000, valueFmt: v => Math.round(v).toLocaleString() });
    const vsi = makeGauge('instVsi', { min: -4000, max: 4000, a0: -180, a1: 0, minorStep: 1000, major: [-4000, -2000, 0, 2000, 4000], unit: 'fpm', labelFmt: v => v / 1000, valueFmt: v => (v >= 0 ? '+' : '') + Math.round(v).toLocaleString() });
    const hdg = makeCompass('instHdg');
    const turn = makeTurn('instTurn');

    // ── Live data ────────────────────────────────────────────────────────
    let prevTrack = null, prevTs = null;
    function angDiff(a, b) { let d = a - b; while (d > 180) d -= 360; while (d < -180) d += 360; return d; }
    function setText(id, t) { const e = document.getElementById(id); if (e) e.textContent = t; }

    function setHidden(id, hidden) { const e = document.getElementById(id); if (e) e.hidden = hidden; }

    function update(ac) {
        const stateEl = document.getElementById('acState');
        if (!ac) {
            if (stateEl) { stateEl.textContent = 'NO DATA'; stateEl.className = 'pfd-state nodata'; }
            asi.set(null); alt.set(null); vsi.set(null); hdg.set(null); turn.set(null);
            setHidden('ehsSection', true); setHidden('ehsBadge', true);
            return;
        }
        if (stateEl) { stateEl.textContent = 'LIVE'; stateEl.className = 'pfd-state live'; }
        setText('acCall', (ac.callsign || ac.icao || '--').trim());
        setText('acType', ac.type_desc || ac.type_code || '--');
        setText('acReg', ac.registration || '--');
        setText('acIcao', (ac.icao || ICAO || '------').toUpperCase());
        setText('acSquawk', ac.squawk || '----');

        const gs = ac.speed, altitude = ac.altitude, trk = ac.heading, vs = ac.vertical_rate;
        const hasEhs = !!ac.ehs;

        // Airspeed: indicated airspeed when Enhanced Mode-S is available, else ground speed.
        if (ac.ias != null) { asi.set(ac.ias); setText('nameAsi', 'AIRSPEED (IAS)'); }
        else { asi.set(gs); setText('nameAsi', 'AIRSPEED (GS)'); }

        alt.set(altitude);
        vsi.set(vs);

        // Heading: magnetic heading when available, else ground track.
        if (ac.mag_heading != null) { hdg.set(ac.mag_heading); setText('nameHdg', 'HEADING (MAG)'); }
        else { hdg.set(trk); setText('nameHdg', 'TRACK'); }

        // Turn/bank: actual bank from EHS roll when available, else derived turn rate.
        let rate = null;
        const now = Date.now();
        if (trk != null && prevTrack != null && prevTs != null) {
            const dt = (now - prevTs) / 1000;
            if (dt > 0.2 && dt < 30) rate = angDiff(trk, prevTrack) / dt;
        }
        if (trk != null) { prevTrack = trk; prevTs = now; }
        if (ac.roll != null) { turn.setBank(ac.roll); setText('nameTurn', 'BANK'); }
        else { turn.set(rate); setText('nameTurn', 'TURN'); }

        setText('nameAlt', ac.sel_altitude != null ? 'ALTITUDE · SEL ' + Math.round(ac.sel_altitude).toLocaleString() : 'ALTITUDE');

        setText('dAlt', altitude != null ? Math.round(altitude).toLocaleString() + ' ft' : '--');
        setText('dGs', gs != null ? Math.round(gs) + ' kt' : '--');
        setText('dTrk', trk != null ? String(Math.round(trk)).padStart(3, '0') + '°' : '--');
        setText('dVs', vs != null ? (vs >= 0 ? '+' : '') + Math.round(vs).toLocaleString() + ' fpm' : '--');
        setText('dTurn', rate != null ? (rate >= 0 ? '+' : '') + rate.toFixed(1) + ' °/s' : '--');
        setText('dPos', (ac.lat != null && ac.lon != null) ? ac.lat.toFixed(3) + ', ' + ac.lon.toFixed(3) : '--');

        // Enhanced Mode-S readouts.
        setHidden('ehsSection', !hasEhs);
        setHidden('ehsBadge', !hasEhs);
        if (hasEhs) {
            setText('dIas', ac.ias != null ? Math.round(ac.ias) + ' kt' : '--');
            setText('dTas', ac.tas != null ? Math.round(ac.tas) + ' kt' : '--');
            setText('dMach', ac.mach != null ? ac.mach.toFixed(2) : '--');
            setText('dMagHdg', ac.mag_heading != null ? String(Math.round(ac.mag_heading)).padStart(3, '0') + '°' : '--');
            setText('dRoll', ac.roll != null ? (ac.roll >= 0 ? 'R' : 'L') + Math.abs(Math.round(ac.roll)) + '°' : '--');
            setText('dSelAlt', ac.sel_altitude != null ? Math.round(ac.sel_altitude).toLocaleString() + ' ft' : '--');
        }
    }

    async function poll() {
        try {
            const r = await fetch('/adsb/aircraft?icao=' + encodeURIComponent(ICAO));
            const d = await r.json();
            update((d.aircraft && d.aircraft[0]) || null);
        } catch (e) { /* keep last frame */ }
    }

    if (!ICAO) {
        setText('acState', 'NO AIRCRAFT');
    } else {
        poll();
        setInterval(poll, POLL_MS);
    }
})();
