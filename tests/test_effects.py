import subprocess

import pytest

from krzykacz.effects import FfmpegEffects, NullEffects
from krzykacz.procutil import PLAYBACK_TIMEOUT_SLACK_S

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


def spawn_sleeper(monkeypatch, fail_on=None):
    """Replaces subprocess.Popen with a `sleep 30`, so it hangs. Returns the
    list that collects its pid(s). If `fail_on` is given, spawning that
    command raises instead."""
    pids = []

    def popen(cmd, **kwargs):
        if fail_on and cmd[0] == fail_on:
            raise OSError(f"{fail_on} not found")
        proc = REAL_POPEN(["sleep", "30"], **kwargs)
        pids.append(proc.pid)
        return proc

    monkeypatch.setattr(subprocess, "Popen", popen)
    return pids


def test_decode_runs_ffmpeg_with_the_fixed_output_format_and_returns_its_stdout(
    monkeypatch, tmp_path
):
    captured = []

    class FakeResult:
        stdout = b"pcm-bytes"

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return FakeResult()

    monkeypatch.setattr("krzykacz.effects.subprocess.run", fake_run)

    result = FfmpegEffects().decode(tmp_path / "boom.ogg")

    assert result == b"pcm-bytes"
    assert captured[0] == [
        "ffmpeg",
        "-loglevel",
        "error",
        "-i",
        str(tmp_path / "boom.ogg"),
        "-f",
        "s16le",
        "-ar",
        str(FfmpegEffects.SAMPLE_RATE),
        "-ac",
        str(FfmpegEffects.CHANNELS),
        "-",
    ]


def test_decode_is_killed_and_reaped_on_timeout(monkeypatch, tmp_path):
    """Regression test: subprocess.run(timeout=...) must kill and reap the
    hung ffmpeg itself -- a stalled decode must not leak a process holding
    stdout open forever."""
    pids = spawn_sleeper(monkeypatch)
    monkeypatch.setattr("krzykacz.effects.DECODE_TIMEOUT_S", 0.05)

    with pytest.raises(subprocess.TimeoutExpired):
        FfmpegEffects().decode(tmp_path / "whatever.ogg")

    assert len(pids) == 1
    assert not running(pids[0]), "ffmpeg was left running after decode timed out"


def test_decode_spawn_failure_propagates(monkeypatch, tmp_path):
    spawn_sleeper(monkeypatch, fail_on="ffmpeg")

    with pytest.raises(OSError):
        FfmpegEffects().decode(tmp_path / "whatever.ogg")


def test_play_pcm_sizes_timeout_from_audio_length(monkeypatch):
    # The actual kill-on-timeout mechanism is communicate_or_kill's own --
    # covered directly (and fast, with a real hung process) by
    # test_procutil.test_communicate_or_kill_kills_hung_process. This only
    # checks that play_pcm feeds it the right process and timeout.
    captured = []

    class FakePopen:
        def __init__(self, *args, **kwargs):
            pass

    def fake_communicate_or_kill(proc, data, timeout):
        captured.append((data, timeout))

    monkeypatch.setattr("krzykacz.effects.subprocess.Popen", FakePopen)
    monkeypatch.setattr("krzykacz.effects.communicate_or_kill", fake_communicate_or_kill)

    five_seconds = b"\x00" * (FfmpegEffects.SAMPLE_RATE * 2 * FfmpegEffects.CHANNELS * 5)
    FfmpegEffects().play_pcm(five_seconds)

    data, timeout = captured[0]
    assert data == five_seconds
    assert timeout == pytest.approx(5.0 + PLAYBACK_TIMEOUT_SLACK_S, abs=0.1)


def test_play_pcm_spawn_failure_propagates(monkeypatch):
    spawn_sleeper(monkeypatch, fail_on="aplay")

    with pytest.raises(OSError):
        FfmpegEffects().play_pcm(b"")


def test_play_decodes_then_plays(monkeypatch, tmp_path):
    """play() is decode() -> play_pcm() -- the ABC's default composition."""
    calls = []

    class RecordingEffects(FfmpegEffects):
        def decode(self, path):
            calls.append(("decode", path))
            return b"pcm-bytes"

        def play_pcm(self, data):
            calls.append(("play_pcm", data))

    RecordingEffects().play(tmp_path / "boom.ogg")

    assert calls == [("decode", tmp_path / "boom.ogg"), ("play_pcm", b"pcm-bytes")]


def test_null_effects_decode_returns_no_audio_and_logs(tmp_path, caplog):
    with caplog.at_level("INFO"):
        result = NullEffects().decode(tmp_path / "boom.ogg")

    assert result == b""
    assert str(tmp_path / "boom.ogg") in caplog.text


def test_null_effects_play_pcm_does_nothing():
    NullEffects().play_pcm(b"whatever")  # must not raise
