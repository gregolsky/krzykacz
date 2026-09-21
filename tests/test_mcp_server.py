import asyncio

import pytest
from conftest import FakeAnnouncer, make_describers, make_pickers

from krzykacz.mcp_server import (
    _build_mcp_server,
    _client_ip,
    _submit_message,
    _submit_repeat,
    _wrap_auth,
    _wrap_client_ip,
    build_mcp_app,
)
from krzykacz.protocol import Msg, Repeat
from krzykacz.ratelimit import IpRateLimiter



def test_submit_message_builds_msg_envelope():
    announcer = FakeAnnouncer()

    result = _submit_message(announcer.submit, "hello", "justyna", 2)

    assert announcer.submitted == [Msg(content="hello", voice="justyna", repeat_count=2)]
    assert result is True


def test_submit_message_defaults_to_none_voice_and_repeat():
    announcer = FakeAnnouncer()

    _submit_message(announcer.submit, "hello", None, None)

    assert announcer.submitted == [Msg(content="hello")]


def test_submit_message_forwards_announcer_result():
    announcer = FakeAnnouncer(accept=False)

    assert _submit_message(announcer.submit, "hello", None, None) is False


def test_submit_repeat_builds_repeat_envelope():
    announcer = FakeAnnouncer()

    _submit_repeat(announcer.submit, -2)

    assert announcer.submitted == [Repeat(number=-2)]


async def _call_asgi(app, headers):
    scope = {"type": "http", "headers": headers}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


async def _inner_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_wrap_auth_returns_original_app_when_no_token():
    assert _wrap_auth(_inner_app, None) is _inner_app


def test_wrap_auth_rejects_missing_header():
    app = _wrap_auth(_inner_app, "secret")

    sent = asyncio.run(_call_asgi(app, headers=[]))

    assert sent[0]["status"] == 401


def test_wrap_auth_rejects_wrong_token():
    app = _wrap_auth(_inner_app, "secret")

    sent = asyncio.run(_call_asgi(app, headers=[(b"authorization", b"Bearer wrong")]))

    assert sent[0]["status"] == 401


def test_wrap_auth_allows_correct_token():
    app = _wrap_auth(_inner_app, "secret")

    sent = asyncio.run(_call_asgi(app, headers=[(b"authorization", b"Bearer secret")]))

    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b"ok"


def test_wrap_auth_passes_through_non_http_scope():
    app = _wrap_auth(_inner_app, "secret")
    sent = []

    async def receive():
        return {}

    async def send(message):
        sent.append(message)

    asyncio.run(app({"type": "lifespan"}, receive, send))

    assert sent[0]["status"] == 200


def test_build_mcp_app_registers_tools_and_wires_auth():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()

    app = build_mcp_app(
        "127.0.0.1", "secret", announcer.submit, make_describers(), make_pickers()
    )

    # Auth is wired: an unauthenticated HTTP request never reaches the app.
    sent = asyncio.run(_call_asgi(app, headers=[]))
    assert sent[0]["status"] == 401
    assert announcer.submitted == []


def test_build_mcp_app_without_token_still_wraps_for_ip_capture():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()

    app = build_mcp_app(
        "127.0.0.1", None, announcer.submit, make_describers(), make_pickers()
    )

    # No token -> _wrap_auth is a no-op, but _wrap_client_ip always wraps
    # the result, since rate limiting/audit need a source IP regardless of
    # whether auth is configured.
    assert getattr(app, "__name__", None) == "middleware"


async def _call_asgi_scope(app, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


def test_wrap_client_ip_captures_scope_client_for_the_request():
    captured = {}

    async def inner(scope, receive, send):
        captured["ip"] = _client_ip.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})

    app = _wrap_client_ip(inner)
    asyncio.run(_call_asgi_scope(app, {"type": "http", "client": ("10.0.0.5", 54321), "headers": []}))

    assert captured["ip"] == "10.0.0.5"
    assert _client_ip.get() is None  # reset once the request finishes


def test_wrap_client_ip_is_none_when_scope_has_no_client():
    captured = {}

    async def inner(scope, receive, send):
        captured["ip"] = _client_ip.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})

    app = _wrap_client_ip(inner)
    asyncio.run(_call_asgi_scope(app, {"type": "http", "headers": []}))

    assert captured["ip"] is None


def test_wrap_client_ip_passes_through_non_http_scope():
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["type"])

    app = _wrap_client_ip(inner)
    asyncio.run(_call_asgi_scope(app, {"type": "lifespan"}))

    assert calls == ["lifespan"]


def test_send_message_description_lists_available_voices():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    describers = make_describers(
        voices=lambda: {"tts": "piper", "voices": ["darkman", "justyna"], "default_voice": "darkman"}
    )
    mcp = _build_mcp_server(FakeAnnouncer().submit, describers, make_pickers(), IpRateLimiter(0))

    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    assert "darkman" in tools["send_message"].description
    assert "justyna" in tools["send_message"].description


def test_all_six_tools_are_registered():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    mcp = _build_mcp_server(
        FakeAnnouncer().submit, make_describers(), make_pickers(), IpRateLimiter(0)
    )

    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    assert set(tools) == {
        "send_message",
        "play_recent_message",
        "list_voices",
        "list_effects",
        "random_sound",
        "random_curse",
    }
    assert tools["list_voices"].title == "List voices"
    assert tools["list_effects"].title == "List sound effects"
    assert tools["random_sound"].title == "Play random sound"
    assert tools["random_curse"].title == "Play random curse"


def test_list_voices_tool_returns_the_voice_view():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    describers = make_describers(
        voices=lambda: {"tts": "piper", "voices": ["darkman", "kopa"], "default_voice": "darkman"}
    )
    mcp = _build_mcp_server(FakeAnnouncer().submit, describers, make_pickers(), IpRateLimiter(0))

    result = asyncio.run(mcp.call_tool("list_voices", {}))

    # The SDK wraps a plain-dict return under "result" (see the tool's
    # generated output_schema); the text content carries the same payload
    # as JSON, which is what a model actually reads.
    assert result.structured_content["result"] == {
        "tts": "piper",
        "voices": ["darkman", "kopa"],
        "default_voice": "darkman",
    }
    assert "kopa" in result.content[0].text


def test_list_effects_tool_reads_the_effect_view_per_call():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    files = []
    describers = make_describers(effects=lambda: {"effects": list(files)})
    mcp = _build_mcp_server(FakeAnnouncer().submit, describers, make_pickers(), IpRateLimiter(0))

    first = asyncio.run(mcp.call_tool("list_effects", {}))
    assert first.structured_content["result"] == {"effects": []}

    files.append("late.ogg")

    second = asyncio.run(mcp.call_tool("list_effects", {}))
    assert second.structured_content["result"] == {"effects": ["late.ogg"]}


def test_read_only_tools_are_not_rate_limited():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    mcp = _build_mcp_server(
        FakeAnnouncer().submit, make_describers(), make_pickers(), IpRateLimiter(60)
    )

    reset_token = _client_ip.set("9.9.9.9")
    try:
        # Neither touches the light or speaker, so repeated calls stay free --
        # and they must not eat the budget the speaking tools need either.
        asyncio.run(mcp.call_tool("list_voices", {}))
        asyncio.run(mcp.call_tool("list_effects", {}))
        speaking = asyncio.run(mcp.call_tool("send_message", {"content": "hello"}))
    finally:
        _client_ip.reset(reset_token)

    assert "rate limited" not in str(speaking.structured_content)


def test_play_recent_message_tool_is_named_and_titled():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    mcp = _build_mcp_server(
        FakeAnnouncer().submit, make_describers(), make_pickers(), IpRateLimiter(0)
    )

    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    assert "play_recent_message" in tools
    assert tools["play_recent_message"].title == "Play recent message"
    assert "-1" in tools["play_recent_message"].description


def test_send_message_tool_submits_via_call_tool():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()
    mcp = _build_mcp_server(announcer.submit, make_describers(), make_pickers(), IpRateLimiter(0))

    asyncio.run(mcp.call_tool("send_message", {"content": "hello"}))

    assert announcer.submitted == [Msg(content="hello")]


def test_send_message_tool_rate_limits_repeat_calls_from_same_ip():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()
    mcp = _build_mcp_server(announcer.submit, make_describers(), make_pickers(), IpRateLimiter(60))

    reset_token = _client_ip.set("9.9.9.9")
    try:
        asyncio.run(mcp.call_tool("send_message", {"content": "first"}))
        asyncio.run(mcp.call_tool("send_message", {"content": "second"}))
    finally:
        _client_ip.reset(reset_token)

    # The second call from the same IP within the window must not reach the
    # announcer.
    assert announcer.submitted == [Msg(content="first")]


def test_send_message_tool_without_captured_ip_falls_back_to_shared_bucket():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()
    mcp = _build_mcp_server(announcer.submit, make_describers(), make_pickers(), IpRateLimiter(60))

    # No _wrap_client_ip in play (e.g. a direct call_tool, as here) ->
    # _client_ip.get() is None. Rather than skip the check (fail open, no
    # limit at all), this falls back to a shared "unknown" bucket, so calls
    # from an unattributed source are still throttled, just as one group.
    asyncio.run(mcp.call_tool("send_message", {"content": "first"}))
    asyncio.run(mcp.call_tool("send_message", {"content": "second"}))

    assert announcer.submitted == [Msg(content="first")]


def test_play_recent_message_tool_rate_limits_repeat_calls_from_same_ip():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()
    mcp = _build_mcp_server(announcer.submit, make_describers(), make_pickers(), IpRateLimiter(60))

    reset_token = _client_ip.set("9.9.9.9")
    try:
        asyncio.run(mcp.call_tool("play_recent_message", {"number": -1}))
        asyncio.run(mcp.call_tool("play_recent_message", {"number": -1}))
    finally:
        _client_ip.reset(reset_token)

    assert announcer.submitted == [Repeat(number=-1)]


def test_send_message_and_play_recent_message_share_one_rate_limit_budget():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()
    shared_limiter = IpRateLimiter(60)
    mcp = _build_mcp_server(announcer.submit, make_describers(), make_pickers(), shared_limiter)

    reset_token = _client_ip.set("9.9.9.9")
    try:
        asyncio.run(mcp.call_tool("send_message", {"content": "hello"}))
        asyncio.run(mcp.call_tool("play_recent_message", {"number": -1}))
    finally:
        _client_ip.reset(reset_token)

    # The second tool call from the same IP consumes the same budget as the
    # first, even though it's a different tool -- IpRateLimiter is shared.
    assert announcer.submitted == [Msg(content="hello")]


def test_random_sound_tool_submits_the_picked_msg():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()
    picked = Msg(content="<fight>")
    pickers = make_pickers(sound=lambda: picked)
    mcp = _build_mcp_server(announcer.submit, make_describers(), pickers, IpRateLimiter(0))

    result = asyncio.run(mcp.call_tool("random_sound", {}))

    assert announcer.submitted == [picked]
    assert "<fight>" in result.structured_content["result"]


def test_random_sound_tool_reports_nothing_to_play():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()
    pickers = make_pickers(sound=lambda: None)
    mcp = _build_mcp_server(announcer.submit, make_describers(), pickers, IpRateLimiter(0))

    result = asyncio.run(mcp.call_tool("random_sound", {}))

    assert announcer.submitted == []
    assert "nothing to play" in result.structured_content["result"]


def test_random_curse_tool_forwards_intensity_and_style():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()
    seen = {}

    def curse(intensity, style):
        seen["intensity"] = intensity
        seen["style"] = style
        return Msg(content="Motyla noga!", voice="darkman", speed=1.1)

    mcp = _build_mcp_server(
        announcer.submit, make_describers(), make_pickers(curse=curse), IpRateLimiter(0)
    )

    result = asyncio.run(mcp.call_tool("random_curse", {"intensity": "mild", "style": "funny"}))

    assert seen == {"intensity": "mild", "style": "funny"}
    assert announcer.submitted == [Msg(content="Motyla noga!", voice="darkman", speed=1.1)]
    assert "Motyla noga!" in result.structured_content["result"]


def test_random_sound_and_random_curse_share_the_rate_limit_budget_with_send_message():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()
    shared_limiter = IpRateLimiter(60)
    pickers = make_pickers(sound=lambda: Msg(content="<fight>"))
    mcp = _build_mcp_server(announcer.submit, make_describers(), pickers, shared_limiter)

    reset_token = _client_ip.set("9.9.9.9")
    try:
        asyncio.run(mcp.call_tool("send_message", {"content": "hello"}))
        blocked = asyncio.run(mcp.call_tool("random_sound", {}))
    finally:
        _client_ip.reset(reset_token)

    assert announcer.submitted == [Msg(content="hello")]
    assert "rate limited" in blocked.structured_content["result"]


def _mcp_with_control(announcer, limiter=None):
    return _build_mcp_server(
        announcer.submit,
        make_describers(),
        make_pickers(),
        limiter or IpRateLimiter(0),
        announcer.control,
    )


def test_mute_tools_are_registered_only_with_a_control():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")

    with_control = {t.name for t in asyncio.run(_mcp_with_control(FakeAnnouncer()).list_tools())}

    assert {"set_mute", "queue_status"} <= with_control


def test_set_mute_tool_toggles_the_announcer():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()
    mcp = _mcp_with_control(announcer)

    muted = asyncio.run(mcp.call_tool("set_mute", {"muted": True}))
    assert announcer.muted is True
    assert "muted" in muted.content[0].text

    asyncio.run(mcp.call_tool("set_mute", {"muted": False}))
    assert announcer.muted is False


def test_queue_status_tool_reports_the_queue_and_mute_state():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()
    announcer.muted = True
    announcer.submitted.append(Msg(content="czeka"))
    mcp = _mcp_with_control(announcer)

    result = asyncio.run(mcp.call_tool("queue_status", {}))

    assert result.structured_content["result"] == {
        "playing": None,
        "pending": [{"content": "czeka", "voice": None, "repeat": 1}],
        "muted": True,
    }


def test_send_message_dropped_because_muted_says_so():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer(accept=False)
    announcer.muted = True
    mcp = _mcp_with_control(announcer)

    result = asyncio.run(mcp.call_tool("send_message", {"content": "hello"}))

    assert result.content[0].text == "dropped (muted)"


def test_set_mute_tool_is_not_rate_limited():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()
    mcp = _mcp_with_control(announcer, limiter=IpRateLimiter(60))

    reset_token = _client_ip.set("9.9.9.9")
    try:
        asyncio.run(mcp.call_tool("send_message", {"content": "spends the budget"}))
        asyncio.run(mcp.call_tool("set_mute", {"muted": True}))
    finally:
        _client_ip.reset(reset_token)

    assert announcer.muted is True
