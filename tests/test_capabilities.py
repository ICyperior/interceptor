"""Tests for shared capability detection."""

from unittest.mock import patch

from utils.capabilities import detect_interfaces, detect_mode_availability


class TestModeAvailability:
    def test_all_tools_present(self):
        with patch("utils.capabilities.check_all_dependencies") as mock_deps:
            mock_deps.return_value = {
                key: {"ready": True}
                for key in (
                    "pager",
                    "sensor",
                    "aircraft",
                    "ais",
                    "acars",
                    "aprs",
                    "wifi",
                    "bluetooth",
                    "tscm",
                    "satellite",
                )
            }
            modes = detect_mode_availability()
        assert modes.get("sensor") is True
        assert modes.get("pager") is True
        assert modes.get("adsb") is True  # maps from dep key "aircraft"

    def test_no_tools_present(self):
        with patch("utils.capabilities.check_all_dependencies") as mock_deps:
            mock_deps.return_value = {}
            modes = detect_mode_availability()
        assert modes.get("sensor") is False


class TestInterfaceDetection:
    def test_returns_expected_shape(self, fake_process):
        with patch("subprocess.Popen", return_value=fake_process()):
            interfaces = detect_interfaces()
        assert set(interfaces) == {"wifi_interfaces", "bt_adapters", "sdr_devices"}
        assert isinstance(interfaces["wifi_interfaces"], list)
