import subprocess

import pytest

from krzykacz.procutil import communicate_or_kill, wait_or_kill


def sleeper():
    return subprocess.Popen(
        ["sleep", "30"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL
    )


def test_communicate_or_kill_kills_hung_process():
    proc = sleeper()
    with pytest.raises(subprocess.TimeoutExpired):
        communicate_or_kill(proc, b"", timeout=0.1)
    assert proc.poll() is not None, "process was left running after timeout"


def test_wait_or_kill_kills_hung_process():
    proc = sleeper()
    with pytest.raises(subprocess.TimeoutExpired):
        wait_or_kill(proc, timeout=0.1)
    assert proc.poll() is not None, "process was left running after timeout"


def test_communicate_or_kill_passes_through_on_success():
    proc = subprocess.Popen(["cat"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    communicate_or_kill(proc, b"hello", timeout=5)
    assert proc.returncode == 0


def test_wait_or_kill_passes_through_on_success():
    proc = subprocess.Popen(["true"])
    wait_or_kill(proc, timeout=5)
    assert proc.returncode == 0
