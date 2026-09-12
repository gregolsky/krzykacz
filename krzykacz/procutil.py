from __future__ import annotations

import subprocess
from typing import List, Optional


def aplay_cmd(alsa_device: Optional[str]) -> List[str]:
    """Builds an `aplay` invocation for audio that carries its own header
    (e.g. the WAV that espeak-ng writes). Shared so KRZYKACZ_ALSA_DEVICE is
    honoured identically everywhere we play sound."""
    cmd = ["aplay", "-q"]
    if alsa_device:
        cmd += ["-D", alsa_device]
    return cmd


def aplay_raw_cmd(alsa_device: Optional[str], sample_rate: int, channels: int) -> List[str]:
    """Builds an `aplay` invocation for headerless PCM, which is what both
    `piper --output-raw` and our ffmpeg decode produce."""
    return aplay_cmd(alsa_device) + [
        "-f", "S16_LE", "-r", str(sample_rate), "-c", str(channels), "-t", "raw",
    ]


def communicate_or_kill(
    proc: "subprocess.Popen[bytes]", data: Optional[bytes], timeout: float
) -> None:
    """Like Popen.communicate(), but kills the process on timeout instead of
    leaving it running. Plain communicate(timeout=...) raises TimeoutExpired
    without reaping the child -- for aplay/ffmpeg that means an orphaned
    process left holding the ALSA device, silently blocking every playback
    after it."""
    try:
        proc.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill(proc)
        raise


def wait_or_kill(proc: "subprocess.Popen[bytes]", timeout: float) -> None:
    """Like Popen.wait(), but kills the process on timeout instead of
    leaving it running (see communicate_or_kill)."""
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill(proc)
        raise


def kill(proc: "subprocess.Popen[bytes]") -> None:
    """Kills and reaps a process, ignoring one that already exited. Safe to
    call unconditionally in a finally block.

    Uses wait() rather than communicate() to reap: in a pipeline we hand one
    process's stdout to the next and close our own copy, and communicate()
    would try to read that closed pipe and raise ValueError. The process has
    been SIGKILLed, so it cannot be blocked on a full pipe -- wait() is enough."""
    if proc.poll() is not None:
        return
    proc.kill()
    proc.wait()
