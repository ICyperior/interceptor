"""Unit tests for the signal database loader and match function."""

from __future__ import annotations


class TestLoadSignals:
    def test_returns_list(self):
        from utils.signal_db import load_signals
        signals = load_signals()
        assert isinstance(signals, list)
        assert len(signals) > 0

    def test_cached_on_second_call(self):
        from utils.signal_db import load_signals
        first = load_signals()
        second = load_signals()
        assert first is second  # same object — cached


class TestMatchSignals:
    def test_fm_broadcast_matched_at_center(self):
        from utils.signal_db import match_signals
        results = match_signals(frequency_mhz=98.5)
        names = [r["name"] for r in results]
        assert "FM Broadcast Radio" in names

    def test_frequency_at_exact_range_boundary_included(self):
        from utils.signal_db import match_signals
        # 87.5 MHz is the lower bound of FM broadcast
        results = match_signals(frequency_mhz=87.5)
        names = [r["name"] for r in results]
        assert "FM Broadcast Radio" in names

    def test_frequency_just_outside_range_excluded(self):
        from utils.signal_db import match_signals
        # 87.499 MHz is just below FM broadcast lower bound
        results = match_signals(frequency_mhz=87.499)
        names = [r["name"] for r in results]
        assert "FM Broadcast Radio" not in names

    def test_no_matches_returns_empty_list(self):
        from utils.signal_db import match_signals
        # 5000 MHz has no signals in our database
        results = match_signals(frequency_mhz=5000.0)
        assert results == []

    def test_results_have_score_field(self):
        from utils.signal_db import match_signals
        results = match_signals(frequency_mhz=98.5)
        assert len(results) > 0
        for r in results:
            assert "score" in r
            assert isinstance(r["score"], int)
            assert 0 <= r["score"] <= 100

    def test_results_have_match_reasons(self):
        from utils.signal_db import match_signals
        results = match_signals(frequency_mhz=98.5)
        assert len(results) > 0
        for r in results:
            assert "match_reasons" in r
            assert isinstance(r["match_reasons"], list)

    def test_results_sorted_by_score_descending(self):
        from utils.signal_db import match_signals
        results = match_signals(frequency_mhz=98.5)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_limit_respected(self):
        from utils.signal_db import match_signals
        results = match_signals(frequency_mhz=98.5, limit=2)
        assert len(results) <= 2

    def test_limit_clamped_to_minimum_1(self):
        from utils.signal_db import match_signals
        results = match_signals(frequency_mhz=98.5, limit=0)
        assert len(results) >= 1

    def test_bandwidth_within_range_scores_higher(self):
        from utils.signal_db import match_signals
        # FM broadcast bandwidth_range is 150k–250k Hz; 200k is within
        with_bw = match_signals(frequency_mhz=98.5, bandwidth_hz=200_000)
        without_bw = match_signals(frequency_mhz=98.5)
        fm_with = next(r for r in with_bw if r["name"] == "FM Broadcast Radio")
        fm_without = next(r for r in without_bw if r["name"] == "FM Broadcast Radio")
        assert fm_with["score"] >= fm_without["score"]

    def test_bandwidth_outside_2x_scores_zero_for_bw_criterion(self):
        from utils.signal_db import match_signals
        # FM broadcast max_bw is 250k Hz; 600k is > 2× that
        results = match_signals(frequency_mhz=98.5, bandwidth_hz=600_000)
        fm = next((r for r in results if r["name"] == "FM Broadcast Radio"), None)
        # FM may still appear due to frequency score, but BW reason should not be "within typical"
        if fm:
            assert "bandwidth: within typical" not in fm["match_reasons"]

    def test_modulation_exact_match_scores_higher(self):
        from utils.signal_db import match_signals
        with_mod = match_signals(frequency_mhz=98.5, modulation="WFM")
        without_mod = match_signals(frequency_mhz=98.5)
        fm_with = next(r for r in with_mod if r["name"] == "FM Broadcast Radio")
        fm_without = next(r for r in without_mod if r["name"] == "FM Broadcast Radio")
        assert fm_with["score"] >= fm_without["score"]

    def test_modulation_mismatch_no_mod_reason(self):
        from utils.signal_db import match_signals
        results = match_signals(frequency_mhz=98.5, modulation="LSB")
        fm = next((r for r in results if r["name"] == "FM Broadcast Radio"), None)
        if fm:
            assert "modulation: exact match" not in fm["match_reasons"]

    def test_multi_range_signal_matched_by_any_range(self):
        from utils.signal_db import match_signals
        # POCSAG has ranges in 138-175 MHz and 450-470 MHz
        # 162 MHz is in the first range (maritime VHF area, but also POCSAG territory)
        results_vhf = match_signals(frequency_mhz=155.0)
        results_uhf = match_signals(frequency_mhz=455.0)
        vhf_names = [r["name"] for r in results_vhf]
        uhf_names = [r["name"] for r in results_uhf]
        assert "POCSAG Pager" in vhf_names
        assert "POCSAG Pager" in uhf_names

    def test_region_mismatch_does_not_exclude_signal(self):
        from utils.signal_db import match_signals
        # PMR446 is EU/UK only; should still appear with US region but may score lower
        results = match_signals(frequency_mhz=446.09375, region="US")
        names = [r["name"] for r in results]
        assert "PMR446 (Licence-Free UHF)" in names

    def test_original_signal_dict_not_mutated(self):
        from utils.signal_db import load_signals, match_signals
        original = load_signals()
        first_id_before = original[0]["id"]
        match_signals(frequency_mhz=98.5, modulation="WFM")
        assert original[0]["id"] == first_id_before  # not mutated
        assert "score" not in original[0]  # score not added in-place
