import threading

from krzykacz.announcer import NO_SUCH_MESSAGE, Announcer, _with_terminal_punctuation
from krzykacz.effects import Effects
from krzykacz.light import Light
from krzykacz.protocol import MAX_SPOKEN_BYTES, REPEAT_SEPARATOR, Msg, Repeat
from krzykacz.tts import CachedTts, Prosody, Tts


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
    # concatenable stays at the ABC's default (False), so these fakes
    # exercise the joined-text repeat path. ConcatenatingFakeTts below opts in.
    def __init__(self, fail_on=None, events=None):
        self.said = []
        self.said_with_voice = []
        self.prosodies = []
        self.synthesized = []
        self.played = []
        self.fail_on = fail_on
        self.events = events

    def fingerprint(self, voice=None):
        return f"fake:{voice}"

    def synthesize(self, text, voice=None, prosody=Prosody()):
        self.said.append(text)
        self.said_with_voice.append((text, voice))
        self.prosodies.append(prosody)
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


class ConcatenatingFakeTts(FakeTts):
    """Stands in for a raw-PCM backend: its output can be joined, so the
    announcer renders the body and the separator once each."""

    @property
    def concatenable(self):
        return True


class FakeEffects(Effects):
    """decode() records which resolved path was asked for (`played`, kept
    under its old name since it answers the same question existing tests
    ask -- "which effect files got used") and returns deterministic bytes
    keyed by filename, so a test can tell two different effects apart.
    play_pcm() records what it was actually handed to play, separately --
    that's what Announcer calls during the light-on window."""

    def __init__(self, events=None, fail_decode_on=None):
        self.played = []
        self.play_pcm_calls = []
        self.events = events
        self.fail_decode_on = fail_decode_on

    def decode(self, path):
        self.played.append(path)
        if self.events is not None:
            self.events.append("effects.decode")
        if self.fail_decode_on and path.name == self.fail_decode_on:
            raise RuntimeError("boom")
        return f"pcm:{path.name}".encode()

    def play_pcm(self, data):
        self.play_pcm_calls.append(data)
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
    assert effects.play_pcm_calls == [b"pcm:boom.mp3"]
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


def test_repeat_synthesizes_body_and_separator_once_on_a_concatenable_backend(tmp_path):
    tts = ConcatenatingFakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="Testy padły", repeat_count=3))
    drain(announcer)

    # Two syntheses instead of one big one, and both are cacheable alone.
    assert tts.said == ["Testy padły.", REPEAT_SEPARATOR]
    body = "Testy padły.".encode("utf-8")
    separator = REPEAT_SEPARATOR.encode("utf-8")
    assert tts.played == [body + separator + body + separator + body]


def test_repeat_once_synthesizes_no_separator_on_a_concatenable_backend(tmp_path):
    tts = ConcatenatingFakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="Testy padły"))
    drain(announcer)

    assert tts.said == ["Testy padły."]


def test_concatenated_repeats_drop_whole_copies_to_stay_in_budget(tmp_path):
    tts = ConcatenatingFakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="a" * 500, repeat_count=10))
    drain(announcer)

    # 3 copies * 501 bytes + 2 separators * 12 = 1527, within
    # MAX_SPOKEN_BYTES -- and no copy is cut mid-word, unlike the
    # joined-text path's truncation.
    body = ("a" * 500 + ".").encode("utf-8")
    separator = REPEAT_SEPARATOR.encode("utf-8")
    assert tts.played == [separator.join([body] * 3)]


def test_delivery_knobs_reach_the_backend(tmp_path):
    tts = FakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="szybko", speed=1.5, variation=0.2, rhythm=0.9))
    drain(announcer)

    assert tts.prosodies == [Prosody(speed=1.5, variation=0.2, rhythm=0.9)]


def test_unset_knobs_stay_none_so_the_backend_keeps_its_defaults(tmp_path):
    tts = FakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="zwyczajnie"))
    drain(announcer)

    assert tts.prosodies == [Prosody()]


def test_separator_is_rendered_with_the_same_knobs_as_the_body(tmp_path):
    tts = ConcatenatingFakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="raz", repeat_count=2, speed=1.4))
    drain(announcer)

    # A separator at a different tempo would jump out of the message.
    assert tts.prosodies == [Prosody(speed=1.4), Prosody(speed=1.4)]


def test_repeat_on_a_cached_concatenable_backend_caches_body_and_separator_separately(tmp_path):
    # Composition of two features: a repeat on a concatenable backend
    # synthesizes the body and the separator once each (see
    # test_repeat_synthesizes_body_and_separator_once_...), and CachedTts
    # caches per synthesize() call -- so each piece should land as its own
    # cache entry, and a second identical repeat should touch the wrapped
    # backend zero times, not twice.
    inner = ConcatenatingFakeTts()
    cached = CachedTts(inner, str(tmp_path / "cache"), ttl_s=3600, max_bytes=10_000_000)
    announcer = make_announcer(tmp_path, tts=cached)
    announcer.start()

    announcer.submit(Msg(content="Testy padły", repeat_count=3))
    drain(announcer)

    assert set(inner.said) == {"Testy padły.", REPEAT_SEPARATOR}
    assert len(list((tmp_path / "cache").iterdir())) == 2
    first_audio = inner.played[-1]

    announcer.submit(Msg(content="Testy padły", repeat_count=3))
    drain(announcer)

    # Both pieces served from cache -- no new synthesize() calls -- and the
    # replayed audio is byte-identical to the first time.
    assert len(inner.said) == 2
    assert inner.played[-1] == first_audio


def test_repeat_count_default_speaks_once(tmp_path):
    tts = FakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="pojedyncza"))
    drain(announcer)

    assert tts.said == ["pojedyncza."]


def test_repeat_count_with_effect_tag_replays_the_whole_sequence(tmp_path):
    # Approved behaviour: `repeat` now replays sounds and speech alike, not
    # just the spoken part -- see krzykacz.announcer.Announcer._render_with_effects.
    (tmp_path / "boom.mp3").write_bytes(b"fake mp3")
    tts = FakeTts()
    effects = FakeEffects()
    announcer = make_announcer(tmp_path, tts=tts, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="<boom.mp3> Testy padły", repeat_count=2))
    drain(announcer)

    # The effect is decoded once and reused for both passes -- not
    # re-decoded per repeat.
    assert effects.played == [tmp_path / "boom.mp3"]
    assert effects.play_pcm_calls == [b"pcm:boom.mp3", b"pcm:boom.mp3"]
    # Likewise the spoken body and the "Powtarzam!" separator are each
    # synthesized once and reused.
    assert tts.said == ["Testy padły.", "Powtarzam!"]
    assert tts.played == [
        "Testy padły.".encode("utf-8"),
        "Powtarzam!".encode("utf-8"),
        "Testy padły.".encode("utf-8"),
    ]


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


def test_effects_decode_also_happens_before_light_turns_on(tmp_path):
    (tmp_path / "boom.mp3").write_bytes(b"fake")
    events = []
    light = FakeLight(events=events)
    effects = FakeEffects(events=events)
    announcer = make_announcer(tmp_path, light=light, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="<boom.mp3>"))
    drain(announcer)

    assert events.index("effects.decode") < events.index("light.on")


def test_repeat_count_is_truncated_to_max_spoken_bytes(tmp_path):
    tts = FakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="a" * 500, repeat_count=10))
    drain(announcer)

    assert len(tts.said) == 1
    assert len(tts.said[0].encode("utf-8")) <= MAX_SPOKEN_BYTES


def test_snapshot_shows_pending_and_playing_while_worker_is_blocked(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class BlockingLight(Light):
        def on(self):
            started.set()
            release.wait(timeout=5)

        def off(self):
            pass

    announcer = make_announcer(tmp_path, light=BlockingLight())
    announcer.start()

    announcer.submit(Msg(content="one"))
    assert started.wait(timeout=2), "worker never started processing the first message"
    announcer.submit(Msg(content="two"))

    snapshot = announcer.snapshot()

    release.set()
    drain(announcer)

    assert snapshot["playing"] == Msg(content="one")
    assert snapshot["pending"] == [Msg(content="two")]


def test_snapshot_is_idle_after_draining(tmp_path):
    announcer = make_announcer(tmp_path)
    announcer.start()

    announcer.submit(Msg(content="one"))
    drain(announcer)

    assert announcer.snapshot() == {"playing": None, "pending": []}


# --- Interleaved sounds/speech (issue #1: multiple sounds in one message) ---


def test_multiple_effects_interleaved_with_speech_play_in_order(tmp_path):
    (tmp_path / "game_over.mp3").write_bytes(b"1")
    (tmp_path / "fight.mp3").write_bytes(b"2")
    events = []
    tts = FakeTts(events=events)
    effects = FakeEffects(events=events)
    announcer = make_announcer(tmp_path, tts=tts, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="<game_over.mp3> Testy padły <fight.mp3> Naprawiam"))
    drain(announcer)

    assert effects.played == [tmp_path / "game_over.mp3", tmp_path / "fight.mp3"]
    assert effects.play_pcm_calls == [b"pcm:game_over.mp3", b"pcm:fight.mp3"]
    assert tts.said == ["Testy padły.", "Naprawiam."]
    # Playback order matches the order the tags/text appeared in the message.
    play_events = [e for e in events if e in ("effects.play", "tts.play")]
    assert play_events == ["effects.play", "tts.play", "effects.play", "tts.play"]


def test_adjacent_effect_tags_are_decoded_separately_but_played_as_one_clip(tmp_path):
    # The issue's case: a run of the same sound, played back to back with no
    # gap -- see krzykacz.effects.Effects.decode's docstring on why merging
    # at the PCM level (one play_pcm call) is both cheaper and gapless
    # compared to one play() call per hit.
    (tmp_path / "step.mp3").write_bytes(b"1")
    tts = FakeTts()
    effects = FakeEffects()
    announcer = make_announcer(tmp_path, tts=tts, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="<step.mp3><step.mp3><step.mp3> Ktoś idzie"))
    drain(announcer)

    assert effects.played == [tmp_path / "step.mp3"] * 3
    assert effects.play_pcm_calls == [b"pcm:step.mp3" * 3]
    assert tts.said == ["Ktoś idzie."]


def test_effect_star_suffix_plays_that_many_copies_as_one_clip(tmp_path):
    (tmp_path / "step.mp3").write_bytes(b"1")
    effects = FakeEffects()
    announcer = make_announcer(tmp_path, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="<step.mp3*6> Ktoś idzie"))
    drain(announcer)

    assert effects.played == [tmp_path / "step.mp3"]  # decoded once
    assert effects.play_pcm_calls == [b"pcm:step.mp3" * 6]  # played six times over


def test_unresolvable_effect_among_several_is_skipped_not_fatal(tmp_path):
    (tmp_path / "boom.mp3").write_bytes(b"1")
    tts = FakeTts()
    effects = FakeEffects()
    announcer = make_announcer(tmp_path, tts=tts, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="<brak.mp3> Uwaga <boom.mp3> Teraz"))
    drain(announcer)

    assert effects.played == [tmp_path / "boom.mp3"]
    assert effects.play_pcm_calls == [b"pcm:boom.mp3"]
    assert tts.said == ["Uwaga.", "Teraz."]


def test_effect_decode_failure_does_not_block_the_rest_of_the_message(tmp_path):
    (tmp_path / "boom.mp3").write_bytes(b"1")
    (tmp_path / "bad.mp3").write_bytes(b"2")
    tts = FakeTts()
    effects = FakeEffects(fail_decode_on="bad.mp3")
    announcer = make_announcer(tmp_path, tts=tts, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="<bad.mp3> Uwaga <boom.mp3> Teraz"))
    drain(announcer)

    assert effects.play_pcm_calls == [b"pcm:boom.mp3"]
    assert tts.said == ["Uwaga.", "Teraz."]


def test_no_effects_message_still_uses_the_single_synthesis_fast_path(tmp_path):
    # A message with no tags at all must still go through the original
    # single-Speech-segment path (_render_plain), not the general
    # per-run renderer -- guards the repeat/cache optimizations above,
    # which only hold for that fast path.
    tts = ConcatenatingFakeTts()
    announcer = make_announcer(tmp_path, tts=tts)
    announcer.start()

    announcer.submit(Msg(content="zwykla wiadomosc"))
    drain(announcer)

    assert tts.said == ["zwykla wiadomosc."]


def test_effects_only_message_speaks_nothing(tmp_path):
    (tmp_path / "boom.mp3").write_bytes(b"1")
    tts = FakeTts()
    effects = FakeEffects()
    announcer = make_announcer(tmp_path, tts=tts, effects=effects)
    announcer.start()

    announcer.submit(Msg(content="<boom.mp3>"))
    drain(announcer)

    assert tts.said == []
    assert effects.play_pcm_calls == [b"pcm:boom.mp3"]
