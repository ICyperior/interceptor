"""Tests for Enhanced Mode-S (aircraft.json) enrichment of ADS-B aircraft."""

import app as app_module
from routes.adsb import _merge_ehs_aircraft


def _reset():
    app_module.adsb_aircraft.clear()


def test_merges_ehs_fields_onto_tracked_aircraft():
    _reset()
    app_module.adsb_aircraft.set("A2A38E", {"icao": "A2A38E", "callsign": "UAL263", "speed": 512})
    a = {
        "hex": "a2a38e",  # lowercase in aircraft.json
        "roll": -12.3, "mag_heading": 331.0, "ias": 280, "tas": 512, "mach": 0.82,
        "nav_altitude_mcp": 37000, "nav_heading": 330,
    }
    assert _merge_ehs_aircraft(a) is True
    rec = app_module.adsb_aircraft.get("A2A38E")
    assert rec["roll"] == -12.3
    assert rec["mag_heading"] == 331.0
    assert rec["ias"] == 280
    assert rec["mach"] == 0.82
    assert rec["sel_altitude"] == 37000
    assert rec["sel_heading"] == 330
    assert rec["ehs"] is True
    # existing SBS fields preserved
    assert rec["callsign"] == "UAL263"
    assert rec["speed"] == 512


def test_skips_untracked_aircraft():
    _reset()
    a = {"hex": "beef01", "roll": 5}
    assert _merge_ehs_aircraft(a) is False
    assert app_module.adsb_aircraft.get("BEEF01") is None


def test_no_ehs_fields_is_noop():
    _reset()
    app_module.adsb_aircraft.set("C0FFEE", {"icao": "C0FFEE", "speed": 400})
    a = {"hex": "c0ffee", "gs": 400}  # only basic fields, no EHS
    assert _merge_ehs_aircraft(a) is False
    rec = app_module.adsb_aircraft.get("C0FFEE")
    assert "ehs" not in rec


def test_selected_altitude_falls_back_to_fms():
    _reset()
    app_module.adsb_aircraft.set("ABCDEF", {"icao": "ABCDEF"})
    a = {"hex": "abcdef", "nav_altitude_fms": 12000}
    assert _merge_ehs_aircraft(a) is True
    assert app_module.adsb_aircraft.get("ABCDEF")["sel_altitude"] == 12000
