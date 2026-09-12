from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .procutil import aplay_raw_cmd, kill, wait_or_kill

logger = logging.getLogger(__name__)


class Effects(ABC):
    @abstractmethod
    def play(self, path: Path) -> None: ...


class FfmpegEffects(Effects):
    """Plays any format ffmpeg can decode (mp3, ogg, wav, ...) by decoding to
    raw PCM and piping into aplay -- the same device-selection pattern as
    PiperTts, so KRZYKACZ_ALSA_DEVICE applies uniformly to speech and effects."""

    SAMPLE_RATE = 44100
    CHANNELS = 2

    def __init__(self, alsa_device: Optional[str] = None):
        self.alsa_device = alsa_device

    def play(self, path: Path) -> None:
        ffmpeg = subprocess.Popen(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-f",
                "s16le",
                "-ar",
                str(self.SAMPLE_RATE),
                "-ac",
                str(self.CHANNELS),
                "-",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
        )
        try:
            aplay = subprocess.Popen(
                aplay_raw_cmd(self.alsa_device, self.SAMPLE_RATE, self.CHANNELS),
                stdin=ffmpeg.stdout,
            )
        except Exception:
            kill(ffmpeg)
            raise

        if ffmpeg.stdout is not None:
            ffmpeg.stdout.close()

        # Both processes must be reaped even if the first wait times out and
        # raises -- otherwise the survivor is left holding the ALSA device,
        # which is exactly the leak these helpers exist to prevent.
        try:
            wait_or_kill(aplay, timeout=30)
            wait_or_kill(ffmpeg, timeout=30)
        finally:
            kill(aplay)
            kill(ffmpeg)


class NullEffects(Effects):
    """No playback -- logs instead. Used for local testing off-device."""

    def play(self, path: Path) -> None:
        logger.info("effect: %s", path)
