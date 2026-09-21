from conftest import make_config

from krzykacz.config import DEFAULT_ESPEAK_VOICES, Config, _parse_espeak_voices, _parse_voices
from krzykacz.tts import Prosody, VoiceSpec


def test_parse_voices_empty_input_yields_empty_dict():
    assert _parse_voices(None) == {}
    assert _parse_voices("") == {}


def test_parse_voices_single_speaker_entry():
    assert _parse_voices("justyna=/models/justyna.onnx") == {
        "justyna": VoiceSpec("/models/justyna.onnx", None)
    }


def test_parse_voices_multiple_entries():
    assert _parse_voices("a=/a.onnx,b=/b.onnx") == {
        "a": VoiceSpec("/a.onnx", None),
        "b": VoiceSpec("/b.onnx", None),
    }


def test_parse_voices_multi_speaker_entry_has_speaker_index():
    assert _parse_voices("staszczyk=/models/pl_PL-tts-pl.onnx:0") == {
        "staszczyk": VoiceSpec("/models/pl_PL-tts-pl.onnx", 0)
    }


def test_parse_voices_mixes_single_and_multi_speaker_entries():
    result = _parse_voices("darkman=/d.onnx,staszczyk=/multi.onnx:0,kopa=/multi.onnx:7")

    assert result == {
        "darkman": VoiceSpec("/d.onnx", None),
        "staszczyk": VoiceSpec("/multi.onnx", 0),
        "kopa": VoiceSpec("/multi.onnx", 7),
    }


def test_parse_voices_malformed_entry_without_equals_is_skipped():
    assert _parse_voices("bad-entry,ok=/ok.onnx") == {"ok": VoiceSpec("/ok.onnx", None)}


def test_parse_voices_strips_whitespace_around_entries():
    assert _parse_voices(" a = /a.onnx , b = /b.onnx:2 ") == {
        "a": VoiceSpec("/a.onnx", None),
        "b": VoiceSpec("/b.onnx", 2),
    }


def test_parse_voices_non_numeric_suffix_after_colon_is_kept_as_path():
    # Only a fully-numeric suffix after the last ":" is a speaker index;
    # anything else is treated as part of the (admittedly unusual) path.
    assert _parse_voices("weird=/models/voice:latest.onnx") == {
        "weird": VoiceSpec("/models/voice:latest.onnx", None)
    }


def test_from_env_leaves_delivery_knobs_unset_by_default(monkeypatch):
    monkeypatch.setenv("KRZYKACZ_TOPIC", "test")
    for name in ("KRZYKACZ_PIPER_SPEED", "KRZYKACZ_PIPER_VARIATION", "KRZYKACZ_PIPER_RHYTHM"):
        monkeypatch.delenv(name, raising=False)

    cfg = Config.from_env()

    # All None -> no piper flags -> piper's own defaults.
    assert cfg.prosody == Prosody()


def test_from_env_reads_and_clamps_delivery_knobs(monkeypatch):
    monkeypatch.setenv("KRZYKACZ_TOPIC", "test")
    monkeypatch.setenv("KRZYKACZ_PIPER_SPEED", "99")
    monkeypatch.setenv("KRZYKACZ_PIPER_VARIATION", "0.25")
    monkeypatch.setenv("KRZYKACZ_PIPER_RHYTHM", "nonsense")

    cfg = Config.from_env()

    assert cfg.prosody.speed == 2.0  # clamped to SPEED_RANGE
    assert cfg.prosody.variation == 0.25
    assert cfg.prosody.rhythm is None  # unparseable -> left to piper


def test_from_env_cache_defaults_to_a_day_in_var_cache(monkeypatch):
    monkeypatch.setenv("KRZYKACZ_TOPIC", "test")
    for name in ("KRZYKACZ_CACHE_DIR", "KRZYKACZ_CACHE_TTL", "KRZYKACZ_CACHE_MAX_MB"):
        monkeypatch.delenv(name, raising=False)

    cfg = Config.from_env()

    # Matches CacheDirectory=krzykacz in the systemd unit.
    assert cfg.cache_dir == "/var/cache/krzykacz"
    assert cfg.cache_ttl == 86400
    assert cfg.cache_max_mb == 200


def test_piper_voices_merges_default_and_extra_as_voicespecs():
    cfg = make_config(
        piper_default_voice="darkman",
        piper_model="/models/darkman.onnx",
        piper_extra_voices={"staszczyk": VoiceSpec("/multi.onnx", 0)},
    )

    assert cfg.piper_voices == {
        "darkman": VoiceSpec("/models/darkman.onnx", None),
        "staszczyk": VoiceSpec("/multi.onnx", 0),
    }


def test_espeak_voices_default_to_the_builtin_set_when_unset():
    assert _parse_espeak_voices(None) == DEFAULT_ESPEAK_VOICES


def test_espeak_voices_builtin_set_has_male_and_female_options():
    assert len(DEFAULT_ESPEAK_VOICES) == 10
    assert DEFAULT_ESPEAK_VOICES["espeak_male"] == "pl+m3"
    assert DEFAULT_ESPEAK_VOICES["espeak_female"] == "pl+f3"


def test_espeak_voices_explicitly_empty_turns_them_off():
    assert _parse_espeak_voices("") == {}


def test_espeak_voices_parse_name_to_spec_and_skip_malformed():
    assert _parse_espeak_voices(" a = pl+m1 ,bad, b=pl+f2,c=") == {"a": "pl+m1", "b": "pl+f2"}


def test_from_env_reads_espeak_voices(monkeypatch):
    monkeypatch.setenv("KRZYKACZ_TOPIC", "t")
    monkeypatch.setenv("KRZYKACZ_ESPEAK_VOICES", "x=pl+m1")

    assert Config.from_env().espeak_voices == {"x": "pl+m1"}
