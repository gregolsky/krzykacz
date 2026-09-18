import logging
import random

from krzykacz.protocol import MAX_CONTENT_BYTES, SPEED_RANGE, Effect, split_segments
from krzykacz.random_picks import (
    CURSES,
    INTENSITIES,
    STYLES,
    _SPEED_STEPS,
    random_curse_msg,
    random_sound_msg,
)


def test_every_curse_has_nonempty_text_within_the_content_limit():
    for curse in CURSES:
        assert curse.text
        assert len(curse.text.encode("utf-8")) < MAX_CONTENT_BYTES


def test_every_curse_has_a_recognized_intensity_and_style():
    for curse in CURSES:
        assert curse.intensity in INTENSITIES
        assert curse.style in STYLES


def test_no_duplicate_curse_texts():
    texts = [curse.text for curse in CURSES]
    assert len(texts) == len(set(texts))


def test_every_intensity_has_at_least_one_curse():
    for intensity in INTENSITIES:
        assert any(curse.intensity == intensity for curse in CURSES)


def test_every_style_has_at_least_one_curse():
    for style in STYLES:
        assert any(curse.style == style for curse in CURSES)


def test_intensity_filter_only_yields_that_intensity():
    rng = random.Random(0)
    for _ in range(50):
        msg = random_curse_msg(rng, ["v"], intensity="mild")
        matches = [c for c in CURSES if c.text == msg.content]
        assert matches and all(c.intensity == "mild" for c in matches)


def test_style_filter_only_yields_that_style():
    rng = random.Random(0)
    for _ in range(50):
        msg = random_curse_msg(rng, ["v"], style="grim")
        matches = [c for c in CURSES if c.text == msg.content]
        assert matches and all(c.style == "grim" for c in matches)


def test_intensity_and_style_together_intersect():
    rng = random.Random(0)
    for _ in range(50):
        msg = random_curse_msg(rng, ["v"], intensity="strong", style="modern")
        matches = [c for c in CURSES if c.text == msg.content]
        assert matches and all(
            c.intensity == "strong" and c.style == "modern" for c in matches
        )


def test_unrecognized_intensity_falls_back_to_the_full_library(caplog):
    rng = random.Random(0)
    with caplog.at_level(logging.WARNING, logger="krzykacz.random_picks"):
        msg = random_curse_msg(rng, ["v"], intensity="nonexistent")

    assert msg is not None
    assert "full library" in caplog.text


def test_empty_intersection_falls_back_to_the_full_library(caplog):
    # grim has no mild entries -- this combination matches nothing.
    assert not [c for c in CURSES if c.intensity == "mild" and c.style == "grim"]

    rng = random.Random(0)
    with caplog.at_level(logging.WARNING, logger="krzykacz.random_picks"):
        msg = random_curse_msg(rng, ["v"], intensity="mild", style="grim")

    assert msg is not None
    assert "full library" in caplog.text


def test_no_voices_configured_yields_none():
    rng = random.Random(0)
    assert random_curse_msg(rng, []) is None


def test_reproducible_with_a_seeded_rng():
    first = random_curse_msg(random.Random(0), ["a", "b", "c"])
    second = random_curse_msg(random.Random(0), ["a", "b", "c"])

    assert first == second


def test_picked_voice_is_always_from_the_supplied_list():
    rng = random.Random(0)
    voices = ["darkman", "justyna", "gosia"]
    for _ in range(50):
        msg = random_curse_msg(rng, voices)
        assert msg.voice in voices


def test_picked_speed_is_always_a_known_step_within_speed_range():
    rng = random.Random(0)
    low, high = SPEED_RANGE
    for _ in range(50):
        msg = random_curse_msg(rng, ["v"])
        assert msg.speed in _SPEED_STEPS
        assert low <= msg.speed <= high


def test_random_sound_excludes_names_with_extensions(tmp_path):
    (tmp_path / "fight").write_bytes(b"x")
    (tmp_path / "boom.ogg").write_bytes(b"x")

    rng = random.Random(0)
    for _ in range(20):
        msg = random_sound_msg(rng, str(tmp_path))
        assert msg.content == "<fight>"


def test_random_sound_excludes_names_with_a_star(tmp_path):
    # A name ending in "*<digits>" would be misread as a repeat-count
    # suffix on a different effect name once built into "<name>" -- see
    # random_sound_msg's docstring.
    (tmp_path / "fight").write_bytes(b"x")
    (tmp_path / "boom*3").write_bytes(b"x")

    rng = random.Random(0)
    for _ in range(20):
        msg = random_sound_msg(rng, str(tmp_path))
        assert msg.content == "<fight>"


def test_random_sound_msg_has_no_spoken_text(tmp_path):
    (tmp_path / "fight").write_bytes(b"x")

    msg = random_sound_msg(random.Random(0), str(tmp_path))

    assert split_segments(msg.content) == [Effect(name="fight", count=1)]


def test_random_sound_returns_none_for_empty_assets_dir(tmp_path):
    assert random_sound_msg(random.Random(0), str(tmp_path)) is None


def test_random_sound_returns_none_for_missing_assets_dir(tmp_path):
    assert random_sound_msg(random.Random(0), str(tmp_path / "nope")) is None


def test_random_sound_returns_none_when_only_extension_files_exist(tmp_path):
    (tmp_path / "boom.ogg").write_bytes(b"x")

    assert random_sound_msg(random.Random(0), str(tmp_path)) is None


def test_random_sound_excludes_names_containing_angle_brackets(tmp_path):
    (tmp_path / "fight").write_bytes(b"x")
    (tmp_path / "foo>bar").write_bytes(b"x")
    (tmp_path / "baz<qux").write_bytes(b"x")

    rng = random.Random(0)
    for _ in range(20):
        msg = random_sound_msg(rng, str(tmp_path))
        assert msg.content == "<fight>"
