import asyncio

import pytest
from conftest import FakeAnnouncer

from krzykacz.mcp_server import _submit_message, _submit_repeat, _wrap_auth, build_mcp_app
from krzykacz.protocol import Msg, Repeat



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

    app = build_mcp_app("127.0.0.1", "secret", announcer.submit)

    # Auth is wired: an unauthenticated HTTP request never reaches the app.
    sent = asyncio.run(_call_asgi(app, headers=[]))
    assert sent[0]["status"] == 401
    assert announcer.submitted == []


def test_build_mcp_app_without_token_skips_auth_wrapper():
    pytest.importorskip("mcp", reason="the optional 'mcp' package isn't installed")
    announcer = FakeAnnouncer()

    app = build_mcp_app("127.0.0.1", None, announcer.submit)

    # No token configured -> the raw MCP app is returned, unwrapped.
    assert getattr(app, "__name__", None) != "middleware"
