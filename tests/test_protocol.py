from krzykacz.protocol import (
    MAX_CONTENT_BYTES,
    MAX_EFFECT_REPEAT,
    MAX_EFFECTS,
    MAX_REPEAT_COUNT,
    RHYTHM_RANGE,
    SPEED_RANGE,
    VARIATION_RANGE,
    Effect,
    Msg,
    Repeat,
    Speech,
    parse,
    parse_tags,
    split_segments,
)


def test_plain_body_no_tags_is_msg():
    assert parse("zwykly tekst") == Msg(content="zwykly tekst")


def test_body_with_none_tags_is_same_as_no_tags():
    assert parse("zwykly tekst", None) == Msg(content="zwykly tekst")


def test_body_with_empty_tags_is_msg():
    assert parse("zwykly tekst", []) == Msg(content="zwykly tekst")


def test_voice_tag_sets_voice():
    assert parse("czesc", ["voice=justyna"]) == Msg(content="czesc", voice="justyna")


def test_repeat_tag_sets_repeat_count():
    assert parse("czesc", ["repeat=3"]) == Msg(content="czesc", repeat_count=3)


def test_voice_and_repeat_tags_combined():
    assert parse("czesc", ["voice=justyna", "repeat=2"]) == Msg(
        content="czesc", voice="justyna", repeat_count=2
    )


def test_speed_variation_and_rhythm_tags_are_parsed():
    assert parse("czesc", ["speed=1.5", "variation=0.2", "rhythm=0.9"]) == Msg(
        content="czesc", speed=1.5, variation=0.2, rhythm=0.9
    )


def test_delivery_knobs_default_to_none_when_absent():
    msg = parse("czesc")

    # None means "leave this instance's configured value alone".
    assert (msg.speed, msg.variation, msg.rhythm) == (None, None, None)


def test_out_of_range_knobs_are_clamped_not_rejected():
    fast = parse("czesc", ["speed=99"])
    slow = parse("czesc", ["speed=0.01"])

    assert (fast.speed, slow.speed) == (SPEED_RANGE[1], SPEED_RANGE[0])


def test_variation_and_rhythm_are_clamped_to_their_own_ranges():
    msg = parse("czesc", ["variation=9", "rhythm=-4"])

    assert (msg.variation, msg.rhythm) == (VARIATION_RANGE[1], RHYTHM_RANGE[0])


def test_non_numeric_knob_is_ignored_rather_than_failing_the_message():
    msg = parse("czesc", ["speed=szybko", "variation=0.3"])

    assert msg.speed is None
    assert msg.variation == 0.3
    assert msg.content == "czesc"


def test_replay_tag_produces_repeat_envelope():
    assert parse("", ["replay=-2"]) == Repeat(number=-2)


def test_replay_tag_takes_precedence_over_body():
    assert parse("ignored text", ["replay=-1"]) == Repeat(number=-1)


def test_replay_tag_invalid_number_falls_back_to_minus_one():
    assert parse("", ["replay=nope"]) == Repeat(number=-1)


def test_replay_tag_empty_value_falls_back_to_minus_one():
    assert parse("", ["replay="]) == Repeat(number=-1)


def test_tag_without_equals_is_ignored():
    assert parse("czesc", ["party", "voice=justyna"]) == Msg(content="czesc", voice="justyna")


def test_unknown_tag_key_is_ignored():
    assert parse("czesc", ["color=red"]) == Msg(content="czesc")


def test_emoji_tag_is_harmless():
    assert parse("czesc", ["\U0001f6a8"]) == Msg(content="czesc")


def test_parse_tags_splits_key_value():
    assert parse_tags(["voice=justyna", "repeat=3"]) == {"voice": "justyna", "repeat": "3"}


def test_parse_tags_ignores_entries_without_equals():
    assert parse_tags(["party", "voice=justyna"]) == {"voice": "justyna"}


def test_parse_tags_last_occurrence_wins():
    assert parse_tags(["voice=a", "voice=b"]) == {"voice": "b"}


def test_parse_tags_strips_whitespace():
    assert parse_tags([" voice = justyna "]) == {"voice": "justyna"}


def test_parse_tags_none_returns_empty_dict():
    assert parse_tags(None) == {}


def test_parse_tags_empty_iterable_returns_empty_dict():
    assert parse_tags([]) == {}


def test_long_content_is_truncated_to_limit():
    huge = "a" * (MAX_CONTENT_BYTES * 2)
    result = parse(huge)
    assert isinstance(result, Msg)
    assert len(result.content.encode("utf-8")) <= MAX_CONTENT_BYTES
    assert result.content == "a" * MAX_CONTENT_BYTES


def test_short_content_is_not_touched():
    assert parse("krotka wiadomosc") == Msg(content="krotka wiadomosc")


def test_truncation_does_not_break_multibyte_utf8():
    # "ą" is 2 bytes in UTF-8; place one right at the boundary to make sure
    # the cut doesn't leave a dangling lead byte that fails to decode.
    huge = "a" * (MAX_CONTENT_BYTES - 1) + "ą" * 10
    result = parse(huge)
    assert len(result.content.encode("utf-8")) <= MAX_CONTENT_BYTES
    # Must decode cleanly -- no exception means no dangling byte survived.
    result.content.encode("utf-8").decode("utf-8")


def test_split_segments_leading_tag_with_text():
    assert split_segments("<boom.mp3> Testy padły") == [
        Effect(name="boom.mp3", count=1),
        Speech("Testy padły"),
    ]


def test_split_segments_tag_without_trailing_text():
    assert split_segments("<boom.mp3>") == [Effect(name="boom.mp3", count=1)]


def test_split_segments_no_tag():
    assert split_segments("zwykla wiadomosc") == [Speech("zwykla wiadomosc")]


def test_split_segments_empty_content():
    assert split_segments("") == []


def test_split_segments_rejects_path_traversal():
    assert split_segments("<../../etc/passwd> tekst") == [Speech("<../../etc/passwd> tekst")]


def test_split_segments_rejects_slash_in_name():
    assert split_segments("<sub/dir.mp3> tekst") == [Speech("<sub/dir.mp3> tekst")]


def test_split_segments_rejects_bare_dots():
    assert split_segments("<..> tekst") == [Speech("<..> tekst")]


def test_split_segments_empty_tag_name():
    assert split_segments("<> tekst") == [Speech("<> tekst")]


def test_split_segments_strips_whitespace_in_name():
    assert split_segments("< boom.mp3 > tekst") == [
        Effect(name="boom.mp3", count=1),
        Speech("tekst"),
    ]


def test_split_segments_interleaves_speech_and_effects_in_order():
    assert split_segments("<game_over> Testy padły <fight> Naprawiam") == [
        Effect(name="game_over", count=1),
        Speech("Testy padły"),
        Effect(name="fight", count=1),
        Speech("Naprawiam"),
    ]


def test_split_segments_tag_can_appear_mid_text_not_just_leading():
    assert split_segments("Uwaga <siren> teraz") == [
        Speech("Uwaga"),
        Effect(name="siren", count=1),
        Speech("teraz"),
    ]


def test_split_segments_adjacent_tags_stay_as_separate_effect_segments():
    # No text between them, but each <...> is still its own Effect --
    # Announcer is the one that merges a run of these into one play call.
    assert split_segments("<step><step> Ktoś idzie") == [
        Effect(name="step", count=1),
        Effect(name="step", count=1),
        Speech("Ktoś idzie"),
    ]


def test_split_segments_star_suffix_sets_repeat_count():
    assert split_segments("<footstep*6> Ktoś idzie") == [
        Effect(name="footstep", count=6),
        Speech("Ktoś idzie"),
    ]


def test_split_segments_star_suffix_is_capped_at_max_effect_repeat():
    assert split_segments(f"<footstep*{MAX_EFFECT_REPEAT + 50}>") == [
        Effect(name="footstep", count=MAX_EFFECT_REPEAT)
    ]


def test_split_segments_star_suffix_zero_or_negative_falls_back_to_one():
    assert split_segments("<footstep*0>") == [Effect(name="footstep", count=1)]


def test_split_segments_total_effects_capped_at_max_effects():
    content = " ".join(f"<step{i}>" for i in range(MAX_EFFECTS + 5))
    segments = split_segments(content)
    assert sum(seg.count for seg in segments if isinstance(seg, Effect)) == MAX_EFFECTS
    assert [seg.name for seg in segments] == [f"step{i}" for i in range(MAX_EFFECTS)]


def test_split_segments_star_suffix_is_clamped_to_the_remaining_budget():
    # Each tag's own "*N" is already capped at MAX_EFFECT_REPEAT (10), so it
    # takes two of them to exceed MAX_EFFECTS (16) -- the second is clamped
    # to whatever's left rather than dropped outright.
    content = f"<a*{MAX_EFFECT_REPEAT}> <b*{MAX_EFFECT_REPEAT}>"
    segments = split_segments(content)
    assert segments == [
        Effect(name="a", count=MAX_EFFECT_REPEAT),
        Effect(name="b", count=MAX_EFFECTS - MAX_EFFECT_REPEAT),
    ]


def test_split_segments_invalid_tag_merges_into_surrounding_text():
    assert split_segments("start <../nope> <boom.mp3> end") == [
        Speech("start <../nope>"),
        Effect(name="boom.mp3", count=1),
        Speech("end"),
    ]


def test_voice_tag_with_invalid_type_is_ignored():
    # parse_tags always yields strings, but build_msg is also called directly
    # elsewhere (MCP) with non-string values -- make sure those degrade too.
    from krzykacz.protocol import build_msg

    assert build_msg("czesc", voice=123) == Msg(content="czesc", voice=None)


def test_voice_tag_with_bad_characters_is_ignored():
    assert parse("czesc", ["voice=../../etc/passwd"]) == Msg(content="czesc", voice=None)


def test_voice_tag_too_long_is_ignored():
    assert parse("czesc", [f"voice={'a' * 100}"]) == Msg(content="czesc", voice=None)


def test_plain_text_has_no_voice():
    assert parse("zwykly tekst").voice is None


def test_default_repeat_count_is_one():
    assert parse("czesc") == Msg(content="czesc", repeat_count=1)


def test_repeat_tag_is_capped_at_max():
    assert parse("czesc", ["repeat=999"]).repeat_count == MAX_REPEAT_COUNT


def test_repeat_tag_zero_or_negative_falls_back_to_one():
    for value in (0, -5):
        assert parse("czesc", [f"repeat={value}"]).repeat_count == 1


def test_repeat_tag_non_numeric_falls_back_to_one():
    assert parse("czesc", ["repeat=duzo"]).repeat_count == 1


def test_plain_text_has_default_repeat_count():
    assert parse("zwykly tekst").repeat_count == 1
