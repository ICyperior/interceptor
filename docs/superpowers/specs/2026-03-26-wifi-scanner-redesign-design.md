# WiFi Scanner UI Redesign

**Date:** 2026-03-26
**Status:** Approved
**Scope:** Frontend only — CSS, JS, HTML. No backend changes.

## Overview

Redesign the WiFi scanner's main content area for better space utilisation, visual clarity, and polish. The three-panel layout (networks table / proximity radar / analysis) is kept but each panel is significantly enhanced.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Table row density | Slightly taller (2-line rows) | Adds visual richness without losing too many rows |
| Radar style | Rotating sweep with trailing glow arc | Most recognisable "radar" metaphor, eye-catching |
| Channel analysis | Heatmap (channels × time) | Shows congestion history, more useful than a snapshot bar chart |
| Row click behaviour | Right panel takeover (detail replaces heatmap) | Keeps spatial layout stable; no scroll disruption |

## CSS Tokens

Tokens confirmed in `index.css :root`: `--bg-primary`, `--bg-secondary`, `--bg-tertiary`, `--bg-card`, `--border-color`, `--border-light`, `--text-primary`, `--text-secondary`, `--text-dim`, `--accent-cyan`, `--accent-cyan-dim`, `--accent-green`, `--accent-green-dim`, `--accent-red`, `--accent-red-dim`, `--accent-orange`, `--accent-amber`, `--accent-amber-dim`.

**Not defined in `index.css :root`** — do not use:
- `--accent-yellow` (used in some existing WiFi rules but undefined — use `--accent-orange` instead)
- `--accent-cyan-rgb` (undefined — use the literal `rgba(74, 163, 255, 0.07)` for the selected row tint)

## Component Designs

### 1. Status Bar

Enhanced version of the existing `wifi-status-bar`:

- Existing fields preserved: Networks · Clients · Hidden (IDs: `wifiNetworkCount`, `wifiClientCount`, `wifiHiddenCount`)
- **New — Open count:** Add `<div class="wifi-status-item"><span class="wifi-status-label">Open</span><span class="wifi-status-value" id="wifiOpenCount" style="color:var(--accent-red);">0</span></div>` after the Hidden item. Populated by `renderNetworks()` counting networks where `security === 'Open'`.
- **Scan indicator — HTML:** Replace the existing `<div class="wifi-status-item" id="wifiScanStatus">` with:
  ```html
  <div class="wifi-scan-indicator" id="wifiScanIndicator">
    <span class="wifi-scan-dot"></span>
    <span class="wifi-scan-text">IDLE</span>
  </div>
  ```
  CSS: `.wifi-scan-dot` is a 7×7px circle with `animation: wifi-pulse 1.2s ease-in-out infinite` (opacity 1→0.4→1, scale 1→0.7→1). The dot is hidden (`display:none`) when idle; shown when scanning. `.wifi-scan-indicator` is floated/pushed to `margin-left: auto`.
- `updateScanningState(scanning, scanMode)` — revised body:
  ```js
  const dot  = elements.scanIndicator?.querySelector('.wifi-scan-dot');
  const text = elements.scanIndicator?.querySelector('.wifi-scan-text');
  if (dot)  dot.style.display  = scanning ? 'inline-block' : 'none';
  if (text) text.textContent   = scanning
    ? `SCANNING (${scanMode === 'quick' ? 'Quick' : 'Deep'})`
    : 'IDLE';
  ```
  `elements.scanIndicator` replaces `elements.scanStatus` in the `elements` map, pointing to `#wifiScanIndicator`.

### 2. Networks Table

**Structural change:** Remove `<table id="wifiNetworkTable">` (including `<thead>` and `<tbody id="wifiNetworkTableBody">`). Replace with `<div id="wifiNetworkList" class="wifi-network-list">`. Update `elements.networkTable → elements.networkList` and `elements.networkTableBody → elements.networkList` in the `elements` map in `wifi.js`.

**Sort controls:** The `th[data-sort]` click listener on the old table is removed. Add three small text-style sort buttons to the `.wifi-networks-header`:
```html
<div class="wifi-sort-controls">
  <span class="wifi-sort-label">Sort:</span>
  <button class="wifi-sort-btn active" data-sort="signal">Signal</button>
  <button class="wifi-sort-btn" data-sort="ssid">SSID</button>
  <button class="wifi-sort-btn" data-sort="channel">Ch</button>
</div>
```
Clicking a button sets the existing `sortBy` state variable and calls `renderNetworks()`. Active button gets `.active` class (cyan text).

**Filter buttons:** `#wifiNetworkFilters` / `.wifi-filter-btn` bar (All / 2.4G / 5G / Open / Hidden) is kept as-is in HTML. The JS `applyFilters()` function is adapted to operate on `<div class="network-row">` elements using their `data-band` and `data-security` attributes (same logic, different element type).

**Row HTML structure:**
```html
<div class="network-row threat-{open|safe|hidden}"
     data-bssid="AA:BB:CC:DD:EE:FF"
     data-band="2.4"
     data-security="Open"
     onclick="WiFiMode.selectNetwork('AA:BB:CC:DD:EE:FF')">
  <div class="row-top">
    <span class="row-ssid [hidden-net]">SSID or [Hidden] BSSID</span>
    <div class="row-badges">
      <span class="badge open|wpa2|wpa3|wep">LABEL</span>
      <!-- if hidden: --><span class="badge hidden-tag">HIDDEN</span>
    </div>
  </div>
  <div class="row-bottom">
    <div class="signal-bar-wrap">
      <div class="signal-track">
        <div class="signal-fill strong|medium|weak" style="width: N%"></div>
      </div>
    </div>
    <div class="row-meta">
      <span>ch N</span>
      <span>N ↔</span>
      <span class="row-rssi">−N</span>
    </div>
  </div>
</div>
```

**Left border colour (CSS class on `.network-row`):**
- `.threat-open` → `border-left: 3px solid var(--accent-red)`
- `.threat-safe` → `border-left: 3px solid var(--accent-green)` (WPA2, WPA3)
- `.threat-hidden` → `border-left: 3px solid var(--border-color)`

**Signal bar fill width:** `pct = Math.max(0, Math.min(100, (rssi + 100) / 80 * 100))` where −100 dBm → 0%, −20 dBm → 100%.

**Signal fill classes:**
- `.strong` (rssi > −55): `background: linear-gradient(90deg, var(--accent-green), #88d49b)`
- `.medium` (−55 ≥ rssi > −70): `background: linear-gradient(90deg, var(--accent-green), var(--accent-orange))`
- `.weak` (rssi ≤ −70): `background: linear-gradient(90deg, var(--accent-orange), var(--accent-red))`

**Security string → badge class mapping** (mirrors existing `securityClass` logic in `wifi.js`):
```js
function securityBadgeClass(security) {
  const s = (security || '').toLowerCase();
  if (s === 'open' || s === '') return 'open';
  if (s.includes('wpa3'))       return 'wpa3';
  if (s.includes('wpa'))        return 'wpa2'; // covers WPA2, WPA/WPA2, WPA PSK, etc.
  if (s.includes('wep'))        return 'wep';
  return 'wpa2'; // fallback for unknown encrypted
}
```

**Security badge colours:**
- `.badge.open` — `var(--accent-red)` text + border, `var(--accent-red-dim)` background
- `.badge.wpa2` — `var(--accent-green)` text + border, `var(--accent-green-dim)` background
- `.badge.wpa3` — `var(--accent-cyan)` text + border, `var(--accent-cyan-dim)` background
- `.badge.wep` — `var(--accent-orange)` text + border, `var(--accent-amber-dim)` background
- `.badge.hidden-tag` — `var(--text-dim)` text, `var(--border-color)` border, transparent background

**Radar dot colours (same semantic mapping):**
- Open: `var(--accent-red)` / `#e25d5d`
- WPA2 / WPA3: `var(--accent-green)` / `#38c180`
- WEP: `var(--accent-orange)` / `#d6a85e`
- Unknown / Hidden: `#484f58`

**Selected state persistence across re-renders:** `WiFiMode` stores the selected BSSID in module-level `let selectedBssid = null`. After `renderNetworks()` rebuilds the list, if `selectedBssid` is set, find the matching row by `data-bssid` and add `.selected` to it; also refresh `#wifiDetailView` with updated data for that network.

**Empty state:** When network list is empty, insert `<div class="wifi-network-placeholder"><p>No networks detected.<br>Start a scan to begin.</p></div>`.

**Row states:**
- Hover: `background: var(--bg-tertiary)`
- Selected: `background: rgba(74, 163, 255, 0.07)` + `border-left-color: var(--accent-cyan)` (via `.selected` class, overrides threat colour)

### 3. Proximity Radar

**Existing `#wifiProximityRadar` div** contents replaced with an inline SVG. `wifi.js` adds `renderRadar(networks)`.

**SVG:** `width="100%" viewBox="0 0 210 210"`, centre `(105, 105)`. A `<clipPath id="wifi-radar-clip"><circle cx="105" cy="105" r="100"/></clipPath>` is applied to the rotating group to prevent arc overflow.

**Rings (static, outside rotating group):**
```html
<circle cx="105" cy="105" r="100" fill="none" stroke="#00b4d8" stroke-width="0.5" opacity="0.12"/>
<circle cx="105" cy="105" r="70"  fill="none" stroke="#00b4d8" stroke-width="0.5" opacity="0.18"/>
<circle cx="105" cy="105" r="40"  fill="none" stroke="#00b4d8" stroke-width="0.5" opacity="0.25"/>
<circle cx="105" cy="105" r="15"  fill="none" stroke="#00b4d8" stroke-width="0.5" opacity="0.35"/>
```

**Sweep animation — CSS:**
```css
.wifi-radar-sweep {
  transform-origin: 105px 105px;
  animation: wifi-radar-rotate 3s linear infinite;
}
@keyframes wifi-radar-rotate {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
```

**Sweep group** `<g class="wifi-radar-sweep" clip-path="url(#wifi-radar-clip)">`:
- Trailing arc 60°: `<path d="M105,105 L105,5 A100,100 0 0,1 191.6,155 Z" fill="#00b4d8" opacity="0.08"/>`
  _(endpoint derived: x = 105 + 100·sin(60°) ≈ 191.6, y = 105 − 100·cos(60°) + 100 = 155)_
- Trailing arc 90°: `<path d="M105,105 L105,5 A100,100 0 0,1 205,105 Z" fill="#00b4d8" opacity="0.04"/>`
  _(endpoint: x=205, y=105 — 90° clockwise from top)_
- Sweep line: `<line x1="105" y1="105" x2="105" y2="5" stroke="#00b4d8" stroke-width="1.5" opacity="0.7"/>`

**Network dots** (rendered as SVG `<circle>` elements outside the rotating group, replaced by `renderRadar(networks)`):
- Angle per dot: determined by `bssidToAngle(bssid)` — a simple hash of the BSSID string modulo 2π. This produces stable positions across re-renders for the same network.
- Radius from centre: `dotR = 5 + (1 - Math.max(0, Math.min(1, (rssi + 100) / 80))) * 90` — stronger signal → smaller radius
- Zone thresholds: dotR < 35 = Near, 35–70 = Mid, >70 = Far
- Visual radius: Near → 6, Mid → 4.5, Far → 3 (px)
- Each dot: a main `<circle>` + a glow halo `<circle>` at 1.5× visual radius, same fill colour, 12% opacity
- Colour: see Section 2 "Radar dot colours"

**Zone counts:** `renderRadar()` updates `#wifiZoneImmediate`, `#wifiZoneNear`, `#wifiZoneFar` (IDs unchanged).

### 4. Right Panel — Channel Heatmap + Security Ring

**Removed elements:** The existing `.wifi-channel-section` (containing `.wifi-channel-tabs` and `#wifiChannelChart`), `.wifi-security-section`, and the IDs `openCount`, `wpa2Count`, `wpa3Count`, `wepCount` are all removed from the HTML. Any JS references to these IDs are also removed.

**New `.wifi-analysis-panel` inner HTML:**
```html
<div class="wifi-analysis-panel-header">
  <span class="panel-title" id="wifiRightPanelTitle">Channel Heatmap</span>
  <button class="wifi-detail-back-btn" id="wifiDetailBackBtn" style="display:none"
          onclick="WiFiMode.closeDetail()">← Back</button>
</div>

<div id="wifiHeatmapView">
  <div class="wifi-heatmap-wrap">
    <div class="wifi-heatmap-label">2.4 GHz · Last <span id="wifiHeatmapCount">0</span> scans</div>
    <div class="wifi-heatmap-ch-labels">
      <!-- 11 divs, text content 1–11 -->
    </div>
    <div class="wifi-heatmap-grid" id="wifiHeatmapGrid"></div>
    <div class="wifi-heatmap-legend">
      <span>Low</span>
      <div class="wifi-heatmap-legend-grad"></div>
      <span>High</span>
    </div>
  </div>
  <div class="wifi-security-ring-wrap">
    <svg id="wifiSecurityRingSvg" viewBox="0 0 48 48" width="48" height="48">
      <!-- arcs injected by renderSecurityRing() -->
      <circle cx="24" cy="24" r="9" fill="var(--bg-primary)"/>
    </svg>
    <div class="wifi-security-ring-legend" id="wifiSecurityRingLegend">
      <!-- legend rows injected by renderSecurityRing() -->
    </div>
  </div>
</div>

<div id="wifiDetailView" style="display:none">
  <!-- detail content — see Section 5 -->
</div>
```

**5 GHz heatmap:** Tab toggle removed. Heatmap always shows 2.4 GHz. **5 GHz networks are excluded from `channelHistory` snapshots** — only networks with `band === '2.4'` are counted when building each snapshot.

**Heatmap data source:** Module-level `let channelHistory = []` (max 10 entries). Each `renderNetworks()` prepends `{ timestamp: Date.now(), channels: { 1:N, …, 11:N } }` built from 2.4 GHz networks only. Entries beyond 10 are dropped. `wifiHeatmapCount` span is updated with `channelHistory.length`.

**Heatmap grid DOM structure:** `#wifiHeatmapGrid` is a CSS grid container (`display: grid; grid-template-columns: 26px repeat(11, 1fr); gap: 2px`). `renderHeatmap()` clears and rebuilds it on every call. For each of the up to 10 history entries (newest first), it creates one row of 12 elements: a time-label `<div class="wifi-heatmap-time-label">` (text "now" for index 0, empty for others) followed by 11 cell `<div class="wifi-heatmap-cell">` elements whose `background` is set by `congestionColor()`. Total DOM nodes: up to 10 × 12 = 120 divs, fully rebuilt on each render call.

**Cell colour — `congestionColor(value, maxValue)`:**
```js
function congestionColor(value, maxValue) {
  if (value === 0 || maxValue === 0) return '#0d1117';
  const ratio = value / maxValue;
  if (ratio < 0.05)  return '#0d1117';
  if (ratio < 0.25)  return `rgba(13,74,110,${(ratio * 4).toFixed(2)})`;
  if (ratio < 0.5)   return `rgba(14,165,233,${ratio.toFixed(2)})`;
  if (ratio < 0.75)  return `rgba(249,115,22,${ratio.toFixed(2)})`;
  return               `rgba(239,68,68,${ratio.toFixed(2)})`;
}
```
`maxValue` = the maximum cell value across the entire `channelHistory` array (for consistent colour scale).

**Empty/loading state:** When `channelHistory.length === 0`, `#wifiHeatmapGrid` shows a single placeholder div: `<div class="wifi-heatmap-empty">Scan to populate channel history</div>`.

**Security ring — `renderSecurityRing(networks)`:**

Circumference `C = 2 * Math.PI * 15 ≈ 94.25`. Each segment is a `<circle>` element with `stroke-dasharray="${arcLen} ${C - arcLen}"` and a `stroke-dashoffset` that positions it after the previous segments. The ring starts at the top by applying `transform="rotate(-90 24 24)"` to each arc.

Standard SVG donut-segment technique — `dashoffset` for segment N = `-(sum of all preceding arcLengths)`:

```js
const C = 2 * Math.PI * 15; // ≈ 94.25
const segments = [
  { label: 'WPA2', color: 'var(--accent-green)', count: wpa2 },
  { label: 'Open', color: 'var(--accent-red)',   count: open },
  { label: 'WPA3', color: 'var(--accent-cyan)',  count: wpa3 },
  { label: 'WEP',  color: 'var(--accent-orange)',count: wep  },
];
const total = segments.reduce((s, seg) => s + seg.count, 0) || 1;
let offset = 0;
segments.forEach(seg => {
  const arcLen = (seg.count / total) * C;
  // <circle cx="24" cy="24" r="15" fill="none"
  //   stroke="seg.color" stroke-width="7"
  //   stroke-dasharray="${arcLen} ${C - arcLen}"
  //   stroke-dashoffset="${-offset}"
  //   transform="rotate(-90 24 24)"/>
  offset += arcLen;
});
// Worked example: total=10, WPA2=7, Open=3:
//   WPA2: arcLen=66.0, dasharray="66 28.2", dashoffset="0"
//   Open: arcLen=28.2, dasharray="28.2 66",  dashoffset="-66"
```

- Centre hole: `<circle cx="24" cy="24" r="9" fill="var(--bg-primary)"/>` injected last (on top)
- Legend rows injected into `#wifiSecurityRingLegend`: coloured square + name + count

### 5. Right Panel — Network Detail

**`#wifiDetailDrawer` deletion:** Delete the entire `<div class="wifi-detail-drawer" id="wifiDetailDrawer">` block (~lines 940–1000 of `index.html`). Also remove all associated CSS from `index.css`: `.wifi-detail-drawer`, `.wifi-detail-drawer.open`, `.wifi-detail-header`, `.wifi-detail-title`, `.wifi-detail-essid`, `.wifi-detail-bssid`, `.wifi-detail-close`, `.wifi-detail-content`, `.wifi-detail-grid`, `.wifi-detail-stat`. In `wifi.js`, remove `detailDrawer` from the `elements` map and remove its usage in the existing `closeDetail()` (`detailDrawer.classList.remove('open')`) since `closeDetail()` is being replaced.

**`#wifiDetailView` inner HTML:**
```html
<div class="wifi-detail-inner">
  <div class="wifi-detail-head">
    <div class="wifi-detail-essid" id="wifiDetailEssid">—</div>
    <div class="wifi-detail-bssid" id="wifiDetailBssid">—</div>
  </div>

  <div class="wifi-detail-signal-bar">
    <div class="wifi-detail-signal-labels">
      <span>Signal</span>
      <span id="wifiDetailRssi">—</span>
    </div>
    <div class="wifi-detail-signal-track">
      <div class="wifi-detail-signal-fill" id="wifiDetailSignalFill" style="width:0%"></div>
    </div>
  </div>

  <div class="wifi-detail-grid">
    <div class="wifi-detail-stat"><span class="label">Channel</span><span class="value" id="wifiDetailChannel">—</span></div>
    <div class="wifi-detail-stat"><span class="label">Band</span><span class="value" id="wifiDetailBand">—</span></div>
    <div class="wifi-detail-stat"><span class="label">Security</span><span class="value" id="wifiDetailSecurity">—</span></div>
    <div class="wifi-detail-stat"><span class="label">Cipher</span><span class="value" id="wifiDetailCipher">—</span></div>
    <div class="wifi-detail-stat"><span class="label">Clients</span><span class="value" id="wifiDetailClients">—</span></div>
    <div class="wifi-detail-stat"><span class="label">First Seen</span><span class="value" id="wifiDetailFirstSeen">—</span></div>
    <div class="wifi-detail-stat"><span class="label">Vendor</span><span class="value" id="wifiDetailVendor" style="font-size:11px;">—</span></div>
  </div>

  <!-- Existing client list sub-panel — copy verbatim from current #wifiDetailDrawer -->
  <div class="wifi-detail-clients" id="wifiDetailClientList" style="display: none;">
    <h6>Connected Clients <span class="wifi-client-count-badge" id="wifiClientCountBadge"></span></h6>
    <div class="wifi-client-list"></div>
  </div>

  <div class="wifi-detail-actions">
    <!-- Copy this onclick verbatim from the existing locate button in #wifiDetailDrawer: -->
    <button class="wfl-locate-btn" title="Locate this AP"
      onclick="(function(){ var p={bssid: document.getElementById('wifiDetailBssid')?.textContent, ssid: document.getElementById('wifiDetailEssid')?.textContent}; if(typeof WiFiLocate!=='undefined'){WiFiLocate.handoff(p);return;} if(typeof switchMode==='function'){switchMode('wifi_locate').then(function(){if(typeof WiFiLocate!=='undefined')WiFiLocate.handoff(p);});} })()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="10" r="3"/><path d="M12 21.7C17.3 17 20 13 20 10a8 8 0 1 0-16 0c0 3 2.7 7 8 11.7z"/></svg>
      Locate
    </button>
    <button class="wifi-detail-close-btn" onclick="WiFiMode.closeDetail()">Close</button>
  </div>
</div>
```

**Signal bar fill width:** Same formula as table rows — `pct = Math.max(0, Math.min(100, (rssi + 100) / 80 * 100))`.

**Show/hide:**
- `WiFiMode.selectNetwork(bssid)`: hides `#wifiHeatmapView`, shows `#wifiDetailView`, sets `#wifiRightPanelTitle` text to "Network Detail", shows `#wifiDetailBackBtn`, stores `selectedBssid = bssid`
- `WiFiMode.closeDetail()`: reverses — shows heatmap, hides detail, restores title to "Channel Heatmap", hides back button, sets `selectedBssid = null`

**"← Back" vs "Close":** Both call `WiFiMode.closeDetail()`. "← Back" is in the panel header (always reachable at top); "Close" is at the bottom of the detail body for users who scrolled down.

**`wifiClientCountBadge`** is preserved inside `#wifiDetailClientList` — no changes to the client list sub-panel.

## File Changes

| File | Change |
|---|---|
| `static/css/index.css` | Update WiFi section CSS (~line 3515): add `.wifi-scan-indicator`, `.wifi-scan-dot` + keyframes; replace table CSS with `.wifi-network-list`, `.network-row`, `.row-top`, `.row-bottom`, `.signal-bar-*`, `.badge.*`, `.wifi-sort-*`; add `.wifi-radar-sweep` + `@keyframes wifi-radar-rotate`; replace channel/security section CSS with `.wifi-heatmap-*`, `.wifi-security-ring-*`, `.wifi-analysis-panel-header`, `.wifi-detail-back-btn`; add `.wifi-detail-inner`, `.wifi-detail-*` |
| `templates/index.html` | WiFi section (~line 820): replace `<table>` with `<div id="wifiNetworkList">`, add sort controls to header, add `#wifiOpenCount` to status bar, replace `#wifiScanStatus` with `#wifiScanIndicator`, replace right panel contents with `#wifiHeatmapView` / `#wifiDetailView`, add `#wifiRightPanelTitle` + `#wifiDetailBackBtn` to panel header, inline radar SVG shell (static rings + empty dot group), remove `#wifiDetailDrawer` |
| `static/js/modes/wifi.js` | Update `elements` map (remove `networkTable`, `networkTableBody`, `detailDrawer`, `wpa3Count`, `wpa2Count`, `wepCount`, `openCount`; add `networkList`, `openCount` → `wifiOpenCount`, `scanIndicator`, `heatmapGrid`, `heatmapCount`, `securityRingSvg`, `securityRingLegend`, `heatmapView`, `detailView`, `rightPanelTitle`, `detailBackBtn`, `detailSignalFill`); update `renderNetworks()` (div rows, filter + sort, persistence of `selectedBssid`); update `updateScanningState()`; add `renderRadar(networks)`; add `renderHeatmap()`; add `renderSecurityRing(networks)`; add `selectNetwork(bssid)` / `closeDetail()`; remove `th[data-sort]` listener |
| `templates/partials/modes/wifi.html` | No changes — sidebar out of scope |

## Out of Scope

- WiFi locate mode (separate)
- Sidebar panel (signal source, scan settings, attack options, handshake capture)
- Mobile/responsive layout changes
- 5 GHz channel heatmap data population
- Any backend / route changes
