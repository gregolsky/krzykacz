from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .procutil import (
    PLAYBACK_TIMEOUT_SLACK_S,
    aplay_raw_cmd,
    communicate_or_kill,
    raw_pcm_duration_s,
)

logger = logging.getLogger(__name__)

# How long decode() waits for ffmpeg to finish before giving up. A flat
# constant rather than one scaling with input size (contrast
# SYNTHESIS_TIMEOUT_* in krzykacz.tts, which scales with *output* length,
# known in advance) -- decode doesn't know a clip's duration until ffmpeg has
# already produced it, and sound-effect files are short by nature (a curated
# soundboard, not arbitrary uploads).
DECODE_TIMEOUT_S = 30.0


class Effects(ABC):
    @abstractmethod
    def decode(self, path: Path) -> bytes:
        """Decodes `path` to raw PCM -- s16le at a fixed rate/channel count
        (FfmpegEffects.SAMPLE_RATE/CHANNELS for that backend). Headerless
        like Tts.synthesize's output, so a caller can concatenate several
        decoded effects, or an effect with speech, before a single
        play_pcm call -- see Announcer._announce."""
        ...

    @abstractmethod
    def play_pcm(self, data: bytes) -> None:
        """Plays raw PCM exactly as decode() produces it."""
        ...

    def play(self, path: Path) -> None:
        """Convenience: decode then play immediately. Announcer doesn't call
        this directly -- it needs decode()'d bytes to interleave with speech
        -- but it's the natural single-file entry point otherwise."""
        self.play_pcm(self.decode(path))


class FfmpegEffects(Effects):
    """Plays any format ffmpeg can decode (mp3, ogg, wav, ...) via ffmpeg + aplay
    -- the same device-selection pattern as PiperTts, so KRZYKACZ_ALSA_DEVICE
    applies uniformly to speech and effects."""

    SAMPLE_RATE = 44100
    CHANNELS = 2

    def __init__(self, alsa_device: Optional[str] = None):
        self.alsa_device = alsa_device

    def decode(self, path: Path) -> bytes:
        # subprocess.run(timeout=...) kills and reaps the child itself on
        # TimeoutExpired -- same one-shot shape as PiperTts.synthesize /
        # EspeakTts.synthesize, no separate kill() bookkeeping needed here.
        result = subprocess.run(
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
            timeout=DECODE_TIMEOUT_S,
        )
        return result.stdout

    def play_pcm(self, data: bytes) -> None:
        aplay = subprocess.Popen(
            aplay_raw_cmd(self.alsa_device, self.SAMPLE_RATE, self.CHANNELS),
            stdin=subprocess.PIPE,
        )
        duration = raw_pcm_duration_s(data, self.SAMPLE_RATE, self.CHANNELS)
        communicate_or_kill(aplay, data, timeout=duration + PLAYBACK_TIMEOUT_SLACK_S)


class NullEffects(Effects):
    """No playback -- logs instead. Used for local testing off-device."""

    def decode(self, path: Path) -> bytes:
        logger.info("effect: %s", path)
        return b""

    def play_pcm(self, data: bytes) -> None:
        pass
