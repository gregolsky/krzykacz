from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Sequence

logger = logging.getLogger(__name__)


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


def describe(
    tts_backend: str, voices: Sequence[str], default_voice: str, assets_dir: str
) -> Dict[str, object]:
    """Builds the /v1/metadata payload: what this instance can actually play.

    `tts` matters to callers because it decides how `voice` is interpreted --
    piper voice names from the configured voice map, versus espeak-ng
    language codes passed straight through."""
    return {
        "tts": tts_backend,
        "voices": list(voices),
        "default_voice": default_voice,
        "effects": list_effects(assets_dir),
    }
