from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class Tts(ABC):
    @abstractmethod
    def synthesize(self, text: str, voice: Optional[str] = None) -> bytes: ...

    @abstractmethod
    def play(self, audio: bytes) -> None: ...

    def say(self, text: str, voice: Optional[str] = None) -> None:
        """Convenience: synthesize then play immediately. Announcer calls the
        two steps separately so synthesis can happen before the light turns
        on -- see Announcer._announce."""
        self.play(self.synthesize(text, voice))


def _aplay_cmd(alsa_device: Optional[str]) -> list[str]:
    cmd = ["aplay", "-q"]
    if alsa_device:
        cmd += ["-D", alsa_device]
    return cmd


class PiperTts(Tts):
    """Synthesizes with the `piper` CLI (subprocess, not the Python API -- keeps us
    decoupled from onnxruntime version churn) and plays raw PCM via aplay.

    `voices` maps a short name (as sent in the ntfy message's "voice" field) to
    an .onnx model path. An unrecognized or absent voice falls back to
    `default_voice`."""

    def __init__(
        self,
        voices: Dict[str, str],
        default_voice: str,
        alsa_device: Optional[str] = None,
        sample_rate: int = 22050,
    ):
        if default_voice not in voices:
            raise ValueError(f"default_voice {default_voice!r} not in voices {list(voices)}")
        self.voices = voices
        self.default_voice = default_voice
        self.alsa_device = alsa_device
        # Must match the voice models' output rate (22050 Hz for the "medium" pl_PL voices).
        self.sample_rate = sample_rate

    def _model_path(self, voice: Optional[str]) -> str:
        if voice and voice in self.voices:
            return self.voices[voice]
        if voice:
            logger.warning("Unknown voice %r, using default %r", voice, self.default_voice)
        return self.voices[self.default_voice]

    def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        model_path = self._model_path(voice)
        result = subprocess.run(
            ["piper", "--model", model_path, "--output-raw"],
            input=text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        return result.stdout

    def play(self, audio: bytes) -> None:
        aplay = subprocess.Popen(
            _aplay_cmd(self.alsa_device)
            + ["-f", "S16_LE", "-r", str(self.sample_rate), "-c", "1", "-t", "raw"],
            stdin=subprocess.PIPE,
        )
        aplay.communicate(audio, timeout=30)


class EspeakTts(Tts):
    """Fallback backend: espeak-ng, piped through aplay for consistent ALSA device
    selection with PiperTts. `voice` here (if given) is passed straight through as
    an espeak-ng voice/language code -- it isn't matched against Piper's named
    voices."""

    def __init__(self, voice: str = "pl", alsa_device: Optional[str] = None):
        self.voice = voice
        self.alsa_device = alsa_device

    def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        result = subprocess.run(
            ["espeak-ng", "-v", voice or self.voice, "--stdout", text],
            stdout=subprocess.PIPE,
            timeout=60,
        )
        return result.stdout

    def play(self, audio: bytes) -> None:
        aplay = subprocess.Popen(_aplay_cmd(self.alsa_device), stdin=subprocess.PIPE)
        aplay.communicate(audio, timeout=30)
