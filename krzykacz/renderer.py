from __future__ import annotations

import logging
from itertools import groupby
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .assets import resolve_effect
from .effects import Effects
from .protocol import MAX_SPOKEN_BYTES, REPEAT_SEPARATOR, Effect, Msg, Speech, split_segments
from .tts import Prosody, Tts

logger = logging.getLogger(__name__)

# One playback item: ("speech", TTS PCM) or ("effect", ffmpeg PCM). The two
# never merge -- different sample rates -- so each is played with its own call.
PlaybackItem = Tuple[str, bytes]

TERMINAL_PUNCTUATION = ".!?:;…"


def with_terminal_punctuation(text: str) -> str:
    """Appends a "." if the text doesn't already end in sentence-final
    punctuation. Piper needs that cue for a natural intonation drop -- without
    it, a repeated message runs into "Powtarzam!" with no audible boundary."""
    text = text.rstrip()
    if text and text[-1] not in TERMINAL_PUNCTUATION:
        return text + "."
    return text


def repeats_within_budget(per_copy_bytes: int, repeat_count: int) -> int:
    """How many copies of `per_copy_bytes` of spoken text, joined by
    REPEAT_SEPARATOR, fit in MAX_SPOKEN_BYTES -- spent in whole copies, since
    rendered audio can only be cut at a copy boundary. A pass with no speech
    (per_copy_bytes 0) isn't bounded here; repeat_count is already capped by
    protocol.MAX_REPEAT_COUNT."""
    if per_copy_bytes <= 0:
        return repeat_count
    separator = len(REPEAT_SEPARATOR.encode("utf-8"))
    fits = (MAX_SPOKEN_BYTES + separator) // (per_copy_bytes + separator)
    return max(1, min(repeat_count, fits))


def _merge_adjacent(items: List[PlaybackItem]) -> List[PlaybackItem]:
    """Joins consecutive items of the same kind into one, so a stretch of
    speech (or of effects) plays as one gapless aplay call. Both formats are
    headerless PCM, so byte concatenation is audio concatenation."""
    return [(kind, b"".join(data for _, data in run)) for kind, run in groupby(items, key=lambda item: item[0])]


class Renderer:
    """Turns a message into the audio to play, in order: speech synthesized,
    `<effect>` tags decoded, the whole pass repeated with "Powtarzam!" between
    copies. Never raises -- a part that fails to render is logged and left
    out, so the rest of the message still plays."""

    def __init__(self, tts: Tts, effects: Effects, assets_dir: Path):
        self._tts = tts
        self._effects = effects
        self._assets_dir = assets_dir

    def render(self, msg: Msg) -> List[PlaybackItem]:
        segments = split_segments(msg.content)
        prosody = Prosody(speed=msg.speed, variation=msg.variation, rhythm=msg.rhythm)
        # Per message, so "<step><step>" decodes the file once, as "<step*2>" does.
        decoded: Dict[str, Optional[bytes]] = {}
        one_pass = [
            item
            for item in (self._render_segment(s, msg.voice, prosody, decoded) for s in segments)
            if item is not None
        ]

        per_copy_bytes = sum(
            len(with_terminal_punctuation(s.text).encode("utf-8"))
            for s in segments
            if isinstance(s, Speech)
        )
        copies = repeats_within_budget(per_copy_bytes, msg.repeat_count) if one_pass else 1
        if copies < msg.repeat_count:
            logger.info("Repeat %d exceeds the spoken-bytes budget, playing %d", msg.repeat_count, copies)

        items = list(one_pass)
        if copies > 1:
            # The bare constant, same prosody: one cache entry per voice and
            # knobs, shared by every repeated message.
            separator = self._synthesize(REPEAT_SEPARATOR, msg.voice, prosody)
            items = []
            for i in range(copies):
                if i > 0 and separator is not None:
                    items.append(("speech", separator))
                items.extend(one_pass)
        return _merge_adjacent(items)

    def _render_segment(
        self,
        segment: object,
        voice: Optional[str],
        prosody: Prosody,
        decoded: Dict[str, Optional[bytes]],
    ) -> Optional[PlaybackItem]:
        if isinstance(segment, Speech):
            text = with_terminal_punctuation(segment.text)
            audio = self._synthesize(text, voice, prosody) if text else None
            return None if audio is None else ("speech", audio)
        if isinstance(segment, Effect):
            if segment.name not in decoded:
                decoded[segment.name] = self._decode(segment.name)
            clip = decoded[segment.name]
            return None if clip is None else ("effect", clip * segment.count)
        return None

    def _synthesize(self, text: str, voice: Optional[str], prosody: Prosody) -> Optional[bytes]:
        try:
            return self._tts.synthesize(text, voice, prosody)
        except Exception:
            logger.exception("Failed to synthesize %r", text)
            return None

    def _decode(self, name: str) -> Optional[bytes]:
        path = resolve_effect(self._assets_dir, name)
        if path is None:
            logger.warning("Effect %r not found in %s, skipping", name, self._assets_dir)
            return None
        try:
            return self._effects.decode(path)
        except Exception:
            logger.exception("Failed to decode effect %r", name)
            return None
