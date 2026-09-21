from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterator, Optional, Tuple

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


# Ships as the default so the espeak-ng voices work without any configuration.
# The wire names are aliases because the protocol's voice-name alphabet has no
# "+"; each value is an espeak-ng language plus a variant (see
# `espeak-ng --voices=variant`).
DEFAULT_ESPEAK_VOICES: Dict[str, str] = {
    "espeak_male": "pl+m3",
    "espeak_male2": "pl+m1",
    "espeak_male3": "pl+m7",
    "espeak_female": "pl+f3",
    "espeak_female2": "pl+f1",
    "espeak_female3": "pl+f5",
    "espeak_whisper": "pl+whisper",
    "espeak_croak": "pl+croak",
    "espeak_announcer": "pl+announcer",
    "espeak_robot": "pl+klatt2",
}


def _split_entries(raw: Optional[str]) -> Iterator[Tuple[str, str]]:
    """Yields (name, value) from "name1=value1,name2=value2". Blank input and
    entries without an "=" yield nothing -- a typo in one extra voice
    shouldn't crash startup."""
    if not raw:
        return
    for part in raw.split(","):
        name, sep, value = part.strip().partition("=")
        if sep and name.strip():
            yield name.strip(), value.strip()


def _parse_voices(raw: Optional[str]) -> Dict[str, VoiceSpec]:
    """Parses "name1=/path1.onnx,name2=/path2.onnx:3" into a dict. A value
    ending in ":<digits>" addresses a specific embedded speaker index within
    a multi-speaker model (e.g. hvsr-robotics/tts-pl-piper-v2 -- one .onnx
    file, several named voices sharing it via a different index each);
    anything else is an ordinary single-speaker model path."""
    voices: Dict[str, VoiceSpec] = {}
    for name, value in _split_entries(raw):
        path, colon, speaker = value.rpartition(":")
        if colon and speaker.isdigit():
            voices[name] = VoiceSpec(path, int(speaker))
        else:
            voices[name] = VoiceSpec(value, None)
    return voices


def _parse_espeak_voices(raw: Optional[str]) -> Dict[str, str]:
    """Parses "espeak_male=pl+m3,espeak_female=pl+f3". Unset means the
    built-in set; an explicitly empty value means no espeak-ng voices."""
    if raw is None:
        return dict(DEFAULT_ESPEAK_VOICES)
    return {name: spec for name, spec in _split_entries(raw) if spec}


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
    espeak_voices: Dict[str, str]
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
            espeak_voices=_parse_espeak_voices(_env("KRZYKACZ_ESPEAK_VOICES")),
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
