"""Unit tests for the ISS telemetry bridge frame parser.

The live NASA feed can't be exercised in CI (and is often in LOS), so these
tests drive the TLCP update parser directly to lock in the AOS value path and
the mapping to friendly keys.
"""

from utils.iss_telemetry import TELEMETRY_ITEMS, ISSTelemetryClient


def _pos_of(pui):
    return list(TELEMETRY_ITEMS).index(pui) + 1  # TLCP item positions are 1-based


def test_handle_update_parses_value():
    c = ISSTelemetryClient()
    pos = _pos_of("USLAB000058")  # cabin pressure
    c._handle_update(f"U,1,{pos},12345|14.72")
    assert c._values["USLAB000058"] == 14.72
    assert c._last_update_ts > 0


def test_handle_update_skips_unchanged_and_bad_values():
    c = ISSTelemetryClient()
    pos = _pos_of("NODE3000001")
    c._handle_update(f"U,1,{pos},12345|$")      # "$" = unchanged from snapshot
    c._handle_update(f"U,1,{pos},12345|")        # empty value
    c._handle_update(f"U,1,{pos},12345|notnum")  # non-numeric
    assert "NODE3000001" not in c._values


def test_snapshot_mapping_uses_friendly_keys_and_units():
    c = ISSTelemetryClient()
    c._handle_update(f"U,1,{_pos_of('S0000004')},1|42.5")   # sarj_port
    c._handle_update(f"U,1,{_pos_of('P4000007')},1|-88.0")  # bga_2a
    # Build the mapped view the way get_snapshot() does, without starting the
    # background network thread.
    mapped = {}
    for pui, (key, unit) in TELEMETRY_ITEMS.items():
        if pui in c._values:
            mapped[key] = {"value": c._values[pui], "unit": unit}
    assert mapped["sarj_port"] == {"value": 42.5, "unit": "deg"}
    assert mapped["bga_2a"] == {"value": -88.0, "unit": "deg"}
