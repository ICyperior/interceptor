"""drain_in_background keeps a chatty process from blocking on a full stderr pipe."""

import subprocess
import sys

from utils.process import drain_in_background


def test_process_writing_lots_to_stderr_still_finishes():
    # ~1 MB of stderr: far more than a pipe buffer holds, so without draining
    # the child blocks on write and never exits (as hackrf_transfer would)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys\nfor _ in range(16384): sys.stderr.write('x' * 64)\nprint('done')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    drain_in_background(proc.stderr)
    assert proc.stdout.read().strip() == b"done"
    assert proc.wait(timeout=20) == 0
