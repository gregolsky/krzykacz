from __future__ import annotations

import subprocess
from typing import Optional


def communicate_or_kill(proc: "subprocess.Popen[bytes]", input: Optional[bytes], timeout: float) -> None:
    """Like Popen.communicate(), but kills the process on timeout instead of
    leaving it running. Plain communicate(timeout=...) raises TimeoutExpired
    without reaping the child -- for aplay/ffmpeg that means an orphaned
    process left holding the ALSA device, silently blocking every playback
    after it."""
    try:
        proc.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise


def wait_or_kill(proc: "subprocess.Popen[bytes]", timeout: float) -> None:
    """Like Popen.wait(), but kills the process on timeout instead of
    leaving it running (see communicate_or_kill)."""
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
