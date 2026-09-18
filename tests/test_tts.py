import pytest

from krzykacz.tts import (
    PLAYBACK_TIMEOUT_SLACK_S,
    EspeakTts,
    PiperTts,
    Prosody,
    Tts,
    VoiceSpec,
)


def _capture_cmd(monkeypatch):
    """Records the argv each synthesize() would have run."""
    captured = []

    class FakeResult:
        stdout = b""

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return FakeResult()

    monkeypatch.setattr("krzykacz.tts.subprocess.run", fake_run)
    return captured


def test_say_default_impl_calls_synthesize_then_play_in_order():
    calls = []

    class RecordingTts(Tts):
        def fingerprint(self, voice=None):
            return f"recording:{voice}"

        def synthesize(self, text, voice=None, prosody=Prosody()):
            calls.append(("synthesize", text, voice))
            return b"audio-bytes"

        def play(self, audio):
            calls.append(("play", audio))

    RecordingTts().say("hej", voice="justyna")

    assert calls == [("synthesize", "hej", "justyna"), ("play", b"audio-bytes")]


def test_default_voice_must_be_in_voices_dict():
    with pytest.raises(ValueError):
        PiperTts(voices={"darkman": "/models/darkman.onnx"}, default_voice="justyna")


def test_model_path_resolves_known_voice():
    tts = PiperTts(
        voices={"darkman": "/models/darkman.onnx", "justyna": "/models/justyna.onnx"},
        default_voice="darkman",
    )
    assert tts._model_path("justyna") == "/models/justyna.onnx"


def test_model_path_falls_back_to_default_for_unknown_voice():
    tts = PiperTts(
        voices={"darkman": "/models/darkman.onnx", "justyna": "/models/justyna.onnx"},
        default_voice="darkman",
    )
    assert tts._model_path("nieznany") == "/models/darkman.onnx"


def test_model_path_falls_back_to_default_when_no_voice_given():
    tts = PiperTts(
        voices={"darkman": "/models/darkman.onnx", "justyna": "/models/justyna.onnx"},
        default_voice="darkman",
    )
    assert tts._model_path(None) == "/models/darkman.onnx"


def test_model_path_resolves_a_speaker_within_a_multi_speaker_voice():
    tts = PiperTts(
        voices={
            "darkman": "/models/darkman.onnx",
            "kopa": VoiceSpec("/models/pl_PL-tts-pl.onnx", 7),
        },
        default_voice="darkman",
    )
    assert tts._model_path("kopa") == "/models/pl_PL-tts-pl.onnx"


def test_synthesize_includes_speaker_flag_for_multi_speaker_voice(monkeypatch):
    captured = _capture_cmd(monkeypatch)
    tts = PiperTts(
        voices={
            "darkman": "/models/darkman.onnx",
            "kopa": VoiceSpec("/models/pl_PL-tts-pl.onnx", 7),
        },
        default_voice="darkman",
    )

    tts.synthesize("hej", voice="kopa")

    assert captured[0] == [
        "piper",
        "--model",
        "/models/pl_PL-tts-pl.onnx",
        "--output-raw",
        "--speaker",
        "7",
    ]


def test_unset_knobs_pass_no_flags(monkeypatch):
    captured = _capture_cmd(monkeypatch)
    tts = PiperTts(voices={"darkman": "/models/darkman.onnx"}, default_voice="darkman")

    tts.synthesize("hej")

    # No flags at all, so piper keeps its own defaults and the audio is
    # identical to what it was before these knobs existed.
    assert captured[0] == ["piper", "--model", "/models/darkman.onnx", "--output-raw"]


def test_speed_is_passed_as_the_inverse_length_scale(monkeypatch):
    captured = _capture_cmd(monkeypatch)
    tts = PiperTts(voices={"darkman": "/models/darkman.onnx"}, default_voice="darkman")

    tts.synthesize("hej", prosody=Prosody(speed=2.0))

    assert "--length_scale" in captured[0]
    assert captured[0][captured[0].index("--length_scale") + 1] == "0.5000"


def test_variation_and_rhythm_are_passed_through_unchanged(monkeypatch):
    captured = _capture_cmd(monkeypatch)
    tts = PiperTts(voices={"darkman": "/models/darkman.onnx"}, default_voice="darkman")

    tts.synthesize("hej", prosody=Prosody(variation=0.25, rhythm=0.9))

    cmd = captured[0]
    assert cmd[cmd.index("--noise_scale") + 1] == "0.2500"
    assert cmd[cmd.index("--noise_w") + 1] == "0.9000"


def test_per_message_knobs_override_the_instance_defaults(monkeypatch):
    captured = _capture_cmd(monkeypatch)
    tts = PiperTts(
        voices={"darkman": "/models/darkman.onnx"},
        default_voice="darkman",
        prosody=Prosody(speed=1.0, variation=0.5),
    )

    tts.synthesize("hej", prosody=Prosody(speed=2.0))

    cmd = captured[0]
    assert cmd[cmd.index("--length_scale") + 1] == "0.5000"
    # variation wasn't overridden, so the instance default still applies.
    assert cmd[cmd.index("--noise_scale") + 1] == "0.5000"


def test_instance_prosody_is_part_of_the_fingerprint():
    voices = {"darkman": "/models/darkman.onnx"}
    plain = PiperTts(voices=voices, default_voice="darkman")
    fast = PiperTts(voices=voices, default_voice="darkman", prosody=Prosody(speed=1.5))

    # Otherwise changing KRZYKACZ_PIPER_SPEED would serve cached audio
    # rendered at the old setting.
    assert plain.fingerprint() != fast.fingerprint()


def test_espeak_maps_speed_to_words_per_minute(monkeypatch):
    captured = _capture_cmd(monkeypatch)
    tts = EspeakTts(voice="pl")

    tts.synthesize("hej", prosody=Prosody(speed=2.0, variation=0.2))

    cmd = captured[0]
    assert cmd[cmd.index("-s") + 1] == str(EspeakTts.BASE_WORDS_PER_MINUTE * 2)
    # variation/rhythm have no espeak-ng equivalent and are ignored.
    assert "--noise_scale" not in cmd


def test_espeak_without_speed_passes_no_rate_flag(monkeypatch):
    captured = _capture_cmd(monkeypatch)

    EspeakTts(voice="pl").synthesize("hej")

    assert "-s" not in captured[0]


def test_synthesize_timeout_scales_with_text_length(monkeypatch):
    captured = []

    class FakeResult:
        stdout = b""

    def fake_run(cmd, **kwargs):
        captured.append(kwargs["timeout"])
        return FakeResult()

    monkeypatch.setattr("krzykacz.tts.subprocess.run", fake_run)
    tts = PiperTts(voices={"darkman": "/models/darkman.onnx"}, default_voice="darkman")

    tts.synthesize("a" * 10)
    tts.synthesize("a" * 1000)

    short_timeout, long_timeout = captured
    assert long_timeout > short_timeout


def test_play_timeout_scales_with_audio_length(monkeypatch):
    captured = []

    class FakePopen:
        def __init__(self, *args, **kwargs):
            pass

    def fake_communicate_or_kill(proc, data, timeout):
        captured.append(timeout)

    monkeypatch.setattr("krzykacz.tts.subprocess.Popen", FakePopen)
    monkeypatch.setattr("krzykacz.tts.communicate_or_kill", fake_communicate_or_kill)

    tts = PiperTts(
        voices={"darkman": "/models/darkman.onnx"}, default_voice="darkman", sample_rate=22050
    )

    five_seconds_of_audio = b"\x00" * (22050 * 2 * 5)
    tts.play(five_seconds_of_audio)

    assert captured[0] == pytest.approx(5.0 + PLAYBACK_TIMEOUT_SLACK_S, abs=0.1)
