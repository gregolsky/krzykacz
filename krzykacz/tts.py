from __future__ import annotations

import io
import logging
import subprocess
import wave
from abc import ABC, abstractmethod
from typing import Dict, Optional

from .procutil import aplay_cmd, aplay_raw_cmd, communicate_or_kill

logger = logging.getLogger(__name__)

# Both timeouts below scale with how much audio/text is involved, rather than
# being a flat constant -- a flat 60s/30s was simultaneously too tight for a
# long message on a slow Pi and too loose for a one-line alert. The
# multipliers are estimates (~15 Polish chars/sec of speech); measure the
# real numbers on target hardware if messages start timing out.
SYNTHESIS_TIMEOUT_BASE_S = 30.0
SYNTHESIS_TIMEOUT_PER_BYTE_S = 0.1
PLAYBACK_TIMEOUT_SLACK_S = 10.0


def _synthesis_timeout(text: str) -> float:
    return SYNTHESIS_TIMEOUT_BASE_S + len(text.encode("utf-8")) * SYNTHESIS_TIMEOUT_PER_BYTE_S


def _raw_pcm_duration_s(audio: bytes, sample_rate: int, channels: int) -> float:
    bytes_per_frame = 2 * channels  # S16_LE
    return len(audio) / (sample_rate * bytes_per_frame)


def _wav_duration_s(audio: bytes) -> float:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())
    except (wave.Error, EOFError):
        return 0.0


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
            timeout=_synthesis_timeout(text),
        )
        return result.stdout

    def play(self, audio: bytes) -> None:
        aplay = subprocess.Popen(
            aplay_raw_cmd(self.alsa_device, self.sample_rate, channels=1),
            stdin=subprocess.PIPE,
        )
        duration = _raw_pcm_duration_s(audio, self.sample_rate, channels=1)
        communicate_or_kill(aplay, audio, timeout=duration + PLAYBACK_TIMEOUT_SLACK_S)


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
            timeout=_synthesis_timeout(text),
        )
        return result.stdout

    def play(self, audio: bytes) -> None:
        aplay = subprocess.Popen(aplay_cmd(self.alsa_device), stdin=subprocess.PIPE)
        # The WAV header carries its own sample rate, so duration is computed
        # from that rather than assumed -- espeak-ng's output rate isn't
        # tracked on this class.
        communicate_or_kill(aplay, audio, timeout=_wav_duration_s(audio) + PLAYBACK_TIMEOUT_SLACK_S)
