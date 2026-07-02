"""Schema validation for data/signals.json."""

from __future__ import annotations

import json
from pathlib import Path

SIGNALS_PATH = Path(__file__).resolve().parent.parent / "data" / "signals.json"

VALID_REGIONS = {"GLOBAL", "EU", "US", "UK", "AU"}


def _load() -> list[dict]:
    assert SIGNALS_PATH.exists(), f"signals.json not found at {SIGNALS_PATH}"
    with open(SIGNALS_PATH) as f:
        data = json.load(f)
    assert isinstance(data, list), "signals.json must be a JSON array"
    return data


class TestSignalsJsonSchema:
    def test_file_loads_as_list(self):
        data = _load()
        assert len(data) > 0

    def test_all_ids_unique(self):
        data = _load()
        ids = [s["id"] for s in data]
        assert len(ids) == len(set(ids)), f"Duplicate ids: {[x for x in ids if ids.count(x) > 1]}"

    def test_required_string_fields(self):
        data = _load()
        for s in data:
            for field in ("id", "name", "description"):
                assert field in s, f"Missing '{field}' in signal {s.get('id', '?')}"
                assert isinstance(s[field], str), f"'{field}' must be str in {s['id']}"
                assert s[field].strip(), f"'{field}' must not be empty in {s['id']}"

    def test_frequency_ranges_valid(self):
        data = _load()
        for s in data:
            sid = s.get("id", "?")
            assert "frequency_ranges" in s, f"Missing frequency_ranges in {sid}"
            assert isinstance(s["frequency_ranges"], list), f"frequency_ranges must be list in {sid}"
            assert len(s["frequency_ranges"]) > 0, f"frequency_ranges must not be empty in {sid}"
            for r in s["frequency_ranges"]:
                assert isinstance(r.get("min_hz"), int), f"min_hz must be int in {sid}"
                assert isinstance(r.get("max_hz"), int), f"max_hz must be int in {sid}"
                assert r["min_hz"] > 0, f"min_hz must be > 0 in {sid}"
                assert r["min_hz"] < r["max_hz"], f"min_hz must be < max_hz in {sid}"

    def test_bandwidth_range_valid_or_null(self):
        data = _load()
        for s in data:
            sid = s.get("id", "?")
            assert "bandwidth_range" in s, f"Missing bandwidth_range in {sid}"
            bw = s["bandwidth_range"]
            if bw is None:
                continue
            assert isinstance(bw.get("min_hz"), int), f"bandwidth_range.min_hz must be int in {sid}"
            assert isinstance(bw.get("max_hz"), int), f"bandwidth_range.max_hz must be int in {sid}"
            assert bw["min_hz"] > 0, f"bandwidth_range.min_hz must be > 0 in {sid}"
            assert bw["min_hz"] < bw["max_hz"], f"bandwidth_range.min_hz must be < max_hz in {sid}"

    def test_modulations_uppercase_strings(self):
        data = _load()
        for s in data:
            sid = s.get("id", "?")
            assert "modulations" in s, f"Missing modulations in {sid}"
            assert isinstance(s["modulations"], list), f"modulations must be list in {sid}"
            for m in s["modulations"]:
                assert isinstance(m, str), f"modulation token must be str in {sid}"
                assert m == m.upper(), f"modulation '{m}' must be uppercase in {sid}"
                assert m.strip(), f"modulation token must not be empty in {sid}"

    def test_categories_list_of_strings(self):
        data = _load()
        for s in data:
            sid = s.get("id", "?")
            assert "categories" in s, f"Missing categories in {sid}"
            assert isinstance(s["categories"], list), f"categories must be list in {sid}"
            for c in s["categories"]:
                assert isinstance(c, str) and c.strip(), f"category must be non-empty str in {sid}"

    def test_regions_valid_values(self):
        data = _load()
        for s in data:
            sid = s.get("id", "?")
            assert "regions" in s, f"Missing regions in {sid}"
            assert isinstance(s["regions"], list), f"regions must be list in {sid}"
            assert len(s["regions"]) > 0, f"regions must not be empty in {sid}"
            for r in s["regions"]:
                assert r in VALID_REGIONS, f"Invalid region '{r}' in {sid}. Valid: {VALID_REGIONS}"

    def test_sigidwiki_url_string_or_null(self):
        data = _load()
        for s in data:
            sid = s.get("id", "?")
            assert "sigidwiki_url" in s, f"Missing sigidwiki_url in {sid}"
            url = s["sigidwiki_url"]
            if url is not None:
                assert isinstance(url, str), f"sigidwiki_url must be str or null in {sid}"
                assert url.startswith("https://www.sigidwiki.com/"), f"sigidwiki_url must be sigidwiki.com URL in {sid}"
