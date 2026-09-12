import subprocess

import pytest

from krzykacz.effects import FfmpegEffects

REAL_POPEN = subprocess.Popen


def running(pid: int) -> bool:
    """True if the pid is alive and not yet reaped. Zombies don't count --
    a killed-and-reaped child is exactly what we want to see."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
            state = stat.read().rsplit(") ", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return False
    return state != "Z"


def spawn_sleepers(monkeypatch, fail_on=None):
    """Replaces every subprocess.Popen with a `sleep 30`, so both halves of
    the pipeline hang. Returns the list that collects their pids. If `fail_on`
    is given, spawning that command raises instead."""
    pids = []

    def popen(cmd, **kwargs):
        if fail_on and cmd[0] == fail_on:
            raise OSError(f"{fail_on} not found")
        proc = REAL_POPEN(["sleep", "30"], **kwargs)
        pids.append(proc.pid)
        return proc

    monkeypatch.setattr(subprocess, "Popen", popen)
    return pids


def timeout_immediately(proc, timeout):
    raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)


def test_both_processes_are_killed_when_playback_times_out(monkeypatch, tmp_path):
    """Regression test: wait_or_kill(aplay) raising must not leave ffmpeg
    running. Before the try/finally, the second process survived -- and since
    a stalled ffmpeg never writes, it never gets SIGPIPE to clean itself up."""
    pids = spawn_sleepers(monkeypatch)
    monkeypatch.setattr("krzykacz.effects.wait_or_kill", timeout_immediately)

    with pytest.raises(subprocess.TimeoutExpired):
        FfmpegEffects().play(tmp_path / "whatever.ogg")

    assert len(pids) == 2, "expected both ffmpeg and aplay to be spawned"
    for pid in pids:
        assert not running(pid), f"pid {pid} was left running after the timeout"


def test_aplay_spawn_failure_kills_ffmpeg(monkeypatch, tmp_path):
    """If aplay can't start at all, the already-spawned ffmpeg must not leak."""
    pids = spawn_sleepers(monkeypatch, fail_on="aplay")

    with pytest.raises(OSError):
        FfmpegEffects().play(tmp_path / "whatever.ogg")

    assert len(pids) == 1
    assert not running(pids[0]), "ffmpeg was left running after aplay failed to start"
