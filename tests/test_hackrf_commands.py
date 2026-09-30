"""Tests for HackRF command builder."""

from unittest.mock import MagicMock

import pytest

from utils.sdr import hackrf
from utils.sdr.base import SDRDevice, SDRType
from utils.sdr.hackrf import HackRFCommandBuilder


def _make_device(serial: str = "abc123") -> SDRDevice:
    return SDRDevice(
        sdr_type=SDRType.HACKRF,
        index=0,
        name="HackRF One",
        serial=serial,
        driver="hackrf",
        capabilities=HackRFCommandBuilder.CAPABILITIES,
    )


class TestHackRFCapabilities:
    def test_gain_max_reflects_combined_lna_vga(self):
        """gain_max should be LNA(40) + VGA(62) = 102."""
        assert HackRFCommandBuilder.CAPABILITIES.gain_max == 102.0

    def test_frequency_range(self):
        caps = HackRFCommandBuilder.CAPABILITIES
        assert caps.freq_min_mhz == 1.0
        assert caps.freq_max_mhz == 6000.0

    def test_tx_capable(self):
        assert HackRFCommandBuilder.CAPABILITIES.tx_capable is True


class TestSplitGain:
    def test_low_gain_all_to_lna(self):
        builder = HackRFCommandBuilder()
        lna, vga = builder._split_gain(30)
        assert lna == 30
        assert vga == 0

    def test_gain_at_lna_max(self):
        builder = HackRFCommandBuilder()
        lna, vga = builder._split_gain(40)
        assert lna == 40
        assert vga == 0

    def test_high_gain_splits_across_stages(self):
        builder = HackRFCommandBuilder()
        lna, vga = builder._split_gain(80)
        assert lna == 40
        assert vga == 40

    def test_max_combined_gain(self):
        builder = HackRFCommandBuilder()
        lna, vga = builder._split_gain(102)
        assert lna == 40
        assert vga == 62


class TestBuildAdsbCommand:
    def test_contains_soapy_device_type(self):
        builder = HackRFCommandBuilder()
        cmd = builder.build_adsb_command(_make_device(), gain=40)
        assert "--device-type" in cmd
        # readsb's SoapySDR device type is "soapy" (not "soapysdr");
        # the wrong value made readsb exit immediately for HackRF (#346).
        assert cmd[cmd.index("--device-type") + 1] == "soapy"

    def test_includes_serial_in_device_string(self):
        builder = HackRFCommandBuilder()
        cmd = builder.build_adsb_command(_make_device(serial="deadbeef"), gain=40)
        device_idx = cmd.index("--device")
        assert "deadbeef" in cmd[device_idx + 1]


class TestBuildIQCaptureCommand:
    def test_outputs_cu8_to_stdout(self):
        builder = HackRFCommandBuilder()
        cmd = builder.build_iq_capture_command(_make_device(), frequency_mhz=100.0, sample_rate=2048000, gain=40)
        assert "-F" in cmd
        assert "CU8" in cmd
        assert cmd[-1] == "-"

    def test_gain_split_in_command(self):
        builder = HackRFCommandBuilder()
        cmd = builder.build_iq_capture_command(_make_device(), frequency_mhz=100.0, gain=80)
        gain_idx = cmd.index("-g")
        assert cmd[gain_idx + 1] == "LNA=40,VGA=40"


class TestBuildHackRFTransferIQCommand:
    """Fallback I/Q capture when SoapySDR's rx_sdr is not installed."""

    def test_streams_to_stdout_with_frequency_and_rate(self):
        cmd = HackRFCommandBuilder().build_hackrf_transfer_iq_command(
            _make_device(), frequency_mhz=143.05, sample_rate=2000000
        )
        assert cmd[0].endswith("hackrf_transfer")
        assert cmd[cmd.index("-r") + 1] == "-"
        assert cmd[cmd.index("-f") + 1] == "143050000"
        assert cmd[cmd.index("-s") + 1] == "2000000"
        assert cmd[cmd.index("-d") + 1] == "abc123"

    def test_gain_rounded_to_hardware_steps(self):
        # 70 dB -> LNA 40 (8 dB steps) + VGA 30 (2 dB steps)
        cmd = HackRFCommandBuilder().build_hackrf_transfer_iq_command(_make_device(), frequency_mhz=100.0, gain=70)
        assert cmd[cmd.index("-l") + 1] == "40"
        assert cmd[cmd.index("-g") + 1] == "30"
        cmd = HackRFCommandBuilder().build_hackrf_transfer_iq_command(_make_device(), frequency_mhz=100.0, gain=21)
        assert cmd[cmd.index("-l") + 1] == "16"
        assert cmd[cmd.index("-g") + 1] == "0"

    def test_auto_gain_and_unknown_serial_add_no_flags(self):
        cmd = HackRFCommandBuilder().build_hackrf_transfer_iq_command(_make_device("N/A"), frequency_mhz=100.0)
        assert "-l" not in cmd and "-g" not in cmd and "-d" not in cmd

    def test_bias_tee(self):
        cmd = HackRFCommandBuilder().build_hackrf_transfer_iq_command(_make_device(), frequency_mhz=100.0, bias_t=True)
        assert cmd[cmd.index("-p") + 1] == "1"


class TestAISCommand:
    def test_selects_the_hackrf_by_serial_with_native_gain(self):
        cmd = HackRFCommandBuilder().build_ais_command(_make_device("abc123"), gain=55, tcp_port=10110)
        assert cmd[cmd.index("-d") + 1] == "abc123"
        assert cmd[cmd.index("-gf") + 1 : cmd.index("-gf") + 5] == ["LNA", "40", "VGA", "15"]

    @pytest.mark.parametrize("serial", ["N/A", ""])
    def test_refuses_without_a_serial(self, serial):
        # Without -d, AIS-catcher would open whichever SDR it finds first
        with pytest.raises(ValueError, match="serial"):
            HackRFCommandBuilder().build_ais_command(_make_device(serial), gain=40)

    def test_bias_t_is_warned_about_not_silently_dropped(self, monkeypatch):
        warn = MagicMock()
        monkeypatch.setattr(hackrf.logger, "warning", warn)
        cmd = HackRFCommandBuilder().build_ais_command(_make_device(), gain=40, bias_t=True)
        assert "Bias-T" in warn.call_args[0][0]
        assert "biastee" not in " ".join(cmd).lower()
