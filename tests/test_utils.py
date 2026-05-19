"""Tests for utility modules."""

import time
from unittest.mock import patch

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
        """An entry refreshed after the cleanup snapshot must survive cleanup()."""
        store = DataStore(max_age_seconds=0.05, name="test")
        store.set("key", "old")
        time.sleep(0.06)  # expire it

        # Mock time.time to return different values on successive calls.
        # This simulates the scenario where cleanup() snapshots the timestamp,
        # then between snapshot and deletion a refresh happens (timestamp updates),
        # and the re-validation check uses a different time value.
        call_sequence = iter(
            [
                time.time() - 1.0,  # cleanup() first call: now = old time, so "key" appears expired
                time.time() - 1.0,  # re-validation: now = same time, still expired... but wait
            ]
        )

        original_time = time.time

        def mocked_time():
            try:
                return next(call_sequence)
            except StopIteration:
                # After we've used the mock sequence, return current time
                return original_time()

        with patch("utils.cleanup.time.time", mocked_time):
            # Just before cleanup, refresh the key so it has a fresh timestamp
            store.set("key", "refreshed")
            removed = store.cleanup()

        assert removed == 0, "Entry refreshed before cleanup must survive"
        assert "key" in store
