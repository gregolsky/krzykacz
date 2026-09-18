from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .protocol import RHYTHM_RANGE, SPEED_RANGE, VARIATION_RANGE, clean_scale
from .tts import Prosody, VoiceSpec


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


def _env_bool(name: str) -> bool:
    return _env(name, "0").strip().lower() in ("1", "true", "yes", "on")


def _env_scale(name: str, bounds: Tuple[float, float]) -> Optional[float]:
    """An unset (or unparseable) synthesis knob stays None, which means "pass
    no flag and let piper use its own default" -- validated and clamped the
    same way a per-message tag is."""
    return clean_scale(_env(name), bounds)


def _parse_voices(raw: Optional[str]) -> Dict[str, VoiceSpec]:
    """Parses "name1=/path1.onnx,name2=/path2.onnx:3" into a dict. A value
    ending in ":<digits>" addresses a specific embedded speaker index within
    a multi-speaker model (e.g. hvsr-robotics/tts-pl-piper-v2 -- one .onnx
    file, several named voices sharing it via a different index each);
    anything else is an ordinary single-speaker model path. Blank input
    yields an empty dict; malformed entries (no "=") are skipped with a
    warning rather than crashing startup over a typo in one extra voice."""
    if not raw:
        return {}
    voices: Dict[str, VoiceSpec] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, value = part.partition("=")
        if not sep:
            continue
        value = value.strip()
        path, colon, speaker = value.rpartition(":")
        if colon and speaker.isdigit():
            voices[name.strip()] = VoiceSpec(path, int(speaker))
        else:
            voices[name.strip()] = VoiceSpec(value, None)
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
    piper_extra_voices: Dict[str, VoiceSpec]
    espeak_voice: str
    alsa_device: Optional[str]
    prosody: Prosody

    effects_backend: str
    assets_dir: str
    history_size: int
    queue_size: int

    cache_dir: str
    cache_ttl: float
    cache_max_mb: int

    http_enabled: bool
    http_host: str
    http_port: int

    mcp_enabled: bool
    mcp_host: str
    mcp_port: int

    auth_token: Optional[str]
    rate_limit_interval: float

    @property
    def piper_voices(self) -> Dict[str, VoiceSpec]:
        voices: Dict[str, VoiceSpec] = {self.piper_default_voice: VoiceSpec(self.piper_model, None)}
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
            prosody=Prosody(
                speed=_env_scale("KRZYKACZ_PIPER_SPEED", SPEED_RANGE),
                variation=_env_scale("KRZYKACZ_PIPER_VARIATION", VARIATION_RANGE),
                rhythm=_env_scale("KRZYKACZ_PIPER_RHYTHM", RHYTHM_RANGE),
            ),
            effects_backend=_env("KRZYKACZ_EFFECTS", "ffmpeg"),
            assets_dir=_env("KRZYKACZ_ASSETS_DIR", "/var/lib/krzykacz/assets"),
            history_size=int(_env("KRZYKACZ_HISTORY", "10")),
            queue_size=int(_env("KRZYKACZ_QUEUE_SIZE", "10")),
            # Matches CacheDirectory=krzykacz in the systemd unit, which is
            # what makes this path writable under ProtectSystem=strict.
            cache_dir=_env("KRZYKACZ_CACHE_DIR", "/var/cache/krzykacz"),
            cache_ttl=float(_env("KRZYKACZ_CACHE_TTL", "86400")),
            cache_max_mb=int(_env("KRZYKACZ_CACHE_MAX_MB", "200")),
            http_enabled=_env_bool("KRZYKACZ_HTTP_ENABLED"),
            http_host=_env("KRZYKACZ_HTTP_HOST", "0.0.0.0"),
            http_port=int(_env("KRZYKACZ_HTTP_PORT", "8123")),
            mcp_enabled=_env_bool("KRZYKACZ_MCP_ENABLED"),
            mcp_host=_env("KRZYKACZ_MCP_HOST", "0.0.0.0"),
            mcp_port=int(_env("KRZYKACZ_MCP_PORT", "8124")),
            auth_token=_env("KRZYKACZ_AUTH_TOKEN"),
            rate_limit_interval=float(_env("KRZYKACZ_RATE_LIMIT_INTERVAL", "10")),
        )
