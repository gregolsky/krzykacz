import os

from krzykacz.tts import SWEEP_INTERVAL_S, CachedTts, PiperTts, Prosody, Tts, VoiceSpec


class CountingTts(Tts):
    """Renders `text` as bytes and counts how often it was asked to."""

    def __init__(self, print_name="counting", concatenable=False):
        self.calls = []
        self._print_name = print_name
        self._concatenable = concatenable

    def fingerprint(self, voice=None):
        return f"{self._print_name}:{voice}"

    @property
    def concatenable(self):
        return self._concatenable

    def synthesize(self, text, voice=None, prosody=Prosody()):
        self.calls.append((text, voice, prosody))
        return f"audio:{text}:{voice}:{prosody.speed}".encode("utf-8")

    def play(self, audio):
        pass


def make_cache(tmp_path, inner=None, ttl_s=3600, max_bytes=10_000_000):
    return CachedTts(inner or CountingTts(), str(tmp_path / "cache"), ttl_s, max_bytes)


def test_first_call_synthesizes_and_second_is_served_from_disk(tmp_path):
    inner = CountingTts()
    cache = make_cache(tmp_path, inner)

    first = cache.synthesize("Testy padły", voice="justyna")
    second = cache.synthesize("Testy padły", voice="justyna")

    assert first == second
    assert len(inner.calls) == 1


def test_different_text_is_a_separate_entry(tmp_path):
    inner = CountingTts()
    cache = make_cache(tmp_path, inner)

    cache.synthesize("jedno")
    cache.synthesize("drugie")

    assert len(inner.calls) == 2


def test_different_voice_is_a_separate_entry(tmp_path):
    inner = CountingTts()
    cache = make_cache(tmp_path, inner)

    cache.synthesize("to samo", voice="justyna")
    cache.synthesize("to samo", voice="darkman")

    assert len(inner.calls) == 2


def test_two_speakers_of_one_multi_speaker_model_do_not_share_an_entry(tmp_path):
    # The case that makes fingerprint() resolve the model+speaker rather
    # than trust the voice name: one .onnx file, two voices, same text.
    piper = PiperTts(
        voices={
            "staszczyk": VoiceSpec("/models/pl_PL-tts-pl.onnx", 0),
            "kopa": VoiceSpec("/models/pl_PL-tts-pl.onnx", 7),
        },
        default_voice="staszczyk",
    )

    assert piper.fingerprint("staszczyk") != piper.fingerprint("kopa")


def test_prosody_is_part_of_the_key(tmp_path):
    inner = CountingTts()
    cache = make_cache(tmp_path, inner)

    cache.synthesize("to samo", prosody=Prosody(speed=1.0))
    cache.synthesize("to samo", prosody=Prosody(speed=1.5))

    assert len(inner.calls) == 2


def test_prosody_is_forwarded_to_the_backend(tmp_path):
    inner = CountingTts()
    cache = make_cache(tmp_path, inner)

    cache.synthesize("hej", voice="justyna", prosody=Prosody(speed=1.5, variation=0.2))

    assert inner.calls == [("hej", "justyna", Prosody(speed=1.5, variation=0.2))]


def test_entry_older_than_the_ttl_is_resynthesized(tmp_path):
    inner = CountingTts()
    cache = make_cache(tmp_path, inner, ttl_s=60)

    cache.synthesize("stare")
    for entry in (tmp_path / "cache").iterdir():
        aged = os.stat(entry).st_mtime - 120
        os.utime(entry, (aged, aged))
    cache.synthesize("stare")

    assert len(inner.calls) == 2


def test_expired_entry_is_removed_on_the_miss(tmp_path):
    cache = make_cache(tmp_path, ttl_s=60)
    cache.synthesize("stare")
    (entry,) = list((tmp_path / "cache").iterdir())
    aged = os.stat(entry).st_mtime - 120
    os.utime(entry, (aged, aged))

    cache.synthesize("stare")

    # Re-created under the same key rather than left as a stale duplicate.
    assert len(list((tmp_path / "cache").iterdir())) == 1


class FailingTts(CountingTts):
    def synthesize(self, text, voice=None, prosody=Prosody()):
        super().synthesize(text, voice, prosody)
        return b""


def test_empty_audio_is_not_cached(tmp_path):
    inner = FailingTts()
    cache = make_cache(tmp_path, inner)

    cache.synthesize("nic")
    cache.synthesize("nic")

    # Caching a failed synthesis would serve silence for the whole TTL.
    assert len(inner.calls) == 2
    # Nothing was written, so the directory was never even created.
    assert not (tmp_path / "cache").exists()


def test_unwritable_cache_directory_still_synthesizes(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_bytes(b"x")
    inner = CountingTts()
    cache = CachedTts(inner, str(blocker / "cache"), 3600, 10_000_000)

    audio = cache.synthesize("mimo wszystko")

    assert audio == inner.synthesize("mimo wszystko", None, Prosody())
    assert len(inner.calls) == 2  # ours plus the assertion's own call


def test_no_temporary_files_are_left_behind(tmp_path):
    cache = make_cache(tmp_path)

    cache.synthesize("czysto")

    assert [entry.suffix for entry in (tmp_path / "cache").iterdir()] == [".raw"]


def test_sweep_drops_expired_entries(tmp_path):
    cache = make_cache(tmp_path, ttl_s=60)
    cache.synthesize("pierwsze")
    (stale,) = list((tmp_path / "cache").iterdir())
    aged = os.stat(stale).st_mtime - 120
    os.utime(stale, (aged, aged))

    # Force the next write to sweep rather than wait out the interval.
    cache._last_sweep -= SWEEP_INTERVAL_S + 1
    cache.synthesize("drugie")

    remaining = [entry.name for entry in (tmp_path / "cache").iterdir()]
    assert stale.name not in remaining
    assert len(remaining) == 1


def test_sweep_evicts_oldest_entries_over_the_size_cap(tmp_path):
    # Cap set to exactly one entry, measured rather than hardcoded, so two
    # entries can never both fit.
    probe = make_cache(tmp_path / "probe")
    probe.synthesize("najstarsze")
    (probe_entry,) = list((tmp_path / "probe" / "cache").iterdir())

    cache = CachedTts(
        CountingTts(), str(tmp_path / "cache"), 3600, max_bytes=probe_entry.stat().st_size
    )
    cache.synthesize("najstarsze")
    (oldest,) = list((tmp_path / "cache").iterdir())
    older = os.stat(oldest).st_mtime - 30
    os.utime(oldest, (older, older))

    cache._last_sweep -= SWEEP_INTERVAL_S + 1
    cache.synthesize("nowsze")

    remaining = [entry.name for entry in (tmp_path / "cache").iterdir()]
    assert oldest.name not in remaining
    assert len(remaining) == 1


def test_sweep_is_rate_limited_between_writes(tmp_path):
    cache = make_cache(tmp_path, ttl_s=60)

    cache.synthesize("pierwsze")
    (stale,) = list((tmp_path / "cache").iterdir())
    aged = os.stat(stale).st_mtime - 120
    os.utime(stale, (aged, aged))
    cache.synthesize("drugie")  # within the interval -> no sweep

    # Still there: the scan is O(entries), so it mustn't run on every write.
    assert stale.name in [entry.name for entry in (tmp_path / "cache").iterdir()]


def test_fingerprint_and_concatenable_are_delegated(tmp_path):
    inner = CountingTts(print_name="inner", concatenable=True)
    cache = make_cache(tmp_path, inner)

    assert cache.fingerprint("justyna") == "inner:justyna"
    assert cache.concatenable is True


def test_play_is_delegated(tmp_path):
    played = []

    class RecordingTts(CountingTts):
        def play(self, audio):
            played.append(audio)

    cache = make_cache(tmp_path, RecordingTts())
    cache.play(b"pcm")

    assert played == [b"pcm"]
