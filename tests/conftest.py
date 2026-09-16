from krzykacz.config import Config


class FakeAnnouncer:
    def __init__(self, accept=True):
        self.submitted = []
        self._accept = accept
        self.playing = None

    def submit(self, envelope):
        self.submitted.append(envelope)
        return self._accept

    def snapshot(self):
        return {"playing": self.playing, "pending": list(self.submitted)}


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
        alsa_device=None,
        effects_backend="null",
        assets_dir="/tmp",
        history_size=10,
        queue_size=10,
        http_enabled=True,
        http_host="127.0.0.1",
        http_port=0,
        mcp_enabled=False,
        mcp_host="127.0.0.1",
        mcp_port=0,
        auth_token=None,
    )
    defaults.update(overrides)
    return Config(**defaults)
