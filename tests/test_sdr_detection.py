"""Tests for SDR detection parsing (RTL-SDR and HackRF)."""

from unittest.mock import MagicMock, patch

import pytest

import utils.sdr.detection as detection_mod
from utils.sdr.base import SDRType
from utils.sdr.detection import detect_hackrf_devices, detect_rtlsdr_devices


@pytest.fixture(autouse=True)
def _clear_detection_caches():
    """Reset detection caches before each test."""
    detection_mod._hackrf_cache = []
    detection_mod._hackrf_cache_ts = 0.0
    yield


# ---- RTL-SDR detection tests (Popen-based) ----

_RTL_SERIAL_OUTPUT = [
    "Found 3 device(s):",
    "  0:  ??C?, , SN:",
    "  1:  ??C?, , SN:",
    "  2:  RTLSDRBlog, Blog V4, SN: 1",
]


def _make_rtl_test_mock(device_lines, *, exit_after_lines=None):
    """Return a Popen mock for rtl_test -t.

    stderr emits ``device_lines`` via readline() then returns ``""``.
    ``exit_after_lines`` (optional int) makes poll() return a code after
    that many readline calls.
    """
    mock = MagicMock()
    mock.poll.return_value = None
    mock.wait.return_value = 0
    mock.returncode = None
    mock.send_signal = MagicMock()

    stderr = MagicMock()
    idx = {"i": 0}

    def readline():
        if idx["i"] < len(device_lines):
            idx["i"] += 1
            return device_lines[idx["i"] - 1] + "\n"
        return ""

    stderr.readline = readline
    stderr.fileno.return_value = 999
    stderr.read.return_value = ""  # text=True → strings, not bytes
    mock.stderr = stderr
    mock.stdout = MagicMock()
    mock.stdout.readline = lambda: ""
    mock.stdout.read.return_value = ""  # text=True → strings, not bytes

    if exit_after_lines is not None:
        def poll_with_exit():
            if idx["i"] >= exit_after_lines:
                mock.returncode = 1
                return 1
            return None

        mock.poll = poll_with_exit

    return mock


def _make_select_patch():
    """Patch ``select.select`` to always report fd 999 as ready."""
    def select_side_effect(rlist, wlist, xlist, timeout=None):
        if 999 in rlist:
            return ([999], [], [])
        return ([], [], [])
    return patch("select.select", side_effect=select_side_effect)


def _popen_and_select_patch(device_lines, *, exit_after_lines=None):
    """Return a context-manager tuple for patching Popen + select."""
    mock = _make_rtl_test_mock(device_lines, exit_after_lines=exit_after_lines)
    return (
        patch("subprocess.Popen", return_value=mock),
        _make_select_patch(),
    )


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/rtl_test")
def test_detect_rtlsdr_devices_filters_empty_serial_entries(_mock_tool_path):
    """Ignore malformed rtl_test rows that have an empty SN field."""
    popen_patch, select_patch = _popen_and_select_patch(_RTL_SERIAL_OUTPUT)
    with popen_patch, select_patch:
        devices = detect_rtlsdr_devices()

    assert len(devices) == 1
    assert devices[0].sdr_type == SDRType.RTL_SDR
    assert devices[0].index == 2
    assert devices[0].name == "RTLSDRBlog, Blog V4"
    assert devices[0].serial == "1"


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/rtl_test")
def test_detect_rtlsdr_devices_uses_replace_decode_mode(_mock_tool_path):
    """Run rtl_test with tolerant decoding for malformed output bytes."""
    popen_patch, select_patch = _popen_and_select_patch(["Found 0 device(s):"])
    with popen_patch as popen_mock, select_patch:
        detect_rtlsdr_devices()

    _, kwargs = popen_mock.call_args
    assert kwargs["text"] is True
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/rtl_test")
def test_detect_rtlsdr_devices_gathers_all_devices_before_sigint(_mock_tool_path):
    """All announced devices are collected before we SIGINT rtl_test.

    Regression: rtl_test emits each device line only after opening that dongle
    (~50-100ms apart), so breaking on the first line and sending SIGINT dropped
    every device after the first — INTERCEPT saw one dongle when two were
    connected. We must read until all "Found N" devices have arrived.
    """
    mock = _make_rtl_test_mock([
        "Found 2 device(s):",
        "  0:  RTLSDRBlog, Blog V4, SN: 10000000",
        "  1:  RTLSDRBlog, Blog V4, SN: 20000000",
    ])
    mock.poll.return_value = None  # stays running -> we'll SIGINT it
    with patch("subprocess.Popen", return_value=mock), \
         _make_select_patch():
        devices = detect_rtlsdr_devices()

    assert len(devices) == 2
    assert [d.serial for d in devices] == ["10000000", "20000000"]
    assert [d.index for d in devices] == [0, 1]
    # We still stop as soon as the last device is seen rather than waiting for
    # the benchmark loop, so rtl_test is SIGINT'd, not left to run.
    mock.send_signal.assert_called()


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/rtl_test")
def test_detect_rtlsdr_devices_stops_on_benchmark_start(_mock_tool_path):
    """If the 'Found N' header is missing, stop when the benchmark loop starts."""
    mock = _make_rtl_test_mock([
        "  0:  RTLSDRBlog, Blog V4, SN: 10000000",
        "  1:  RTLSDRBlog, Blog V4, SN: 20000000",
        "Using device 0: Generic RTL2832U OEM",
    ])
    mock.poll.return_value = None
    with patch("subprocess.Popen", return_value=mock), \
         _make_select_patch():
        devices = detect_rtlsdr_devices()

    assert [d.serial for d in devices] == ["10000000", "20000000"]


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/rtl_test")
def test_detect_rtlsdr_devices_handles_process_exit_without_sigint(_mock_tool_path):
    """If rtl_test exits on its own before we send SIGINT, output is still parsed."""
    mock = _make_rtl_test_mock([
        "Found 1 device(s):",
        "  0:  Realtek, RTL2838UHIDIR, SN: DEADBEEF",
    ], exit_after_lines=2)
    mock.wait.return_value = 1
    with patch("subprocess.Popen", return_value=mock), \
         _make_select_patch():
        devices = detect_rtlsdr_devices()

    assert len(devices) == 1
    assert devices[0].serial == "DEADBEEF"


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/rtl_test")
def test_detect_rtlsdr_devices_returns_empty_when_no_device_found(_mock_tool_path):
    """No devices found -> empty list, no exception."""
    popen_patch, select_patch = _popen_and_select_patch(["Found 0 device(s):"])
    with popen_patch, select_patch:
        devices = detect_rtlsdr_devices()

    assert devices == []


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/rtl_test")
def test_detect_rtlsdr_devices_falls_back_to_count_when_no_serial(_mock_tool_path):
    """When serials are unparseable, fall back to 'Found N device' count."""
    popen_patch, select_patch = _popen_and_select_patch([
        "Found 2 device(s):",
        "  0:  Realtek, RTL2838UHIDIR, SN: ",
        "  1:  Realtek, RTL2838UHIDIR, SN: ",
    ])
    with popen_patch, select_patch:
        devices = detect_rtlsdr_devices()

    assert len(devices) == 2
    assert all(d.serial == "Unknown" for d in devices)


# ---- HackRF detection tests ----

HACKRF_INFO_OUTPUT = (
    "hackrf_info version: 2024.02.1\n"
    "libhackrf version: 2024.02.1 (0.9)\n"
    "Found HackRF\n"
    "Index: 0\n"
    "Serial number: 0000000000000000a06063c8234e925f\n"
    "Board ID Number: 2 (HackRF One)\n"
    "Firmware Version: 2024.02.1 (API:1.08)\n"
    "Part ID Number: 0xa000cb3c 0x00614764\n"
    "Hardware Revision: r9\n"
    "Hardware supported by installed firmware:\n"
    "    HackRF One\n"
)


def _make_hackrf_mock(stdout_text, stderr_text="", returncode=0):
    """Return a MagicMock for subprocess.run that yields the given output."""
    mock_result = MagicMock()
    mock_result.stdout = stdout_text
    mock_result.stderr = stderr_text
    mock_result.returncode = returncode
    return mock_result


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/hackrf_info")
@patch("utils.sdr.detection.subprocess.run")
def test_detect_hackrf_from_stdout(mock_run, _mock_tool_path):
    """Parse HackRF device info from stdout."""
    mock_run.return_value = _make_hackrf_mock(HACKRF_INFO_OUTPUT)

    devices = detect_hackrf_devices()

    assert len(devices) == 1
    assert devices[0].sdr_type == SDRType.HACKRF
    assert devices[0].name == "HackRF One"
    assert devices[0].serial == "0000000000000000a06063c8234e925f"
    assert devices[0].index == 0


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/hackrf_info")
@patch("utils.sdr.detection.subprocess.run")
def test_detect_hackrf_from_stderr(mock_run, _mock_tool_path):
    """Parse HackRF device info when output goes to stderr (newer firmware)."""
    mock_run.return_value = _make_hackrf_mock("", HACKRF_INFO_OUTPUT)

    devices = detect_hackrf_devices()

    assert len(devices) == 1
    assert devices[0].sdr_type == SDRType.HACKRF
    assert devices[0].name == "HackRF One"
    assert devices[0].serial == "0000000000000000a06063c8234e925f"


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/hackrf_info")
@patch("utils.sdr.detection.subprocess.run")
def test_detect_hackrf_nonzero_exit_with_valid_output(mock_run, _mock_tool_path):
    """Parse HackRF info even when hackrf_info exits non-zero (device busy)."""
    mock_run.return_value = _make_hackrf_mock("", HACKRF_INFO_OUTPUT, returncode=1)

    devices = detect_hackrf_devices()

    assert len(devices) == 1
    assert devices[0].name == "HackRF One"


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/hackrf_info")
@patch("utils.sdr.detection.subprocess.run")
def test_detect_hackrf_fallback_no_serial(mock_run, _mock_tool_path):
    """Fallback detection when serial is missing but 'Found HackRF' present."""
    mock_run.return_value = _make_hackrf_mock(
        "Found HackRF\nBoard ID Number: 2 (HackRF One)\n"
    )

    devices = detect_hackrf_devices()

    assert len(devices) == 1
    assert devices[0].name == "HackRF One"
    assert devices[0].serial == "Unknown"


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/hackrf_info")
@patch("utils.sdr.detection.subprocess.run")
def test_detect_hackrf_parses_legacy_serial_format(mock_run, _mock_tool_path):
    """Accept legacy 'Serial Number' casing and spaced hex format."""
    mock_run.return_value = _make_hackrf_mock(
        "Found HackRF\n"
        "Index: 0\n"
        "Serial Number: 0x00000000 00000000 a06063c8 234e925f\n"
        "Board ID Number: 3 (HackRF Pro)\n"
    )

    devices = detect_hackrf_devices()

    assert len(devices) == 1
    assert devices[0].name == "HackRF Pro"
    assert devices[0].serial == "0000000000000000a06063c8234e925f"
