from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from itertools import groupby
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .effects import Effects
from .light import Light
from .protocol import (
    MAX_SPOKEN_BYTES,
    REPEAT_SEPARATOR,
    Effect,
    Envelope,
    Msg,
    Repeat,
    Segment,
    Speech,
    _truncate,
    preview,
    split_segments,
)
from .tts import Prosody, Tts

logger = logging.getLogger(__name__)

# The signature of Announcer.submit: hands an envelope to the playback queue,
# returning False if it was dropped because the queue is full. Message sources
# (ntfy, HTTP, MCP) depend on this rather than on Announcer itself.
Submit = Callable[[Envelope], bool]

NO_SUCH_MESSAGE = "Nie ma takiej wiadomości"

_TERMINAL_PUNCTUATION = ".!?:;…"

# One playback item: ("speech", tts-shaped audio) or ("effect", effects-
# shaped PCM). The two never merge with each other -- they're different
# audio formats/sample rates -- only runs of the same kind do (see
# Announcer._render_pass).
PlaybackItem = Tuple[str, bytes]


def _repeats_within_budget(per_copy_bytes: int, repeat_count: int) -> int:
    """How many copies of a per_copy_bytes-sized spoken chunk, joined by
    REPEAT_SEPARATOR, fit in MAX_SPOKEN_BYTES. Same budget the joined-text
    path gets from _truncate, but spent in whole copies -- concatenated
    audio can only be cut at a copy boundary, which beats _truncate's
    mid-word cut of the last one.

    per_copy_bytes is 0 for a pass with no spoken text at all (an
    effects-only message) -- nothing here bounds how many times a sound
    effect repeats, so the count passes through unclamped (repeat_count is
    already <= MAX_REPEAT_COUNT from protocol.build_msg)."""
    if per_copy_bytes <= 0:
        return repeat_count
    separator = len(REPEAT_SEPARATOR.encode("utf-8"))
    fits = (MAX_SPOKEN_BYTES + separator) // (per_copy_bytes + separator)
    return max(1, min(repeat_count, fits))


def _with_terminal_punctuation(text: str) -> str:
    """Appends a "." if the text doesn't already end in sentence-final
    punctuation. Piper needs that cue for a natural intonation drop -- without
    it, a repeated message runs into "Powtarzam!" with no audible boundary."""
    text = text.rstrip()
    if text and text[-1] not in _TERMINAL_PUNCTUATION:
        return text + "."
    return text


class Announcer:
    """Single-worker FIFO: consumes envelopes, drives light + effect + TTS in
    sequence. Blink once, light on, interleaved sounds/speech in the order
    they appear in the message, light off. Nothing is ever interrupted -- the
    next envelope waits in the queue."""

    def __init__(
        self,
        light: Light,
        tts: Tts,
        effects: Effects,
        assets_dir: str,
        history_size: int = 10,
        queue_size: int = 10,
    ):
        self._light = light
        self._tts = tts
        self._effects = effects
        self._assets_dir = Path(assets_dir).resolve()
        self._queue: "queue.Queue[Envelope]" = queue.Queue(maxsize=queue_size)
        self._history: "deque[Msg]" = deque(maxlen=history_size)
        self._current: Optional[Envelope] = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def snapshot(self) -> Dict[str, object]:
        """Returns what's currently playing (or None if idle) and what's
        still waiting -- used by GET /v1/queue. queue.Queue has no safe
        public iteration, hence locking its mutex to read the raw deque."""
        with self._queue.mutex:
            pending = list(self._queue.queue)
        return {"playing": self._current, "pending": pending}

    def submit(self, envelope: Envelope) -> bool:
        try:
            self._queue.put_nowait(envelope)
        except queue.Full:
            logger.warning(
                "Queue full (%d), dropping: %r", self._queue.maxsize, envelope
            )
            return False
        logger.info("Queued: %r (queue size: %d)", envelope, self._queue.qsize())
        return True

    def _run(self) -> None:
        while True:
            envelope = self._queue.get()
            self._current = envelope
            try:
                self._handle(envelope)
            except Exception:
                logger.exception("Failed to handle %r", envelope)
            finally:
                self._current = None
                self._queue.task_done()

    def _handle(self, envelope: Envelope) -> None:
        if isinstance(envelope, Msg):
            self._history.append(envelope)
            logger.info("New message: %s", preview(envelope.content))
            msg = envelope
        elif isinstance(envelope, Repeat):
            try:
                msg = self._history[envelope.number]
                logger.info("Repeat #%d: %s", envelope.number, preview(msg.content))
            except IndexError:
                logger.warning("repeat %s: no such message in history", envelope.number)
                msg = Msg(content=NO_SUCH_MESSAGE)
        else:
            logger.warning("Unknown envelope type: %r", envelope)
            return

        self._announce(msg)

    def _announce(self, msg: Msg) -> None:
        segments = split_segments(msg.content)
        voice = msg.voice
        prosody = Prosody(speed=msg.speed, variation=msg.variation, rhythm=msg.rhythm)

        # Render before touching the light: Piper's subprocess start + model
        # load + inference (and, for effects, the ffmpeg decode) is what
        # causes the multi-second gap between "light on" and "sound starts".
        # Doing it first means light and sound begin together instead of the
        # light sitting there in silence.
        logger.info(
            "Rendering (voice=%s, repeats=%d): %s",
            voice or "default", msg.repeat_count, preview(msg.content),
        )
        if any(isinstance(segment, Effect) for segment in segments):
            items = self._render_with_effects(segments, msg.repeat_count, voice, prosody)
        else:
            items = self._render_plain(segments, msg.repeat_count, voice, prosody)

        try:
            self._light.blink(1)
            self._light.on()
        except Exception:
            logger.exception("Failed to turn light on")

        try:
            for kind, data in items:
                if kind == "effect":
                    logger.info("Playing effect")
                    self._effects.play_pcm(data)
                else:
                    logger.info("Playing speech")
                    self._tts.play(data)
        except Exception:
            logger.exception("Failed to play effect or speak message")
        finally:
            try:
                self._light.off()
            except Exception:
                logger.exception("Failed to turn light off")

    def _render_plain(
        self, segments: List[Segment], repeat_count: int, voice: Optional[str], prosody: Prosody
    ) -> List[PlaybackItem]:
        """Fast path for a message with no effect tags -- segments then holds
        at most one Speech (split_segments never splits content that has no
        tag to split around). Delegates repeat handling to
        _synthesize_plain, unchanged from before interleaved effects
        existed, cache behavior included."""
        text = " ".join(segment.text for segment in segments if isinstance(segment, Speech))
        if not text:
            return []
        spoken = _with_terminal_punctuation(text)
        try:
            audio = self._synthesize_plain(spoken, repeat_count, voice, prosody)
        except Exception:
            logger.exception("Failed to synthesize message")
            return []
        return [("speech", audio)]

    def _synthesize_plain(
        self, spoken: str, repeat_count: int, voice: Optional[str], prosody: Prosody
    ) -> bytes:
        """Renders `spoken`, repeated `repeat_count` times with
        REPEAT_SEPARATOR between copies.

        A backend whose output concatenates (raw PCM -- see
        Tts.concatenable) renders the body and the separator once each and
        the copies are joined as audio: one synthesis instead of N, and both
        pieces are cacheable on their own, the separator across every
        message in that voice. The joins land on sentence boundaries, where
        a listener expects a pause anyway.

        Any other backend keeps the joined-text path: one synthesis of the
        whole thing, exactly as before."""
        if repeat_count <= 1:
            return self._tts.synthesize(spoken, voice, prosody)

        if not self._tts.concatenable:
            joined = _truncate(REPEAT_SEPARATOR.join([spoken] * repeat_count), MAX_SPOKEN_BYTES)
            return self._tts.synthesize(joined, voice, prosody)

        copies = _repeats_within_budget(len(spoken.encode("utf-8")), repeat_count)
        if copies < repeat_count:
            logger.info("Repeat %d exceeds the spoken-bytes budget, playing %d", repeat_count, copies)
        body = self._tts.synthesize(spoken, voice, prosody)
        if copies <= 1:
            return body
        # Same prosody for the separator, so it doesn't jump out of the
        # message it's separating.
        separator = self._tts.synthesize(REPEAT_SEPARATOR, voice, prosody)
        return separator.join([body] * copies)

    def _render_with_effects(
        self, segments: List[Segment], repeat_count: int, voice: Optional[str], prosody: Prosody
    ) -> List[PlaybackItem]:
        """General renderer, used once a message contains at least one
        effect tag: renders one pass of `segments` in order (see
        _render_pass), then, for repeat_count > 1, replays that same
        rendered pass with a synthesized "Powtarzam!" between repeats --
        the whole sequence, sounds included, rather than just the spoken
        part (see _synthesize_plain for the no-effects case, which only
        ever repeats text).

        The pass and the separator are each rendered once and reused
        byte-for-byte across repeats -- a repeated interleaved message
        doesn't re-synthesize or re-decode per copy, same reuse-over-
        redo spirit as _synthesize_plain's concatenable path."""
        pass_items = self._render_pass(segments, voice, prosody)
        if not pass_items or repeat_count <= 1:
            return pass_items

        per_copy_bytes = sum(
            len(_with_terminal_punctuation(segment.text).encode("utf-8"))
            for segment in segments
            if isinstance(segment, Speech)
        )
        copies = _repeats_within_budget(per_copy_bytes, repeat_count)
        if copies < repeat_count:
            logger.info("Repeat %d exceeds the spoken-bytes budget, playing %d", repeat_count, copies)
        if copies <= 1:
            return pass_items

        separator_item = self._render_separator(voice, prosody)

        result: List[PlaybackItem] = []
        for i in range(copies):
            if i > 0 and separator_item is not None:
                result.append(separator_item)
            result.extend(pass_items)
        return result

    def _render_separator(self, voice: Optional[str], prosody: Prosody) -> Optional[PlaybackItem]:
        """Renders the "Powtarzam!" heard between repeats of an interleaved
        message. Deliberately calls synthesize() with the exact same text
        _synthesize_plain's concatenable branch uses (the bare
        REPEAT_SEPARATOR constant, not run through _with_terminal_punctuation
        or .strip()) so the two paths land on the same CachedTts entry --
        see _synthesize_plain's docstring on the separator being "cacheable
        ... across every message in that voice". A text-level mismatch here
        would silently stop that sharing for any repeated message that also
        has an effect tag."""
        try:
            return ("speech", self._tts.synthesize(REPEAT_SEPARATOR, voice, prosody))
        except Exception:
            logger.exception("Failed to synthesize repeat separator")
            return None

    def _render_pass(
        self, segments: List[Segment], voice: Optional[str], prosody: Prosody
    ) -> List[PlaybackItem]:
        """Renders one pass of `segments` (no repetition) into an ordered
        list of playback items, merging each run of consecutive same-kind
        segments into one item: an effect run is always joined at the audio
        level (ffmpeg's raw-PCM output concatenates byte-wise); a speech run
        is joined at the text level and synthesized once on a backend that
        can't concatenate audio itself, or synthesized segment-by-segment
        and joined at the audio level on one that can (see Tts.concatenable)
        -- same distinction _synthesize_plain's repeat path already relies
        on. Speech and effect items never merge with each other: they're
        different audio formats/sample rates, always played with a separate
        call.

        In practice a "speech run" here is always exactly one segment --
        split_segments never emits two Speech segments back to back, each
        maximal stretch of literal text already becomes one -- so
        _render_speech_run's multi-segment merging is a safety net for an
        input shape this call site never actually produces, not a case this
        code relies on. Kept rather than trimmed to a single segment: a
        future change to split_segments that *did* start emitting adjacent
        Speech segments would otherwise silently lose whichever ones a
        narrower signature dropped."""
        items: List[PlaybackItem] = []
        for is_effect, run in groupby(segments, key=lambda s: isinstance(s, Effect)):
            item = self._render_effect_run(list(run)) if is_effect else self._render_speech_run(
                list(run), voice, prosody
            )
            if item is not None:
                items.append(item)
        return items

    def _render_effect_run(self, run: List[Effect]) -> Optional[PlaybackItem]:
        # Decoded once per distinct name, not once per Effect: "<step><step>"
        # (repetition written out as separate tags) should cost exactly what
        # "<step*2>" costs -- one ffmpeg decode reused via `* count` -- not
        # one decode per occurrence of the same file. A name that fails to
        # resolve/decode is cached as None too, so a repeated bad tag is
        # looked up and logged once instead of once per occurrence.
        decoded: Dict[str, Optional[bytes]] = {}
        clips: List[bytes] = []
        for effect in run:
            if effect.name not in decoded:
                decoded[effect.name] = self._decode_effect(effect.name)
            clip = decoded[effect.name]
            if clip is not None:
                clips.append(clip * effect.count)
        if not clips:
            return None
        return ("effect", b"".join(clips))

    def _decode_effect(self, name: str) -> Optional[bytes]:
        """Resolves and decodes one effect by name, or None (logged) if it
        doesn't resolve to a file or fails to decode -- never raises, same
        skip-don't-fail stance as the rest of this module."""
        path = self._resolve_effect(name)
        if path is None:
            logger.warning("Effect %r not found in %s, skipping", name, self._assets_dir)
            return None
        try:
            return self._effects.decode(path)
        except Exception:
            logger.exception("Failed to decode effect %r", name)
            return None

    def _render_speech_run(
        self, run: List[Speech], voice: Optional[str], prosody: Prosody
    ) -> Optional[PlaybackItem]:
        texts = [_with_terminal_punctuation(segment.text) for segment in run]
        texts = [text for text in texts if text]
        if not texts:
            return None

        if not self._tts.concatenable:
            joined = _truncate(" ".join(texts), MAX_SPOKEN_BYTES)
            try:
                return ("speech", self._tts.synthesize(joined, voice, prosody))
            except Exception:
                logger.exception("Failed to synthesize message")
                return None

        parts: List[bytes] = []
        for text in texts:
            try:
                parts.append(self._tts.synthesize(text, voice, prosody))
            except Exception:
                logger.exception("Failed to synthesize message")
        if not parts:
            return None
        return ("speech", b"".join(parts))

    def _resolve_effect(self, name: str) -> Optional[Path]:
        candidate = (self._assets_dir / name).resolve()
        try:
            candidate.relative_to(self._assets_dir)
        except ValueError:
            logger.warning("Rejecting effect outside assets directory: %r", name)
            return None
        if not candidate.is_file():
            return None
        return candidate
