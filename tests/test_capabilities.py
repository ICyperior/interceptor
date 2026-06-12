"""Tests for shared capability detection."""

from unittest.mock import MagicMock, patch

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

    def test_pre_computed_dep_status_skips_probe(self):
        """Passing dep_status must not trigger a second check_all_dependencies call."""
        pre_computed = {
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
        with patch("utils.capabilities.check_all_dependencies") as mock_deps:
            modes = detect_mode_availability(dep_status=pre_computed)
        mock_deps.assert_not_called()
        assert modes.get("sensor") is True
        assert modes.get("adsb") is True


class TestInterfaceDetection:
    def test_darwin_wifi_parsing(self):
        """The Darwin branch must parse a Wi-Fi device out of networksetup output."""
        networksetup_output = (
            "Hardware Port: Wi-Fi\n"
            "Device: en0\n"
            "Ethernet Address: aa:bb:cc:dd:ee:ff\n"
            "\n"
            "Hardware Port: Thunderbolt Bridge\n"
            "Device: bridge0\n"
        )

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.stdout = networksetup_output
            result.stderr = ""
            result.returncode = 0
            return result

        with (
            patch("utils.capabilities.platform.system", return_value="Darwin"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            interfaces = detect_interfaces()

        names = [i["name"] for i in interfaces["wifi_interfaces"]]
        assert "en0" in names
        # Verify the full shape of the parsed entry
        en0 = next(i for i in interfaces["wifi_interfaces"] if i["name"] == "en0")
        assert "display_name" in en0
        assert "type" in en0
        assert "monitor_capable" in en0
        # Thunderbolt Bridge must not appear — it has no Wi-Fi/AirPort keyword
        assert "bridge0" not in names


class TestFallback:
    def test_fallback_uses_check_tool(self):
        """When check_all_dependencies raises, fall back to per-tool checks."""
        with (
            patch(
                "utils.capabilities.check_all_dependencies",
                side_effect=RuntimeError("module unavailable"),
            ),
            patch("utils.capabilities.check_tool", return_value=False) as mock_check,
        ):
            modes = detect_mode_availability()
        assert modes.get("sensor") is False
        assert mock_check.called

    def test_fallback_extra_mode_tools(self):
        """EXTRA_MODE_TOOLS modes (dsc, rtlamr, listening_post) reflect check_tool's return."""
        with (
            patch(
                "utils.capabilities.check_all_dependencies",
                side_effect=RuntimeError("module unavailable"),
            ),
            patch("utils.capabilities.check_tool", return_value=False),
        ):
            modes = detect_mode_availability()
        assert modes.get("dsc") is False
        assert modes.get("rtlamr") is False
        assert modes.get("listening_post") is False
