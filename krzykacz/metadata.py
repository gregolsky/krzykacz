from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence

from .protocol import (
    MAX_CONTENT_BYTES,
    MAX_EFFECT_REPEAT,
    MAX_EFFECTS,
    MAX_REPEAT_COUNT,
    MAX_SPOKEN_BYTES,
    RHYTHM_RANGE,
    SPEED_RANGE,
    VARIATION_RANGE,
    Envelope,
    Repeat,
)

logger = logging.getLogger(__name__)

# A read-only view of this instance, rendered fresh on each call: the
# describe_* functions below, with their instance-specific arguments already
# bound (see krzykacz.__main__).
Describer = Callable[[], Dict[str, object]]


class Control(NamedTuple):
    """What a transport can do to the running announcer besides submitting to
    it: read what's queued (and whether it's muted), and switch mute. Bundled
    like Describers so both transports are handed the same instance's
    controls, bound methods of krzykacz.announcer.Announcer."""

    snapshot: Callable[[], Dict[str, object]]
    set_muted: Callable[[bool], None]
    # Separate from `snapshot` so a transport explaining a refused submit
    # reads one flag instead of copying the whole queue under its lock.
    is_muted: Callable[[], bool]


class Describers(NamedTuple):
    """The read-only views this instance exposes, one callable per concern.
    Bundled so a transport takes a single argument rather than one per view,
    and so both transports are guaranteed to be describing the same
    instance."""

    voices: Describer
    effects: Describer
    limits: Describer


def list_effects(assets_dir: str) -> List[str]:
    """Sorted names of the sound-effect files available to the `<file>` tag.

    Every regular file counts -- the effects backend plays anything ffmpeg can
    decode, so filtering by extension here would hide usable files. A missing
    or unreadable assets directory yields an empty list rather than failing the
    request; it just means this instance has no effects installed."""
    try:
        return sorted(entry.name for entry in Path(assets_dir).iterdir() if entry.is_file())
    except OSError as exc:
        logger.warning("Cannot list effects in %s: %s", assets_dir, exc)
        return []


def describe_voices(
    tts_backend: str, voices: Sequence[str], default_voice: str
) -> Dict[str, object]:
    """What can be passed as `voice`.

    `tts` matters to callers because it decides how `voice` is interpreted --
    piper voice names from the configured voice map, versus espeak-ng
    language codes passed straight through. Multi-speaker models contribute
    one name each (see krzykacz.tts.VoiceSpec), so this is a flat list
    regardless of how many .onnx files back it."""
    return {
        "tts": tts_backend,
        "voices": list(voices),
        "default_voice": default_voice,
    }


def describe_effects(assets_dir: str) -> Dict[str, object]:
    """What can be named in a `<file>` tag. Read fresh from disk on every
    call, so effects added by download_effects.sh show up without a
    restart."""
    return {"effects": list_effects(assets_dir)}


def describe_limits(
    history_size: int,
    queue_size: int,
    rate_limit_interval: float,
    curse_intensities: Sequence[str],
    curse_styles: Sequence[str],
) -> Dict[str, object]:
    """The numbers a caller would otherwise have to hardcode from the README:
    how long a message may be, how far `repeat` and `replay` reach, how often
    it may call at all, and the valid random_curse filter values.

    `curse_intensities`/`curse_styles` are passed in rather than imported
    from krzykacz.random_picks directly -- that module already imports
    list_effects from this one, and importing back would cycle."""
    return {
        "max_content_bytes": MAX_CONTENT_BYTES,
        "max_spoken_bytes": MAX_SPOKEN_BYTES,
        "max_repeat": MAX_REPEAT_COUNT,
        # How many effect tags (a "<name*N>" suffix counted as N) one message
        # may pack in, and how high N itself may go -- see
        # krzykacz.protocol.split_segments.
        "max_effects": MAX_EFFECTS,
        "max_effect_repeat": MAX_EFFECT_REPEAT,
        "history_size": history_size,
        "queue_size": queue_size,
        "rate_limit_interval": rate_limit_interval,
        # Accepted ranges for the delivery knobs; a value outside one is
        # clamped, not rejected.
        "speed_range": list(SPEED_RANGE),
        "variation_range": list(VARIATION_RANGE),
        "rhythm_range": list(RHYTHM_RANGE),
        "curse_intensities": list(curse_intensities),
        "curse_styles": list(curse_styles),
    }


def _serialize_envelope(envelope: Optional[Envelope]) -> Optional[Dict[str, object]]:
    if envelope is None:
        return None
    if isinstance(envelope, Repeat):
        return {"replay": envelope.number}
    return {"content": envelope.content, "voice": envelope.voice, "repeat": envelope.repeat_count}


def describe_queue(snapshot: Callable[[], Dict[str, object]]) -> Dict[str, object]:
    """Adapts the announcer's snapshot into the queue view both transports
    serve (GET /v1/queue, the queue_status MCP tool). Lives here rather than
    on the announcer because the envelope-to-JSON shape is a presentation
    choice, not the announcer's."""
    state = snapshot()
    return {
        "playing": _serialize_envelope(state["playing"]),
        "pending": [_serialize_envelope(envelope) for envelope in state["pending"]],
        "muted": bool(state.get("muted", False)),
    }
