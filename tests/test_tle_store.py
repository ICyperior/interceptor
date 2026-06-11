"""Tests for the unified TLE store."""

from pathlib import Path

import pytest

from utils import tle_store


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    """Point the store at a throwaway database file."""
    monkeypatch.setattr(tle_store, "_DB_PATH", tmp_path / "tle.db")
    tle_store._reset_for_tests()


SAMPLE = (
    "ISS (ZARYA)",
    "1 25544U 98067A   23321.52083333  .00016717  00000-0  30171-3 0  9992",
    "2 25544  51.6416  20.4567 0004561  45.3212  67.8912 15.49876543123456",
)


class TestTLEStore:
    def test_seed_from_static_data(self):
        """First access seeds from data/satellites.py TLE_SATELLITES."""
        tles = tle_store.all_tles()
        assert "ISS" in tles
        name, l1, l2 = tles["ISS"]
        assert l1.startswith("1 ")
        assert l2.startswith("2 ")

    def test_update_and_get(self):
        tle_store.update({"TEST-SAT": SAMPLE})
        assert tle_store.get_tle("TEST-SAT") == SAMPLE

    def test_get_missing_returns_none(self):
        assert tle_store.get_tle("NO-SUCH-SAT") is None

    def test_update_overwrites(self):
        tle_store.update({"TEST-SAT": SAMPLE})
        newer = (SAMPLE[0], SAMPLE[1].replace("23321", "26100"), SAMPLE[2])
        tle_store.update({"TEST-SAT": newer})
        assert tle_store.get_tle("TEST-SAT") == newer

    def test_persists_across_reset(self):
        """Data survives a cache reset (i.e., it actually hit the database)."""
        tle_store.update({"TEST-SAT": SAMPLE})
        tle_store._reset_for_tests()
        assert tle_store.get_tle("TEST-SAT") == SAMPLE

    def test_update_before_first_read_keeps_seed(self):
        """An update() on a fresh DB must not prevent the static seed."""
        tle_store.update({"TEST-SAT": SAMPLE})
        tles = tle_store.all_tles()
        assert "TEST-SAT" in tles
        assert "ISS" in tles  # static seed still present

    def test_update_waits_for_concurrent_writer(self):
        """A short-lived writer in another connection must not make update() raise."""
        import sqlite3
        import threading

        tle_store.all_tles()  # ensure DB exists
        blocker = sqlite3.connect(str(tle_store._DB_PATH), check_same_thread=False)
        blocker.execute("BEGIN IMMEDIATE")

        def release_soon():
            import time

            time.sleep(0.2)
            blocker.commit()
            blocker.close()

        t = threading.Thread(target=release_soon)
        t.start()
        tle_store.update({"TEST-SAT": SAMPLE})  # must block briefly, not raise
        t.join()
        assert tle_store.get_tle("TEST-SAT") == SAMPLE


def test_default_db_path_points_at_instance_dir():
    """The unpatched module constant must resolve to <repo>/instance/tle.db."""
    import importlib

    spec = importlib.util.find_spec("utils.tle_store")
    module_file = Path(spec.origin)
    expected = module_file.parent.parent / "instance" / "tle.db"
    # Read the constant from a fresh module instance, not the patched one
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert expected == fresh._DB_PATH
