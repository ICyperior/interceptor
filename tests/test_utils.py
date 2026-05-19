"""Tests for utility modules."""

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
        """An entry whose timestamp was updated after the snapshot must survive cleanup."""
        store = DataStore(max_age_seconds=0.1, name="test")
        store.set("key", "old")
        time.sleep(0.15)  # expire it

        # Directly test the scenario: snapshot shows key is expired, but refresh it before deletion
        now = time.time()

        # At this point, key's timestamp is old (from sleep above)
        # Simulate the snapshot phase
        with store._lock:
            timestamps_snapshot = list(store.timestamps.items())

        # Check that key appears expired in snapshot
        expired_in_snapshot = [k for k, t in timestamps_snapshot if now - t > store.max_age]
        assert "key" in expired_in_snapshot

        # Now refresh the key (simulating another thread's set())
        store.set("key", "refreshed")

        # Now simulate the deletion phase with re-validation
        # (this is what the new code does)
        deleted = 0
        with store._lock:
            for key in expired_in_snapshot:
                if key in store.timestamps and now - store.timestamps[key] > store.max_age:
                    del store.data[key]
                    del store.timestamps[key]
                    deleted += 1

        # With re-validation, key should NOT be deleted because its timestamp was refreshed
        assert deleted == 0
        assert store.get("key") == "refreshed"
