from krzykacz.config import Config
from krzykacz.metadata import Control, Describers
from krzykacz.random_picks import Pickers
from krzykacz.tts import Prosody


class FakeAnnouncer:
    def __init__(self, accept=True):
        self.submitted = []
        self._accept = accept
        self.playing = None
        self.muted = False

    def submit(self, envelope):
        self.submitted.append(envelope)
        return self._accept

    def snapshot(self):
        return {"playing": self.playing, "pending": list(self.submitted), "muted": self.muted}

    def set_muted(self, muted):
        self.muted = muted

    @property
    def control(self):
        return Control(
            snapshot=self.snapshot, set_muted=self.set_muted, is_muted=lambda: self.muted
        )


def make_config(**overrides):
    defaults = dict(
        ntfy_server="https://ntfy.sh",
        topic="test",
        light_backend="null",
        uhubctl_location="1-1",
        uhubctl_port="2",
        tts_backend="espeak",
        piper_default_voice="darkman",
        piper_model="unused.onnx",
        piper_extra_voices={},
        espeak_voice="pl",
        espeak_voices={},
        alsa_device=None,
        prosody=Prosody(),
        effects_backend="null",
        assets_dir="/tmp",
        history_size=10,
        queue_size=10,
        cache_dir="/tmp/krzykacz-test-cache",
        cache_ttl=0,  # disabled unless a test opts in -- keeps tests off the disk
        cache_max_mb=200,
        http_enabled=True,
        http_host="127.0.0.1",
        http_port=0,
        mcp_enabled=False,
        mcp_host="127.0.0.1",
        mcp_port=0,
        auth_token=None,
        rate_limit_interval=0,
    )
    defaults.update(overrides)
    return Config(**defaults)


def make_describers(voices=None, effects=None, limits=None):
    """A Describers bundle with stub views, so a test only has to spell out
    the one it actually asserts on."""
    return Describers(
        voices=voices or (lambda: {"tts": "espeak", "voices": ["pl"], "default_voice": "pl"}),
        effects=effects or (lambda: {"effects": []}),
        limits=limits or (lambda: {"max_repeat": 10, "queue_size": 10}),
    )


def make_pickers(sound=None, curse=None):
    """A Pickers bundle with stub actions, so a test only has to spell out
    the one it actually asserts on. Defaults return None (nothing to play),
    matching an instance with no curated sounds/voices configured."""
    return Pickers(
        sound=sound or (lambda: None),
        curse=curse or (lambda intensity, style: None),
    )
