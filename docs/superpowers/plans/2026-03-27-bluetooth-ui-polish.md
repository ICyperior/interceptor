# Bluetooth UI Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the WiFi scanner's visual polish to the Bluetooth scanner: WiFi-style 2-line device rows, CSS animated radar sweep with trailing arc, and an enhanced device list header with scan indicator and sort controls.

**Architecture:** Pure frontend — HTML structure in `templates/index.html`, styles in `static/css/index.css`, JS logic in `static/js/modes/bluetooth.js`, and the shared radar component `static/js/components/proximity-radar.js`. Each task is independently committable and leaves the UI functional.

**Tech Stack:** Vanilla JS (ES6 IIFE module pattern), CSS animations, inline SVG, Flask/Jinja2 templates.

---

## Spec & reference

- **Spec:** `docs/superpowers/specs/2026-03-27-bluetooth-ui-polish-design.md`
- **Start the app for manual verification:**
  ```bash
  sudo -E venv/bin/python intercept.py
  # Open http://localhost:5050/?mode=bluetooth
  ```

## File map

| File | What changes |
|---|---|
| `static/js/components/proximity-radar.js` | `createSVG()` — add clip path + trailing arc group + CSS class; remove `animateSweep()` and its call; update `setPaused()` |
| `static/css/index.css` | Add `.bt-radar-sweep` + `@keyframes bt-radar-rotate` (~line 4410); add `.bt-scan-indicator`, `.bt-scan-dot` (~line 4836); add `.bt-controls-row`, `.bt-sort-group`, `.bt-filter-group`, `.bt-sort-btn` (~line 4944); replace `.bt-device-row` and its children with 2-line structure (~line 5130) |
| `templates/index.html` | Add `#btScanIndicator` to header (~line 1189); insert `.bt-controls-row` between signal strip and search (~line 1228); remove old `.bt-device-filters` div (lines 1231–1237) |
| `static/js/modes/bluetooth.js` | Add `sortBy` state; add `initSortControls()`; add `renderAllDevices()`; update `initDeviceFilters()` to use new `#btFilterGroup`; update `setScanning()` to drive `#btScanIndicator`; remove `locateBtn` branch from `initListInteractions()`; rewrite `createSimpleDeviceCard()` |

---

## Task 1: Proximity Radar — CSS animation + trailing glow arc

**Files:**
- Modify: `static/js/components/proximity-radar.js` (lines 58–165)
- Modify: `static/css/index.css` (~line 4410, after `.bt-radar-panel #btProximityRadar` block)

### Context

`createSVG()` currently renders a `<line class="radar-sweep">` and then calls `animateSweep()` which runs a `requestAnimationFrame` loop that mutates the line's `x2`/`y2` attributes each frame. We replace this with:
- A `<g class="bt-radar-sweep">` containing two trailing arc `<path>` elements and the sweep `<line>`, all clipped to the radar circle
- A CSS `@keyframes` rotation on `.bt-radar-sweep` (same approach as the WiFi radar's `.wifi-radar-sweep`)
- `animateSweep()` deleted entirely
- `setPaused()` updated to toggle `animationPlayState` instead of the `isPaused` flag check in `rotate()`

**Geometry** (`CONFIG.size = 280`, so `center = 140`, `outerRadius = center − CONFIG.padding = 120`):
- Sweep line: `x1=140 y1=140 x2=140 y2=20` (pointing up from centre)
- Clip circle: `cx=140 cy=140 r=120`
- 90° trailing arc (light): `M140,140 L140,20 A120,120 0 0,1 260,140 Z`
- 60° trailing arc (denser): `M140,140 L140,20 A120,120 0 0,1 244,200 Z`
  _(these match the proportional geometry used by the WiFi radar at its scale)_

- [ ] **Step 1: Replace `createSVG()` sweep section**

In `proximity-radar.js`, find and replace the lines that render the sweep and call `animateSweep` (the last 20 lines of `createSVG()`, roughly lines 97–134):

Replace:
```js
                <!-- Sweep line (animated) -->
                <line class="radar-sweep" x1="${center}" y1="${center}"
                      x2="${center}" y2="${CONFIG.padding}"
                      stroke="rgba(0, 212, 255, 0.5)" stroke-width="1" />
```
With (inside the template literal):
```js
                <!-- Clip path to keep arc inside circle -->
                <clipPath id="radarClip"><circle cx="${center}" cy="${center}" r="${center - CONFIG.padding}"/></clipPath>

                <!-- CSS-animated sweep group: trailing arcs + sweep line -->
                <g class="bt-radar-sweep" clip-path="url(#radarClip)">
                    <path d="M${center},${center} L${center},${CONFIG.padding} A${center - CONFIG.padding},${center - CONFIG.padding} 0 0,1 ${center + (center - CONFIG.padding)},${center} Z"
                          fill="#00b4d8" opacity="0.035"/>
                    <path d="M${center},${center} L${center},${CONFIG.padding} A${center - CONFIG.padding},${center - CONFIG.padding} 0 0,1 ${Math.round(center + (center - CONFIG.padding) * Math.sin(Math.PI / 3))},${Math.round(center + (center - CONFIG.padding) * (1 - Math.cos(Math.PI / 3)))} Z"
                          fill="#00b4d8" opacity="0.07"/>
                    <line x1="${center}" y1="${center}" x2="${center}" y2="${CONFIG.padding}"
                          stroke="#00b4d8" stroke-width="1.5" opacity="0.75"/>
                </g>
```

Also add `<clipPath id="radarClip">` to the `<defs>` block (before the closing `</defs>`):
```js
                    <clipPath id="radarClip">
                        <circle cx="${center}" cy="${center}" r="${center - CONFIG.padding}"/>
                    </clipPath>
```

- [ ] **Step 2: Remove `animateSweep()` call and function**

At the end of `createSVG()` (line ~133), remove:
```js
        // Add sweep animation
        animateSweep();
```

Delete the entire `animateSweep()` function (lines 139–165):
```js
    /**
     * Animate the radar sweep line
     */
    function animateSweep() {
        ...
    }
```

- [ ] **Step 3: Update `setPaused()` to use CSS animationPlayState**

Replace the current `setPaused()` (line 494):
```js
    function setPaused(paused) {
        isPaused = paused;
    }
```
With:
```js
    function setPaused(paused) {
        isPaused = paused;
        const sweep = svg?.querySelector('.bt-radar-sweep');
        if (sweep) sweep.style.animationPlayState = paused ? 'paused' : 'running';
    }
```

- [ ] **Step 4: Add CSS animation to `index.css`**

In `index.css`, find the line `.bt-radar-panel #btProximityRadar {` block (line ~4402). Add the following immediately after its closing `}` (after line ~4409):

```css
/* Bluetooth radar — CSS sweep animation (replaces rAF loop in proximity-radar.js) */
.bt-radar-sweep {
    transform-origin: 140px 140px;
    animation: bt-radar-rotate 3s linear infinite;
}

@keyframes bt-radar-rotate {
    from { transform: rotate(0deg); }
    to   { transform: rotate(360deg); }
}
```

- [ ] **Step 5: Verify manually**

Start the app (`sudo -E venv/bin/python intercept.py`), navigate to `/?mode=bluetooth`, and confirm:
- Radar sweep rotates continuously with a trailing blue glow arc
- Clicking "Pause" stops the rotation (the sweep group freezes)
- Clicking the filter buttons (New Only / Strongest / Unapproved) still works

- [ ] **Step 6: Commit**

```bash
git add static/js/components/proximity-radar.js static/css/index.css
git commit -m "feat(bluetooth): CSS animated radar sweep with trailing glow arc"
```

---

## Task 2: Device list header — scan indicator + controls row

**Files:**
- Modify: `templates/index.html` (lines 1186–1237)
- Modify: `static/css/index.css` (~lines 4836 and 4944)

### Context

The header row (`wifi-device-list-header`) currently has title + count. We add a pulsing scan indicator (IDLE/SCANNING) right-aligned in that row.

Between the signal distribution strip (`.bt-list-signal-strip`, ends ~line 1227) and the search toolbar (`.bt-device-toolbar`, line 1228) we insert a new `.bt-controls-row` with two halves:
- Left: sort buttons (Signal / Name / Seen / Dist), contained in `#btSortGroup`
- Right: filter buttons (All / New / Named / Strong / Trackers), contained in `#btFilterGroup`

The old `.bt-device-filters` div (lines 1231–1237) is deleted entirely — filters move into the controls row.

- [ ] **Step 1: Add scan indicator to the header**

In `index.html`, find the `.wifi-device-list-header` block for BT (~line 1186):
```html
                        <div class="wifi-device-list-header">
                            <h5>Bluetooth Devices</h5>
                            <span class="device-count">(<span id="btDeviceListCount">0</span>)</span>
                        </div>
```
Replace with:
```html
                        <div class="wifi-device-list-header">
                            <h5>Bluetooth Devices</h5>
                            <span class="device-count">(<span id="btDeviceListCount">0</span>)</span>
                            <div class="bt-scan-indicator" id="btScanIndicator">
                                <span class="bt-scan-dot" style="display:none;"></span>
                                <span class="bt-scan-text">IDLE</span>
                            </div>
                        </div>
```

- [ ] **Step 2: Insert controls row, remove old filter div**

In `index.html`, find the `.bt-device-toolbar` and `.bt-device-filters` block (lines 1228–1237):
```html
                        <div class="bt-device-toolbar">
                            <input type="search" id="btDeviceSearch" class="bt-device-search" placeholder="Filter by name, MAC, manufacturer...">
                        </div>
                        <div class="bt-device-filters" id="btDeviceFilters">
                            <button class="bt-filter-btn active" data-filter="all">All</button>
                            <button class="bt-filter-btn" data-filter="new">New</button>
                            <button class="bt-filter-btn" data-filter="named">Named</button>
                            <button class="bt-filter-btn" data-filter="strong">Strong</button>
                            <button class="bt-filter-btn" data-filter="trackers">Trackers</button>
                        </div>
```
Replace with:
```html
                        <div class="bt-controls-row">
                            <div class="bt-sort-group" id="btSortGroup">
                                <span class="bt-sort-label">Sort</span>
                                <button class="bt-sort-btn active" data-sort="rssi">Signal</button>
                                <button class="bt-sort-btn" data-sort="name">Name</button>
                                <button class="bt-sort-btn" data-sort="seen">Seen</button>
                                <button class="bt-sort-btn" data-sort="distance">Dist</button>
                            </div>
                            <div class="bt-filter-group" id="btFilterGroup">
                                <button class="bt-filter-btn active" data-filter="all">All</button>
                                <button class="bt-filter-btn" data-filter="new">New</button>
                                <button class="bt-filter-btn" data-filter="named">Named</button>
                                <button class="bt-filter-btn" data-filter="strong">Strong</button>
                                <button class="bt-filter-btn" data-filter="trackers">Trackers</button>
                            </div>
                        </div>
                        <div class="bt-device-toolbar">
                            <input type="search" id="btDeviceSearch" class="bt-device-search" placeholder="Filter by name, MAC, manufacturer...">
                        </div>
```

- [ ] **Step 3: Add scan indicator CSS**

In `index.css`, find `.bt-list-summary {` (~line 4837). Add the following immediately before it:

```css
/* Bluetooth scan indicator (header) */
.bt-scan-indicator {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 10px;
    color: var(--text-dim);
    letter-spacing: 0.5px;
}

.bt-scan-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--accent-cyan);
    animation: bt-scan-pulse 1.2s ease-in-out infinite;
}

@keyframes bt-scan-pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%       { opacity: 0.4; transform: scale(0.7); }
}

.bt-scan-text {
    font-size: 10px;
    color: var(--text-dim);
    letter-spacing: 0.05em;
}

.bt-scan-text.active {
    color: var(--accent-cyan);
    font-weight: 600;
}
```

- [ ] **Step 4: Add controls row CSS**

In `index.css`, find `.bt-device-filters {` (~line 4933). Replace the entire `.bt-device-filters` block (lines 4933–4944) and the `.bt-filter-btn` blocks (lines 4946–4969) with:

```css
/* Bluetooth controls row: sort + filter combined */
.bt-controls-row {
    display: flex;
    align-items: stretch;
    border-bottom: 1px solid var(--border-color);
    background: var(--bg-primary);
    flex-shrink: 0;
    position: sticky;
    top: 44px;
    z-index: 3;
}

.bt-sort-group {
    display: flex;
    align-items: center;
    gap: 2px;
    padding: 5px 10px;
    border-right: 1px solid var(--border-color);
    flex-shrink: 0;
}

.bt-filter-group {
    display: flex;
    align-items: center;
    gap: 3px;
    padding: 5px 8px;
    flex-wrap: wrap;
}

.bt-sort-label {
    font-size: 9px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-right: 4px;
}

.bt-sort-btn {
    background: none;
    border: none;
    color: var(--text-dim);
    font-size: 10px;
    font-family: var(--font-mono);
    cursor: pointer;
    padding: 2px 6px;
    border-radius: 3px;
    transition: color 0.15s;
}

.bt-sort-btn:hover { color: var(--text-primary); }
.bt-sort-btn.active { color: var(--accent-cyan); background: rgba(74,163,255,0.08); }

.bt-filter-btn {
    padding: 3px 8px;
    font-size: 10px;
    font-family: var(--font-mono);
    background: none;
    border: 1px solid var(--border-color);
    border-radius: 3px;
    color: var(--text-dim);
    cursor: pointer;
    transition: all 0.15s;
}

.bt-filter-btn:hover {
    color: var(--text-primary);
    border-color: var(--border-light);
}

.bt-filter-btn.active {
    color: var(--accent-cyan);
    border-color: rgba(74,163,255,0.4);
    background: rgba(74,163,255,0.08);
}
```

- [ ] **Step 5: Verify manually**

Reload the app, navigate to `/?mode=bluetooth`. Confirm:
- Header shows "IDLE" text right-aligned (no pulsing dot yet — JS wiring is Task 3)
- Controls row appears between signal strip and search: "Sort Signal Name Seen Dist | All New Named Strong Trackers"
- Filter and sort buttons are styled and visually clickable (they don't work yet — JS is Task 3)
- Old `.bt-device-filters` div is gone

- [ ] **Step 6: Commit**

```bash
git add templates/index.html static/css/index.css
git commit -m "feat(bluetooth): scan indicator and sort+filter controls row in device list header"
```

---

## Task 3: JS wiring — scan indicator, sort, filter handler, locate branch cleanup

**Files:**
- Modify: `static/js/modes/bluetooth.js`
  - `setScanning()` (~line 984)
  - `initDeviceFilters()` (~line 130)
  - `init()` (~line 91)
  - `initListInteractions()` (~line 161)
  - Module-level state (~line 38)

### Context

Four independent changes to `bluetooth.js`:
1. `setScanning()` drives `#btScanIndicator` (dot visible + "SCANNING" text when scanning)
2. `initDeviceFilters()` targets the new `#btFilterGroup` instead of `#btDeviceFilters`
3. New `initSortControls()` + `renderAllDevices()` functions, called from `init()`
4. The `locateBtn` branch in `initListInteractions()` is removed (no locate buttons in rows)

- [ ] **Step 1: Add `sortBy` state variable**

In `bluetooth.js`, find the module-level state block (~line 38, near `let currentDeviceFilter = 'all'`). Add:
```js
    let sortBy = 'rssi';
```
Place it directly after `let currentDeviceFilter = 'all';`.

- [ ] **Step 2: Update `setScanning()` to drive the scan indicator**

In `bluetooth.js`, find `function setScanning(scanning)` (~line 984). At the end of the function body (after the `statusDot`/`statusText` block, around line 1010), add:
```js
        // Drive the per-panel scan indicator
        const scanDot  = document.getElementById('btScanIndicator')?.querySelector('.bt-scan-dot');
        const scanText = document.getElementById('btScanIndicator')?.querySelector('.bt-scan-text');
        if (scanDot)  scanDot.style.display  = scanning ? 'inline-block' : 'none';
        if (scanText) {
            scanText.textContent = scanning ? 'SCANNING' : 'IDLE';
            scanText.classList.toggle('active', scanning);
        }
```

- [ ] **Step 3: Update `initDeviceFilters()` to use new container ID**

In `bluetooth.js`, find `function initDeviceFilters()` (~line 130). Change:
```js
        const filterContainer = document.getElementById('btDeviceFilters');
```
To:
```js
        const filterContainer = document.getElementById('btFilterGroup');
```
(Everything else in the function — the click handler, search input listener — stays identical.)

- [ ] **Step 4: Add `renderAllDevices()` function**

In `bluetooth.js`, add the following new function after `renderDevice()` (~after line 1367):
```js
    /**
     * Re-render all devices in the current sort order, then re-apply the active filter.
     */
    function renderAllDevices() {
        if (!deviceContainer) return;
        deviceContainer.innerHTML = '';

        const sorted = [...devices.values()].sort((a, b) => {
            if (sortBy === 'rssi')     return (b.rssi_current ?? -100) - (a.rssi_current ?? -100);
            if (sortBy === 'name')     return (a.name || '\uFFFF').localeCompare(b.name || '\uFFFF');
            if (sortBy === 'seen')     return (b.seen_count || 0) - (a.seen_count || 0);
            if (sortBy === 'distance') return (a.estimated_distance_m ?? 9999) - (b.estimated_distance_m ?? 9999);
            return 0;
        });

        sorted.forEach(device => renderDevice(device, false));
        applyDeviceFilter();
        if (selectedDeviceId) highlightSelectedDevice(selectedDeviceId);
    }
```

- [ ] **Step 5: Add `initSortControls()` function**

In `bluetooth.js`, add the following new function after `initDeviceFilters()` (~after line 159):
```js
    function initSortControls() {
        const sortGroup = document.getElementById('btSortGroup');
        if (!sortGroup) return;
        sortGroup.addEventListener('click', (e) => {
            const btn = e.target.closest('.bt-sort-btn');
            if (!btn) return;
            const sort = btn.dataset.sort;
            if (!sort) return;
            sortBy = sort;
            sortGroup.querySelectorAll('.bt-sort-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderAllDevices();
        });
    }
```

- [ ] **Step 6: Call `initSortControls()` from `init()`**

In `bluetooth.js`, find `function init()` (~line 91). After the line `initDeviceFilters();` (~line 120), add:
```js
        initSortControls();
```

- [ ] **Step 7: Remove `locateBtn` branch from `initListInteractions()`**

In `bluetooth.js`, find `function initListInteractions()` (~line 161). Remove these lines from the click handler:
```js
                const locateBtn = event.target.closest('.bt-locate-btn[data-locate-id]');
                if (locateBtn) {
                    event.preventDefault();
                    locateById(locateBtn.dataset.locateId);
                    return;
                }
```
The click handler body should now go directly to:
```js
                const row = event.target.closest('.bt-device-row[data-bt-device-id]');
                if (!row) return;
                selectDevice(row.dataset.btDeviceId);
```

- [ ] **Step 8: Verify manually**

Reload. Navigate to `/?mode=bluetooth`. Start a scan.
- Header shows pulsing dot + "SCANNING" text; stops when scan ends → "IDLE"
- Sort buttons work: clicking "Name" re-orders the device list alphabetically; "Signal" puts strongest first
- Filter buttons work: "New" shows only new devices; "Trackers" shows only trackers
- Clicking a device row still opens the detail panel

- [ ] **Step 9: Commit**

```bash
git add static/js/modes/bluetooth.js
git commit -m "feat(bluetooth): scan indicator, sort controls, updated filter handler"
```

---

## Task 4: Device row rewrite — WiFi-style 2-line layout

**Files:**
- Modify: `static/js/modes/bluetooth.js` — `createSimpleDeviceCard()` (~line 1369)
- Modify: `static/css/index.css` — `.bt-device-row` block (~lines 4790, 5130–5333)

### Context

`createSimpleDeviceCard()` currently produces a 3-part layout (`.bt-row-main` / `.bt-row-secondary` / `.bt-row-actions`). We replace it with a 2-line WiFi-style layout:

**Top line (`.bt-row-top`):** protocol badge + device name + tracker/IRK/risk/cluster badges (left); flag badges + status dot (right)

**Bottom line (`.bt-row-bottom`):** full-width signal bar + flex meta row (manufacturer or address · distance · RSSI value)

The locate button moves out of the row entirely (it exists in the detail panel, which is unchanged).

The `.bt-status-dot.known` colour changes from green to grey (matching WiFi's "safe" colour logic — green was misleading for "known" devices).

**CSS classes removed** (no longer emitted by JS, safe to delete):
`.bt-row-main`, `.bt-row-left`, `.bt-row-right`, `.bt-rssi-container`, `.bt-rssi-bar-bg`, `.bt-rssi-bar`, `.bt-rssi-value`, `.bt-row-secondary`, `.bt-row-actions`, `.bt-row-actions .bt-locate-btn` (and its `:hover`, `:active`, `svg` variants)

**CSS classes added:**
`.bt-row-top`, `.bt-row-top-left`, `.bt-row-top-right`, `.bt-row-name`, `.bt-unnamed`, `.bt-signal-bar-wrap`, `.bt-signal-track`, `.bt-signal-fill` (+ `.strong`, `.medium`, `.weak`), `.bt-row-bottom`, `.bt-row-meta`, `.bt-row-rssi` (+ `.strong`, `.medium`, `.weak`)

- [ ] **Step 1: Update `.bt-device-row` base CSS**

In `index.css`, find `.bt-device-row {` (~line 5130). Replace the entire block:
```css
.bt-device-row {
    display: flex;
    flex-direction: column;
    background: var(--bg-tertiary);
    border: 1px solid var(--border-color);
    border-left: 4px solid #666;
    border-radius: 6px;
    padding: 10px 12px;
    margin-bottom: 6px;
    cursor: pointer;
    transition: all 0.15s ease;
}
```
With:
```css
.bt-device-row {
    display: flex;
    flex-direction: column;
    border-left: 3px solid transparent;
    padding: 9px 12px;
    cursor: pointer;
    border-bottom: 1px solid rgba(255, 255, 255, 0.03);
    transition: background 0.12s;
}
```

- [ ] **Step 2: Update `.bt-device-row` interactive states**

Find `.bt-device-row:last-child`, `.bt-device-row:hover`, `.bt-device-row:focus-visible` (~lines 5143–5155). Replace all three:
```css
.bt-device-row:last-child {
    border-bottom: none;
}

.bt-device-row:hover { background: var(--bg-tertiary); }

.bt-device-row:focus-visible {
    outline: 1px solid var(--accent-cyan);
    outline-offset: -1px;
}
```

Also find `.bt-device-row.selected` (~line 4790). Replace:
```css
.bt-device-row.selected {
    background: rgba(0, 212, 255, 0.1);
    border-color: var(--accent-cyan);
}
```
With:
```css
.bt-device-row.selected {
    background: rgba(74, 163, 255, 0.07);
    border-left-color: var(--accent-cyan) !important;
}
```

- [ ] **Step 3: Remove old row-structure CSS, add new 2-line CSS**

In `index.css`, find and delete the following blocks (lines ~5157–5333):
- `.bt-row-main { … }`
- `.bt-row-left { … }`
- `.bt-row-right { … }`
- `.bt-rssi-container { … }`
- `.bt-rssi-bar-bg { … }`
- `.bt-rssi-bar { … }`
- `.bt-rssi-value { … }`
- `.bt-row-secondary { … }`
- `.bt-row-actions { … }`
- `.bt-row-actions .bt-locate-btn { … }` (and the `:hover`, `:active`, `svg` variants)

In their place, add:
```css
/* Bluetooth device row — 2-line WiFi-style layout */
.bt-row-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    margin-bottom: 7px;
}

.bt-row-top-left {
    display: flex;
    align-items: center;
    gap: 5px;
    min-width: 0;
    flex: 1;
    overflow: hidden;
}

.bt-row-top-right {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
}

.bt-row-name {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.bt-row-name.bt-unnamed {
    color: var(--text-dim);
    font-style: italic;
}

.bt-row-bottom {
    display: flex;
    align-items: center;
    gap: 8px;
}

.bt-signal-bar-wrap { flex: 1; }

.bt-signal-track {
    height: 4px;
    background: var(--border-color);
    border-radius: 2px;
    overflow: hidden;
}

.bt-signal-fill {
    height: 100%;
    border-radius: 2px;
    transition: width 0.4s ease;
}

.bt-signal-fill.strong { background: linear-gradient(90deg, var(--accent-green), #88d49b); }
.bt-signal-fill.medium { background: linear-gradient(90deg, var(--accent-green), var(--accent-orange)); }
.bt-signal-fill.weak   { background: linear-gradient(90deg, var(--accent-orange), var(--accent-red)); }

.bt-row-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
    font-size: 10px;
    color: var(--text-dim);
    white-space: nowrap;
}

.bt-row-rssi { font-family: var(--font-mono); font-size: 10px; }
.bt-row-rssi.strong { color: var(--accent-green); }
.bt-row-rssi.medium { color: var(--accent-amber, #eab308); }
.bt-row-rssi.weak   { color: var(--accent-red); }
```

- [ ] **Step 4: Update `.bt-status-dot.known` colour**

Find `.bt-status-dot.known` (~line 5274). The current value is `background: #22c55e`. Change to:
```css
.bt-status-dot.known {
    background: #484f58;
}
```
(Green was misleading — "known" is neutral, not safe.)

- [ ] **Step 5: Rewrite `createSimpleDeviceCard()`**

In `bluetooth.js`, replace the entire body of `createSimpleDeviceCard(device)` (~lines 1369–1511) with:

```js
    function createSimpleDeviceCard(device) {
        const protocol = device.protocol || 'ble';
        const rssi = device.rssi_current;
        const inBaseline = device.in_baseline || false;
        const isNew = !inBaseline;
        const hasName = !!device.name;
        const isTracker = device.is_tracker === true;
        const trackerType = device.tracker_type;
        const trackerConfidence = device.tracker_confidence;
        const riskScore = device.risk_score || 0;
        const agentName = device._agent || 'Local';
        const seenBefore = device.seen_before === true;

        // Signal bar
        const rssiPercent = rssi != null ? Math.max(0, Math.min(100, ((rssi + 100) / 70) * 100)) : 0;
        const fillClass = rssi == null ? 'weak'
                        : rssi >= -60 ? 'strong'
                        : rssi >= -75 ? 'medium' : 'weak';

        const displayName = device.name || formatDeviceId(device.address);
        const name = escapeHtml(displayName);
        const addr = escapeHtml(isUuidAddress(device) ? formatAddress(device) : (device.address || 'Unknown'));
        const mfr = device.manufacturer_name ? escapeHtml(device.manufacturer_name) : '';
        const seenCount = device.seen_count || 0;
        const searchIndex = [
            displayName, device.address, device.manufacturer_name,
            device.tracker_name, device.tracker_type, agentName
        ].filter(Boolean).join(' ').toLowerCase();

        // Protocol badge
        const protoBadge = protocol === 'ble'
            ? '<span class="bt-proto-badge ble">BLE</span>'
            : '<span class="bt-proto-badge classic">CLASSIC</span>';

        // Tracker badge
        let trackerBadge = '';
        if (isTracker) {
            const confColor = trackerConfidence === 'high' ? '#ef4444'
                            : trackerConfidence === 'medium' ? '#f97316' : '#eab308';
            const confBg = trackerConfidence === 'high' ? 'rgba(239,68,68,0.15)'
                         : trackerConfidence === 'medium' ? 'rgba(249,115,22,0.15)' : 'rgba(234,179,8,0.15)';
            const typeLabel = trackerType === 'airtag' ? 'AirTag'
                            : trackerType === 'tile' ? 'Tile'
                            : trackerType === 'samsung_smarttag' ? 'SmartTag'
                            : trackerType === 'findmy_accessory' ? 'FindMy'
                            : trackerType === 'chipolo' ? 'Chipolo' : 'TRACKER';
            trackerBadge = '<span class="bt-tracker-badge" style="background:' + confBg + ';color:' + confColor
                + ';font-size:9px;padding:1px 5px;border-radius:3px;font-weight:600;">' + typeLabel + '</span>';
        }

        // IRK badge
        const irkBadge = device.has_irk ? '<span class="bt-irk-badge">IRK</span>' : '';

        // Risk badge
        let riskBadge = '';
        if (riskScore >= 0.3) {
            const riskColor = riskScore >= 0.5 ? '#ef4444' : '#f97316';
            riskBadge = '<span class="bt-risk-badge" style="color:' + riskColor
                + ';font-size:8px;font-weight:600;">' + Math.round(riskScore * 100) + '% RISK</span>';
        }

        // MAC cluster badge
        const clusterBadge = device.mac_cluster_count > 1
            ? '<span class="bt-mac-cluster-badge">' + device.mac_cluster_count + ' MACs</span>'
            : '';

        // Flag badges (go to top-right, before status dot)
        const hFlags = device.heuristic_flags || [];
        let flagBadges = '';
        if (device.is_persistent || hFlags.includes('persistent'))
            flagBadges += '<span class="bt-flag-badge persistent">PERSIST</span>';
        if (device.is_beacon_like || hFlags.includes('beacon_like'))
            flagBadges += '<span class="bt-flag-badge beacon-like">BEACON</span>';
        if (device.is_strong_stable || hFlags.includes('strong_stable'))
            flagBadges += '<span class="bt-flag-badge strong-stable">STABLE</span>';

        // Status dot
        let statusDot;
        if (isTracker && trackerConfidence === 'high') {
            statusDot = '<span class="bt-status-dot tracker" style="background:#ef4444;"></span>';
        } else if (isNew) {
            statusDot = '<span class="bt-status-dot new"></span>';
        } else {
            statusDot = '<span class="bt-status-dot known"></span>';
        }

        // Bottom meta items
        const metaLabel = mfr || addr;  // already HTML-escaped above
        const distM = device.estimated_distance_m;
        const distStr = distM != null ? '~' + distM.toFixed(1) + 'm' : '';
        let metaHtml = '<span>' + metaLabel + '</span>';
        if (distStr) metaHtml += '<span>' + distStr + '</span>';
        metaHtml += '<span class="bt-row-rssi ' + fillClass + '">' + (rssi != null ? rssi : '—') + '</span>';
        if (seenBefore) metaHtml += '<span class="bt-history-badge">SEEN</span>';
        if (agentName !== 'Local')
            metaHtml += '<span class="agent-badge agent-remote" style="font-size:8px;padding:1px 4px;">'
                + escapeHtml(agentName) + '</span>';

        // Left border colour
        const borderColor = isTracker && trackerConfidence === 'high' ? '#ef4444'
                          : isTracker ? '#f97316'
                          : rssi != null && rssi >= -60 ? 'var(--accent-green)'
                          : rssi != null && rssi >= -75 ? 'var(--accent-amber, #eab308)'
                          : 'var(--accent-red)';

        return '<div class="bt-device-row' + (isTracker ? ' is-tracker' : '') + '"'
            + ' data-bt-device-id="' + escapeAttr(device.device_id) + '"'
            + ' data-is-new="' + isNew + '"'
            + ' data-has-name="' + hasName + '"'
            + ' data-rssi="' + (rssi ?? -100) + '"'
            + ' data-is-tracker="' + isTracker + '"'
            + ' data-search="' + escapeAttr(searchIndex) + '"'
            + ' role="button" tabindex="0" data-keyboard-activate="true"'
            + ' style="border-left-color:' + borderColor + ';">'
            // Top line
            + '<div class="bt-row-top">'
                + '<div class="bt-row-top-left">'
                    + protoBadge
                    + '<span class="bt-row-name' + (hasName ? '' : ' bt-unnamed') + '">' + name + '</span>'
                    + trackerBadge + irkBadge + riskBadge + clusterBadge
                + '</div>'
                + '<div class="bt-row-top-right">'
                    + flagBadges + statusDot
                + '</div>'
            + '</div>'
            // Bottom line
            + '<div class="bt-row-bottom">'
                + '<div class="bt-signal-bar-wrap">'
                    + '<div class="bt-signal-track">'
                        + '<div class="bt-signal-fill ' + fillClass + '" style="width:' + rssiPercent.toFixed(1) + '%"></div>'
                    + '</div>'
                + '</div>'
                + '<div class="bt-row-meta">' + metaHtml + '</div>'
            + '</div>'
        + '</div>';
    }
```

- [ ] **Step 6: Verify manually**

Reload and start a scan. Confirm:
- Each device row has two lines: name + badges on top, signal bar + meta on bottom
- Locate button is gone from rows; still present in the detail panel (right-click a device, check the detail panel at left)
- Strong signal rows have green bar + green RSSI; medium amber; weak red
- Tracker rows have red left border; AirTag/Tile labels show
- Unnamed devices show address in italic grey
- Selecting a row highlights it in cyan; detail panel populates
- `PERSIST`, `BEACON`, `STABLE` flag badges appear top-right when set

- [ ] **Step 7: Run backend tests to confirm no regressions**

```bash
pytest tests/test_bluetooth.py tests/test_bluetooth_api.py -v
```
Expected: all pass (frontend-only change, backend untouched).

- [ ] **Step 8: Commit**

```bash
git add static/js/modes/bluetooth.js static/css/index.css
git commit -m "feat(bluetooth): WiFi-style 2-line device rows"
```
