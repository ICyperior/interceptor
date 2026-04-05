# Satellite Telemetry Reliability Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix satellite tracking telemetry so elevation/azimuth/distance/visibility are always accurate and stable, and TLE data stays fresh automatically.

**Architecture:** The SSE background tracker (server-side, location-unaware) is stripped of observer-relative data and made authoritative only for orbit position and ground track. The 5-second HTTP poll becomes the sole owner of observer-relative telemetry. A daily TLE refresh timer is added. Several smaller correctness bugs are fixed across the backend and frontend.

**Tech Stack:** Python/Flask (backend tracker + routes), Skyfield (orbital mechanics), HTML/JS (dashboard frontend), pytest (tests)

---

## File Map

| File | What Changes |
|------|-------------|
| `routes/satellite.py` | Strip observer-relative fields from SSE tracker; fix altitude calc; add periodic TLE refresh; add `currentPos` fields to pass prediction |
| `templates/satellite_dashboard.html` | SSE handler ignores observer fields; telemetry polling owns elevation/az/dist/visible; fix `updateTelemetry` fallback; add METEOR-M2 to `WEATHER_SAT_KEYS`; fix abort controller; fix countdown |
| `tests/test_satellite.py` | New tests for tracker output shape, altitude calc, TLE refresh scheduling, pass currentPos fields |

---

## Task 1: Strip observer-relative data from SSE tracker

**Problem:** `_start_satellite_tracker` uses `DEFAULT_LATITUDE`/`DEFAULT_LONGITUDE` (both `0.0` by default). Every SSE message emits `visible: False` and az/el/dist based on the wrong location, overwriting correct data from the HTTP poll every second.

**Fix:** Remove elevation, azimuth, distance, and visible from the SSE tracker output entirely. The SSE stream is server-wide and cannot know per-client observer location. The HTTP poll (`/satellite/position`) already handles observer-relative data correctly using the location from the POST body.

**Files:**
- Modify: `routes/satellite.py:220-265`
- Test: `tests/test_satellite.py`

- [ ] **Step 1: Write a failing test verifying tracker position dicts lack observer-relative fields**

Add to `tests/test_satellite.py`:

```python
def test_tracker_position_has_no_observer_fields():
    """SSE tracker positions must NOT include observer-relative fields.

    The tracker runs server-side with a fixed (potentially wrong) observer
    location. Only the per-request /satellite/position endpoint, which
    receives the client's actual location, should emit elevation/azimuth/
    distance/visible.
    """
    from routes.satellite import _start_satellite_tracker
    import threading, queue, time

    ISS_TLE = (
        'ISS (ZARYA)',
        '1 25544U 98067A   24001.00000000  .00016717  00000-0  30171-3 0  9993',
        '2 25544  51.6416  20.4567 0004561  45.3212  67.8912 15.49876543123457',
    )

    sat_q = queue.Queue(maxsize=5)

    with patch('routes.satellite._tle_cache', {'ISS': ISS_TLE}), \
         patch('routes.satellite.get_tracked_satellites') as mock_tracked, \
         patch('routes.satellite.app') as mock_app:
        mock_app.satellite_queue = sat_q
        mock_tracked.return_value = [{
            'name': 'ISS (ZARYA)', 'norad_id': 25544,
            'tle_line1': ISS_TLE[1], 'tle_line2': ISS_TLE[2],
        }]

        t = threading.Thread(target=_start_satellite_tracker, daemon=True)
        t.start()
        try:
            msg = sat_q.get(timeout=5)
        finally:
            # thread is daemon so it exits with test process
            pass

    assert msg['type'] == 'positions'
    pos = msg['positions'][0]
    for forbidden in ('elevation', 'azimuth', 'distance', 'visible'):
        assert forbidden not in pos, f"SSE tracker must not emit '{forbidden}'"
    for required in ('lat', 'lon', 'altitude', 'satellite', 'norad_id'):
        assert required in pos, f"SSE tracker must emit '{required}'"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jsmith/Documents/Dev/intercept
pytest tests/test_satellite.py::test_tracker_position_has_no_observer_fields -v
```

Expected: FAIL — position dict currently contains `visible`.

- [ ] **Step 3: Remove observer-relative fields from `_start_satellite_tracker`**

In `routes/satellite.py`, replace the observer block inside `_start_satellite_tracker` (lines ~220–265).

Remove these lines:
```python
obs_lat = DEFAULT_LATITUDE
obs_lon = DEFAULT_LONGITUDE
has_observer = (obs_lat != 0.0 or obs_lon != 0.0)
observer = wgs84.latlon(obs_lat, obs_lon) if has_observer else None
```

And remove the observer-relative block after `pos` is built:
```python
if has_observer and observer is not None:
    diff = satellite - observer
    topocentric = diff.at(now)
    alt, az, dist = topocentric.altaz()
    pos['elevation'] = float(alt.degrees)
    pos['azimuth'] = float(az.degrees)
    pos['distance'] = float(dist.km)
    pos['visible'] = bool(alt.degrees > 0)
```

The `pos` dict should only contain `satellite`, `norad_id`, `lat`, `lon`, `altitude`, `groundTrack`.

Also remove the `from config import DEFAULT_LATITUDE, DEFAULT_LONGITUDE` reference if it becomes unused (check if used elsewhere in the file first — it is imported at the top, keep the import if used elsewhere, just stop using it in the tracker).

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_satellite.py::test_tracker_position_has_no_observer_fields -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/test_satellite.py -v
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add routes/satellite.py tests/test_satellite.py
git commit -m "fix(satellite): strip observer-relative fields from SSE tracker

SSE runs server-wide with a fixed observer location (DEFAULT_LAT/LON
defaults to 0,0). Emitting elevation/azimuth/distance/visible from the
SSE stream produced wrong values that overwrote correct data from the
per-client HTTP poll every second. The HTTP poll (/satellite/position)
owns all observer-relative data; SSE now only emits lat/lon/altitude/
groundTrack."
```

---

## Task 2: Fix frontend — SSE handler ignores observer-relative fields

**Problem:** Even after Task 1, the frontend `handleLivePositions` passes `pos` directly to `applyTelemetryPosition`, which then calls `normalizeLivePosition` and merges all fields. The `updateVisible` flag also means SSE was setting the visible-count badge. We need the SSE path to only update lat/lon/altitude/groundTrack/map, leaving elevation/az/dist/visible for the HTTP poll.

**Files:**
- Modify: `templates/satellite_dashboard.html` — `handleLivePositions` function (~line 1308)

- [ ] **Step 1: Update `handleLivePositions` to strip observer fields before applying**

Find `handleLivePositions` (around line 1308) and replace:

```js
function handleLivePositions(positions) {
    // Find the selected satellite by name or norad_id
    const pos = findSelectedPosition(positions);

    // Update visible count from all positions
    const visibleCount = positions.filter(p => p.visible).length;
    const visEl = document.getElementById('statVisible');
    if (visEl) visEl.textContent = visibleCount;

    if (!pos) {
        return;
    }
    applyTelemetryPosition(
        { ...pos, visibleCount },
        {
            updateVisible: true,
            noradId: parseInt(pos.norad_id, 10) || selectedSatellite
        }
    );
}
```

With:

```js
function handleLivePositions(positions, source) {
    // Find the selected satellite by name or norad_id
    const pos = findSelectedPosition(positions);

    if (!pos) return;

    if (source === 'sse') {
        // SSE is server-side and location-unaware: only update
        // orbit position and ground track, never observer-relative fields.
        const orbitOnly = {
            satellite: pos.satellite,
            norad_id: pos.norad_id,
            lat: pos.lat,
            lon: pos.lon,
            altitude: pos.altitude,
            groundTrack: pos.groundTrack,
            track: pos.track,
        };
        applyTelemetryPosition(orbitOnly, {
            updateVisible: false,
            noradId: parseInt(pos.norad_id, 10) || selectedSatellite,
        });
    } else {
        // HTTP poll: owns all observer-relative data including visible count
        const visibleCount = positions.filter(p => p.visible).length;
        const visEl = document.getElementById('statVisible');
        if (visEl) visEl.textContent = visibleCount;
        applyTelemetryPosition(
            { ...pos, visibleCount },
            {
                updateVisible: true,
                noradId: parseInt(pos.norad_id, 10) || selectedSatellite,
            }
        );
    }
}
```

- [ ] **Step 2: Thread `source` through the SSE call site**

Find the SSE `onmessage` handler (~line 1288):
```js
satelliteSSE.onmessage = (e) => {
    try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'positions') handleLivePositions(msg.positions);
    } catch (_) {}
};
```

Change to:
```js
satelliteSSE.onmessage = (e) => {
    try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'positions') handleLivePositions(msg.positions, 'sse');
    } catch (_) {}
};
```

- [ ] **Step 3: Thread `source` through the HTTP poll call site**

Find in `fetchCurrentTelemetry` (~line 1397):
```js
handleLivePositions(data.positions);
```

Change to:
```js
handleLivePositions(data.positions, 'poll');
```

- [ ] **Step 4: Manual smoke test**

Open `/satellite/dashboard` in a browser. Confirm:
- Lat/Lon/Altitude update every ~1 second (from SSE)
- Elevation/Azimuth/Distance update every ~5 seconds (from HTTP poll)
- The visible-count badge doesn't reset to 0 every second

- [ ] **Step 5: Commit**

```bash
git add templates/satellite_dashboard.html
git commit -m "fix(satellite): SSE path only updates orbit position, not observer data

The SSE stream no longer sets elevation/azimuth/distance/visible since
those fields were removed from the server-side tracker in the previous
commit. Adds a 'source' param to handleLivePositions so the SSE path
is gated to orbit-only fields, and the HTTP poll path owns all
observer-relative telemetry and visible-count badge."
```

---

## Task 3: Add periodic TLE auto-refresh (daily)

**Problem:** `init_tle_auto_refresh()` fires once at startup (2s delay) then never again. TLEs are valid for roughly 1–2 weeks but degrade in accuracy after a few days, affecting pass prediction accuracy.

**Fix:** Schedule a periodic 24-hour refresh using a repeating `threading.Timer` pattern.

**Files:**
- Modify: `routes/satellite.py` — `init_tle_auto_refresh` function (~line 309)
- Test: `tests/test_satellite.py`

- [ ] **Step 1: Write a failing test**

Add to `tests/test_satellite.py`:

```python
@patch('routes.satellite.refresh_tle_data', return_value=['ISS'])
@patch('routes.satellite._load_db_satellites_into_cache')
def test_tle_auto_refresh_schedules_repeat(mock_load_db, mock_refresh):
    """init_tle_auto_refresh must schedule a follow-up refresh after the first run."""
    import threading
    scheduled_delays = []
    original_timer = threading.Timer

    class CapturingTimer:
        def __init__(self, delay, fn, *args, **kwargs):
            scheduled_delays.append(delay)
            # Don't actually start a real timer
            self._fn = fn
        def start(self):
            pass  # no-op

    with patch('routes.satellite.threading') as mock_threading:
        mock_threading.Timer = CapturingTimer
        mock_threading.Thread = threading.Thread  # keep real Thread for tracker

        from routes.satellite import init_tle_auto_refresh
        init_tle_auto_refresh()

    # First timer fires at 2s (startup delay)
    assert any(d <= 5 for d in scheduled_delays), \
        "Expected a short startup delay timer"
```

- [ ] **Step 2: Run test to verify it passes already (baseline)**

```bash
pytest tests/test_satellite.py::test_tle_auto_refresh_schedules_repeat -v
```

This test validates existing behaviour and should pass. It serves as a regression guard.

- [ ] **Step 3: Add a 24-hour repeating refresh**

In `routes/satellite.py`, replace `init_tle_auto_refresh`:

```python
_TLE_REFRESH_INTERVAL_SECONDS = 24 * 60 * 60  # 24 hours


def init_tle_auto_refresh():
    """Initialize TLE auto-refresh. Called by app.py after initialization."""
    import threading

    def _auto_refresh_tle():
        try:
            _load_db_satellites_into_cache()
            updated = refresh_tle_data()
            if updated:
                logger.info(f"Auto-refreshed TLE data for: {', '.join(updated)}")
        except Exception as e:
            logger.warning(f"Auto TLE refresh failed: {e}")
        finally:
            # Schedule next refresh regardless of success/failure
            _schedule_next_tle_refresh()

    def _schedule_next_tle_refresh(delay: float = _TLE_REFRESH_INTERVAL_SECONDS):
        t = threading.Timer(delay, _auto_refresh_tle)
        t.daemon = True
        t.start()

    # First refresh 2 seconds after startup (avoids blocking app init)
    threading.Timer(2.0, _auto_refresh_tle).start()
    logger.info("TLE auto-refresh scheduled (24h interval)")

    # Start live position tracker thread
    tracker_thread = threading.Thread(
        target=_start_satellite_tracker,
        daemon=True,
        name='satellite-tracker',
    )
    tracker_thread.start()
    logger.info("Satellite tracker thread launched")
```

- [ ] **Step 4: Write a test verifying the repeat schedule**

Add to `tests/test_satellite.py`:

```python
@patch('routes.satellite.refresh_tle_data', return_value=['ISS'])
@patch('routes.satellite._load_db_satellites_into_cache')
def test_tle_auto_refresh_schedules_daily_repeat(mock_load_db, mock_refresh):
    """After the first TLE refresh, a 24-hour follow-up must be scheduled."""
    import threading
    scheduled_delays = []

    class CapturingTimer:
        def __init__(self, delay, fn, *a, **kw):
            scheduled_delays.append(delay)
            self._fn = fn
            self._ran = False
        def start(self):
            # Execute immediately so we can check the chained schedule
            if not self._ran and scheduled_delays[0] <= 5:
                self._ran = True
                self._fn()  # run the first (startup) timer inline

    with patch('routes.satellite.threading') as mock_threading:
        mock_threading.Timer = CapturingTimer
        mock_threading.Thread = threading.Thread

        # Re-import to pick up patched threading
        import importlib, routes.satellite as sat_mod
        sat_mod.init_tle_auto_refresh()

    # Should have scheduled startup delay AND a 24h follow-up
    assert any(d >= 86000 for d in scheduled_delays), \
        f"Expected a ~24h repeat timer; got delays: {scheduled_delays}"
```

- [ ] **Step 5: Run new test**

```bash
pytest tests/test_satellite.py::test_tle_auto_refresh_schedules_daily_repeat -v
```

Expected: PASS

- [ ] **Step 6: Run full suite**

```bash
pytest tests/test_satellite.py -v
```

- [ ] **Step 7: Commit**

```bash
git add routes/satellite.py tests/test_satellite.py
git commit -m "feat(satellite): add 24-hour periodic TLE auto-refresh

TLE data was only refreshed once at startup. After each refresh, a new
24-hour timer is now scheduled (in the finally block so it fires even
on refresh failure). This keeps orbital elements fresh and pass
predictions accurate over multi-day deployments."
```

---

## Task 4: Fix `updateTelemetry` fallback — add proper currentPos fields

**Problem:** When `latestLivePosition` is null (e.g. before first SSE/poll arrives), `updateTelemetry(pass)` falls back to `pass.currentPos`. But `currentPos` only has `lat` and `lon` (set in `predict_passes` at `satellite.py:509-517`). The fallback code reads `pos.alt`, `pos.el`, `pos.az`, `pos.dist` which are always undefined, so altitude/elevation/azimuth/distance always show `---` in this state.

**Fix:** Populate `currentPos` with full position data (altitude, elevation, azimuth, distance, visible) in the `/satellite/predict` backend handler using Skyfield.

**Files:**
- Modify: `routes/satellite.py` — `predict_passes` route handler (~line 508)
- Test: `tests/test_satellite.py`

- [ ] **Step 1: Write a failing test**

Add to `tests/test_satellite.py`:

```python
@patch('routes.satellite._get_tracked_satellite_maps', return_value=({}, {}))
@patch('routes.satellite._get_timescale')
def test_predict_passes_currentpos_has_full_fields(mock_ts, mock_maps, client):
    """currentPos in pass results must include altitude, elevation, azimuth, distance."""
    from skyfield.api import load
    ts = load.timescale(builtin=True)
    mock_ts.return_value = ts

    payload = {
        'latitude': 51.5074,
        'longitude': -0.1278,
        'hours': 48,
        'minEl': 5,
        'satellites': ['ISS'],
    }
    response = client.post('/satellite/predict', json=payload)
    assert response.status_code == 200
    data = response.json
    assert data['status'] == 'success'
    if data['passes']:
        cp = data['passes'][0].get('currentPos', {})
        for field in ('lat', 'lon', 'altitude', 'elevation', 'azimuth', 'distance'):
            assert field in cp, f"currentPos missing field: {field}"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_satellite.py::test_predict_passes_currentpos_has_full_fields -v
```

Expected: FAIL — `currentPos` currently only has `lat` and `lon`.

- [ ] **Step 3: Enrich `currentPos` in the predict route**

In `routes/satellite.py` inside `predict_passes`, find the block (~line 508-528):

```python
for sat_name, norad_id, tle_data in resolved_satellites:
    current_pos = None
    try:
        satellite = EarthSatellite(tle_data[1], tle_data[2], tle_data[0], ts)
        geo = satellite.at(t0)
        sp = wgs84.subpoint(geo)
        current_pos = {
            'lat': float(sp.latitude.degrees),
            'lon': float(sp.longitude.degrees),
        }
    except Exception:
        pass
```

Replace with:

```python
for sat_name, norad_id, tle_data in resolved_satellites:
    current_pos = None
    try:
        satellite = EarthSatellite(tle_data[1], tle_data[2], tle_data[0], ts)
        geo = satellite.at(t0)
        sp = wgs84.subpoint(geo)
        subpoint_alt = float(sp.elevation.km)
        current_pos = {
            'lat': float(sp.latitude.degrees),
            'lon': float(sp.longitude.degrees),
            'altitude': subpoint_alt,
        }
        # Add observer-relative data using the request's observer location
        try:
            diff = satellite - observer
            topo = diff.at(t0)
            alt_deg, az_deg, dist_km = topo.altaz()
            current_pos['elevation'] = round(float(alt_deg.degrees), 1)
            current_pos['azimuth'] = round(float(az_deg.degrees), 1)
            current_pos['distance'] = round(float(dist_km.km), 1)
            current_pos['visible'] = bool(alt_deg.degrees > 0)
        except Exception:
            pass
    except Exception:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_satellite.py::test_predict_passes_currentpos_has_full_fields -v
```

Expected: PASS

- [ ] **Step 5: Fix `updateTelemetry` fallback in the frontend to use correct field names**

In `templates/satellite_dashboard.html`, find `updateTelemetry` (~line 2380):

```js
function updateTelemetry(pass) {
    if (latestLivePosition) {
        applyTelemetryPosition(latestLivePosition);
        return;
    }
    if (!pass || !pass.currentPos) {
        clearTelemetry();
        return;
    }

    const pos = pass.currentPos;
    const telLat = document.getElementById('telLat');
    ...
    if (telAlt && Number.isFinite(pos.alt)) telAlt.textContent = pos.alt.toFixed(0) + ' km';
    if (telEl && Number.isFinite(pos.el)) telEl.textContent = pos.el.toFixed(1) + '°';
    if (telAz && Number.isFinite(pos.az)) telAz.textContent = pos.az.toFixed(1) + '°';
    if (telDist && Number.isFinite(pos.dist)) telDist.textContent = pos.dist.toFixed(0) + ' km';
}
```

Replace with a call to the existing `applyTelemetryPosition` to keep display logic in one place:

```js
function updateTelemetry(pass) {
    if (latestLivePosition) {
        applyTelemetryPosition(latestLivePosition);
        return;
    }
    if (!pass || !pass.currentPos) {
        clearTelemetry();
        return;
    }
    // currentPos now contains full position data (lat, lon, altitude,
    // elevation, azimuth, distance, visible) from the predict endpoint.
    applyTelemetryPosition(pass.currentPos);
}
```

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/test_satellite.py -v
```

- [ ] **Step 7: Commit**

```bash
git add routes/satellite.py templates/satellite_dashboard.html tests/test_satellite.py
git commit -m "fix(satellite): populate currentPos with full telemetry in pass predictions

Previously currentPos only had lat/lon so the updateTelemetry fallback
(used before first live position arrives) always showed '---' for
altitude/elevation/azimuth/distance. currentPos now includes all fields
computed from the request's observer location. updateTelemetry simplified
to delegate to applyTelemetryPosition."
```

---

## Task 5: Fix altitude calculation to use WGS84 subpoint elevation

**Problem:** `_start_satellite_tracker` and `get_satellite_position` compute altitude as `geocentric.distance().km - 6371` (fixed spherical Earth radius). The `wgs84.subpoint()` call already returns a subpoint with an accurate `.elevation.km` property that accounts for Earth's oblateness.

**Files:**
- Modify: `routes/satellite.py` — tracker loop (~line 248-255) and `/position` handler (~line 636-641)
- Test: `tests/test_satellite.py`

- [ ] **Step 1: Write a test for altitude field presence and plausibility**

Add to `tests/test_satellite.py`:

```python
def test_satellite_altitude_is_plausible():
    """Satellite altitude must be in a plausible orbital range (100–50000 km)."""
    from skyfield.api import EarthSatellite, wgs84, load
    ISS_TLE = (
        'ISS (ZARYA)',
        '1 25544U 98067A   24001.00000000  .00016717  00000-0  30171-3 0  9993',
        '2 25544  51.6416  20.4567 0004561  45.3212  67.8912 15.49876543123457',
    )
    ts = load.timescale(builtin=True)
    satellite = EarthSatellite(ISS_TLE[1], ISS_TLE[2], ISS_TLE[0], ts)
    now = ts.now()
    geocentric = satellite.at(now)
    subpoint = wgs84.subpoint(geocentric)
    altitude = float(subpoint.elevation.km)
    assert 100 < altitude < 50000, f"Altitude {altitude} km is outside plausible range"
```

- [ ] **Step 2: Run test to verify it passes (validates approach)**

```bash
pytest tests/test_satellite.py::test_satellite_altitude_is_plausible -v
```

Expected: PASS — this confirms `subpoint.elevation.km` works.

- [ ] **Step 3: Update tracker loop altitude**

In `routes/satellite.py` `_start_satellite_tracker`, find (~line 248):

```python
pos = {
    ...
    'altitude': float(geocentric.distance().km - 6371),
    ...
}
```

Replace with:

```python
pos = {
    ...
    'altitude': float(subpoint.elevation.km),
    ...
}
```

(`subpoint` is already computed on the line above as `subpoint = wgs84.subpoint(geocentric)`)

- [ ] **Step 4: Update `/satellite/position` handler altitude**

In `routes/satellite.py` `get_satellite_position`, find (~line 634):

```python
pos_data = {
    ...
    'altitude': float(geocentric.distance().km - 6371),
    ...
}
```

Replace with (note: `subpoint` is computed just above as `subpoint = wgs84.subpoint(geocentric)`):

```python
pos_data = {
    ...
    'altitude': float(subpoint.elevation.km),
    ...
}
```

- [ ] **Step 5: Update `currentPos` altitude in predict route (from Task 4)**

In the `predict_passes` handler, the `current_pos` block now uses `subpoint.elevation.km` (already done in Task 4). Verify it matches the pattern.

- [ ] **Step 6: Run full suite**

```bash
pytest tests/test_satellite.py -v
```

- [ ] **Step 7: Commit**

```bash
git add routes/satellite.py tests/test_satellite.py
git commit -m "fix(satellite): use wgs84 subpoint elevation for altitude

Replace geocentric.distance().km - 6371 (fixed spherical radius) with
wgs84.subpoint(geocentric).elevation.km in both the SSE tracker and
the /position endpoint. This accounts for Earth's oblateness and
matches the subpoint already being computed."
```

---

## Task 6: Add METEOR-M2 to weather satellite handoff keys

**Problem:** `WEATHER_SAT_KEYS` only contains `'METEOR-M2-3'` and `'METEOR-M2-4'`. METEOR-M2 (NORAD 40069) is tracked and displayed but has no "→ Capture" button in the pass list.

**Files:**
- Modify: `templates/satellite_dashboard.html` — `WEATHER_SAT_KEYS` constant (~line 2135)

- [ ] **Step 1: Add METEOR-M2 to the set**

Find:
```js
const WEATHER_SAT_KEYS = new Set([
    'METEOR-M2-3', 'METEOR-M2-4'
]);
```

Replace with:
```js
const WEATHER_SAT_KEYS = new Set([
    'METEOR-M2', 'METEOR-M2-3', 'METEOR-M2-4'
]);
```

- [ ] **Step 2: Verify in browser**

Open `/satellite/dashboard`, calculate passes for METEOR-M2. Confirm a "→ Capture" button appears on each pass item.

- [ ] **Step 3: Commit**

```bash
git add templates/satellite_dashboard.html
git commit -m "fix(satellite): add METEOR-M2 to weather satellite handoff keys

METEOR-M2 (NORAD 40069) is a weather satellite with LRPT downlink but
was missing from WEATHER_SAT_KEYS, so no capture button appeared in
the pass list. Adds it alongside M2-3 and M2-4."
```

---

## Task 7: Simplify `_telemetryAbortController` management

**Problem:** The abort controller in `fetchCurrentTelemetry` has redundant null-checks in both the try and catch blocks. The pattern where `_telemetryAbortController` is checked against `controller` in the success path AND again in the catch path, combined with `_activeTelemetryRequestKey` deduplication, is overly complex and has a subtle issue: if `_telemetryAbortController?.signal?.aborted` is checked after it was already set to null, the check is always false.

**Fix:** Simplify to a single active-request guard pattern: clear the controller in `finally`, not in both try and catch.

**Files:**
- Modify: `templates/satellite_dashboard.html` — `fetchCurrentTelemetry` function (~line 1354)

- [ ] **Step 1: Simplify `fetchCurrentTelemetry`**

Find `fetchCurrentTelemetry` (~line 1354) and replace the function body:

```js
async function fetchCurrentTelemetry(requestedSatellite = selectedSatellite, selectionToken = _satelliteSelectionRequestToken) {
    const lat = parseFloat(document.getElementById('obsLat')?.value);
    const lon = parseFloat(document.getElementById('obsLon')?.value);
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || !selectedSatellite) return;

    const requestKey = `telemetry:${requestedSatellite}:${lat.toFixed(3)}:${lon.toFixed(3)}`;
    if (_activeTelemetryRequestKey === requestKey) return;  // identical request already in flight

    // Cancel any in-flight request for a different satellite/location
    if (_telemetryAbortController) {
        _telemetryAbortController.abort();
        _telemetryAbortController = null;
    }

    const controller = new AbortController();
    _telemetryAbortController = controller;
    _activeTelemetryRequestKey = requestKey;

    try {
        const timeoutId = setTimeout(() => controller.abort(), TELEMETRY_FETCH_TIMEOUT_MS);
        const response = await fetch('/satellite/position', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            signal: controller.signal,
            body: JSON.stringify({
                latitude: lat,
                longitude: lon,
                satellites: [requestedSatellite],
                includeTrack: false
            })
        });
        clearTimeout(timeoutId);

        if (!response.ok) return;
        const contentType = response.headers.get('Content-Type') || '';
        if (!contentType.includes('application/json')) return;
        const data = await response.json();
        if (data.status !== 'success' || !Array.isArray(data.positions)) return;

        // Discard if satellite or selection changed while request was in flight
        if (selectionToken !== _satelliteSelectionRequestToken || requestedSatellite !== selectedSatellite) return;

        const pos = data.positions.find(p => parseInt(p.norad_id, 10) === requestedSatellite) || null;
        if (!pos) return;
        cacheLivePosition(requestedSatellite, pos);
        handleLivePositions(data.positions, 'poll');

    } catch (err) {
        if (err?.name === 'AbortError') return;  // expected on cancel/timeout
        // unexpected error — log but don't crash
        console.debug('Telemetry fetch error:', err);
    } finally {
        // Always release the controller slot so the next poll can run
        if (_telemetryAbortController === controller) {
            _telemetryAbortController = null;
        }
        if (_activeTelemetryRequestKey === requestKey) {
            _activeTelemetryRequestKey = null;
        }
    }
}
```

- [ ] **Step 2: Manual smoke test**

Open `/satellite/dashboard`. Switch between satellites rapidly. Confirm:
- Telemetry updates within ~5s of switching
- No stale data from the previous satellite appears after switching
- No console errors

- [ ] **Step 3: Commit**

```bash
git add templates/satellite_dashboard.html
git commit -m "refactor(satellite): simplify telemetry abort controller management

The previous pattern had redundant null-checks in both try and catch,
and a subtle bug where checking signal.aborted after setting the
controller to null was always false. Consolidated to a single
active-request guard with cleanup in finally."
```

---

## Task 8: Fix ground track blocking the 1Hz tracker loop

**Problem:** `_start_satellite_tracker` computes a 90-point orbit track on every cache miss inside the 1Hz loop. With many tracked satellites, cold-cache startup means multiple expensive Skyfield loops block the tracker for several seconds, causing the SSE stream to go silent until they complete.

**Fix:** Compute ground tracks lazily in a thread pool so the main tracker loop stays snappy. If a track is not yet cached, emit the position without a ground track (the frontend already handles missing `groundTrack` gracefully).

**Files:**
- Modify: `routes/satellite.py` — `_start_satellite_tracker` function (~line 266)

- [ ] **Step 1: Refactor ground track computation to a thread pool**

At the top of `routes/satellite.py`, add import (it's stdlib):

```python
from concurrent.futures import ThreadPoolExecutor
```

Add a module-level thread pool (near the other module-level state, around line 50):

```python
# Thread pool for background ground-track computation (non-blocking from tracker loop)
_track_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='sat-track')
_track_in_progress: set = set()  # keys currently being computed
```

In `_start_satellite_tracker`, replace the ground track block (~lines 266-286):

```python
# Ground track with caching (90 points, TTL 1800s)
cache_key_track = (sat_name, tle1[:20])
cached = _track_cache.get(cache_key_track)
if cached and (time.time() - cached[1]) < _TRACK_CACHE_TTL:
    pos['groundTrack'] = cached[0]
else:
    track = []
    for minutes_offset in range(-45, 46, 1):
        ...
```

With:

```python
# Ground track with caching (90 points, TTL 1800s)
cache_key_track = (sat_name, tle1[:20])
cached = _track_cache.get(cache_key_track)
if cached and (time.time() - cached[1]) < _TRACK_CACHE_TTL:
    pos['groundTrack'] = cached[0]
elif cache_key_track not in _track_in_progress:
    # Kick off computation in background — don't block the 1Hz loop
    _track_in_progress.add(cache_key_track)

    def _compute_track(sat_obj, ts_ref, now_dt_ref, key, sat_name_ref):
        try:
            track = []
            for minutes_offset in range(-45, 46, 1):
                t_point = ts_ref.utc(now_dt_ref + timedelta(minutes=minutes_offset))
                try:
                    geo = sat_obj.at(t_point)
                    sp = wgs84.subpoint(geo)
                    track.append({
                        'lat': float(sp.latitude.degrees),
                        'lon': float(sp.longitude.degrees),
                        'past': minutes_offset < 0,
                    })
                except Exception:
                    continue
            _track_cache[key] = (track, time.time())
        except Exception:
            pass
        finally:
            _track_in_progress.discard(key)

    _track_executor.submit(_compute_track, satellite, ts, now_dt, cache_key_track, sat_name)
    # groundTrack omitted this tick — frontend retains previous value from SSE merge
```

- [ ] **Step 2: Run full test suite to confirm no regressions**

```bash
pytest tests/test_satellite.py -v
```

- [ ] **Step 3: Commit**

```bash
git add routes/satellite.py
git commit -m "perf(satellite): compute ground tracks in thread pool, not inline

Ground track computation (90 Skyfield points per satellite) was blocking
the 1Hz tracker loop on every cache miss. On cold start with multiple
tracked satellites this could stall the SSE stream for several seconds.
Tracks are now computed in a 2-worker ThreadPoolExecutor. The tracker
loop emits position without groundTrack on cache miss; clients retain
the previous track via SSE merge until the new one is ready."
```

---

## Task 9: Fix countdown when all passes are in the past

**Problem:** `updateCountdown` falls back to `passes[0]` when no future pass is found. If `passes[0]` is in the past, the countdown displays 00:00:00:00 perpetually and the satellite name is misleading.

**Files:**
- Modify: `templates/satellite_dashboard.html` — `updateCountdown` function (~line 2406)

- [ ] **Step 1: Fix the countdown fallback**

Find `updateCountdown` (~line 2406). Replace the section that handles the no-future-pass case:

```js
if (!nextPass) nextPass = passes[0];
```

With:

```js
if (!nextPass) {
    // All passes in window are in the past — show stale state
    document.getElementById('countdownSat').textContent = 'NO UPCOMING PASSES';
    document.getElementById('countDays').textContent = '--';
    document.getElementById('countHours').textContent = '--';
    document.getElementById('countMins').textContent = '--';
    document.getElementById('countSecs').textContent = '--';
    ['countDays', 'countHours', 'countMins', 'countSecs'].forEach(id => {
        document.getElementById(id)?.classList.remove('active');
    });
    return;
}
```

- [ ] **Step 2: Manual verification**

To test, temporarily set `passes` to a list with a past timestamp in the browser console:
```js
passes = [{ satellite: 'ISS', startTimeISO: '2020-01-01T00:00:00', maxEl: 45, duration: 5 }];
updateCountdown();
```
Confirm the countdown shows `NO UPCOMING PASSES` and `--` for all fields.

- [ ] **Step 3: Commit**

```bash
git add templates/satellite_dashboard.html
git commit -m "fix(satellite): show 'NO UPCOMING PASSES' when all passes are in the past

updateCountdown fell back to passes[0] even when it was in the past,
showing 00:00:00:00 with a stale satellite name indefinitely. Now
displays a clear 'NO UPCOMING PASSES' state when no future pass exists
in the current 48-hour prediction window."
```

---

## Final: Run full test suite and verify

- [ ] **Run all tests**

```bash
cd /Users/jsmith/Documents/Dev/intercept
pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Lint check**

```bash
ruff check routes/satellite.py
```

Expected: no new errors.

- [ ] **Manual end-to-end verification checklist**

Open `/satellite/dashboard` and confirm:
1. Lat/Lon updates smoothly every ~1 second
2. Elevation/Azimuth/Distance update every ~5 seconds (not every 1 second)
3. Visible-count badge reflects client's actual location
4. Selecting a pass before first live data arrives shows altitude/el/az in telemetry panel
5. METEOR-M2 passes show "→ Capture" button
6. Switching satellites rapidly shows no stale data from previous satellite
7. Countdown shows `NO UPCOMING PASSES` rather than 00:00:00:00 when window is expired
