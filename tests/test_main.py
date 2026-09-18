import pytest
from conftest import make_config

from krzykacz.__main__ import build_tts
from krzykacz.tts import CachedTts, EspeakTts, PiperTts, Prosody


def test_piper_backend_without_cache_returns_plain_piper_tts():
    tts = build_tts(make_config(tts_backend="piper", cache_ttl=0))

    assert isinstance(tts, PiperTts)


def test_espeak_backend_without_cache_returns_plain_espeak_tts():
    tts = build_tts(make_config(tts_backend="espeak", cache_ttl=0))

    assert isinstance(tts, EspeakTts)


def test_positive_cache_ttl_wraps_the_backend(tmp_path):
    tts = build_tts(
        make_config(tts_backend="piper", cache_ttl=60, cache_dir=str(tmp_path / "cache"))
    )

    assert isinstance(tts, CachedTts)


def test_unknown_backend_raises():
    with pytest.raises(ValueError):
        build_tts(make_config(tts_backend="nope"))


def test_configured_prosody_reaches_the_backend():
    prosody = Prosody(speed=1.5, variation=0.2)

    tts = build_tts(make_config(tts_backend="piper", prosody=prosody))

    assert tts.prosody == prosody
