from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

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
        aplay_cmd = ["aplay", "-q"]
        if self.alsa_device:
            aplay_cmd += ["-D", self.alsa_device]
        aplay_cmd += [
            "-f",
            "S16_LE",
            "-r",
            str(self.SAMPLE_RATE),
            "-c",
            str(self.CHANNELS),
            "-t",
            "raw",
        ]
        aplay = subprocess.Popen(aplay_cmd, stdin=ffmpeg.stdout)
        if ffmpeg.stdout is not None:
            ffmpeg.stdout.close()
        aplay.wait(timeout=30)
        ffmpeg.wait(timeout=30)


class NullEffects(Effects):
    """No playback -- logs instead. Used for local testing off-device."""

    def play(self, path: Path) -> None:
        logger.info("effect: %s", path)
