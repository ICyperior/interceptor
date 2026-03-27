# Bluetooth UI Polish — Apply WiFi Treatment

**Date:** 2026-03-27
**Status:** Approved
**Scope:** Frontend only — CSS, JS, HTML. No backend changes.

## Overview

Apply the same visual polish introduced in the WiFi scanner redesign to the Bluetooth scanner. Three areas are updated: device rows, proximity radar, and the device list header. The signal distribution strip is retained.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Row structure | Match WiFi 2-line layout closely | Visual consistency across modes |
| Locate button placement | Remove from rows, keep in detail panel only | Rows are already information-dense; locate is a detail-panel action |
| Radar sweep | CSS animation + trailing glow arc | Matches WiFi, replaces rAF loop in ProximityRadar |
| Header additions | Scan indicator + sort controls | Matches WiFi; sort by Signal / Name / Seen / Distance |
| Signal distribution strip | Keep | Provides a useful at-a-glance signal breakdown not present in WiFi |
| Sort/filter layout | Sort group + filter group on one combined controls row | Saves vertical space vs separate rows |

## CSS Tokens

Reuse tokens already defined in `index.css :root` — no new tokens needed:
`--bg-primary`, `--bg-secondary`, `--bg-tertiary`, `--bg-card`, `--border-color`, `--border-light`, `--text-primary`, `--text-secondary`, `--text-dim`, `--accent-cyan`, `--accent-cyan-dim`, `--accent-green`, `--accent-green-dim`, `--accent-red`, `--accent-red-dim`, `--accent-orange`, `--accent-amber-dim`.

Hardcoded literals used in WiFi are reused here for consistency:
- Selected row tint: `rgba(74, 163, 255, 0.07)`
- Radar arc fill: `rgba(0, 180, 216, N)` (same as WiFi's `#00b4d8`)

## Component Designs

### 1. Device List Header

**Scan indicator** — new `<div class="bt-scan-indicator" id="btScanIndicator">` added to `.wifi-device-list-header` (right-aligned via `margin-left: auto`):
```html
<div class="bt-scan-indicator" id="btScanIndicator">
  <span class="bt-scan-dot"></span>
  <span class="bt-scan-text">IDLE</span>
</div>
```
`.bt-scan-dot` is 7×7px circle, `animation: bt-scan-pulse 1.2s ease-in-out infinite` (opacity/scale identical to WiFi). Dot hidden when idle (same as WiFi pattern).

`setScanning(scanning)` updated — add after the existing start/stop btn toggle:
```js
const dot  = document.getElementById('btScanIndicator')?.querySelector('.bt-scan-dot');
const text = document.getElementById('btScanIndicator')?.querySelector('.bt-scan-text');
if (dot)  dot.style.display  = scanning ? 'inline-block' : 'none';
if (text) text.textContent   = scanning ? 'SCANNING' : 'IDLE';
```

### 2. Sort + Filter Controls Row

**Replaces** the separate `.bt-device-toolbar` (search stays above) and `.bt-device-filters` divs. A new combined controls row sits between the signal strip and the search input:

```html
<div class="bt-controls-row">
  <div class="bt-sort-group">
    <span class="bt-sort-label">Sort</span>
    <button class="bt-sort-btn active" data-sort="rssi">Signal</button>
    <button class="bt-sort-btn" data-sort="name">Name</button>
    <button class="bt-sort-btn" data-sort="seen">Seen</button>
    <button class="bt-sort-btn" data-sort="distance">Dist</button>
  </div>
  <div class="bt-filter-group">
    <button class="bt-filter-btn active" data-filter="all">All</button>
    <button class="bt-filter-btn" data-filter="new">New</button>
    <button class="bt-filter-btn" data-filter="named">Named</button>
    <button class="bt-filter-btn" data-filter="strong">Strong</button>
    <button class="bt-filter-btn" data-filter="trackers">Trackers</button>
  </div>
</div>
```

**Sort state:** add `let sortBy = 'rssi'` to module-level state. Sort button click handler (bound in `init()`):
```js
document.querySelectorAll('.bt-sort-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    sortBy = btn.dataset.sort;
    document.querySelectorAll('.bt-sort-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderAllDevices();
  });
});
```

**`renderAllDevices()`** — new function that re-renders all devices in sorted order:
```js
function renderAllDevices() {
  if (!deviceContainer) return;
  deviceContainer.innerHTML = '';
  const sorted = [...devices.values()].sort((a, b) => {
    if (sortBy === 'rssi')     return (b.rssi_current ?? -100) - (a.rssi_current ?? -100);
    if (sortBy === 'name')     return (a.name || 'zzz').localeCompare(b.name || 'zzz');
    if (sortBy === 'seen')     return (b.seen_count || 0) - (a.seen_count || 0);
    if (sortBy === 'distance') return (a.estimated_distance_m ?? 999) - (b.estimated_distance_m ?? 999);
    return 0;
  });
  sorted.forEach(device => renderDevice(device, false));
  applyDeviceFilter();
  highlightSelectedDevice(selectedDeviceId);
}
```

### 3. Device Rows

**`createSimpleDeviceCard(device)`** is rewritten to produce WiFi-style 2-line rows. All existing data attributes and filter attributes are preserved.

**Row HTML structure:**
```html
<div class="bt-device-row [is-tracker]"
     data-bt-device-id="…"
     data-is-new="…"
     data-has-name="…"
     data-rssi="…"
     data-is-tracker="…"
     data-search="…"
     role="button" tabindex="0" data-keyboard-activate="true"
     style="border-left-color: COLOR;">
  <div class="bt-row-top">
    <div class="bt-row-top-left">
      <span class="bt-proto-badge [ble|classic]">BLE|CLASSIC</span>
      <span class="bt-row-name [bt-unnamed]">NAME or ADDRESS</span>
      <!-- conditional badges: tracker, IRK, risk, flags, cluster -->
    </div>
    <div class="bt-row-top-right">
      <!-- conditional: flag badges -->
      <span class="bt-status-dot [new|known|tracker]"></span>
    </div>
  </div>
  <div class="bt-row-bottom">
    <div class="bt-signal-bar-wrap">
      <div class="bt-signal-track">
        <div class="bt-signal-fill [strong|medium|weak]" style="width: N%"></div>
      </div>
    </div>
    <div class="bt-row-meta">
      <span>MANUFACTURER or —</span>
      <span>~Xm</span>       <!-- omitted if no distance -->
      <span class="bt-row-rssi [strong|medium|weak]">−N</span>
    </div>
  </div>
</div>
```

**Signal fill width:** `pct = rssi != null ? Math.max(0, Math.min(100, (rssi + 100) / 70 * 100)) : 0` (preserves existing BT formula — range is −100 to −30, not −100 to −20 as in WiFi).

**Signal fill class thresholds** (match existing `getRssiColor` breakpoints):
- `.strong` (rssi ≥ −60): `background: linear-gradient(90deg, var(--accent-green), #88d49b)`
- `.medium` (−60 > rssi ≥ −75): `background: linear-gradient(90deg, var(--accent-green), var(--accent-orange))`
- `.weak` (rssi < −75): `background: linear-gradient(90deg, var(--accent-orange), var(--accent-red))`

**Left border colour:**
- High-confidence tracker: `#ef4444`
- Any tracker (lower confidence): `#f97316`
- Non-tracker: result of `getRssiColor(rssi)` (unchanged)

**Unnamed device:** Address shown in `.bt-row-name.bt-unnamed` with `color: var(--text-dim); font-style: italic`.

**Badges moved to top-right (not top-left):** `PERSIST`, `BEACON`, `STABLE` flag badges move to `.bt-row-top-right` (before status dot), keeping top-left clean. Tracker, IRK, risk, and cluster badges remain in `.bt-row-top-left` after the name.

**Locate button:** Removed from the row entirely. It exists only in `#btDetailContent` (already present, no change needed).

**Selected row state:**
```css
.bt-device-row.selected {
  background: rgba(74, 163, 255, 0.07);
  border-left-color: var(--accent-cyan) !important;
}
```
`highlightSelectedDevice()` is unchanged — it adds/removes `.selected` by `data-bt-device-id`.

### 4. Proximity Radar

**`proximity-radar.js` changes:**

`createSVG()` — add trailing arc group and CSS sweep class:
- Replace `<line class="radar-sweep" …>` with a `<g class="bt-radar-sweep" clip-path="url(#radarClip)">` containing:
  - Two trailing arc `<path>` elements (90° and 60°) with low opacity fills
  - The sweep `<line>` element
- Add `<clipPath id="radarClip"><circle …/></clipPath>` to `<defs>` to prevent arc overflow

`animateSweep()` — remove entirely. The CSS animation replaces it.

**CSS in `index.css` (BT section):**
```css
.bt-radar-sweep {
  transform-origin: CENTER_X px CENTER_Y px; /* 140px 140px — half of CONFIG.size */
  animation: bt-radar-rotate 3s linear infinite;
}
@keyframes bt-radar-rotate {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
```

`isPaused` handling: when paused, add `animation-play-state: paused` to `.bt-radar-sweep` via a class toggle instead of the rAF check. `setPaused(paused)` updated:
```js
const sweep = svg?.querySelector('.bt-radar-sweep');
if (sweep) sweep.style.animationPlayState = paused ? 'paused' : 'running';
```

**Arc path geometry** (center = 140, outerRadius = center − padding = 120):
- 90° arc endpoint: `(140 + 120, 140)` → `(260, 140)`
- 60° arc endpoint: `x = 140 + 120·sin(60°) ≈ 244`, `y = 140 − 120·cos(60°) + 120 = 200` → `(244, 200)`
- Sweep line: `x1=140 y1=140 x2=140 y2=20` (straight up)

## File Changes

| File | Change |
|---|---|
| `templates/index.html` | BT device list header: add `#btScanIndicator`; insert new `.bt-controls-row` (sort group + filter group) between `.bt-list-signal-strip` and `.bt-device-toolbar`; remove old standalone `.bt-device-filters` div; `.bt-device-toolbar` (search input) is kept unchanged |
| `static/js/modes/bluetooth.js` | Add `sortBy` state; add `renderAllDevices()`; rewrite `createSimpleDeviceCard()` for 2-line rows (no locate button); update `setScanning()` to drive `#btScanIndicator`; bind sort button listener in `init()`; remove the `locateBtn` branch from the delegated click handler in `bindListListeners()` (no `.bt-locate-btn[data-locate-id]` elements will exist in rows) |
| `static/js/components/proximity-radar.js` | `createSVG()`: add clip path + trailing arc group + CSS class on sweep; remove `animateSweep()` function and its call; update `setPaused()` to use `animationPlayState` |
| `static/css/index.css` | BT section: add `.bt-scan-indicator`, `.bt-scan-dot` + `@keyframes bt-scan-pulse`; replace `.bt-row-main`, `.bt-row-left/right`, `.bt-rssi-*`, `.bt-row-actions` with `.bt-row-top/bottom`, `.bt-signal-*`, `.bt-row-meta`, `.bt-row-name`; add `.bt-sort-btn`, `.bt-controls-row`, `.bt-sort-group`, `.bt-filter-group`; add `.bt-radar-sweep` + `@keyframes bt-radar-rotate` |

## Out of Scope

- Sidebar panel (scanner config, export, baseline)
- Tracker detection panel (left column)
- Zone summary cards under radar
- Radar filter buttons (New Only / Strongest / Unapproved) and Pause button
- Device detail panel (`#btDetailContent`) — already well-structured
- Bluetooth locate mode
- Any backend / route changes
