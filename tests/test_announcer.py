import threading

from krzykacz.announcer import NO_SUCH_MESSAGE, Announcer, _with_terminal_punctuation
from krzykacz.effects import Effects
from krzykacz.light import Light
from krzykacz.protocol import Msg, Repeat
from krzykacz.tts import Tts


class FakeLight(Light):
    def __init__(self, events=None):
        self.calls = []
        self.events = events

    def on(self):
        self.calls.append("on")
        if self.events is not None:
            self.events.append("light.on")

    def off(self):
        self.calls.append("off")
        if self.events is not None:
            self.events.append("light.off")


class FakeTts(Tts):
    def __init__(self, fail_on=None, events=None):
        self.said = []
        self.said_with_voice = []
        self.synthesized = []
        self.played = []
        self.fail_on = fail_on
        self.events = events

    def synthesize(self, text, voice=None):
        self.said.append(text)
        self.said_with_voice.append((text, voice))
        self.synthesized.append(text)
        if self.events is not None:
            self.events.append("tts.synthesize")
        if self.fail_on and text == self.fail_on:
            raise RuntimeError("boom")
        return text.encode("utf-8")

    def play(self, audio):
        self.played.append(audio)
        if self.events is not None:
            self.events.append("tts.play")


class FakeEffects(Effects):
    def __init__(self, events=None):
        self.played = []
        self.events = events

    def play(self, path):
        self.played.append(path)
        if self.events is not None:
            self.events.append("effects.play")


def make_announcer(
    assets_dir, light=None, tts=None, effects=None, history_size=10, queue_size=10
):
    announcer = Announcer(
        light or FakeLight(),
        tts or FakeTts(),
        effects or FakeEffects(),
        assets_dir=str(assets_dir),
        history_size=history_size,
        queue_size=queue_size,
    )
    return announcer


def drain(announcer, timeout=2.0):
    """Blocks until the announcer's queue is fully processed."""
    done = threading.Event()

    def waiter():
        announcer._queue.join()
        done.set()

    threading.Thread(target=waiter, daemon=True).start()
    if not done.wait(timeout):
        raise TimeoutError("announcer did not drain in time")


def test_msg_is_spoken_and_lights_blink_then_on_then_off(tmp_path):
    light = FakeLight()
    tts = FakeTts()
    announcer = make_announcer(tmp_path, light=light, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="witaj swiecie"))
    drain(announcer)

    assert tts.said == ["witaj swiecie."]
    # blink(1) is on/off, then a sustained on, then off at the end.
    assert light.calls == ["on", "off", "on", "off"]


def test_repeat_plays_history_entry_without_readding_it(tmp_path):
    tts = FakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="pierwsza"))
    announcer.submit(Msg(content="druga"))
    announcer.submit(Repeat(number=-2))
    drain(announcer)

    assert tts.said == ["pierwsza.", "druga.", "pierwsza."]


def test_repeat_out_of_range_speaks_fallback(tmp_path):
    tts = FakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Repeat(number=-1))
    drain(announcer)

    assert tts.said == [_with_terminal_punctuation(NO_SUCH_MESSAGE)]


def test_queue_processes_in_fifo_order(tmp_path):
    tts = FakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    for i in range(5):
        announcer.submit(Msg(content=f"wiadomosc {i}"))
    drain(announcer)

    assert tts.said == [f"wiadomosc {i}." for i in range(5)]


def test_light_turns_off_even_if_tts_raises(tmp_path):
    light = FakeLight()
    tts = FakeTts(fail_on="wybuchnie.")
    announcer = make_announcer(tmp_path, light=light, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="wybuchnie"))
    drain(announcer)

    assert light.calls[-1] == "off"


def test_effect_tag_plays_sound_then_speaks_rest(tmp_path):
    (tmp_path / "boom.mp3").write_bytes(b"fake mp3")
    tts = FakeTts()
    effects = FakeEffects()
    announcer = make_announcer(tmp_path, tts=tts, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="<boom.mp3> Testy padły"))
    drain(announcer)

    assert effects.played == [tmp_path / "boom.mp3"]
    assert tts.said == ["Testy padły."]


def test_effect_tag_without_text_only_plays_sound(tmp_path):
    (tmp_path / "boom.mp3").write_bytes(b"fake mp3")
    tts = FakeTts()
    effects = FakeEffects()
    announcer = make_announcer(tmp_path, tts=tts, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="<boom.mp3>"))
    drain(announcer)

    assert effects.played == [tmp_path / "boom.mp3"]
    assert tts.said == []


def test_missing_effect_file_falls_back_to_speaking_rest(tmp_path):
    tts = FakeTts()
    effects = FakeEffects()
    announcer = make_announcer(tmp_path, tts=tts, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="<brak.mp3> Testy padły"))
    drain(announcer)

    assert effects.played == []
    assert tts.said == ["Testy padły."]


def test_voice_is_passed_through_to_tts(tmp_path):
    tts = FakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="czesc", voice="justyna"))
    drain(announcer)

    assert tts.said_with_voice == [("czesc.", "justyna")]


def test_repeat_replays_with_original_voice(tmp_path):
    tts = FakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="czesc", voice="justyna"))
    announcer.submit(Repeat(number=-1))
    drain(announcer)

    assert tts.said_with_voice == [("czesc.", "justyna"), ("czesc.", "justyna")]


def test_no_voice_specified_passes_none(tmp_path):
    tts = FakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="czesc"))
    drain(announcer)

    assert tts.said_with_voice == [("czesc.", None)]


def test_repeat_count_joins_content_with_separator(tmp_path):
    tts = FakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="Testy padły", repeat_count=3))
    drain(announcer)

    assert tts.said == ["Testy padły. Powtarzam! Testy padły. Powtarzam! Testy padły."]


def test_repeat_count_default_speaks_once(tmp_path):
    tts = FakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="pojedyncza"))
    drain(announcer)

    assert tts.said == ["pojedyncza."]


def test_repeat_count_with_effect_tag_only_repeats_spoken_part(tmp_path):
    (tmp_path / "boom.mp3").write_bytes(b"fake mp3")
    tts = FakeTts()
    effects = FakeEffects()
    announcer = make_announcer(tmp_path, tts=tts, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="<boom.mp3> Testy padły", repeat_count=2))
    drain(announcer)

    assert effects.played == [tmp_path / "boom.mp3"]
    assert tts.said == ["Testy padły. Powtarzam! Testy padły."]


def test_effect_path_traversal_is_rejected(tmp_path):
    outside = tmp_path.parent / "secret.mp3"
    outside.write_bytes(b"nope")
    try:
        tts = FakeTts()
        effects = FakeEffects()
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        announcer = make_announcer(assets_dir, tts=tts, effects=effects)
        announcer.start()

        announcer.submit(Msg(content="<../secret.mp3> tekst"))
        drain(announcer)

        assert effects.played == []
        # The tag wasn't a valid effect reference, so it's spoken as literal text.
        assert tts.said == ["<../secret.mp3> tekst."]
    finally:
        outside.unlink(missing_ok=True)


def test_with_terminal_punctuation_appends_dot_when_missing():
    assert _with_terminal_punctuation("bez kropki") == "bez kropki."


def test_with_terminal_punctuation_leaves_existing_punctuation():
    for text in ("już jest.", "pytanie?", "krzyk!", "dwukropek:", "średnik;", "wielokropek…"):
        assert _with_terminal_punctuation(text) == text


def test_with_terminal_punctuation_empty_string_untouched():
    assert _with_terminal_punctuation("") == ""


def test_with_terminal_punctuation_strips_trailing_whitespace_before_checking():
    assert _with_terminal_punctuation("koniec.   ") == "koniec."


def test_synthesis_happens_before_light_turns_on(tmp_path):
    events = []
    light = FakeLight(events=events)
    tts = FakeTts(events=events)
    effects = FakeEffects(events=events)
    announcer = make_announcer(tmp_path, light=light, tts=tts, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="test"))
    drain(announcer)

    assert events.index("tts.synthesize") < events.index("light.on")
    assert events.index("light.on") < events.index("tts.play")


def test_queue_drops_messages_beyond_max_size(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class BlockingLight(Light):
        def __init__(self):
            self.calls = []

        def on(self):
            self.calls.append("on")
            started.set()
            release.wait(timeout=5)

        def off(self):
            self.calls.append("off")

    light = BlockingLight()
    tts = FakeTts()
    announcer = make_announcer(tmp_path, light=light, tts=tts, queue_size=3)
    announcer.start()

    # First message is dequeued immediately and blocks inside light.on() --
    # the queue itself is empty again the moment the worker picks it up.
    assert announcer.submit(Msg(content="one")) is True
    assert started.wait(timeout=2), "worker never started processing the first message"

    for i in range(3):
        assert announcer.submit(Msg(content=f"queued-{i}")) is True
    assert announcer.submit(Msg(content="overflow")) is False  # queue is full (3/3)

    release.set()
    drain(announcer, timeout=5)

    assert tts.said == ["one.", "queued-0.", "queued-1.", "queued-2."]


def test_effect_plays_after_light_on_and_before_speech(tmp_path):
    (tmp_path / "boom.mp3").write_bytes(b"fake")
    events = []
    light = FakeLight(events=events)
    tts = FakeTts(events=events)
    effects = FakeEffects(events=events)
    announcer = make_announcer(tmp_path, light=light, tts=tts, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="<boom.mp3> test"))
    drain(announcer)

    synth_idx = events.index("tts.synthesize")
    first_light_on_idx = events.index("light.on")
    effect_idx = events.index("effects.play")
    play_idx = events.index("tts.play")
    last_off_idx = len(events) - 1 - events[::-1].index("light.off")

    assert synth_idx < first_light_on_idx
    assert first_light_on_idx < effect_idx < play_idx
    assert play_idx < last_off_idx
