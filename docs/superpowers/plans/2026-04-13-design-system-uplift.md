# Design System Uplift + Sidebar Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the "Mission Control" visual language as the new baseline — deeper blacks, cyan-glow active states, panel polish, scanline texture, and nav group state persistence — so every subsequent sub-project inherits a consistent, polished foundation.

**Architecture:** Four targeted CSS file edits (variables, layout, components, index) plus one JS enhancement for localStorage-backed nav state. No structural changes; all changes are additive or small replacements within existing rules.

**Tech Stack:** CSS custom properties, Jinja2 templates, vanilla JS, localStorage API

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `static/css/core/variables.css` | Modify | Deepen bg tokens; add `--accent-cyan-glow` and `--scanline` |
| `static/css/core/layout.css` | Modify | Nav active state → left-border glow; hover → cyan-glow bg; group header accent line |
| `static/css/core/components.css` | Modify | Panel indicator pulse animation; card vignette; scanline texture on visuals containers |
| `static/css/index.css` | Modify | Scanline texture on existing `.visuals-container` if not covered by components.css |
| `templates/partials/nav.html` | Modify | Add `data-group` attributes to dropdown containers (already present); verify `toggleNavDropdown` call exists |
| `templates/index.html` | Modify | Add `initNavGroupState()` call in page init; add the JS function |

---

## Task 1: Deepen Background Tokens

**Files:**
- Modify: `static/css/core/variables.css`

Note on current values (from file, not the UI guide which is out of date):
- `--bg-primary` is currently `#0b1118`
- `--bg-secondary` is currently `#101823`
- `--bg-tertiary` is currently `#151f2b`
- `--bg-card` is currently `#121a25`

- [ ] **Step 1: Update background tokens in dark theme**

In `static/css/core/variables.css`, replace the four background values in the `:root` block:

```css
/* Backgrounds - layered depth system */
--bg-primary: #07090e;
--bg-secondary: #0b1018;
--bg-tertiary: #101520;
--bg-card: #0d1219;
--bg-elevated: #161d28;
```

- [ ] **Step 2: Add `--accent-cyan-glow` and `--scanline` tokens**

In `static/css/core/variables.css`, after the `--accent-amber-dim` line, add:

```css
--accent-cyan-glow: rgba(74, 163, 255, 0.12);
```

After the `--noise-image` line, add a new comment block and token:

```css
/* Scanline overlay texture */
--scanline: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 2px,
    rgba(0, 0, 0, 0.04) 2px,
    rgba(0, 0, 0, 0.04) 4px
);
```

- [ ] **Step 3: Update light theme overrides to match**

In `static/css/core/variables.css`, in the `[data-theme="light"]` block, the bg values don't need changing (they're already light colours). But add the missing `--accent-cyan-glow` override so it doesn't bleed through in light mode:

```css
--accent-cyan-glow: rgba(31, 95, 168, 0.08);
--scanline: none;
```

- [ ] **Step 4: Verify no visual regressions**

Start the dev server: `sudo -E venv/bin/python intercept.py`

Open `http://localhost:5000` and check:
- Background is noticeably deeper/richer without being pure black
- Text remains readable
- No elements that used bg colours are now invisible (white text on near-white, etc.)

- [ ] **Step 5: Commit**

```bash
git add static/css/core/variables.css
git commit -m "style: deepen background tokens and add scanline/glow variables"
```

---

## Task 2: Nav Active State → Left-Border Glow

**Files:**
- Modify: `static/css/core/layout.css`

The current active state uses `box-shadow: inset 0 -2px 0 var(--accent-cyan)` (a bottom underline). We're replacing this with a left-border glow — more ops-center, less browser-tab.

- [ ] **Step 1: Replace `.mode-nav-btn.active` style**

In `static/css/core/layout.css`, find and replace the `.mode-nav-btn.active` block (currently at line ~749):

```css
.mode-nav-btn.active {
    background: var(--accent-cyan-glow);
    color: var(--text-primary);
    border-color: transparent;
    border-left: 2px solid var(--accent-cyan);
    box-shadow: -2px 0 8px rgba(74, 163, 255, 0.2);
    padding-left: 12px; /* compensate for 2px border */
}
```

- [ ] **Step 2: Replace `.mode-nav-dropdown.has-active .mode-nav-dropdown-btn` style**

Find and replace the `.mode-nav-dropdown.has-active .mode-nav-dropdown-btn` block (currently at line ~856):

```css
.mode-nav-dropdown.has-active .mode-nav-dropdown-btn {
    background: var(--accent-cyan-glow);
    color: var(--text-primary);
    border-color: transparent;
    border-left: 2px solid var(--accent-cyan);
    box-shadow: -2px 0 8px rgba(74, 163, 255, 0.2);
}
```

- [ ] **Step 3: Replace `.mode-nav-dropdown-menu .mode-nav-btn.active` style**

Find and replace the block at line ~903:

```css
.mode-nav-dropdown-menu .mode-nav-btn.active {
    background: var(--accent-cyan-glow);
    color: var(--text-primary);
    border-left: 2px solid var(--accent-cyan);
    box-shadow: -2px 0 6px rgba(74, 163, 255, 0.15);
    padding-left: 10px;
}
```

- [ ] **Step 4: Enhance hover state with cyan-glow background**

Find `.mode-nav-btn:hover` (line ~743) and replace:

```css
.mode-nav-btn:hover {
    background: var(--accent-cyan-glow);
    color: var(--text-primary);
    border-color: var(--border-color);
}
```

Find `.mode-nav-dropdown-btn:hover` (line ~840) and replace:

```css
.mode-nav-dropdown-btn:hover {
    background: var(--accent-cyan-glow);
    color: var(--text-primary);
    border-color: var(--border-color);
}
```

- [ ] **Step 5: Update light theme active state overrides**

Find the `[data-theme="light"] .mode-nav-btn.active` block (line ~1105) and replace:

```css
[data-theme="light"] .mode-nav-btn.active {
    background: rgba(31, 95, 168, 0.08);
    border-left: 2px solid var(--accent-cyan);
    box-shadow: -2px 0 6px rgba(31, 95, 168, 0.15);
    padding-left: 12px;
}

[data-theme="light"] .mode-nav-dropdown-btn:hover,
[data-theme="light"] .mode-nav-dropdown.open .mode-nav-dropdown-btn,
[data-theme="light"] .mode-nav-dropdown.has-active .mode-nav-dropdown-btn {
    background: rgba(31, 95, 168, 0.06);
    border-left: 2px solid var(--accent-cyan);
    box-shadow: -2px 0 6px rgba(31, 95, 168, 0.12);
}

[data-theme="light"] .mode-nav-dropdown-menu .mode-nav-btn.active {
    background: rgba(31, 95, 168, 0.08);
    border-left: 2px solid var(--accent-cyan);
    padding-left: 10px;
}
```

- [ ] **Step 6: Verify in browser**

Open `http://localhost:5000`, switch between a few modes. Verify:
- Active mode button has visible left-border cyan glow
- Hover on inactive buttons shows subtle cyan-glow background
- Light theme still works (toggle via moon/sun icon)

- [ ] **Step 7: Commit**

```bash
git add static/css/core/layout.css
git commit -m "style: nav active state → left-border cyan glow, hover → glow bg"
```

---

## Task 3: Panel Indicator Pulse Animation

**Files:**
- Modify: `static/css/core/components.css`

The `.panel-indicator.active` currently has a static green dot with glow. We're adding a CSS pulse animation so active panels visually breathe.

- [ ] **Step 1: Write a test for the panel indicator class**

```bash
# Check that panel-indicator.active elements exist in the rendered HTML
# (manual spot-check — open any mode and inspect the DOM)
# Confirm .panel-indicator.active is present on the panel header dot
```

- [ ] **Step 2: Add pulse keyframes and apply to active indicator**

In `static/css/core/components.css`, find `.panel-indicator.active` (line ~236) and replace the block:

```css
@keyframes panel-pulse {
    0%, 100% {
        box-shadow: 0 0 4px var(--status-online), 0 0 8px rgba(56, 193, 128, 0.4);
        opacity: 1;
    }
    50% {
        box-shadow: 0 0 8px var(--status-online), 0 0 16px rgba(56, 193, 128, 0.6);
        opacity: 0.85;
    }
}

.panel-indicator.active {
    background: var(--status-online);
    box-shadow: 0 0 8px var(--status-online);
    animation: panel-pulse 2s ease-in-out infinite;
}
```

- [ ] **Step 3: Respect reduced-motion preference**

Add below the above block:

```css
@media (prefers-reduced-motion: reduce) {
    .panel-indicator.active {
        animation: none;
    }
}
```

- [ ] **Step 4: Verify in browser**

Open `http://localhost:5000`, start any mode (e.g. Pager). Verify the panel indicator dot pulses gently when active, is static when inactive.

- [ ] **Step 5: Commit**

```bash
git add static/css/core/components.css
git commit -m "style: add pulse animation to active panel indicators"
```

---

## Task 4: Card Vignette + Scanline Texture

**Files:**
- Modify: `static/css/core/components.css`
- Modify: `static/css/index.css` (for `.visuals-container` if not in components.css)

- [ ] **Step 1: Add inner vignette to `.panel` cards**

In `static/css/core/components.css`, find the `.panel` rule. After the existing properties, add `box-shadow`:

```css
.panel {
    /* ... existing properties ... */
    box-shadow: var(--shadow-sm), inset 0 0 40px rgba(0, 0, 0, 0.25);
}
```

If a `.panel` rule doesn't exist at the top level in components.css, search for it:
```bash
grep -n "^\.panel {" static/css/core/components.css static/css/index.css
```
Add the `box-shadow` to whichever file defines it.

- [ ] **Step 2: Add scanline texture to visuals containers**

Visuals containers are mode-specific and likely defined in `static/css/index.css`. Search for the class:

```bash
grep -n "visuals-container" static/css/index.css static/css/core/components.css
```

In whichever file defines `.visuals-container`, add an `::after` pseudo-element:

```css
.visuals-container {
    position: relative; /* ensure this is set */
}

.visuals-container::after {
    content: '';
    position: absolute;
    inset: 0;
    background: var(--scanline);
    pointer-events: none;
    z-index: 1;
    border-radius: inherit;
}
```

- [ ] **Step 3: Verify scanline doesn't block interactions**

Open a mode with a visuals container (e.g. Bluetooth radar, TSCM). Verify:
- Subtle horizontal scanline texture is visible
- Clicking/interacting with the visual still works (pointer-events: none is set)
- Light theme: scanline is `none` (set in Task 1 Step 3)

- [ ] **Step 4: Commit**

```bash
git add static/css/core/components.css static/css/index.css
git commit -m "style: add card vignette and scanline texture to visuals containers"
```

---

## Task 5: Nav Group State Persistence

**Files:**
- Modify: `templates/index.html`

The nav HTML already has `data-group` attributes on `.mode-nav-dropdown` containers and calls `toggleNavDropdown()` on button clicks. We need to persist which groups are open/closed so the state survives page reloads.

- [ ] **Step 1: Find where `toggleNavDropdown` is defined**

```bash
grep -n "toggleNavDropdown" templates/index.html
```

Note the line number where the function is defined (it will be inside a `<script>` block).

- [ ] **Step 2: Write a unit test for state persistence logic**

Create `tests/test_nav_state.py`:

```python
"""Tests for nav group localStorage persistence (JS logic verified via structure check)."""
import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app({'TESTING': True})
    with app.test_client() as client:
        yield client


def test_index_page_includes_nav_state_init(client):
    """nav group init function must be present in the index page."""
    resp = client.get('/')
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'initNavGroupState' in html
    assert 'localStorage' in html


def test_nav_groups_have_data_group_attributes(client):
    """Each nav group must have a data-group attribute for state keying."""
    resp = client.get('/')
    html = resp.data.decode()
    for group in ['signals', 'tracking', 'space', 'wireless', 'intel']:
        assert f'data-group="{group}"' in html, f"Missing data-group={group}"
```

- [ ] **Step 3: Run the test to confirm it fails (function not yet added)**

```bash
pytest tests/test_nav_state.py -v
```

Expected: FAIL — `initNavGroupState` not in HTML yet.

- [ ] **Step 4: Add `initNavGroupState` function to index.html**

Locate the `toggleNavDropdown` function in `templates/index.html`. Immediately after it, add:

```javascript
function initNavGroupState() {
    const NAV_STATE_KEY = 'intercept_nav_groups';
    let savedState = {};
    try {
        savedState = JSON.parse(localStorage.getItem(NAV_STATE_KEY) || '{}');
    } catch (e) {
        savedState = {};
    }

    document.querySelectorAll('.mode-nav-dropdown[data-group]').forEach(dropdown => {
        const group = dropdown.dataset.group;
        // If saved state says closed AND this group has no active item, close it
        if (savedState[group] === false) {
            const hasActive = dropdown.classList.contains('has-active');
            if (!hasActive) {
                dropdown.classList.remove('open');
                const btn = dropdown.querySelector('.mode-nav-dropdown-btn');
                if (btn) btn.setAttribute('aria-expanded', 'false');
            }
        } else if (savedState[group] === true) {
            dropdown.classList.add('open');
            const btn = dropdown.querySelector('.mode-nav-dropdown-btn');
            if (btn) btn.setAttribute('aria-expanded', 'true');
        }
    });
}

function saveNavGroupState() {
    const NAV_STATE_KEY = 'intercept_nav_groups';
    const state = {};
    document.querySelectorAll('.mode-nav-dropdown[data-group]').forEach(dropdown => {
        state[dropdown.dataset.group] = dropdown.classList.contains('open');
    });
    try {
        localStorage.setItem(NAV_STATE_KEY, JSON.stringify(state));
    } catch (e) { /* storage full or unavailable */ }
}
```

- [ ] **Step 5: Update `toggleNavDropdown` to call `saveNavGroupState`**

Find the existing `toggleNavDropdown` function. At the end of its body (before the closing `}`), add:

```javascript
saveNavGroupState();
```

- [ ] **Step 6: Call `initNavGroupState` on page load**

Find where other init functions are called on page load (look for `DOMContentLoaded` or a function like `initApp()`). Add:

```javascript
initNavGroupState();
```

- [ ] **Step 7: Run tests to confirm they pass**

```bash
pytest tests/test_nav_state.py -v
```

Expected: PASS — both tests green.

- [ ] **Step 8: Verify in browser**

Open `http://localhost:5000`. Open the Signals group, close the Tracking group. Reload the page. Verify state is restored. Verify that a group containing the active mode never closes even if saved as closed.

- [ ] **Step 9: Commit**

```bash
git add templates/index.html tests/test_nav_state.py
git commit -m "feat: persist nav group open/closed state to localStorage"
```

---

## Task 6: Final Visual Review

- [ ] **Step 1: Run the full test suite**

```bash
pytest
```

Expected: all tests pass (0 failures).

- [ ] **Step 2: Full visual walkthrough**

Open `http://localhost:5000` and check each of the following:

1. **Backgrounds** — page feels deeper/richer. Cards have subtle inner vignette.
2. **Active nav item** — left-border cyan glow. Looks crisp, not garish.
3. **Hover states** — subtle cyan glow background on hover.
4. **Active panels** — indicator dot pulses gently.
5. **Visuals containers** — faint scanline texture visible on radar, waterfall, etc.
6. **Nav group persistence** — collapse groups, reload, state is preserved.
7. **Light theme** — toggle via header icon. No scanlines, no dark backgrounds bleeding through.
8. **Reduced motion** — in DevTools > Rendering, enable "Emulate CSS prefers-reduced-motion: reduce". Pulse animation stops.

- [ ] **Step 3: Final commit if any touch-up needed**

```bash
git add -p  # stage only intentional changes
git commit -m "style: design system uplift visual touch-ups"
```

---

## Self-Review Checklist

- **Spec coverage:** Token deepening ✓, `--accent-cyan-glow` ✓, `--scanline` ✓, active left-border glow ✓, hover glow bg ✓, panel indicator pulse ✓, card vignette ✓, scanline on visuals ✓, nav group persistence ✓, localStorage state ✓
- **No placeholders:** All steps have exact code or exact commands
- **Type consistency:** `initNavGroupState` / `saveNavGroupState` / `toggleNavDropdown` names consistent throughout
- **Light theme:** Every dark-theme change has a corresponding light-theme override or is guarded
- **Reduced motion:** Pulse animation explicitly disabled under `prefers-reduced-motion`
