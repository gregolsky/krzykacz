from __future__ import annotations

import hashlib
import logging
import os
import struct
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple, Union

from .procutil import (
    PLAYBACK_TIMEOUT_SLACK_S,
    aplay_raw_cmd,
    communicate_or_kill,
    raw_pcm_duration_s,
)
from .protocol import preview

logger = logging.getLogger(__name__)

# How often CachedTts is allowed to scan its directory for expired/excess
# entries. The scan is O(entries), so it's rate-limited rather than run on
# every miss.
SWEEP_INTERVAL_S = 3600.0

# Scales with how much text is involved, rather than being a flat constant --
# a flat 60s was simultaneously too tight for a long message on a slow Pi and
# too loose for a one-line alert. The multiplier is an estimate (~15 Polish
# chars/sec of speech); measure the real number on target hardware if
# messages start timing out.
SYNTHESIS_TIMEOUT_BASE_S = 30.0
SYNTHESIS_TIMEOUT_PER_BYTE_S = 0.1


def _synthesis_timeout(text: str) -> float:
    return SYNTHESIS_TIMEOUT_BASE_S + len(text.encode("utf-8")) * SYNTHESIS_TIMEOUT_PER_BYTE_S


def _wav_to_pcm(audio: bytes) -> bytes:
    """Returns the sample data of a RIFF/WAVE stream, headerless -- the same
    shape PiperTts's `--output-raw` produces.

    Deliberately not the `wave` module: `espeak-ng --stdout` writes to a pipe
    and can't seek back to patch the header, so it declares a placeholder
    data-chunk length (~2 GiB) that `wave` would believe. The declared size is
    ignored here; whatever follows the `data` chunk header is the audio.
    Anything that isn't a WAVE stream (espeak-ng failed and printed nothing,
    say) yields b"", which callers already treat as "nothing to say"."""
    if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return b""
    pos = 12
    while pos + 8 <= len(audio):
        chunk_id = audio[pos : pos + 4]
        (size,) = struct.unpack("<I", audio[pos + 4 : pos + 8])
        if chunk_id == b"data":
            return audio[pos + 8 :]
        pos += 8 + size + (size & 1)  # chunks are word-aligned
    return b""


def _play_raw_pcm(audio: bytes, alsa_device: Optional[str], sample_rate: int) -> None:
    """Plays headerless S16_LE mono PCM through aplay. Shared by every engine
    that renders that format, so how it's played (and how long it's allowed to
    take) is decided once."""
    aplay = subprocess.Popen(
        aplay_raw_cmd(alsa_device, sample_rate, channels=1), stdin=subprocess.PIPE
    )
    duration = raw_pcm_duration_s(audio, sample_rate, channels=1)
    communicate_or_kill(aplay, audio, timeout=duration + PLAYBACK_TIMEOUT_SLACK_S)


class Prosody(NamedTuple):
    """The three synthesis knobs, in protocol terms rather than Piper's.

    `None` on a field means "leave it to whatever the instance is
    configured with", which in turn means "don't pass the flag at all" --
    that keeps an unset knob rendering byte-identically to before these
    existed.

    - `speed` is the inverse of Piper's `--length_scale`, because a caller
      thinks in "1.5x faster", not "0.67x the phoneme duration".
    - `variation` is `--noise_scale` unchanged: how far a rendition strays
      from the voice's average (pitch/timbre wobble).
    - `rhythm` is `--noise_w` unchanged: how far per-phoneme durations
      stray from the predicted ones.
    """

    speed: Optional[float] = None
    variation: Optional[float] = None
    rhythm: Optional[float] = None

    def merge(self, override: "Prosody") -> "Prosody":
        """`override`'s set fields win; the rest fall back to this one --
        per-message knobs over the instance's configured defaults."""
        return Prosody(
            speed=override.speed if override.speed is not None else self.speed,
            variation=override.variation if override.variation is not None else self.variation,
            rhythm=override.rhythm if override.rhythm is not None else self.rhythm,
        )


class Tts(ABC):
    @abstractmethod
    def synthesize(
        self, text: str, voice: Optional[str] = None, prosody: Prosody = Prosody()
    ) -> bytes: ...

    @abstractmethod
    def play(self, audio: bytes) -> None: ...

    @abstractmethod
    def fingerprint(self, voice: Optional[str] = None) -> str:
        """Identifies everything that decides what `voice` actually renders
        as -- the model, the speaker within it, the output format. CachedTts
        keys on this rather than on the voice name, because a name is not a
        stable identity: KRZYKACZ_PIPER_VOICES can remap it to a different
        model or speaker, and an unknown name falls back to the default
        voice."""

    @property
    def concatenable(self) -> bool:
        """Whether two `synthesize` results can be played back to back by
        simply concatenating their bytes. True for a headerless stream (raw
        PCM at a fixed rate), false for a format carrying a per-file header
        (WAV: the joined bytes would announce only the first chunk's
        length). Defaults to false -- a backend opts in."""
        return False

    def say(self, text: str, voice: Optional[str] = None, prosody: Prosody = Prosody()) -> None:
        """Convenience: synthesize then play immediately. Announcer calls the
        two steps separately so synthesis can happen before the light turns
        on -- see Announcer._announce."""
        self.play(self.synthesize(text, voice, prosody))


class VoiceSpec(NamedTuple):
    """Which .onnx model to run, and which embedded speaker index to select
    within it. `speaker` is None for an ordinary single-speaker model;
    multi-speaker models (e.g. hvsr-robotics/tts-pl-piper-v2, which bakes 8
    named speakers into one file) give each speaker its own voice name, all
    pointing at the same `model_path` with a different `speaker` index."""

    model_path: str
    speaker: Optional[int] = None


def _as_spec(entry: Union[str, VoiceSpec]) -> VoiceSpec:
    return entry if isinstance(entry, VoiceSpec) else VoiceSpec(entry, None)


class PiperTts(Tts):
    """Synthesizes with the `piper` CLI (subprocess, not the Python API -- keeps us
    decoupled from onnxruntime version churn) and plays raw PCM via aplay.

    `voices` maps a short name (as sent in the ntfy message's "voice" field) to
    a `VoiceSpec`, or plainly to an .onnx model path (equivalent to
    `VoiceSpec(path, speaker=None)`) for an ordinary single-speaker model. An
    unrecognized or absent voice falls back to `default_voice`."""

    def __init__(
        self,
        voices: Dict[str, Union[str, VoiceSpec]],
        default_voice: str,
        alsa_device: Optional[str] = None,
        sample_rate: int = 22050,
        prosody: Prosody = Prosody(),
    ):
        self.voices: Dict[str, VoiceSpec] = {name: _as_spec(entry) for name, entry in voices.items()}
        if default_voice not in self.voices:
            raise ValueError(f"default_voice {default_voice!r} not in voices {list(self.voices)}")
        self.default_voice = default_voice
        self.alsa_device = alsa_device
        # Must match the voice models' output rate (22050 Hz for the "medium" pl_PL voices).
        self.sample_rate = sample_rate
        self.prosody = prosody

    def _voice_spec(self, voice: Optional[str]) -> VoiceSpec:
        if voice and voice in self.voices:
            return self.voices[voice]
        if voice:
            logger.warning("Unknown voice %r, using default %r", voice, self.default_voice)
        return self.voices[self.default_voice]

    def _model_path(self, voice: Optional[str]) -> str:
        return self._voice_spec(voice).model_path

    def fingerprint(self, voice: Optional[str] = None) -> str:
        spec = self._voice_spec(voice)
        # The resolved model and speaker, not the requested name: two names
        # can address the same multi-speaker file at different indices, and
        # an unknown name resolves to the default voice. The instance's
        # prosody belongs here too -- it changes the audio, so changing
        # KRZYKACZ_PIPER_SPEED must not serve entries rendered at the old one.
        return f"piper:{spec.model_path}:{spec.speaker}:{self.sample_rate}:{self.prosody}"

    @property
    def concatenable(self) -> bool:
        # `piper --output-raw` is headerless PCM at a fixed rate.
        return True

    def synthesize(
        self, text: str, voice: Optional[str] = None, prosody: Prosody = Prosody()
    ) -> bytes:
        spec = self._voice_spec(voice)
        effective = self.prosody.merge(prosody)
        cmd = ["piper", "--model", spec.model_path, "--output-raw"]
        if spec.speaker is not None:
            cmd += ["--speaker", str(spec.speaker)]
        # A knob left unset passes no flag, so piper keeps its own default
        # and the audio is identical to what it was before these existed.
        if effective.speed is not None:
            # Piper scales duration, the protocol scales tempo -- inverse.
            cmd += ["--length_scale", f"{1.0 / effective.speed:.4f}"]
        if effective.variation is not None:
            cmd += ["--noise_scale", f"{effective.variation:.4f}"]
        if effective.rhythm is not None:
            cmd += ["--noise_w", f"{effective.rhythm:.4f}"]
        result = subprocess.run(
            cmd,
            input=text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_synthesis_timeout(text),
        )
        return result.stdout

    def play(self, audio: bytes) -> None:
        _play_raw_pcm(audio, self.alsa_device, self.sample_rate)


class EspeakTts(Tts):
    """espeak-ng, rendered to the same headerless 22050 Hz mono PCM as
    PiperTts so the two can share one playback path and be freely
    concatenated -- see RoutedTts.

    `voices` maps a wire name (`espeak_male`) to an espeak-ng voice spec
    (`pl+m3`: language plus a male/female/character variant). The mapping
    exists because the protocol's voice-name alphabet has no `+` in it. A
    name that isn't in `voices` is passed straight through as a spec, and no
    name at all means `voice` -- which is how `KRZYKACZ_TTS=espeak` has
    always worked.

    The protocol's knobs are Piper's, so two of the three map only
    approximately. Each is anchored so that Piper's default lands on
    espeak-ng's default, leaving an untagged message neutral:

    - `speed` -> `-s` words per minute, `175 * speed`.
    - `variation` -> `-p` pitch (0-99). Both are "how far from the voice's
      middle"; espeak-ng has no sampling noise to widen, so pitch is the
      closest audible stand-in.
    - `rhythm` -> `-g` pause between words, in 10 ms units. Only above
      Piper's default: a gap can't be negative, so a rhythm below 0.8 is
      the same as 0.8."""

    SAMPLE_RATE = 22050  # espeak-ng's own output rate; verified on the Pi.
    BASE_WORDS_PER_MINUTE = 175
    DEFAULT_PITCH = 50
    MAX_PITCH = 99
    # The Piper defaults the anchors above are relative to.
    PIPER_DEFAULT_VARIATION = 0.667
    PIPER_DEFAULT_RHYTHM = 0.8

    def __init__(
        self,
        voice: str = "pl",
        alsa_device: Optional[str] = None,
        prosody: Prosody = Prosody(),
        voices: Optional[Dict[str, str]] = None,
    ):
        self.voice = voice
        self.alsa_device = alsa_device
        self.prosody = prosody
        self.voices: Dict[str, str] = dict(voices or {})

    def _spec(self, voice: Optional[str]) -> str:
        return self.voices.get(voice, voice) if voice else self.voice

    def _flags(self, prosody: Prosody) -> List[str]:
        flags: List[str] = []
        if prosody.speed is not None:
            flags += ["-s", str(int(self.BASE_WORDS_PER_MINUTE * prosody.speed))]
        if prosody.variation is not None:
            pitch = round(self.DEFAULT_PITCH * prosody.variation / self.PIPER_DEFAULT_VARIATION)
            flags += ["-p", str(min(max(pitch, 0), self.MAX_PITCH))]
        if prosody.rhythm is not None:
            gap = round((prosody.rhythm - self.PIPER_DEFAULT_RHYTHM) * 10)
            flags += ["-g", str(max(gap, 0))]
        return flags

    def fingerprint(self, voice: Optional[str] = None) -> str:
        return f"espeak:{self._spec(voice)}:{self.SAMPLE_RATE}:{self.prosody}"

    @property
    def concatenable(self) -> bool:
        return True

    def synthesize(
        self, text: str, voice: Optional[str] = None, prosody: Prosody = Prosody()
    ) -> bytes:
        cmd = ["espeak-ng", "-v", self._spec(voice)] + self._flags(self.prosody.merge(prosody))
        # Text goes on stdin, not argv: argv would let a message starting
        # with "-" be parsed as an option.
        result = subprocess.run(
            cmd + ["--stdout"],
            input=text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_synthesis_timeout(text),
        )
        return _wav_to_pcm(result.stdout)

    def play(self, audio: bytes) -> None:
        _play_raw_pcm(audio, self.alsa_device, self.SAMPLE_RATE)


class RoutedTts(Tts):
    """Serves Piper's voices and espeak-ng's from one place, choosing by voice
    name, so both are ordinary `voice=` values.

    That only works because both engines emit the same thing -- headerless
    S16_LE mono at one sample rate -- which is what lets `play` and
    `concatenable` be answered once here, rather than per voice (`Tts.play`
    isn't told which voice made the audio). A name in neither map falls to
    Piper, which resolves it to its default voice as it always has. A name in
    both goes to Piper."""

    def __init__(self, piper: PiperTts, espeak: EspeakTts):
        if piper.sample_rate != espeak.SAMPLE_RATE:
            raise ValueError(
                f"Piper voices render at {piper.sample_rate} Hz but espeak-ng at "
                f"{espeak.SAMPLE_RATE} Hz; they can't share a playback path"
            )
        self._piper = piper
        self._espeak = espeak
        shadowed = sorted(set(espeak.voices) & set(piper.voices))
        if shadowed:
            logger.warning("Voices defined for both Piper and espeak-ng, using Piper: %s", shadowed)
        self._espeak_names = frozenset(espeak.voices) - frozenset(piper.voices)

    def _route(self, voice: Optional[str]) -> Tts:
        return self._espeak if voice in self._espeak_names else self._piper

    def fingerprint(self, voice: Optional[str] = None) -> str:
        return self._route(voice).fingerprint(voice)

    @property
    def concatenable(self) -> bool:
        return True

    def synthesize(
        self, text: str, voice: Optional[str] = None, prosody: Prosody = Prosody()
    ) -> bytes:
        return self._route(voice).synthesize(text, voice, prosody)

    def play(self, audio: bytes) -> None:
        # Piper's device and rate serve both engines -- __init__ checked
        # that they render at the same rate.
        self._piper.play(audio)


class CachedTts(Tts):
    """Caches another backend's synthesized audio on disk, keyed by what was
    said and by what would render it (`Tts.fingerprint`).

    Synthesis is the multi-second part of announcing a message -- `piper`'s
    process start, model load and inference (see Announcer._announce). A
    replay, a nightly-identical alert or a repeated message pays that twice
    for byte-identical output, so the second time is served from a file
    instead.

    Every cache error is non-fatal: on anything the filesystem refuses, this
    logs and falls through to the wrapped backend, so a full SD card or a
    read-only directory degrades speed, never playback.
    """

    def __init__(self, inner: Tts, cache_dir: str, ttl_s: float, max_bytes: int):
        self._inner = inner
        self._dir = Path(cache_dir)
        self._ttl_s = ttl_s
        self._max_bytes = max_bytes
        self._last_sweep = 0.0

    def fingerprint(self, voice: Optional[str] = None) -> str:
        return self._inner.fingerprint(voice)

    @property
    def concatenable(self) -> bool:
        return self._inner.concatenable

    def play(self, audio: bytes) -> None:
        self._inner.play(audio)

    def _path(self, text: str, voice: Optional[str], prosody: Prosody) -> Path:
        # Everything that changes the bytes: what renders this voice, the
        # per-message knobs, and the text itself.
        material = f"{self._inner.fingerprint(voice)}\0{prosody}\0{text}"
        key = hashlib.sha256(material.encode("utf-8"))
        return self._dir / f"{key.hexdigest()}.raw"

    def synthesize(
        self, text: str, voice: Optional[str] = None, prosody: Prosody = Prosody()
    ) -> bytes:
        path = self._path(text, voice, prosody)

        cached = self._read(path)
        if cached is not None:
            logger.info("Cache hit (%s): %s", path.name, preview(text))
            return cached

        audio = self._inner.synthesize(text, voice, prosody)
        # An empty result means the backend failed (a crashed `piper` exits
        # with empty stdout). Caching it would serve silence for the whole
        # TTL instead of retrying next time.
        if audio:
            self._write(path, audio)
            self._maybe_sweep()
        return audio

    def _read(self, path: Path) -> Optional[bytes]:
        try:
            age = time.time() - path.stat().st_mtime
            if age > self._ttl_s:
                path.unlink(missing_ok=True)
                return None
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("Cannot read cache entry %s: %s", path, exc)
            return None

    def _write(self, path: Path, audio: bytes) -> None:
        # Written to a temporary name and renamed, so a crash mid-write can't
        # leave a truncated entry behind -- that would later be played as
        # clipped audio rather than re-synthesized.
        tmp = path.with_suffix(".tmp")
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(audio)
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("Cannot write cache entry %s: %s", path, exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _maybe_sweep(self) -> None:
        # Rate-limited the same way IpRateLimiter._prune is, and for the same
        # reason: the scan is O(entries), so it must not run per call. No lock
        # -- only the announcer's single worker synthesizes, and two
        # overlapping sweeps would merely unlink the same expired files.
        now = time.monotonic()
        if now - self._last_sweep < SWEEP_INTERVAL_S:
            return
        self._last_sweep = now
        self._sweep()

    def _sweep(self) -> None:
        """Drops expired entries, then the oldest ones until the directory
        fits in `max_bytes` -- an SD card shouldn't fill up because a week of
        distinct messages each earned a 24h entry."""
        entries: List[Tuple[float, int, Path]] = []
        try:
            for entry in self._dir.iterdir():
                try:
                    if not entry.is_file():
                        continue
                    info = entry.stat()
                except OSError:
                    continue  # vanished or unreadable; nothing to reclaim here
                entries.append((info.st_mtime, info.st_size, entry))
        except OSError as exc:
            logger.warning("Cannot sweep cache in %s: %s", self._dir, exc)
            return

        cutoff = time.time() - self._ttl_s
        live: List[Tuple[int, Path]] = []
        for mtime, size, entry in sorted(entries, key=lambda item: item[0]):
            if mtime < cutoff:
                self._discard(entry)
            else:
                live.append((size, entry))

        total = sum(size for size, _ in live)
        for size, entry in live:  # oldest first, from the sort above
            if total <= self._max_bytes:
                break
            self._discard(entry)
            total -= size

    def _discard(self, entry: Path) -> None:
        try:
            entry.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Cannot drop cache entry %s: %s", entry, exc)
