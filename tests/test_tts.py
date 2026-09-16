import pytest

from krzykacz.tts import PLAYBACK_TIMEOUT_SLACK_S, PiperTts, Tts


def test_say_default_impl_calls_synthesize_then_play_in_order():
    calls = []

    class RecordingTts(Tts):
        def synthesize(self, text, voice=None):
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
