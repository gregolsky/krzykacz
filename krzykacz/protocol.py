from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple, Union

logger = logging.getLogger(__name__)

MAX_CONTENT_BYTES = 800

# Cap on the fully-joined text after `repeat` duplication -- without this,
# repeat=10 on a near-MAX_CONTENT_BYTES message is ~9 minutes of audio again,
# defeating the point of shrinking MAX_CONTENT_BYTES. Applied in
# Announcer._announce, after REPEAT_SEPARATOR.join(...).
MAX_SPOKEN_BYTES = 1600

_EFFECT_TAG_RE = re.compile(r"^<([^<>]+)>\s*(.*)$", re.DOTALL)


MAX_VOICE_CHARS = 64
_VOICE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

MAX_REPEAT_COUNT = 10
REPEAT_SEPARATOR = " Powtarzam! "


@dataclass(frozen=True)
class Msg:
    content: str
    voice: Optional[str] = None
    repeat_count: int = 1


@dataclass(frozen=True)
class Repeat:
    number: int = -1


Envelope = Union[Msg, Repeat]


def preview(text: str, limit: int = 80) -> str:
    """Shortens text for logging -- messages can be up to MAX_CONTENT_BYTES,
    far too long to dump into a log line."""
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _truncate(text: str, max_bytes: int = MAX_CONTENT_BYTES) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    logger.warning("Message content truncated from %d to %d bytes", len(encoded), max_bytes)
    # errors="ignore" drops a partial multi-byte char left dangling at the cut.
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _clean_voice(value: object) -> Optional[str]:
    """Validates the optional "voice" field: a short alnum/-/_ identifier.
    Anything else (wrong type, too long, odd characters) is silently
    dropped -- an unrecognized voice just falls back to the default one,
    it's never worth failing the whole message over."""
    if not isinstance(value, str):
        return None
    if not value or len(value) > MAX_VOICE_CHARS or not _VOICE_NAME_RE.match(value):
        return None
    return value


def _clean_repeat_count(value: object) -> int:
    """Validates the optional "repeat" field: how many times to speak the
    message, separated by "Powtarzam!". Missing, non-numeric, or out-of-range
    values fall back to 1 (spoken once, no repetition) rather than failing
    the message -- this is a nice-to-have emphasis knob, not something worth
    dropping a whole alert over."""
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 1
    if count < 1:
        return 1
    return min(count, MAX_REPEAT_COUNT)


def build_msg(content: str, voice: object = None, repeat: object = None) -> Msg:
    """Builds a validated Msg from already-typed fields (as opposed to
    `parse`, which extracts them from a raw body + tags) -- used by the
    MCP endpoint, which receives typed arguments directly."""
    return Msg(
        content=_truncate(content),
        voice=_clean_voice(voice),
        repeat_count=_clean_repeat_count(repeat),
    )


def build_repeat(number: object) -> Repeat:
    try:
        number = int(number)
    except (TypeError, ValueError):
        number = -1
    return Repeat(number=number)


def parse_tags(tags: Optional[Iterable[str]]) -> Dict[str, str]:
    """Extracts "key=value" entries from ntfy tags into a dict. ntfy also
    uses tags for plain emoji/text markers, so entries without "=" are
    ignored rather than treated as an error. When a key appears more than
    once, the last occurrence wins."""
    params: Dict[str, str] = {}
    if not tags:
        return params
    for tag in tags:
        if not isinstance(tag, str) or "=" not in tag:
            continue
        key, _, value = tag.partition("=")
        params[key.strip()] = value.strip()
    return params


def parse(body: str, tags: Optional[Iterable[str]] = None) -> Envelope:
    """Parse a krzykacz message: `body` is the literal text to speak (or
    empty, for a `replay` request), and `tags` carries parameters as
    "key=value" entries (see parse_tags). Parameters travel in ntfy's `Tags`
    header/field rather than the body, because ntfy does not forward
    arbitrary custom HTTP headers to subscribers -- `tags` is one of the few
    fields that does survive.

    Recognized keys: `voice` and `repeat` (see build_msg), and `replay` (a
    history index to replay instead of speaking `body`, see build_repeat).
    Content longer than MAX_CONTENT_BYTES is truncated -- this is meant to be
    read aloud, not archived.
    """
    params = parse_tags(tags)

    if "replay" in params:
        return build_repeat(params["replay"])

    return build_msg(body, voice=params.get("voice"), repeat=params.get("repeat"))


def split_effect(content: str) -> Tuple[Optional[str], str]:
    """Splits a leading "<filename> rest of text" tag off message content.

    Returns (filename, remaining_text). filename is None (and the original
    content returned untouched) unless the content starts with a bare
    "<name>" tag naming a plain filename -- no path separators, no "..".
    That guards against path traversal reaching into the assets directory
    later, since this content comes straight from the network.
    """
    match = _EFFECT_TAG_RE.match(content)
    if not match:
        return None, content

    name, rest = match.group(1).strip(), match.group(2)
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None, content

    return name, rest
