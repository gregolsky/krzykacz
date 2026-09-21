import pytest
from conftest import make_config

from krzykacz.__main__ import build_tts, voice_names
from krzykacz.tts import CachedTts, EspeakTts, PiperTts, Prosody, RoutedTts


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


def test_piper_backend_with_espeak_voices_routes_between_the_engines():
    tts = build_tts(
        make_config(tts_backend="piper", cache_ttl=0, espeak_voices={"espeak_male": "pl+m3"})
    )

    assert isinstance(tts, RoutedTts)


def test_espeak_backend_keeps_a_single_engine_and_knows_the_aliases():
    tts = build_tts(
        make_config(tts_backend="espeak", cache_ttl=0, espeak_voices={"espeak_male": "pl+m3"})
    )

    assert isinstance(tts, EspeakTts)
    assert tts.voices == {"espeak_male": "pl+m3"}


def test_voice_names_lists_piper_voices_then_espeak_aliases():
    cfg = make_config(tts_backend="piper", espeak_voices={"espeak_male": "pl+m3"})

    names, default = voice_names(cfg)

    assert names == ["darkman", "espeak_male"]
    assert default == "darkman"


def test_voice_names_under_espeak_backend_defaults_to_the_language_code():
    cfg = make_config(tts_backend="espeak", espeak_voices={"espeak_male": "pl+m3"})

    names, default = voice_names(cfg)

    assert names == ["pl", "espeak_male"]
    assert default == "pl"
