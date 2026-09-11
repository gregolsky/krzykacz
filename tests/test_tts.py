import pytest

from krzykacz.tts import PiperTts, Tts


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
