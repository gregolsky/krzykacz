import subprocess

from krzykacz.light import UhubctlLight


def test_uhubctl_light_switches_the_configured_port(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    light = UhubctlLight("1-1", "2")
    light.on()
    light.off()

    assert calls == [
        ["uhubctl", "-l", "1-1", "-p", "2", "-a", "on"],
        ["uhubctl", "-l", "1-1", "-p", "2", "-a", "off"],
    ]


def test_uhubctl_failure_is_logged_not_raised(monkeypatch, caplog):
    def fail(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "run", fail)

    UhubctlLight("1-1", "2").off()  # a dark lamp must not stop the message

    assert "uhubctl -a off failed" in caplog.text


def test_missing_uhubctl_binary_is_logged_not_raised(monkeypatch, caplog):
    def missing(cmd, **kw):
        raise FileNotFoundError("uhubctl")

    monkeypatch.setattr(subprocess, "run", missing)

    UhubctlLight("1-1", "2").on()

    assert "uhubctl -a on failed" in caplog.text
