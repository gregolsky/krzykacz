import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from conftest import FakeAnnouncer, make_config, make_describers, make_pickers
from krzykacz.http_server import (
    EFFECTS_PATH,
    LIMITS_PATH,
    MAX_BODY_BYTES,
    PUBLISH_PATH,
    QUEUE_PATH,
    RANDOM_CURSE_PATH,
    RANDOM_SOUND_PATH,
    VOICES_PATH,
    PublishHandler,
    build_http_server,
)
from krzykacz.protocol import Msg, Repeat
from krzykacz.ratelimit import IpRateLimiter



@pytest.fixture
def server_factory():
    servers = []

    def start(cfg, announcer, describers=None, pickers=None, rate_limiter=None):
        server = build_http_server(
            cfg.http_host,
            cfg.http_port,
            cfg.auth_token,
            announcer.submit,
            describers or make_describers(),
            pickers or make_pickers(),
            announcer.control,
            rate_limiter,
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


def test_retired_metadata_path_is_404(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    # Replaced by the per-concern views below; gone, not silently aliased.
    assert get(server, "/v1/metadata")[0] == 404


def test_voices_returns_only_the_voice_view(server_factory):
    announcer = FakeAnnouncer()
    describers = make_describers(
        voices=lambda: {"tts": "piper", "voices": ["darkman", "kopa"], "default_voice": "darkman"},
        effects=lambda: {"effects": ["boom.ogg"]},
    )
    server = server_factory(make_config(), announcer, describers=describers)

    status, body = get(server, VOICES_PATH)

    assert status == 200
    assert body == {"tts": "piper", "voices": ["darkman", "kopa"], "default_voice": "darkman"}


def test_effects_returns_only_the_effect_view(server_factory):
    announcer = FakeAnnouncer()
    describers = make_describers(effects=lambda: {"effects": ["boom.ogg", "fight"]})
    server = server_factory(make_config(), announcer, describers=describers)

    status, body = get(server, EFFECTS_PATH)

    assert status == 200
    assert body == {"effects": ["boom.ogg", "fight"]}


def test_limits_returns_only_the_limit_view(server_factory):
    announcer = FakeAnnouncer()
    describers = make_describers(limits=lambda: {"max_repeat": 10, "rate_limit_interval": 10})
    server = server_factory(make_config(), announcer, describers=describers)

    status, body = get(server, LIMITS_PATH)

    assert status == 200
    assert body == {"max_repeat": 10, "rate_limit_interval": 10}


def test_effects_view_is_reread_per_request(server_factory):
    announcer = FakeAnnouncer()
    files = []
    server = server_factory(
        make_config(), announcer, describers=make_describers(effects=lambda: {"effects": list(files)})
    )

    assert get(server, EFFECTS_PATH)[1] == {"effects": []}

    files.append("late.ogg")

    assert get(server, EFFECTS_PATH)[1] == {"effects": ["late.ogg"]}


def test_read_views_require_auth_when_configured(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(auth_token="secret"), announcer)

    for path in (VOICES_PATH, EFFECTS_PATH, LIMITS_PATH):
        assert get(server, path)[0] == 401
        assert get(server, path, headers={"Authorization": "Bearer secret"})[0] == 200


def test_wrong_method_on_known_path_is_405(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    for path in (PUBLISH_PATH, RANDOM_SOUND_PATH, RANDOM_CURSE_PATH):
        assert get(server, path)[0] == 405
    for path in (VOICES_PATH, EFFECTS_PATH, LIMITS_PATH, QUEUE_PATH):
        assert post(server, path, "{}")[0] == 405
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
        "muted": False,
    }


def test_queue_playing_is_null_when_idle(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    status, body = get(server, QUEUE_PATH)

    assert status == 200
    assert body == {"playing": None, "pending": [], "muted": False}


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


def test_no_rate_limiter_configured_allows_rapid_publishes(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    assert post(server, PUBLISH_PATH, "one")[0] == 202
    assert post(server, PUBLISH_PATH, "two")[0] == 202


def test_second_publish_within_window_is_rate_limited(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer, rate_limiter=IpRateLimiter(60))

    status1, _ = post(server, PUBLISH_PATH, "hello")
    status2, payload2 = post(server, PUBLISH_PATH, "again")

    assert status1 == 202
    assert status2 == 429
    assert payload2 == {"error": "rate limited"}
    assert announcer.submitted == [Msg(content="hello")]


def test_rate_limit_does_not_apply_to_reads(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer, rate_limiter=IpRateLimiter(60))

    post(server, PUBLISH_PATH, "hello")

    for path in (VOICES_PATH, EFFECTS_PATH, LIMITS_PATH, QUEUE_PATH):
        assert get(server, path)[0] == 200


def test_rate_limit_checked_after_auth(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(
        make_config(auth_token="secret"), announcer, rate_limiter=IpRateLimiter(60)
    )

    status, _ = post(server, PUBLISH_PATH, "hello")

    assert status == 401
    assert announcer.submitted == []


def test_audit_log_records_ip_method_path_and_status(server_factory, caplog):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    with caplog.at_level(logging.INFO, logger="krzykacz.audit"):
        post(server, PUBLISH_PATH, "hello")

    assert "ip=127.0.0.1" in caplog.text
    assert "action=POST" in caplog.text
    assert f"path={PUBLISH_PATH!r}" in caplog.text
    assert "status=202" in caplog.text


def test_random_sound_queues_and_echoes_pick(server_factory):
    announcer = FakeAnnouncer()
    picked = Msg(content="<fight>")
    server = server_factory(make_config(), announcer, pickers=make_pickers(sound=lambda: picked))

    status, payload = post(server, RANDOM_SOUND_PATH, "")

    assert status == 202
    assert payload == {"status": "queued", "content": "<fight>", "voice": None, "speed": None}
    assert announcer.submitted == [picked]


def test_random_curse_queues_and_echoes_pick(server_factory):
    announcer = FakeAnnouncer()
    picked = Msg(content="Motyla noga!", voice="darkman", speed=1.1)
    pickers = make_pickers(curse=lambda intensity, style: picked)
    server = server_factory(make_config(), announcer, pickers=pickers)

    status, payload = post(server, RANDOM_CURSE_PATH, "")

    assert status == 202
    assert payload == {
        "status": "queued",
        "content": "Motyla noga!",
        "voice": "darkman",
        "speed": 1.1,
    }
    assert announcer.submitted == [picked]


def test_random_curse_query_string_reaches_picker(server_factory):
    announcer = FakeAnnouncer()
    seen = {}

    def curse(intensity, style):
        seen["intensity"] = intensity
        seen["style"] = style
        return Msg(content="Kurde!")

    server = server_factory(make_config(), announcer, pickers=make_pickers(curse=curse))

    post(server, RANDOM_CURSE_PATH + "?intensity=mild&style=funny", "")

    assert seen == {"intensity": "mild", "style": "funny"}


def test_random_action_returns_503_when_picker_returns_none(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer, pickers=make_pickers(sound=lambda: None))

    status, payload = post(server, RANDOM_SOUND_PATH, "")

    assert status == 503
    assert payload == {"error": "nothing to play"}
    assert announcer.submitted == []


def test_random_sound_is_rate_limited(server_factory):
    announcer = FakeAnnouncer()
    pickers = make_pickers(sound=lambda: Msg(content="<fight>"))
    server = server_factory(
        make_config(), announcer, pickers=pickers, rate_limiter=IpRateLimiter(60)
    )

    status1, _ = post(server, RANDOM_SOUND_PATH, "")
    status2, payload2 = post(server, RANDOM_SOUND_PATH, "")

    assert status1 == 202
    assert status2 == 429
    assert payload2 == {"error": "rate limited"}
    assert len(announcer.submitted) == 1


def test_random_sound_post_with_no_content_length_header_succeeds(server_factory):
    # Unlike /v1/publish, these take no body -- do_POST must not require
    # Content-Length before dispatching to a random action.
    announcer = FakeAnnouncer()
    pickers = make_pickers(sound=lambda: Msg(content="<fight>"))
    server = server_factory(make_config(), announcer, pickers=pickers)

    request = (
        b"POST " + RANDOM_SOUND_PATH.encode() + b" HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    response = _raw_request(server, request)

    assert b" 202 " in response.split(b"\r\n", 1)[0]
    assert announcer.submitted == [Msg(content="<fight>")]


def test_mute_and_unmute_round_trip(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer)

    assert post(server, "/v1/mute", "") == (200, {"muted": True})
    assert announcer.muted is True
    assert get(server, "/v1/queue")[1]["muted"] is True

    assert post(server, "/v1/unmute", "") == (200, {"muted": False})
    assert announcer.muted is False
    assert get(server, "/v1/queue")[1]["muted"] is False


def test_mute_requires_auth(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(auth_token="secret"), announcer)

    status, _ = post(server, "/v1/mute", "")

    assert status == 401
    assert announcer.muted is False


def test_get_on_mute_paths_is_405_not_404(server_factory):
    server = server_factory(make_config(), FakeAnnouncer())

    assert get(server, "/v1/mute")[0] == 405
    assert get(server, "/v1/unmute")[0] == 405


def test_mute_is_not_rate_limited_even_after_the_ip_spent_its_budget(server_factory):
    announcer = FakeAnnouncer()
    server = server_factory(make_config(), announcer, rate_limiter=IpRateLimiter(60))

    assert post(server, PUBLISH_PATH, "one")[0] == 202
    assert post(server, PUBLISH_PATH, "two")[0] == 429  # budget spent

    assert post(server, "/v1/mute", "")[0] == 200
    assert post(server, "/v1/mute", "")[0] == 200
    assert post(server, "/v1/unmute", "")[0] == 200


def test_publish_dropped_because_muted_says_so(server_factory):
    announcer = FakeAnnouncer(accept=False)
    announcer.muted = True
    server = server_factory(make_config(), announcer)

    status, body = post(server, PUBLISH_PATH, "hello")

    assert (status, body) == (503, {"status": "dropped", "reason": "muted"})


def test_publish_dropped_for_a_full_queue_carries_no_mute_reason(server_factory):
    server = server_factory(make_config(), FakeAnnouncer(accept=False))

    status, body = post(server, PUBLISH_PATH, "hello")

    assert (status, body) == (503, {"status": "dropped"})
