import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from conftest import FakeAnnouncer, make_config
from krzykacz.http_server import (
    MAX_BODY_BYTES,
    METADATA_PATH,
    PUBLISH_PATH,
    QUEUE_PATH,
    PublishHandler,
    build_http_server,
)
from krzykacz.protocol import Msg, Repeat



@pytest.fixture
def server_factory():
    servers = []

    def start(cfg, announcer, metadata=None):
        server = build_http_server(
            cfg.http_host,
            cfg.http_port,
            cfg.auth_token,
            announcer.submit,
            metadata or (lambda: {"tts": "espeak", "voices": ["pl"], "default_voice": "pl", "effects": []}),
            announcer.snapshot,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return server

    yield start

    for server in servers:
        server.shutdown()
        server.server_close()


def post(server, path, body, headers=None):
    port = server.server_address[1]
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body.encode("utf-8"),
        headers=headers or {},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def get(server, path, headers=None):
    port = server.server_address[1]
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers=headers or {}, method="GET"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_valid_msg_is_submitted_and_queued(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    status, payload = post(server, PUBLISH_PATH, "hello")

    assert status == 202
    assert payload == {"status": "queued"}
    assert announcer.submitted == [Msg(content="hello")]


def test_tags_header_sets_voice_and_repeat(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    status, _ = post(
        server, PUBLISH_PATH, "hello", headers={"Tags": "voice=justyna,repeat=2"}
    )

    assert status == 202
    assert announcer.submitted == [Msg(content="hello", voice="justyna", repeat_count=2)]


def test_x_tags_alias_is_accepted(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    status, _ = post(server, PUBLISH_PATH, "hello", headers={"X-Tags": "voice=justyna"})

    assert status == 202
    assert announcer.submitted == [Msg(content="hello", voice="justyna")]


def test_replay_tag_is_submitted_as_repeat(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    status, _ = post(server, PUBLISH_PATH, "", headers={"Tags": "replay=-2"})

    assert status == 202
    assert announcer.submitted == [Repeat(number=-2)]


def test_dropped_when_queue_full_returns_503(server_factory):
    announcer = FakeAnnouncer(accept=False)
    server = server_factory(make_config(), announcer)

    status, payload = post(server, PUBLISH_PATH, "hello")

    assert status == 503
    assert payload == {"status": "dropped"}


def test_wrong_path_is_404(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    status, _ = post(server, "/nope", "hello")

    assert status == 404
    assert announcer.submitted == []


def test_oversized_body_is_rejected(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    status, _ = post(server, PUBLISH_PATH, "x" * (MAX_BODY_BYTES + 1))

    assert status == 413
    assert announcer.submitted == []


def test_auth_token_required_when_configured(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(auth_token="secret"), announcer)

    status, _ = post(server, PUBLISH_PATH, "hello")
    assert status == 401
    assert announcer.submitted == []

    status, _ = post(
        server,
        PUBLISH_PATH,
        "hello",
        headers={"Authorization": "Bearer wrong"},
    )
    assert status == 401
    assert announcer.submitted == []

    status, _ = post(
        server,
        PUBLISH_PATH,
        "hello",
        headers={"Authorization": "Bearer secret"},
    )
    assert status == 202
    assert announcer.submitted == [Msg(content="hello")]


def test_no_auth_token_configured_allows_any_request(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(auth_token=None), announcer)

    status, _ = post(server, PUBLISH_PATH, "hello")

    assert status == 202


def _raw_request(server, headers_and_body: bytes, read_timeout=2.0) -> bytes:
    port = server.server_address[1]
    sock = socket.create_connection(("127.0.0.1", port), timeout=read_timeout)
    with sock:
        sock.sendall(headers_and_body)
        chunks = []
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except socket.timeout:
            pass
        return b"".join(chunks)


def test_negative_content_length_is_rejected_immediately(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    request = (
        b"POST /v1/publish HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Length: -1\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    response = _raw_request(server, request)

    assert b" 400 " in response.split(b"\r\n", 1)[0]
    assert announcer.submitted == []


def test_incomplete_body_times_out_instead_of_hanging(server_factory, monkeypatch):
    monkeypatch.setattr(PublishHandler, "timeout", 0.3)
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    # Headers promise a body that never arrives.
    request = (
        b"POST /v1/publish HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Length: 50\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    started = time.monotonic()
    response = _raw_request(server, request, read_timeout=5.0)
    elapsed = time.monotonic() - started

    # The handler's own timeout (0.3s) must cut the connection, not our
    # 5s socket read timeout -- proves the server-side timeout is doing
    # the work rather than the client giving up.
    assert elapsed < 2.0
    assert announcer.submitted == []
    assert response == b"" or response.startswith(b"HTTP/1.")


def test_unversioned_publish_path_is_404(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    status, _ = post(server, "/publish", "hello")

    assert status == 404
    assert announcer.submitted == []


def test_metadata_returns_voices_and_effects(server_factory):
    announcer = FakeAnnouncer()
    payload = {
        "tts": "piper",
        "voices": ["darkman", "justyna"],
        "default_voice": "darkman",
        "effects": ["boom.ogg"],
    }
    server = server_factory(make_config(), announcer, metadata=lambda: payload)

    status, body = get(server, METADATA_PATH)

    assert status == 200
    assert body == payload


def test_metadata_requires_auth_when_configured(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(auth_token="secret"), announcer)

    status, _ = get(server, METADATA_PATH)
    assert status == 401

    status, _ = get(server, METADATA_PATH, headers={"Authorization": "Bearer secret"})
    assert status == 200


def test_wrong_method_on_known_path_is_405(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    assert get(server, PUBLISH_PATH)[0] == 405
    assert post(server, METADATA_PATH, "{}")[0] == 405
    assert post(server, QUEUE_PATH, "{}")[0] == 405
    assert announcer.submitted == []


def test_query_string_does_not_break_routing(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    status, _ = post(server, PUBLISH_PATH + "?source=ci", "hi")

    assert status == 202
    assert announcer.submitted == [Msg(content="hi")]


def test_queue_returns_playing_and_pending(server_factory):
    announcer = FakeAnnouncer()
    announcer.playing = Msg(content="teraz", voice="justyna", repeat_count=2)
    announcer.submitted = [Msg(content="czeka")]
    server = server_factory(make_config(), announcer)

    status, body = get(server, QUEUE_PATH)

    assert status == 200
    assert body == {
        "playing": {"content": "teraz", "voice": "justyna", "repeat": 2},
        "pending": [{"content": "czeka", "voice": None, "repeat": 1}],
    }


def test_queue_playing_is_null_when_idle(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    status, body = get(server, QUEUE_PATH)

    assert status == 200
    assert body == {"playing": None, "pending": []}


def test_queue_serializes_repeat_envelopes(server_factory):
    announcer = FakeAnnouncer()
    announcer.playing = Repeat(number=-1)
    server = server_factory(make_config(), announcer)

    status, body = get(server, QUEUE_PATH)

    assert status == 200
    assert body["playing"] == {"replay": -1}


def test_queue_requires_auth_when_configured(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(auth_token="secret"), announcer)

    status, _ = get(server, QUEUE_PATH)
    assert status == 401

    status, _ = get(server, QUEUE_PATH, headers={"Authorization": "Bearer secret"})
    assert status == 200
