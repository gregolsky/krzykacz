from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

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


def resolve_effect(assets_dir: Path, name: str) -> Optional[Path]:
    """The file an effect tag names, or None if it isn't a file inside
    `assets_dir` (an already-resolved path). The name comes from the network:
    resolving before the containment check also rejects a symlink that
    points outside the directory."""
    candidate = (assets_dir / name).resolve()
    try:
        candidate.relative_to(assets_dir)
    except ValueError:
        logger.warning("Rejecting effect outside assets directory: %r", name)
        return None
    if not candidate.is_file():
        return None
    return candidate
