"""Tests for shared conftest fixtures."""

import subprocess
from unittest.mock import patch


class TestFakeProcess:
    def test_works_with_subprocess_run(self, fake_process):
        """subprocess.run() must unpack communicate() and enter the context manager."""
        proc = fake_process(stdout="hello", stderr="", returncode=0)
        with patch("subprocess.Popen", return_value=proc):
            result = subprocess.run(["anything"], capture_output=True, text=True, timeout=5)
        assert result.stdout == "hello"

    def test_running_process_defaults(self, fake_process):
        proc = fake_process()
        assert proc.poll() is None  # still running
        assert proc.pid == 12345
        assert proc.communicate() == ("", "")

    def test_exited_process_detected_via_run(self, fake_process):
        """An exited process's returncode and stderr surface through subprocess.run."""
        proc = fake_process(running=False, returncode=1, stdout=b"", stderr=b"device busy")
        with patch("subprocess.Popen", return_value=proc):
            result = subprocess.run(["anything"], capture_output=True)
        assert result.returncode == 1
        assert result.stderr == b"device busy"
