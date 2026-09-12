from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


def _parse_voices(raw: Optional[str]) -> Dict[str, str]:
    """Parses "name1=/path1.onnx,name2=/path2.onnx" into a dict. Blank input
    yields an empty dict; malformed entries (no "=") are skipped with a
    warning rather than crashing startup over a typo in one extra voice."""
    if not raw:
        return {}
    voices: Dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, path = part.partition("=")
        if not sep:
            continue
        voices[name.strip()] = path.strip()
    return voices


@dataclass
class Config:
    ntfy_server: str
    topic: str

    light_backend: str
    uhubctl_location: str
    uhubctl_port: str

    tts_backend: str
    piper_default_voice: str
    piper_model: str
    piper_extra_voices: Dict[str, str]
    espeak_voice: str
    alsa_device: Optional[str]

    effects_backend: str
    assets_dir: str
    history_size: int
    queue_size: int

    @property
    def piper_voices(self) -> Dict[str, str]:
        voices = {self.piper_default_voice: self.piper_model}
        voices.update(self.piper_extra_voices)
        return voices

    @classmethod
    def from_env(cls) -> "Config":
        topic = os.environ.get("KRZYKACZ_TOPIC")
        if not topic:
            raise RuntimeError("KRZYKACZ_TOPIC is required")

        return cls(
            ntfy_server=_env("KRZYKACZ_NTFY_SERVER", "https://ntfy.sh"),
            topic=topic,
            light_backend=_env("KRZYKACZ_LIGHT", "uhubctl"),
            uhubctl_location=_env("KRZYKACZ_UHUBCTL_LOC", "1-1"),
            uhubctl_port=_env("KRZYKACZ_UHUBCTL_PORT", "2"),
            tts_backend=_env("KRZYKACZ_TTS", "piper"),
            piper_default_voice=_env("KRZYKACZ_PIPER_DEFAULT_VOICE", "darkman"),
            piper_model=_env(
                "KRZYKACZ_PIPER_MODEL",
                "/var/lib/krzykacz/voices/pl_PL-darkman-medium.onnx",
            ),
            piper_extra_voices=_parse_voices(_env("KRZYKACZ_PIPER_VOICES")),
            espeak_voice=_env("KRZYKACZ_ESPEAK_VOICE", "pl"),
            alsa_device=_env("KRZYKACZ_ALSA_DEVICE"),
            effects_backend=_env("KRZYKACZ_EFFECTS", "ffmpeg"),
            assets_dir=_env("KRZYKACZ_ASSETS_DIR", "/var/lib/krzykacz/assets"),
            history_size=int(_env("KRZYKACZ_HISTORY", "10")),
            queue_size=int(_env("KRZYKACZ_QUEUE_SIZE", "10")),
        )
