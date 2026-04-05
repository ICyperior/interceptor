# WiFi Scanner Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the WiFi scanner's main content area with richer network rows, an animated proximity radar sweep, a channel utilisation heatmap, a security ring chart, and a right-panel network detail view replacing the slide-up drawer.

**Architecture:** All changes are pure frontend — HTML structure in `templates/index.html`, styles in `static/css/index.css`, and JS logic in `static/js/modes/wifi.js`. No backend routes are touched. The five tasks are independent enough to be committed separately and the UI remains functional after each one.

**Tech Stack:** Vanilla JS (ES6 IIFE module pattern), CSS animations, inline SVG, Flask/Jinja2 templates.

---

## Spec & reference

- **Spec:** `docs/superpowers/specs/2026-03-26-wifi-scanner-redesign-design.md`
- **Start the app for manual verification:**
  ```bash
  sudo -E venv/bin/python intercept.py
  # Open http://localhost:5050/?mode=wifi
  ```

## File map

| File | What changes |
|---|---|
| `templates/index.html` | All structural HTML changes (lines ~822–1005 for WiFi section) |
| `static/css/index.css` | WiFi section CSS (lines ~3515–3970+) |
| `static/js/modes/wifi.js` | `cacheDOM()`, `scheduleRender()`, `updateNetworkTable()` → `renderNetworks()`, `updateStats()`, `initProximityRadar()` → `renderRadar()`, `initChannelChart()` → `renderHeatmap()` + `renderSecurityRing()`, `selectNetwork()`, `closeDetail()`, `updateDetailPanel()` |

---

## Task 1: Status bar — Open count + scan indicator

**Files:**
- Modify: `templates/index.html` (lines ~824–841)
- Modify: `static/css/index.css` (lines ~3531–3570)
- Modify: `static/js/modes/wifi.js` (`cacheDOM()` ~line 183, `updateScanningState()` ~line 670, `updateStats()` ~line 1475)

### Context

The status bar currently has: Networks · Clients · Hidden · [scan status text]. We're adding an Open count (red) and replacing the text status with a pulsing dot indicator.

The existing `#wifiScanStatus` element (line 837 of `index.html`) is replaced by `#wifiScanIndicator`. The existing `updateScanningState()` function (line ~670 of `wifi.js`) currently sets `.textContent` and `.className` on `elements.scanStatus` — it needs to toggle the dot's `display` instead.

- [ ] **Step 1: Update status bar HTML**

In `templates/index.html`, find the `wifi-status-bar` div (~line 824) and replace its contents:

```html
<div class="wifi-status-bar">
    <div class="wifi-status-item">
        <span class="wifi-status-label">Networks:</span>
        <span class="wifi-status-value" id="wifiNetworkCount">0</span>
    </div>
    <div class="wifi-status-item">
        <span class="wifi-status-label">Clients:</span>
        <span class="wifi-status-value" id="wifiClientCount">0</span>
    </div>
    <div class="wifi-status-item">
        <span class="wifi-status-label">Hidden:</span>
        <span class="wifi-status-value" id="wifiHiddenCount">0</span>
    </div>
    <div class="wifi-status-item">
        <span class="wifi-status-label">Open:</span>
        <span class="wifi-status-value" id="wifiOpenCount" style="color:var(--accent-red);">0</span>
    </div>
    <div class="wifi-scan-indicator" id="wifiScanIndicator">
        <span class="wifi-scan-dot"></span>
        <span class="wifi-scan-text">IDLE</span>
    </div>
</div>
```

- [ ] **Step 2: Add scan indicator CSS**

In `static/css/index.css`, find the `.wifi-status-bar` block (~line 3531) and add after it:

```css
.wifi-scan-indicator {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 10px;
    color: var(--accent-cyan);
    letter-spacing: 0.5px;
}

.wifi-scan-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--accent-cyan);
    display: none;
    animation: wifi-scan-pulse 1.2s ease-in-out infinite;
}

@keyframes wifi-scan-pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%       { opacity: 0.4; transform: scale(0.7); }
}
```

- [ ] **Step 3: Update JS — `cacheDOM()`**

In `wifi.js`, find `cacheDOM()` (~line 183). Replace:
```js
// Status bar
scanStatus: document.getElementById('wifiScanStatus'),
```
With:
```js
// Status bar
scanIndicator: document.getElementById('wifiScanIndicator'),
openCount: document.getElementById('wifiOpenCount'),
```
(Keep `networkCount`, `clientCount`, `hiddenCount` unchanged. Remove the old `openCount: document.getElementById('openCount')` line in the security counts section.)

- [ ] **Step 4: Update JS — `updateScanningState()`**

Find `updateScanningState()` (~line 670). Replace the body that references `elements.scanStatus` with:

```js
const dot  = elements.scanIndicator?.querySelector('.wifi-scan-dot');
const text = elements.scanIndicator?.querySelector('.wifi-scan-text');
if (dot)  dot.style.display = scanning ? 'inline-block' : 'none';
if (text) text.textContent  = scanning
    ? `SCANNING (${scanMode === 'quick' ? 'Quick' : 'Deep'})`
    : 'IDLE';
```

- [ ] **Step 5: Update JS — `updateStats()` — add Open count, remove old security IDs**

In `updateStats()` (~line 1475), find the block that updates `elements.wpa3Count`, `elements.wpa2Count`, `elements.wepCount`, `elements.openCount`. Replace the four element update lines with:

```js
if (elements.openCount) elements.openCount.textContent = securityCounts.open;
```

(Remove the `wpa3Count`, `wpa2Count`, `wepCount` lines — those elements no longer exist. Keep the `securityCounts` calculation above unchanged, it's still needed by Task 4.)

Also remove `wpa3Count`, `wpa2Count`, `wepCount` from `cacheDOM()` entirely.

- [ ] **Step 6: Verify**

```bash
sudo -E venv/bin/python intercept.py
```
Open `http://localhost:5050/?mode=wifi`. Check:
- Status bar shows Networks / Clients / Hidden / Open (red)
- Clicking Quick Scan shows pulsing cyan dot + "SCANNING (Quick)"
- Stopping shows "IDLE" with no dot
- Open count increments as open networks are discovered

- [ ] **Step 7: Commit**

```bash
git add templates/index.html static/css/index.css static/js/modes/wifi.js
git commit -m "feat(wifi): enhanced status bar with open count and scan indicator"
```

---

## Task 2: Networks table → styled div list

**Files:**
- Modify: `templates/index.html` (~lines 846–881)
- Modify: `static/css/index.css` (~lines 3582–3765)
- Modify: `static/js/modes/wifi.js` (`cacheDOM()`, `updateNetworkTable()`, `createNetworkRow()`, `initNetworkFilters()`, `initSortControls()`, `selectNetwork()`, `closeDetail()`)

### Context

The existing `<table id="wifiNetworkTable">` with 7 columns is replaced by `<div id="wifiNetworkList">`. The `updateNetworkTable()` / `createNetworkRow()` functions are rewritten to generate `<div class="network-row">` elements with two visual lines (SSID + badges on top, signal bar + meta on bottom).

The existing `selectedNetwork` variable is renamed to `selectedBssid` throughout `wifi.js`.

- [ ] **Step 1: Replace table HTML in `index.html`**

Find `.wifi-networks-panel` (~line 846). Replace the `<div class="wifi-networks-header">` and everything inside `.wifi-networks-panel` with:

```html
<div class="wifi-networks-panel">
    <div class="wifi-networks-header">
        <h5>Discovered Networks</h5>
        <div class="wifi-network-filters" id="wifiNetworkFilters">
            <button class="wifi-filter-btn active" data-filter="all">All</button>
            <button class="wifi-filter-btn" data-filter="2.4">2.4G</button>
            <button class="wifi-filter-btn" data-filter="5">5G</button>
            <button class="wifi-filter-btn" data-filter="open">Open</button>
            <button class="wifi-filter-btn" data-filter="hidden">Hidden</button>
        </div>
        <div class="wifi-sort-controls">
            <span class="wifi-sort-label">Sort:</span>
            <button class="wifi-sort-btn active" data-sort="rssi">Signal</button>
            <button class="wifi-sort-btn" data-sort="essid">SSID</button>
            <button class="wifi-sort-btn" data-sort="channel">Ch</button>
        </div>
    </div>
    <div class="wifi-networks-table-wrapper">
        <div id="wifiNetworkList" class="wifi-network-list">
            <div class="wifi-network-placeholder">
                <p>No networks detected.<br>Start a scan to begin.</p>
            </div>
        </div>
    </div>
</div>
```

- [ ] **Step 2: Replace table CSS with row CSS**

In `static/css/index.css`, find the section starting with `/* WiFi Networks Panel (LEFT) */` (~line 3582). Remove all CSS rules for:
- `.wifi-networks-table`, `.wifi-networks-table thead`, `.wifi-networks-table th`, `.wifi-networks-table td`, `.wifi-networks-table th.sortable`, `.wifi-networks-table th:hover`
- `.col-essid`, `.col-bssid`, `.col-channel`, `.col-rssi`, `.col-security`, `.col-clients`, `.col-agent`
- `.wifi-network-row` (old table row)
- `.security-badge`, `.security-open`, `.security-wpa`, `.security-wpa3`, `.security-wep`
- `.signal-strong`, `.signal-medium`, `.signal-weak`, `.signal-very-weak` (old signal classes)
- `.agent-badge`, `.agent-local`, `.agent-remote`

Add in their place:

```css
/* WiFi Network List */
.wifi-network-list {
    display: flex;
    flex-direction: column;
}

.wifi-network-placeholder {
    padding: 32px 16px;
    text-align: center;
    color: var(--text-dim);
    font-size: 11px;
    line-height: 1.6;
}

/* Network rows */
.network-row {
    padding: 9px 14px;
    border-bottom: 1px solid var(--bg-secondary);
    border-left: 3px solid transparent;
    cursor: pointer;
    transition: background 0.15s;
}

.network-row:hover { background: var(--bg-tertiary); }

.network-row.selected {
    background: rgba(74, 163, 255, 0.07);
    border-left-color: var(--accent-cyan) !important;
}

.network-row.threat-open   { border-left-color: var(--accent-red); }
.network-row.threat-safe   { border-left-color: var(--accent-green); }
.network-row.threat-hidden { border-left-color: var(--border-color); }

.row-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 5px;
}

.row-ssid {
    font-size: 12px;
    font-weight: 500;
    color: var(--text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 55%;
}

.row-ssid.hidden-net {
    color: var(--text-dim);
    font-style: italic;
}

.row-badges { display: flex; gap: 4px; align-items: center; flex-shrink: 0; }

.badge {
    font-size: 9px;
    padding: 2px 5px;
    border-radius: 3px;
    font-weight: 600;
    letter-spacing: 0.5px;
    border: 1px solid transparent;
}

.badge.open        { color: var(--accent-red);    background: var(--accent-red-dim);   border-color: var(--accent-red); }
.badge.wpa2        { color: var(--accent-green);  background: var(--accent-green-dim); border-color: var(--accent-green); }
.badge.wpa3        { color: var(--accent-cyan);   background: var(--accent-cyan-dim);  border-color: var(--accent-cyan); }
.badge.wep         { color: var(--accent-orange); background: var(--accent-amber-dim); border-color: var(--accent-orange); }
.badge.hidden-tag  { color: var(--text-dim);      background: transparent;             border-color: var(--border-color); font-size: 8px; }

.row-bottom {
    display: flex;
    align-items: center;
    gap: 8px;
}

.signal-bar-wrap { flex: 1; max-width: 130px; }

.signal-track {
    height: 4px;
    background: var(--bg-elevated);
    border-radius: 2px;
    overflow: hidden;
}

.signal-fill { height: 100%; border-radius: 2px; transition: width 0.3s; }
.signal-fill.strong { background: linear-gradient(90deg, var(--accent-green), #88d49b); }
.signal-fill.medium { background: linear-gradient(90deg, var(--accent-green), var(--accent-orange)); }
.signal-fill.weak   { background: linear-gradient(90deg, var(--accent-orange), var(--accent-red)); }

.row-meta {
    display: flex;
    gap: 10px;
    margin-left: auto;
    color: var(--text-dim);
    font-size: 10px;
}

.row-rssi { color: var(--text-secondary); }

/* Sort controls */
.wifi-sort-controls {
    display: flex;
    align-items: center;
    gap: 4px;
}

.wifi-sort-label {
    font-size: 9px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.wifi-sort-btn {
    padding: 2px 6px;
    font-size: 9px;
    font-family: inherit;
    background: none;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    transition: color 0.15s;
}

.wifi-sort-btn:hover { color: var(--text-primary); }
.wifi-sort-btn.active { color: var(--accent-cyan); }
```

- [ ] **Step 3: Update `cacheDOM()` — swap table refs**

In `wifi.js`'s `cacheDOM()` (~line 183), replace:
```js
networkTable: document.getElementById('wifiNetworkTable'),
networkTableBody: document.getElementById('wifiNetworkTableBody'),
```
With:
```js
networkList: document.getElementById('wifiNetworkList'),
```

- [ ] **Step 4: Rename `selectedNetwork` → `selectedBssid` throughout `wifi.js`**

Find all occurrences of `selectedNetwork` in `wifi.js` and rename to `selectedBssid`. There are ~6 occurrences (declaration, `selectNetwork()`, `closeDetail()`, `scheduleRender` block, `updateNetworkRow()`). Use a search-and-replace.

- [ ] **Step 5: Rewrite `updateNetworkTable()` → `renderNetworks()`**

Rename `updateNetworkTable()` to `renderNetworks()`. Replace the guard at the top:
```js
// old:
if (!elements.networkTableBody) return;
// new:
if (!elements.networkList) return;
```

Replace the empty-state block (the `if (filtered.length === 0)` section) with:
```js
if (filtered.length === 0) {
    let message = networks.size > 0
        ? 'No networks match current filters'
        : (isScanning ? 'Scanning for networks...' : 'Start scanning to discover networks');
    elements.networkList.innerHTML = `<div class="wifi-network-placeholder"><p>${escapeHtml(message)}</p></div>`;
    return;
}
```

Replace the render line:
```js
// old:
elements.networkTableBody.innerHTML = filtered.map(n => createNetworkRow(n)).join('');
// new:
elements.networkList.innerHTML = filtered.map(n => createNetworkRow(n)).join('');
```

Add selected-state re-application after the render line:
```js
// Re-apply selected state after re-render
if (selectedBssid) {
    const sel = elements.networkList.querySelector(`[data-bssid="${CSS.escape(selectedBssid)}"]`);
    if (sel) sel.classList.add('selected');
}
```

Update the `scheduleRender` call in the `requestAnimationFrame` block (line ~1091):
```js
// old: if (pendingRender.table) updateNetworkTable();
if (pendingRender.table) renderNetworks();
```

- [ ] **Step 6: Rewrite `createNetworkRow()` to produce div rows**

Replace the entire `createNetworkRow(network)` function body:

```js
function createNetworkRow(network) {
    const rssi = network.rssi_current;
    const security = network.security || 'Unknown';

    // Badge class
    const sec = security.toLowerCase();
    const badgeClass = sec === 'open' || sec === ''   ? 'open'
                     : sec.includes('wpa3')            ? 'wpa3'
                     : sec.includes('wpa')             ? 'wpa2'
                     : sec.includes('wep')             ? 'wep'
                     : 'wpa2';

    // Threat class (left border)
    const threatClass = badgeClass === 'open' ? 'threat-open'
                      : badgeClass === 'wpa2' || badgeClass === 'wpa3' ? 'threat-safe'
                      : 'threat-hidden';

    // Signal bar width + class
    const pct = rssi != null ? Math.max(0, Math.min(100, (rssi + 100) / 80 * 100)) : 0;
    const fillClass = rssi > -55 ? 'strong' : rssi > -70 ? 'medium' : 'weak';

    const displayName = escapeHtml(network.display_name || network.essid || '[Hidden]');
    const isHidden = network.is_hidden;
    const hiddenTag = isHidden ? '<span class="badge hidden-tag">HIDDEN</span>' : '';

    return `
        <div class="network-row ${threatClass}"
             data-bssid="${escapeHtml(network.bssid)}"
             data-band="${escapeHtml(network.band || '')}"
             data-security="${escapeHtml(security)}"
             onclick="WiFiMode.selectNetwork('${escapeHtml(network.bssid)}')">
            <div class="row-top">
                <span class="row-ssid${isHidden ? ' hidden-net' : ''}">${displayName}</span>
                <div class="row-badges">
                    <span class="badge ${badgeClass}">${escapeHtml(security)}</span>
                    ${hiddenTag}
                </div>
            </div>
            <div class="row-bottom">
                <div class="signal-bar-wrap">
                    <div class="signal-track">
                        <div class="signal-fill ${fillClass}" style="width:${pct.toFixed(1)}%"></div>
                    </div>
                </div>
                <div class="row-meta">
                    <span>ch ${network.channel || '?'}</span>
                    <span>${network.client_count || 0} ↔</span>
                    <span class="row-rssi">${rssi != null ? rssi : '?'}</span>
                </div>
            </div>
        </div>
    `;
}
```

- [ ] **Step 7: Update `initNetworkFilters()` to filter div rows**

In `initNetworkFilters()` (~line 1024), find where filter buttons update the display. The existing logic toggles row visibility. Update it to operate on `.network-row` elements and their `data-band` / `data-security` attributes:

```js
function applyFilter(filter) {
    currentFilter = filter;
    renderNetworks(); // simplest approach: just re-render with new filter
}
```

(The existing filter logic is already applied inside `updateNetworkTable()` / `renderNetworks()` via `currentFilter` — no DOM-level show/hide needed. If the existing `initNetworkFilters()` does DOM-level hiding, simplify it to just call `renderNetworks()` when `currentFilter` changes.)

- [ ] **Step 8: Update `initSortControls()` to use `.wifi-sort-btn`**

In `initSortControls()` (~line 1050), replace the existing `th[data-sort]` listener with:

```js
function initSortControls() {
    document.querySelectorAll('.wifi-sort-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const field = btn.dataset.sort;
            if (currentSort.field === field) {
                currentSort.order = currentSort.order === 'desc' ? 'asc' : 'desc';
            } else {
                currentSort.field = field;
                currentSort.order = 'desc';
            }
            document.querySelectorAll('.wifi-sort-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            scheduleRender({ table: true });
        });
    });
}
```

- [ ] **Step 9: Update `selectNetwork()` and `closeDetail()` to use div rows**

In `selectNetwork()` (~line 1241), replace the row-selection query:
```js
// old:
elements.networkTableBody?.querySelectorAll('.wifi-network-row').forEach(...)
// new:
elements.networkList?.querySelectorAll('.network-row').forEach(row => {
    row.classList.toggle('selected', row.dataset.bssid === bssid);
});
```

In `closeDetail()` (~line 1315), replace the row-deselection query similarly:
```js
elements.networkList?.querySelectorAll('.network-row').forEach(row => {
    row.classList.remove('selected');
});
```

- [ ] **Step 10: Verify**

```bash
sudo -E venv/bin/python intercept.py
```
Open `http://localhost:5050/?mode=wifi`. Check:
- Network list shows styled div rows (two lines each, signal bars, coloured left borders)
- Filter buttons (All / 2.4G / 5G / Open / Hidden) still work
- Sort buttons (Signal / SSID / Ch) work
- Clicking a row highlights it (cyan left border + tinted background)
- Clicking a different row deselects the previous one

- [ ] **Step 11: Commit**

```bash
git add templates/index.html static/css/index.css static/js/modes/wifi.js
git commit -m "feat(wifi): replace table with styled div network rows"
```

---

## Task 3: Proximity radar — animated sweep

**Files:**
- Modify: `templates/index.html` (~lines 882–900)
- Modify: `static/css/index.css` (~line 3787)
- Modify: `static/js/modes/wifi.js` (`initProximityRadar()`, `updateProximityRadar()`, `scheduleRender` block)

### Context

The existing radar uses the external `ProximityRadar` component (`static/js/components/proximity-radar.js`). We're replacing this with a hand-rolled inline SVG. The static SVG rings and the rotating sweep `<g>` are placed directly in the template; JS only manages the network dot positions.

- [ ] **Step 1: Replace radar HTML**

In `index.html`, find `<div id="wifiProximityRadar" class="wifi-radar-container"></div>` (~line 884). Replace the entire `<div class="wifi-radar-panel">` contents with:

```html
<div class="wifi-radar-panel">
    <h5>Proximity Radar</h5>
    <div id="wifiProximityRadar" class="wifi-radar-container">
        <svg width="100%" viewBox="0 0 210 210" id="wifiRadarSvg">
            <defs>
                <clipPath id="wifi-radar-clip">
                    <circle cx="105" cy="105" r="100"/>
                </clipPath>
                <filter id="wifi-glow-sm">
                    <feGaussianBlur stdDeviation="2.5" result="blur"/>
                    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
                </filter>
                <filter id="wifi-glow-md">
                    <feGaussianBlur stdDeviation="4" result="blur"/>
                    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
                </filter>
            </defs>

            <!-- Background rings (static) -->
            <circle cx="105" cy="105" r="100" fill="none" stroke="#00b4d8" stroke-width="0.5" opacity="0.12"/>
            <circle cx="105" cy="105" r="70"  fill="none" stroke="#00b4d8" stroke-width="0.5" opacity="0.18"/>
            <circle cx="105" cy="105" r="40"  fill="none" stroke="#00b4d8" stroke-width="0.5" opacity="0.25"/>
            <circle cx="105" cy="105" r="15"  fill="none" stroke="#00b4d8" stroke-width="0.5" opacity="0.35"/>

            <!-- Crosshairs -->
            <line x1="5" y1="105" x2="205" y2="105" stroke="#00b4d8" stroke-width="0.3" opacity="0.1"/>
            <line x1="105" y1="5" x2="105" y2="205" stroke="#00b4d8" stroke-width="0.3" opacity="0.1"/>

            <!-- Rotating sweep group -->
            <g class="wifi-radar-sweep" clip-path="url(#wifi-radar-clip)">
                <!-- Primary trailing arc: 60° -->
                <path d="M105,105 L105,5 A100,100 0 0,1 191.6,155 Z" fill="#00b4d8" opacity="0.08"/>
                <!-- Secondary trailing arc: 90° -->
                <path d="M105,105 L105,5 A100,100 0 0,1 205,105 Z" fill="#00b4d8" opacity="0.04"/>
                <!-- Sweep line -->
                <line x1="105" y1="105" x2="105" y2="5" stroke="#00b4d8" stroke-width="1.5" opacity="0.7"
                      filter="url(#wifi-glow-sm)"/>
            </g>

            <!-- Centre dot -->
            <circle cx="105" cy="105" r="3" fill="#00b4d8" opacity="0.8"/>

            <!-- Network dots (managed by renderRadar()) -->
            <g id="wifiRadarDots"></g>
        </svg>
    </div>
    <div class="wifi-zone-summary">
        <div class="wifi-zone near">
            <span class="wifi-zone-count" id="wifiZoneImmediate">0</span>
            <span class="wifi-zone-label">Near</span>
        </div>
        <div class="wifi-zone mid">
            <span class="wifi-zone-count" id="wifiZoneNear">0</span>
            <span class="wifi-zone-label">Mid</span>
        </div>
        <div class="wifi-zone far">
            <span class="wifi-zone-count" id="wifiZoneFar">0</span>
            <span class="wifi-zone-label">Far</span>
        </div>
    </div>
</div>
```

- [ ] **Step 2: Add sweep animation CSS**

In `static/css/index.css`, find `.wifi-radar-panel` (~line 3768). Add after its closing brace:

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

- [ ] **Step 3: Add `bssidToAngle()` helper and `renderRadar()` in `wifi.js`**

In `wifi.js`, find the `// Proximity Radar` section (~line 1519). Replace `initProximityRadar()` and `updateProximityRadar()` entirely with:

```js
// Simple hash of BSSID string → stable angle in radians
function bssidToAngle(bssid) {
    let hash = 0;
    for (let i = 0; i < bssid.length; i++) {
        hash = (hash * 31 + bssid.charCodeAt(i)) & 0xffffffff;
    }
    return (hash >>> 0) / 0xffffffff * 2 * Math.PI;
}

function renderRadar(networksList) {
    const dotsGroup = document.getElementById('wifiRadarDots');
    if (!dotsGroup) return;

    const dots = [];
    const zoneCounts = { immediate: 0, near: 0, far: 0 };

    networksList.forEach(network => {
        const rssi = network.rssi_current ?? -100;
        const strength = Math.max(0, Math.min(1, (rssi + 100) / 80));
        const dotR = 5 + (1 - strength) * 90; // stronger = closer to centre
        const angle = bssidToAngle(network.bssid);
        const cx = 105 + dotR * Math.cos(angle);
        const cy = 105 + dotR * Math.sin(angle);

        // Zone counts
        if (dotR < 35)       zoneCounts.immediate++;
        else if (dotR < 70)  zoneCounts.near++;
        else                  zoneCounts.far++;

        // Visual radius by zone
        const vr = dotR < 35 ? 6 : dotR < 70 ? 4.5 : 3;

        // Colour by security
        const sec = (network.security || '').toLowerCase();
        const colour = sec === 'open' || sec === '' ? '#e25d5d'
                     : sec.includes('wpa')         ? '#38c180'
                     : sec.includes('wep')         ? '#d6a85e'
                     : '#484f58';

        dots.push(`
            <circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${vr * 1.5}"
                    fill="${colour}" opacity="0.12"/>
            <circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${vr}"
                    fill="${colour}" opacity="0.9" filter="url(#wifi-glow-sm)"/>
        `);
    });

    dotsGroup.innerHTML = dots.join('');

    if (elements.zoneImmediate) elements.zoneImmediate.textContent = zoneCounts.immediate;
    if (elements.zoneNear)      elements.zoneNear.textContent      = zoneCounts.near;
    if (elements.zoneFar)       elements.zoneFar.textContent       = zoneCounts.far;
}
```

- [ ] **Step 4: Wire `renderRadar()` into `scheduleRender()`**

In `scheduleRender()`'s `requestAnimationFrame` callback (~line 1088), replace:
```js
if (pendingRender.radar) updateProximityRadar();
```
With:
```js
if (pendingRender.radar) renderRadar(Array.from(networks.values()));
```

Also update `init()` to remove the `initProximityRadar()` call (it's now a no-op since the SVG is static in the template).

- [ ] **Step 5: Update `cacheDOM()` — remove old radar ref**

Remove `channelBandTabs: document.getElementById('wifiChannelBandTabs')` (will be removed in Task 4 anyway). Remove `channelChart: document.getElementById('wifiChannelChart')`. Keep `proximityRadar` if referenced elsewhere, otherwise remove.

- [ ] **Step 6: Verify**

```bash
sudo -E venv/bin/python intercept.py
```
Open `http://localhost:5050/?mode=wifi`. Check:
- Radar panel shows a slowly rotating sweep line with a trailing cyan arc
- Starting a scan populates coloured dots at stable positions (same BSSID always at same angle)
- Zone counts (Near / Mid / Far) update
- Open network dots are red; WPA2 dots are green

- [ ] **Step 7: Commit**

```bash
git add templates/index.html static/css/index.css static/js/modes/wifi.js
git commit -m "feat(wifi): animated SVG proximity radar with sweep rotation"
```

---

## Task 4: Channel heatmap + security ring

**Files:**
- Modify: `templates/index.html` (~lines 902–938, the `.wifi-analysis-panel`)
- Modify: `static/css/index.css` (~lines 3824–3916, WiFi analysis panel CSS)
- Modify: `static/js/modes/wifi.js` (`cacheDOM()`, `initChannelChart()`, `updateChannelChart()`, `scheduleRender`)

### Context

The existing right panel has two sub-sections (`.wifi-channel-section` with a bar chart, `.wifi-security-section` with coloured dots). Both are replaced. The new panel has a shared header (`#wifiRightPanelTitle` + `#wifiDetailBackBtn`) used in Task 5, a `#wifiHeatmapView` with the heatmap grid and security ring, and a hidden `#wifiDetailView` placeholder (wired up in Task 5).

- [ ] **Step 1: Replace right panel HTML**

In `index.html`, find `<div class="wifi-analysis-panel">` (~line 902). Replace the entire block (up to the closing `</div>` of `.wifi-analysis-panel`) with:

```html
<div class="wifi-analysis-panel">
    <div class="wifi-analysis-panel-header">
        <span class="panel-title" id="wifiRightPanelTitle">Channel Heatmap</span>
        <button class="wifi-detail-back-btn" id="wifiDetailBackBtn"
                style="display:none" onclick="WiFiMode.closeDetail()">← Back</button>
    </div>

    <!-- Default: heatmap + security ring -->
    <div id="wifiHeatmapView" style="display:flex; flex-direction:column; flex:1; overflow:hidden;">
        <div class="wifi-heatmap-wrap">
            <div class="wifi-heatmap-label">
                2.4 GHz · Last <span id="wifiHeatmapCount">0</span> scans
            </div>
            <div class="wifi-heatmap-ch-labels" id="wifiHeatmapChLabels">
                <!-- 11 channel labels (1–11), generated once by JS -->
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
                <circle cx="24" cy="24" r="9" fill="var(--bg-primary)"/>
            </svg>
            <div class="wifi-security-ring-legend" id="wifiSecurityRingLegend"></div>
        </div>
    </div>

    <!-- On network click: detail panel (wired in Task 5) -->
    <div id="wifiDetailView" style="display:none; flex:1; overflow-y:auto;">
        <!-- populated in Task 5 -->
    </div>
</div>
```

- [ ] **Step 2: Replace analysis panel CSS**

In `static/css/index.css`, find `/* WiFi Analysis Panel (RIGHT) */` (~line 3824). Remove all existing rules for `.wifi-analysis-panel`, `.wifi-channel-section`, `.wifi-security-section`, `.wifi-channel-tabs`, `.channel-band-tab`, `.wifi-channel-chart`, `.wifi-security-stats`, `.wifi-security-item`, `.wifi-security-dot`, `.wifi-security-count`.

Add in their place:

```css
/* WiFi Analysis Panel */
.wifi-analysis-panel {
    display: flex;
    flex-direction: column;
    background: var(--bg-primary);
    border: 1px solid var(--border-color);
    border-radius: 4px;
    overflow: hidden;
}

.wifi-analysis-panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    background: var(--bg-tertiary);
    border-bottom: 1px solid var(--border-color);
    flex-shrink: 0;
}

.wifi-analysis-panel-header .panel-title {
    color: var(--accent-cyan);
    font-size: 10px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
}

.wifi-detail-back-btn {
    font-family: inherit;
    font-size: 9px;
    color: var(--text-dim);
    background: none;
    border: 1px solid var(--border-color);
    border-radius: 3px;
    padding: 2px 8px;
    cursor: pointer;
    transition: color 0.15s;
}

.wifi-detail-back-btn:hover { color: var(--text-primary); }

/* Heatmap */
.wifi-heatmap-wrap {
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    flex: 1;
    overflow: hidden;
}

.wifi-heatmap-label {
    font-size: 9px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 2px;
}

.wifi-heatmap-ch-labels {
    display: grid;
    grid-template-columns: 26px repeat(11, 1fr);
    gap: 2px;
}

.wifi-heatmap-ch-label {
    text-align: center;
    font-size: 8px;
    color: var(--text-dim);
}

.wifi-heatmap-grid {
    display: grid;
    grid-template-columns: 26px repeat(11, 1fr);
    gap: 2px;
    flex: 1;
    min-height: 0;
}

.wifi-heatmap-time-label {
    font-size: 8px;
    color: var(--text-dim);
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding-right: 4px;
}

.wifi-heatmap-cell {
    border-radius: 2px;
    min-height: 10px;
}

.wifi-heatmap-empty {
    grid-column: 1 / -1;
    padding: 16px;
    text-align: center;
    color: var(--text-dim);
    font-size: 10px;
}

.wifi-heatmap-legend {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 9px;
    color: var(--text-dim);
    margin-top: 2px;
}

.wifi-heatmap-legend-grad {
    flex: 1;
    height: 6px;
    border-radius: 3px;
    background: linear-gradient(90deg, #0d1117 0%, #0d4a6e 30%, #0ea5e9 60%, #f97316 80%, #ef4444 100%);
}

/* Security ring */
.wifi-security-ring-wrap {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    background: var(--bg-secondary);
    border-top: 1px solid var(--border-color);
    flex-shrink: 0;
}

.wifi-security-ring-legend {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.wifi-security-ring-item {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 10px;
}

.wifi-security-ring-dot {
    width: 7px;
    height: 7px;
    border-radius: 1px;
    flex-shrink: 0;
}

.wifi-security-ring-name { color: var(--text-dim); flex: 1; }
.wifi-security-ring-count { color: var(--text-primary); font-weight: 600; }
```

- [ ] **Step 3: Update `cacheDOM()` — add heatmap elements, remove old chart/security refs**

In `cacheDOM()`, remove:
```js
channelChart: document.getElementById('wifiChannelChart'),
channelBandTabs: document.getElementById('wifiChannelBandTabs'),
wpa3Count: document.getElementById('wpa3Count'),
wpa2Count: document.getElementById('wpa2Count'),
wepCount: document.getElementById('wepCount'),
```
Add:
```js
heatmapGrid: document.getElementById('wifiHeatmapGrid'),
heatmapChLabels: document.getElementById('wifiHeatmapChLabels'),
heatmapCount: document.getElementById('wifiHeatmapCount'),
securityRingSvg: document.getElementById('wifiSecurityRingSvg'),
securityRingLegend: document.getElementById('wifiSecurityRingLegend'),
heatmapView: document.getElementById('wifiHeatmapView'),
detailView: document.getElementById('wifiDetailView'),
rightPanelTitle: document.getElementById('wifiRightPanelTitle'),
detailBackBtn: document.getElementById('wifiDetailBackBtn'),
```

- [ ] **Step 4: Add `channelHistory` state variable**

Near the top of the module (where `networks`, `clients` etc. are declared), add:
```js
let channelHistory = []; // max 10 entries, each { timestamp, channels: {1:N,...,11:N} }
```

- [ ] **Step 5: Add heatmap initialisation (channel labels)**

Replace `initChannelChart()` with:
```js
function initHeatmap() {
    if (!elements.heatmapChLabels) return;
    // Time-label placeholder + 11 channel labels
    elements.heatmapChLabels.innerHTML =
        '<div class="wifi-heatmap-ch-label"></div>' +
        [1,2,3,4,5,6,7,8,9,10,11].map(ch =>
            `<div class="wifi-heatmap-ch-label">${ch}</div>`
        ).join('');
}
```
Call `initHeatmap()` from `init()` instead of `initChannelChart()`.

- [ ] **Step 6: Add `renderHeatmap()` and `renderSecurityRing()` functions**

Add after `initHeatmap()`:

```js
function renderHeatmap() {
    if (!elements.heatmapGrid) return;

    if (channelHistory.length === 0) {
        elements.heatmapGrid.innerHTML =
            '<div class="wifi-heatmap-empty">Scan to populate channel history</div>';
        if (elements.heatmapCount) elements.heatmapCount.textContent = '0';
        return;
    }

    if (elements.heatmapCount) elements.heatmapCount.textContent = channelHistory.length;

    // Find max value for colour scale
    let maxVal = 1;
    channelHistory.forEach(snap => {
        Object.values(snap.channels).forEach(v => { if (v > maxVal) maxVal = v; });
    });

    const rows = channelHistory.map((snap, i) => {
        const timeLabel = i === 0 ? 'now' : '';
        const cells = [1,2,3,4,5,6,7,8,9,10,11].map(ch => {
            const v = snap.channels[ch] || 0;
            return `<div class="wifi-heatmap-cell" style="background:${congestionColor(v, maxVal)}"></div>`;
        });
        return `<div class="wifi-heatmap-time-label">${timeLabel}</div>${cells.join('')}`;
    });

    elements.heatmapGrid.innerHTML = rows.join('');
}

function congestionColor(value, maxValue) {
    if (value === 0 || maxValue === 0) return '#0d1117';
    const ratio = value / maxValue;
    if (ratio < 0.05)  return '#0d1117';
    if (ratio < 0.25)  return `rgba(13,74,110,${(ratio * 4).toFixed(2)})`;
    if (ratio < 0.5)   return `rgba(14,165,233,${ratio.toFixed(2)})`;
    if (ratio < 0.75)  return `rgba(249,115,22,${ratio.toFixed(2)})`;
    return                     `rgba(239,68,68,${ratio.toFixed(2)})`;
}

function renderSecurityRing(networksList) {
    const svg = elements.securityRingSvg;
    const legend = elements.securityRingLegend;
    if (!svg || !legend) return;

    const C = 2 * Math.PI * 15; // circumference ≈ 94.25
    const sec = networksList.reduce((acc, n) => {
        const s = (n.security || '').toLowerCase();
        if (s.includes('wpa3'))       acc.wpa3++;
        else if (s.includes('wpa'))   acc.wpa2++;
        else if (s.includes('wep'))   acc.wep++;
        else                          acc.open++;
        return acc;
    }, { wpa2: 0, open: 0, wpa3: 0, wep: 0 });

    const total = networksList.length || 1;
    const segments = [
        { label: 'WPA2', color: '#38c180', count: sec.wpa2 },
        { label: 'Open', color: '#e25d5d', count: sec.open },
        { label: 'WPA3', color: '#4aa3ff', count: sec.wpa3 },
        { label: 'WEP',  color: '#d6a85e', count: sec.wep  },
    ];

    let offset = 0;
    const arcs = segments.map(seg => {
        const arcLen = (seg.count / total) * C;
        const arc = `<circle cx="24" cy="24" r="15" fill="none"
            stroke="${seg.color}" stroke-width="7"
            stroke-dasharray="${arcLen.toFixed(2)} ${(C - arcLen).toFixed(2)}"
            stroke-dashoffset="${(-offset).toFixed(2)}"
            transform="rotate(-90 24 24)"/>`;
        offset += arcLen;
        return arc;
    });

    svg.innerHTML = arcs.join('') +
        '<circle cx="24" cy="24" r="9" fill="var(--bg-primary)"/>';

    legend.innerHTML = segments.map(seg => `
        <div class="wifi-security-ring-item">
            <div class="wifi-security-ring-dot" style="background:${seg.color}"></div>
            <span class="wifi-security-ring-name">${seg.label}</span>
            <span class="wifi-security-ring-count">${seg.count}</span>
        </div>
    `).join('');
}
```

- [ ] **Step 7: Snapshot channel history in `renderNetworks()` and call render functions**

At the top of `renderNetworks()` (just after the filter/sort), add the history snapshot:
```js
// Snapshot 2.4 GHz channel utilisation
const snapshot = { timestamp: Date.now(), channels: {} };
for (let ch = 1; ch <= 11; ch++) snapshot.channels[ch] = 0;
Array.from(networks.values())
    .filter(n => n.band && n.band.startsWith('2.4'))
    .forEach(n => {
        const ch = parseInt(n.channel);
        if (ch >= 1 && ch <= 11) snapshot.channels[ch]++;
    });
channelHistory.unshift(snapshot);
if (channelHistory.length > 10) channelHistory.pop();
```

Then after `elements.networkList.innerHTML = ...`, add:
```js
renderHeatmap();
renderSecurityRing(Array.from(networks.values()));
```

- [ ] **Step 8: Remove `updateChannelChart()` call from `scheduleRender()`**

In `scheduleRender()`'s animation frame, replace:
```js
if (pendingRender.chart) updateChannelChart();
```
With nothing (delete this line). The heatmap is now updated from within `renderNetworks()`.

- [ ] **Step 9: Verify**

```bash
sudo -E venv/bin/python intercept.py
```
Open `http://localhost:5050/?mode=wifi`. Check:
- Right panel shows "Channel Heatmap" header
- After scanning, heatmap grid populates with coloured cells (channels 6 and 11 should be hottest if neighbours visible)
- "Last N scans" count increments with each render
- Security ring shows proportional arcs for WPA2/Open/WPA3/WEP with counts

- [ ] **Step 10: Commit**

```bash
git add templates/index.html static/css/index.css static/js/modes/wifi.js
git commit -m "feat(wifi): channel heatmap and security ring chart"
```

---

## Task 5: Network detail panel (right panel takeover)

**Files:**
- Modify: `templates/index.html` (remove `#wifiDetailDrawer`, populate `#wifiDetailView`)
- Modify: `static/css/index.css` (remove drawer CSS, add detail panel CSS)
- Modify: `static/js/modes/wifi.js` (`cacheDOM()`, `selectNetwork()`, `closeDetail()`, `updateDetailPanel()`)

### Context

The existing `#wifiDetailDrawer` slides up from the bottom. It is deleted. The new `#wifiDetailView` div (already added to the HTML in Task 4) is populated here. Clicking a network row hides `#wifiHeatmapView` and shows `#wifiDetailView` in the right panel.

- [ ] **Step 1: Remove `#wifiDetailDrawer` from `index.html`**

Find `<div class="wifi-detail-drawer" id="wifiDetailDrawer">` (~line 940) and delete the entire block (from the opening div to its matching closing `</div>`, approximately 60 lines).

- [ ] **Step 2: Populate `#wifiDetailView` in `index.html`**

Find `<div id="wifiDetailView" style="display:none; flex:1; overflow-y:auto;">` (added in Task 4). Replace the `<!-- populated in Task 5 -->` placeholder with:

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
        <div class="wifi-detail-stat">
            <span class="label">Channel</span>
            <span class="value" id="wifiDetailChannel">—</span>
        </div>
        <div class="wifi-detail-stat">
            <span class="label">Band</span>
            <span class="value" id="wifiDetailBand">—</span>
        </div>
        <div class="wifi-detail-stat">
            <span class="label">Security</span>
            <span class="value" id="wifiDetailSecurity">—</span>
        </div>
        <div class="wifi-detail-stat">
            <span class="label">Cipher</span>
            <span class="value" id="wifiDetailCipher">—</span>
        </div>
        <div class="wifi-detail-stat">
            <span class="label">Clients</span>
            <span class="value" id="wifiDetailClients">—</span>
        </div>
        <div class="wifi-detail-stat">
            <span class="label">First Seen</span>
            <span class="value" id="wifiDetailFirstSeen">—</span>
        </div>
        <div class="wifi-detail-stat" style="grid-column: 1 / -1;">
            <span class="label">Vendor</span>
            <span class="value" id="wifiDetailVendor" style="font-size:11px;">—</span>
        </div>
    </div>

    <div class="wifi-detail-clients" id="wifiDetailClientList" style="display: none;">
        <h6>Connected Clients <span class="wifi-client-count-badge" id="wifiClientCountBadge"></span></h6>
        <div class="wifi-client-list"></div>
    </div>

    <div class="wifi-detail-actions">
        <button class="wfl-locate-btn" title="Locate this AP"
            onclick="(function(){ var p={bssid: document.getElementById('wifiDetailBssid')?.textContent, ssid: document.getElementById('wifiDetailEssid')?.textContent}; if(typeof WiFiLocate!=='undefined'){WiFiLocate.handoff(p);return;} if(typeof switchMode==='function'){switchMode('wifi_locate').then(function(){if(typeof WiFiLocate!=='undefined')WiFiLocate.handoff(p);});} })()">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="10" r="3"/>
                <path d="M12 21.7C17.3 17 20 13 20 10a8 8 0 1 0-16 0c0 3 2.7 7 8 11.7z"/>
            </svg>
            Locate
        </button>
        <button class="wifi-detail-close-btn" onclick="WiFiMode.closeDetail()">Close</button>
    </div>
</div>
```

- [ ] **Step 3: Remove old drawer CSS, add detail panel CSS**

In `static/css/index.css`, find and remove the CSS rules for:
`.wifi-detail-drawer`, `.wifi-detail-drawer.open`, `.wifi-detail-header`, `.wifi-detail-title`, `.wifi-detail-essid` (old), `.wifi-detail-bssid` (old), `.wifi-detail-close`, `.wifi-detail-content`, `.wifi-detail-grid` (old), `.wifi-detail-stat` (old).

Add the new detail panel styles:

```css
/* WiFi Detail Panel */
.wifi-detail-inner {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 12px;
    height: 100%;
}

.wifi-detail-head { display: flex; flex-direction: column; gap: 3px; }

.wifi-detail-essid {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
    word-break: break-word;
}

.wifi-detail-bssid {
    font-size: 10px;
    font-family: monospace;
    color: var(--text-dim);
}

.wifi-detail-signal-bar {
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 4px;
    padding: 8px 10px;
}

.wifi-detail-signal-labels {
    display: flex;
    justify-content: space-between;
    font-size: 9px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 6px;
}

.wifi-detail-signal-track {
    height: 6px;
    background: var(--bg-elevated);
    border-radius: 3px;
    overflow: hidden;
}

.wifi-detail-signal-fill {
    height: 100%;
    border-radius: 3px;
    background: linear-gradient(90deg, var(--accent-green), var(--accent-orange));
    transition: width 0.3s;
}

.wifi-detail-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
}

.wifi-detail-stat {
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 4px;
    padding: 6px 8px;
}

.wifi-detail-stat .label {
    display: block;
    font-size: 9px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 2px;
}

.wifi-detail-stat .value {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-primary);
}

.wifi-detail-actions {
    display: flex;
    gap: 6px;
    margin-top: auto;
    padding-top: 4px;
}

.wifi-detail-close-btn {
    padding: 7px 12px;
    font-family: inherit;
    font-size: 10px;
    background: var(--bg-secondary);
    color: var(--text-dim);
    border: 1px solid var(--border-color);
    border-radius: 4px;
    cursor: pointer;
    transition: color 0.15s;
}

.wifi-detail-close-btn:hover { color: var(--text-primary); }
```

- [ ] **Step 4: Update `cacheDOM()` — remove `detailDrawer`, add new detail elements**

Remove:
```js
detailDrawer: document.getElementById('wifiDetailDrawer'),
```
Add:
```js
detailSignalFill: document.getElementById('wifiDetailSignalFill'),
```
(All other detail element IDs are unchanged and already in `cacheDOM()`.)

- [ ] **Step 5: Rewrite `selectNetwork()` to show right panel detail view**

Replace `selectNetwork(bssid)` (~line 1241):

```js
function selectNetwork(bssid) {
    selectedBssid = bssid;

    // Highlight selected row
    elements.networkList?.querySelectorAll('.network-row').forEach(row => {
        row.classList.toggle('selected', row.dataset.bssid === bssid);
    });

    // Show detail in right panel
    if (elements.heatmapView) elements.heatmapView.style.display = 'none';
    if (elements.detailView)  elements.detailView.style.display  = 'flex';
    if (elements.rightPanelTitle) elements.rightPanelTitle.textContent = 'Network Detail';
    if (elements.detailBackBtn)   elements.detailBackBtn.style.display = 'inline-block';

    updateDetailPanel(bssid);
}
```

- [ ] **Step 6: Rewrite `closeDetail()` to restore heatmap**

Replace `closeDetail()` (~line 1315):

```js
function closeDetail() {
    selectedBssid = null;

    // Deselect all rows
    elements.networkList?.querySelectorAll('.network-row').forEach(row => {
        row.classList.remove('selected');
    });

    // Restore heatmap in right panel
    if (elements.detailView)  elements.detailView.style.display  = 'none';
    if (elements.heatmapView) elements.heatmapView.style.display = 'flex';
    if (elements.rightPanelTitle) elements.rightPanelTitle.textContent = 'Channel Heatmap';
    if (elements.detailBackBtn)   elements.detailBackBtn.style.display = 'none';
}
```

- [ ] **Step 7: Update `updateDetailPanel()` — remove drawer reference, add signal bar**

In `updateDetailPanel()` (~line 1262), remove the drawer guard and the `elements.detailDrawer.classList.add('open')` call:

```js
function updateDetailPanel(bssid, options = {}) {
    const { refreshClients = true } = options;
    // (remove the 'if (!elements.detailDrawer) return;' guard)
    const network = networks.get(bssid);
    if (!network) {
        closeDetail();
        return;
    }
    // ... existing field updates (detailEssid, detailBssid, detailRssi, etc.) ...

    // Add signal bar width update:
    if (elements.detailSignalFill) {
        const rssi = network.rssi_current;
        const pct = rssi != null ? Math.max(0, Math.min(100, (rssi + 100) / 80 * 100)) : 0;
        elements.detailSignalFill.style.width = pct.toFixed(1) + '%';
    }

    // Remove: elements.detailDrawer.classList.add('open');

    if (refreshClients) {
        fetchClientsForNetwork(network.bssid);
    }
}
```

Also update the `scheduleRender` block (line ~1095):
```js
// old: updateDetailPanel(selectedNetwork, ...)
// new:
if (pendingRender.detail && selectedBssid) {
    updateDetailPanel(selectedBssid, { refreshClients: false });
}
```

- [ ] **Step 8: Verify**

```bash
sudo -E venv/bin/python intercept.py
```
Open `http://localhost:5050/?mode=wifi`. Check:
- Clicking a network row: right panel transitions to "Network Detail" with ← Back button
- SSID, BSSID, signal bar, channel, band, security, cipher, clients, first seen, vendor all populate
- Signal bar width reflects RSSI (−48 dBm ≈ 65% width)
- "← Back" button and "Close" button both return to heatmap view
- If the network updates while detail is open, the detail refreshes (via `scheduleRender({ detail: true })`)
- "Locate AP" button switches to wifi_locate mode (requires wifi_locate to be loaded)

- [ ] **Step 9: Run the full test suite to check for regressions**

```bash
cd /Users/jsmith/Documents/Dev/intercept
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all existing Python tests pass (none of them test frontend HTML/JS directly).

- [ ] **Step 10: Commit**

```bash
git add templates/index.html static/css/index.css static/js/modes/wifi.js
git commit -m "feat(wifi): network detail panel replaces slide-up drawer"
```

---

## Done

All five tasks complete. The WiFi scanner now has:
- Animated sweep radar
- Richer network rows with signal bars and colour-coded threat borders
- Channel heatmap with congestion history
- Security ring chart
- Right-panel network detail view

Final smoke check: open `http://localhost:5050/?mode=wifi`, run a Quick Scan, click a network, verify all panels update correctly, click ← Back, verify heatmap returns.
