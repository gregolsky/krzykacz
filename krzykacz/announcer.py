from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from pathlib import Path
from typing import Callable, Dict, Optional

from .effects import Effects
from .light import Light
from .protocol import Envelope, Msg, Repeat, preview
from .renderer import Renderer
from .tts import Tts

logger = logging.getLogger(__name__)

# The signature of Announcer.submit: hands an envelope to the playback queue,
# returning False if it was dropped because the queue is full. Message sources
# (ntfy, HTTP, MCP) depend on this rather than on Announcer itself.
Submit = Callable[[Envelope], bool]

NO_SUCH_MESSAGE = "Nie ma takiej wiadomości"


class Announcer:
    """Single-worker FIFO: consumes envelopes, drives light + effect + TTS in
    sequence. Blink once, light on, interleaved sounds/speech in the order
    they appear in the message, light off. Nothing is ever interrupted -- the
    next envelope waits in the queue.

    The one exception is mute (`set_muted`): it stops submit accepting
    anything, discards whatever is still queued as the worker reaches it, and
    ends the message being played after its current segment. It can't cut a
    segment short -- the `aplay` for it is a local of `Tts.play` / `Effects.play_pcm`
    that nothing else can reach -- so a long segment finishes."""

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
        self._renderer = Renderer(tts, effects, Path(assets_dir).resolve())
        self._queue: "queue.Queue[Envelope]" = queue.Queue(maxsize=queue_size)
        self._history: "deque[Msg]" = deque(maxlen=history_size)
        self._current: Optional[Envelope] = None
        self._muted = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def snapshot(self) -> Dict[str, object]:
        """Returns what's currently playing (or None if idle), what's
        still waiting, and whether mute is on -- used by GET /v1/queue.
        queue.Queue has no safe public iteration, hence locking its mutex to
        read the raw deque."""
        with self._queue.mutex:
            pending = list(self._queue.queue)
        return {"playing": self._current, "pending": pending, "muted": self._muted.is_set()}

    def set_muted(self, muted: bool) -> None:
        if muted:
            self._muted.set()
        else:
            self._muted.clear()
        logger.info("Muted" if muted else "Unmuted")

    def is_muted(self) -> bool:
        return self._muted.is_set()

    def submit(self, envelope: Envelope) -> bool:
        if self._muted.is_set():
            logger.info("Muted, dropping: %r", envelope)
            return False
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
        # Discarded here rather than pulled out of the queue when mute is
        # switched on: the worker still task_done()s it, so queue.join()
        # accounting stays balanced with no queue-internals surgery.
        if self._muted.is_set():
            logger.info("Muted, discarding queued: %r", envelope)
            return
        if isinstance(envelope, Msg):
            # A prepared message was never heard, so it isn't something to
            # `replay` -- keeping it out of history also keeps the replay
            # indices pointing at what people actually heard.
            if not envelope.prepare:
                self._history.append(envelope)
            logger.info(
                "%s: %s",
                "Preparing" if envelope.prepare else "New message",
                preview(envelope.content),
            )
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
        # Render before touching the light: Piper's subprocess start + model
        # load + inference (and the ffmpeg decode of effects) is the
        # multi-second part, so doing it first lets light and sound begin
        # together instead of the light sitting there in silence.
        logger.info(
            "Rendering (voice=%s, repeats=%d): %s",
            msg.voice or "default", msg.repeat_count, preview(msg.content),
        )
        items = self._renderer.render(msg)

        # Rendering is all `prepare` asks for: the synthesized speech now sits
        # in the audio cache (when CachedTts is in use), so the same message
        # sent again without `prepare` skips the multi-second synthesis. The
        # light stays off -- nothing is being said.
        if msg.prepare:
            logger.info("Prepared, not playing: %s", preview(msg.content))
            return

        # Rendering can take seconds; mute may have arrived meanwhile.
        if self._muted.is_set():
            logger.info("Muted while rendering, not playing")
            return

        try:
            self._light.blink(1)
            self._light.on()
        except Exception:
            logger.exception("Failed to turn light on")

        try:
            for kind, data in items:
                if self._muted.is_set():
                    logger.info("Muted, stopping playback")
                    break
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
