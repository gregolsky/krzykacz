from __future__ import annotations

import functools
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Container, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

from .announcer import Submit
from .audit import log_call
from .auth import check_bearer_token
from .metadata import Control, Describer, Describers, describe_queue
from .protocol import MAX_CONTENT_BYTES, Msg, parse
from .random_picks import Pickers
from .ratelimit import IpRateLimiter, rate_limit_or_log

logger = logging.getLogger(__name__)

PUBLISH_PATH = "/v1/publish"
RANDOM_SOUND_PATH = "/v1/random-sound"
RANDOM_CURSE_PATH = "/v1/random-curse"
VOICES_PATH = "/v1/voices"
EFFECTS_PATH = "/v1/effects"
LIMITS_PATH = "/v1/limits"
QUEUE_PATH = "/v1/queue"
MUTE_PATH = "/v1/mute"
UNMUTE_PATH = "/v1/unmute"
POST_PATHS = (PUBLISH_PATH, RANDOM_SOUND_PATH, RANDOM_CURSE_PATH, MUTE_PATH, UNMUTE_PATH)
KNOWN_PATHS = POST_PATHS + (VOICES_PATH, EFFECTS_PATH, LIMITS_PATH, QUEUE_PATH)

MAX_BODY_BYTES = MAX_CONTENT_BYTES


class PublishHandler(BaseHTTPRequestHandler):
    """POST /v1/publish with the same body+Tags ntfy accepts (see
    protocol.parse); POST /v1/random-sound and /v1/random-curse to trigger a
    random pick with no body; POST /v1/mute and /v1/unmute to silence or
    restore the box; plus a read-only GET per concern: /v1/voices,
    /v1/effects, /v1/limits and /v1/queue."""

    # Bounds how long a connection can sit idle (e.g. headers sent, body
    # withheld) before the worker thread gives up -- without this, a client
    # that opens a connection and never finishes sending ties up a thread
    # indefinitely (ThreadingHTTPServer's handler threads aren't daemons).
    timeout = 10

    def __init__(
        self,
        *args,
        submit: Submit,
        get_routes: Dict[str, Describer],
        pickers: Pickers,
        control: Control,
        auth_token: Optional[str],
        rate_limiter: IpRateLimiter,
        **kwargs,
    ):
        self._submit = submit
        self._control = control
        self._get_routes = get_routes
        self._pickers = pickers
        self._auth_token = auth_token
        self._rate_limiter = rate_limiter
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 (stdlib signature)
        logger.info("%s - %s", self.address_string(), format % args)

    def _respond(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        # Audited before the response goes out, not after: the decision is
        # what's being recorded, so it must not depend on the write
        # succeeding (or on the client still being there to read it).
        log_call(self.command, self.client_address[0], path=self.path, status=status)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        if not self._auth_token:
            return True
        return check_bearer_token(self.headers.get("Authorization"), self._auth_token)

    def _route(self, path: str, allowed: Container[str]) -> bool:
        """Authenticates, then matches the path against the paths this HTTP
        method serves. Returns False (having already responded) when the
        request should not reach a handler."""
        if not self._authorized():
            self._respond(401, {"error": "unauthorized"})
            return False
        if path in allowed:
            return True
        if path in KNOWN_PATHS:
            self._respond(405, {"error": "method not allowed"})
        else:
            self._respond(404, {"error": "not found"})
        return False

    def _tags(self) -> List[str]:
        # ntfy's own alias for the same header, honored for symmetry.
        raw = self.headers.get("Tags") or self.headers.get("X-Tags")
        if not raw:
            return []
        return [tag.strip() for tag in raw.split(",") if tag.strip()]

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if not self._route(path, self._get_routes):
            return
        self._respond(200, self._get_routes[path]())

    def do_POST(self) -> None:
        parts = urlsplit(self.path)
        if not self._route(parts.path, POST_PATHS):
            return

        if parts.path in (MUTE_PATH, UNMUTE_PATH):
            self._mute(parts.path == MUTE_PATH)
        elif parts.path == PUBLISH_PATH:
            self._publish()
        elif parts.path == RANDOM_SOUND_PATH:
            self._random("random-sound", self._pickers.sound)
        else:
            params = parse_qs(parts.query)
            intensity = params.get("intensity", [None])[0]
            style = params.get("style", [None])[0]
            self._random(
                "random-curse", functools.partial(self._pickers.curse, intensity, style)
            )

    def _mute(self, muted: bool) -> None:
        """Deliberately not rate-limited, unlike every other POST: this is
        what you reach for while the speaker is misbehaving, so it must not be
        refused because the same IP just spent its budget publishing the
        thing that needs silencing. It's authenticated like the rest, doesn't
        touch the light or the speaker, and is idempotent, so there's nothing
        for a limiter to protect."""
        self._control.set_muted(muted)
        self._respond(200, {"muted": muted})

    def _dropped(self) -> dict:
        """The body for a submit that was refused. Says why when it's mute,
        since "dropped" alone reads as a full queue."""
        payload: dict = {"status": "dropped"}
        if self._control.is_muted():
            payload["reason"] = "muted"
        return payload

    def _publish(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._respond(400, {"error": "missing or invalid Content-Length"})
            return

        if length < 0:
            self._respond(400, {"error": "invalid Content-Length"})
            return

        if length > MAX_BODY_BYTES:
            self._respond(413, {"error": "body too large"})
            return

        # Rate-limited here, after the request is known well-formed -- only
        # a call that could actually reach the announcer should spend this
        # IP's budget. Not in _route: the GET views are harmless reads and
        # stay unlimited.
        if not rate_limit_or_log(self._rate_limiter, self.client_address[0], "publish"):
            self._respond(429, {"error": "rate limited"})
            return

        body = self.rfile.read(length).decode("utf-8", errors="replace")
        envelope = parse(body, self._tags())
        queued = self._submit(envelope)
        self._respond(202 if queued else 503, {"status": "queued"} if queued else self._dropped())

    def _random(self, action: str, make_msg: Callable[[], Optional[Msg]]) -> None:
        """Shared shape for the two randomized actions: no request body (so no
        Content-Length requirement, unlike _publish), rate-limited the same
        way as publish, and the response echoes what was picked since a
        random action gives the caller no other way to learn what fired."""
        if not rate_limit_or_log(self._rate_limiter, self.client_address[0], action):
            self._respond(429, {"error": "rate limited"})
            return

        msg = make_msg()
        if msg is None:
            self._respond(503, {"error": "nothing to play"})
            return

        queued = self._submit(msg)
        self._respond(
            202 if queued else 503,
            {
                **({"status": "queued"} if queued else self._dropped()),
                "content": msg.content,
                "voice": msg.voice,
                "speed": msg.speed,
            },
        )


def build_http_server(
    host: str,
    port: int,
    auth_token: Optional[str],
    submit: Submit,
    describers: Describers,
    pickers: Pickers,
    control: Control,
    rate_limiter: Optional[IpRateLimiter] = None,
) -> ThreadingHTTPServer:
    # Which URL serves which view is decided here, so callers hand over the
    # views (krzykacz.metadata.Describers) and stay out of the URL space.
    get_routes: Dict[str, Describer] = {
        VOICES_PATH: describers.voices,
        EFFECTS_PATH: describers.effects,
        LIMITS_PATH: describers.limits,
        QUEUE_PATH: functools.partial(describe_queue, control.snapshot),
    }
    handler = functools.partial(
        PublishHandler,
        submit=submit,
        get_routes=get_routes,
        pickers=pickers,
        control=control,
        auth_token=auth_token,
        rate_limiter=rate_limiter if rate_limiter is not None else IpRateLimiter.disabled(),
    )
    return ThreadingHTTPServer((host, port), handler)
