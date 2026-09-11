import json

from krzykacz.protocol import MAX_CONTENT_BYTES, MAX_REPEAT_COUNT, Msg, Repeat, parse, split_effect


def test_json_msg():
    assert parse('{"type":"msg","content":"abc"}') == Msg(content="abc")


def test_plain_text_is_msg():
    assert parse("zwykly tekst bez jsona") == Msg(content="zwykly tekst bez jsona")


def test_repeat_default_number():
    assert parse('{"type":"repeat"}') == Repeat(number=-1)


def test_repeat_explicit_number():
    assert parse('{"type":"repeat","number":-2}') == Repeat(number=-2)


def test_repeat_invalid_number_falls_back_to_minus_one():
    assert parse('{"type":"repeat","number":"nope"}') == Repeat(number=-1)


def test_json_without_type_is_msg_with_raw_body():
    body = '{"content":"abc"}'
    assert parse(body) == Msg(content=body)


def test_json_unknown_type_is_msg_with_raw_body():
    body = '{"type":"unknown"}'
    assert parse(body) == Msg(content=body)


def test_msg_without_content_field_is_msg_with_raw_body():
    body = '{"type":"msg"}'
    assert parse(body) == Msg(content=body)


def test_invalid_json_is_msg_with_raw_body():
    body = "{not valid json"
    assert parse(body) == Msg(content=body)


def test_json_array_is_msg_with_raw_body():
    body = "[1, 2, 3]"
    assert parse(body) == Msg(content=body)


def test_long_msg_content_is_truncated_to_8kb():
    huge = "a" * (MAX_CONTENT_BYTES * 2)
    body = json.dumps({"type": "msg", "content": huge})
    result = parse(body)
    assert isinstance(result, Msg)
    assert len(result.content.encode("utf-8")) <= MAX_CONTENT_BYTES
    assert result.content == "a" * MAX_CONTENT_BYTES


def test_long_plain_text_is_truncated_to_8kb():
    body = "a" * (MAX_CONTENT_BYTES * 2)
    result = parse(body)
    assert isinstance(result, Msg)
    assert len(result.content.encode("utf-8")) <= MAX_CONTENT_BYTES


def test_short_content_is_not_touched():
    body = json.dumps({"type": "msg", "content": "krotka wiadomosc"})
    assert parse(body) == Msg(content="krotka wiadomosc")


def test_truncation_does_not_break_multibyte_utf8():
    # "ą" is 2 bytes in UTF-8; place one right at the boundary to make sure
    # the cut doesn't leave a dangling lead byte that fails to decode.
    huge = "a" * (MAX_CONTENT_BYTES - 1) + "ą" * 10
    body = json.dumps({"type": "msg", "content": huge})
    result = parse(body)
    assert len(result.content.encode("utf-8")) <= MAX_CONTENT_BYTES
    # Must decode cleanly -- no exception means no dangling byte survived.
    result.content.encode("utf-8").decode("utf-8")


def test_split_effect_with_text():
    assert split_effect("<boom.mp3> Testy padły") == ("boom.mp3", "Testy padły")


def test_split_effect_without_trailing_text():
    assert split_effect("<boom.mp3>") == ("boom.mp3", "")


def test_split_effect_no_tag():
    assert split_effect("zwykla wiadomosc") == (None, "zwykla wiadomosc")


def test_split_effect_rejects_path_traversal():
    assert split_effect("<../../etc/passwd> tekst") == (None, "<../../etc/passwd> tekst")


def test_split_effect_rejects_slash_in_name():
    assert split_effect("<sub/dir.mp3> tekst") == (None, "<sub/dir.mp3> tekst")


def test_split_effect_rejects_bare_dots():
    assert split_effect("<..> tekst") == (None, "<..> tekst")


def test_split_effect_empty_tag_name():
    assert split_effect("<> tekst") == (None, "<> tekst")


def test_split_effect_strips_whitespace_in_name():
    assert split_effect("< boom.mp3 > tekst") == ("boom.mp3", "tekst")


def test_msg_with_voice_field():
    body = json.dumps({"type": "msg", "content": "czesc", "voice": "justyna"})
    assert parse(body) == Msg(content="czesc", voice="justyna")


def test_msg_without_voice_field_defaults_to_none():
    body = json.dumps({"type": "msg", "content": "czesc"})
    assert parse(body) == Msg(content="czesc", voice=None)


def test_msg_with_invalid_voice_type_is_ignored():
    body = json.dumps({"type": "msg", "content": "czesc", "voice": 123})
    assert parse(body) == Msg(content="czesc", voice=None)


def test_msg_with_voice_containing_bad_characters_is_ignored():
    body = json.dumps({"type": "msg", "content": "czesc", "voice": "../../etc/passwd"})
    assert parse(body) == Msg(content="czesc", voice=None)


def test_msg_with_too_long_voice_is_ignored():
    body = json.dumps({"type": "msg", "content": "czesc", "voice": "a" * 100})
    assert parse(body) == Msg(content="czesc", voice=None)


def test_plain_text_has_no_voice():
    assert parse("zwykly tekst").voice is None


def test_msg_default_repeat_count_is_one():
    body = json.dumps({"type": "msg", "content": "czesc"})
    assert parse(body) == Msg(content="czesc", repeat_count=1)


def test_msg_with_repeat_count():
    body = json.dumps({"type": "msg", "content": "czesc", "repeat": 3})
    assert parse(body) == Msg(content="czesc", repeat_count=3)


def test_msg_repeat_count_is_capped_at_max():
    body = json.dumps({"type": "msg", "content": "czesc", "repeat": 999})
    assert parse(body).repeat_count == MAX_REPEAT_COUNT


def test_msg_repeat_count_zero_or_negative_falls_back_to_one():
    for value in (0, -5):
        body = json.dumps({"type": "msg", "content": "czesc", "repeat": value})
        assert parse(body).repeat_count == 1


def test_msg_repeat_count_non_numeric_falls_back_to_one():
    body = json.dumps({"type": "msg", "content": "czesc", "repeat": "dużo"})
    assert parse(body).repeat_count == 1


def test_plain_text_has_default_repeat_count():
    assert parse("zwykly tekst").repeat_count == 1
