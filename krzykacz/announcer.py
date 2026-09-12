from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from pathlib import Path
from typing import Optional

from .effects import Effects
from .light import Light
from .protocol import REPEAT_SEPARATOR, Envelope, Msg, Repeat, preview, split_effect
from .tts import Tts

logger = logging.getLogger(__name__)

NO_SUCH_MESSAGE = "Nie ma takiej wiadomości"

_TERMINAL_PUNCTUATION = ".!?:;…"


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
    sequence. Blink once, light on, (optional sound effect), speak, light off.
    Nothing is ever interrupted -- the next envelope waits in the queue."""

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
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def submit(self, envelope: Envelope) -> None:
        try:
            self._queue.put_nowait(envelope)
        except queue.Full:
            logger.warning(
                "Queue full (%d), dropping: %r", self._queue.maxsize, envelope
            )
            return
        logger.info("Queued: %r (queue size: %d)", envelope, self._queue.qsize())

    def _run(self) -> None:
        while True:
            envelope = self._queue.get()
            try:
                self._handle(envelope)
            except Exception:
                logger.exception("Failed to handle %r", envelope)
            finally:
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
        effect_name, spoken = split_effect(msg.content)

        # Synthesize before touching the light: Piper's subprocess start +
        # model load + inference is what causes the multi-second gap between
        # "light on" and "sound starts". Doing it first means light and sound
        # begin together instead of the light sitting there in silence.
        audio: Optional[bytes] = None
        if spoken:
            spoken = _with_terminal_punctuation(spoken)
            if msg.repeat_count > 1:
                spoken = REPEAT_SEPARATOR.join([spoken] * msg.repeat_count)
            try:
                logger.info(
                    "Synthesizing (voice=%s, repeats=%d): %s",
                    msg.voice or "default",
                    msg.repeat_count,
                    preview(spoken),
                )
                audio = self._tts.synthesize(spoken, voice=msg.voice)
            except Exception:
                logger.exception("Failed to synthesize message")

        try:
            self._light.blink(1)
            self._light.on()
        except Exception:
            logger.exception("Failed to turn light on")

        try:
            if effect_name:
                path = self._resolve_effect(effect_name)
                if path is not None:
                    logger.info("Playing effect: %s", path.name)
                    self._effects.play(path)
                else:
                    logger.warning(
                        "Effect %r not found in %s, skipping", effect_name, self._assets_dir
                    )
            if audio is not None:
                logger.info("Playing speech")
                self._tts.play(audio)
        except Exception:
            logger.exception("Failed to play effect or speak message")
        finally:
            try:
                self._light.off()
            except Exception:
                logger.exception("Failed to turn light off")

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
