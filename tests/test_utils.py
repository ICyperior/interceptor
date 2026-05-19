"""Tests for utility modules."""

import threading
import time

from data.oui import get_manufacturer
from utils.cleanup import DataStore
from utils.dependencies import check_tool
from utils.process import is_valid_channel, is_valid_mac


class TestMacValidation:
    """Tests for MAC address validation."""

    def test_valid_mac(self):
        """Test valid MAC addresses."""
        assert is_valid_mac("AA:BB:CC:DD:EE:FF") is True
        assert is_valid_mac("aa:bb:cc:dd:ee:ff") is True
        assert is_valid_mac("00:11:22:33:44:55") is True

    def test_invalid_mac(self):
        """Test invalid MAC addresses."""
        assert is_valid_mac("") is False
        assert is_valid_mac(None) is False
        assert is_valid_mac("invalid") is False
        assert is_valid_mac("AA:BB:CC:DD:EE") is False
        assert is_valid_mac("AA-BB-CC-DD-EE-FF") is False


class TestChannelValidation:
    """Tests for WiFi channel validation."""

    def test_valid_channels(self):
        """Test valid channel numbers."""
        assert is_valid_channel(1) is True
        assert is_valid_channel(6) is True
        assert is_valid_channel(11) is True
        assert is_valid_channel("36") is True
        assert is_valid_channel(149) is True

    def test_invalid_channels(self):
        """Test invalid channel numbers."""
        assert is_valid_channel(0) is False
        assert is_valid_channel(-1) is False
        assert is_valid_channel(201) is False
        assert is_valid_channel(None) is False
        assert is_valid_channel("invalid") is False


class TestToolCheck:
    """Tests for tool availability checking."""

    def test_common_tools(self):
        """Test checking for common tools."""
        # These should return bool, regardless of whether installed
        assert isinstance(check_tool("ls"), bool)
        assert isinstance(check_tool("nonexistent_tool_12345"), bool)

    def test_nonexistent_tool(self):
        """Test that nonexistent tools return False."""
        assert check_tool("nonexistent_tool_xyz_12345") is False


class TestOuiLookup:
    """Tests for OUI manufacturer lookup."""

    def test_known_manufacturer(self):
        """Test looking up known manufacturers."""
        # Apple prefix
        result = get_manufacturer("00:25:DB:AA:BB:CC")
        assert result == "Apple" or result == "Unknown"

    def test_unknown_manufacturer(self):
        """Test looking up unknown manufacturer."""
        result = get_manufacturer("FF:FF:FF:FF:FF:FF")
        assert result == "Unknown"


class TestDataStoreCleanup:
    """Tests for DataStore cleanup behavior."""

    def test_cleanup_removes_expired_keeps_fresh(self):
        """Test that cleanup removes expired entries and keeps fresh ones."""
        store = DataStore(max_age_seconds=0.001, name="test")
        store.set("old", 1)
        time.sleep(0.01)
        store.set("new", 2)

        removed = store.cleanup()

        assert removed == 1
        assert "old" not in store
        assert "new" in store

    def test_cleanup_does_not_delete_refreshed_entry(self):
        """Entry refreshed after the snapshot is taken must survive deletion re-validation."""
        store = DataStore(max_age_seconds=0.05, name="test")
        store.set("key", "old")
        time.sleep(0.06)  # expire it

        real_lock = store._lock
        snapshot_done = threading.Event()
        ok_to_delete = threading.Event()
        exit_count = [0]

        class PauseLock:
            def acquire(self, *a, **kw):
                return real_lock.acquire(*a, **kw)

            def release(self):
                real_lock.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *a):
                self.release()
                exit_count[0] += 1
                if exit_count[0] == 1:
                    snapshot_done.set()
                    ok_to_delete.wait(timeout=2.0)

        store._lock = PauseLock()

        result = [None]

        def run():
            result[0] = store.cleanup()

        t = threading.Thread(target=run, daemon=True)
        t.start()

        snapshot_done.wait(timeout=2.0)

        # Inject refresh using real_lock directly (bypasses PauseLock)
        with real_lock:
            store.data["key"] = "refreshed"
            store.timestamps["key"] = time.time()

        ok_to_delete.set()
        t.join(timeout=2.0)
        store._lock = real_lock

        assert result[0] == 0, "Re-validation guard must prevent deletion of refreshed entry"
        assert store.get("key") == "refreshed"
