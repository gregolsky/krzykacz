from __future__ import annotations

import functools
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

from .announcer import Submit
from .auth import check_bearer_token
from .protocol import MAX_CONTENT_BYTES, Envelope, Repeat, parse

logger = logging.getLogger(__name__)

PUBLISH_PATH = "/v1/publish"
METADATA_PATH = "/v1/metadata"
QUEUE_PATH = "/v1/queue"
KNOWN_PATHS = (PUBLISH_PATH, METADATA_PATH, QUEUE_PATH)

# Callable returning the /v1/metadata payload; see krzykacz.metadata.describe.
Metadata = Callable[[], Dict[str, object]]

# Callable returning the /v1/queue payload; see krzykacz.announcer.Announcer.snapshot.
Snapshot = Callable[[], Dict[str, object]]

MAX_BODY_BYTES = MAX_CONTENT_BYTES


def _serialize_envelope(envelope: Optional[Envelope]) -> Optional[Dict[str, object]]:
    if envelope is None:
        return None
    if isinstance(envelope, Repeat):
        return {"replay": envelope.number}
    return {"content": envelope.content, "voice": envelope.voice, "repeat": envelope.repeat_count}


class PublishHandler(BaseHTTPRequestHandler):
    """POST /v1/publish with the same body+Tags ntfy accepts (see
    protocol.parse), GET /v1/metadata for the voices and effects this
    instance can play, and GET /v1/queue for what's playing/pending."""

    # Bounds how long a connection can sit idle (e.g. headers sent, body
    # withheld) before the worker thread gives up -- without this, a client
    # that opens a connection and never finishes sending ties up a thread
    # indefinitely (ThreadingHTTPServer's handler threads aren't daemons).
    timeout = 10

    def __init__(
        self,
        *args,
        submit: Submit,
        metadata: Metadata,
        snapshot: Snapshot,
        auth_token: Optional[str],
        **kwargs,
    ):
        self._submit = submit
        self._metadata = metadata
        self._snapshot = snapshot
        self._auth_token = auth_token
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 (stdlib signature)
        logger.info("%s - %s", self.address_string(), format % args)

    def _respond(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        if not self._auth_token:
            return True
        return check_bearer_token(self.headers.get("Authorization"), self._auth_token)

    def _route(self, path: str, allowed: str) -> bool:
        """Authenticates, then matches the path against the single path
        allowed for this HTTP method. Returns False (having already
        responded) when the request should not reach a handler."""
        if not self._authorized():
            self._respond(401, {"error": "unauthorized"})
            return False
        if path == allowed:
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
        if path == QUEUE_PATH:
            if not self._route(path, QUEUE_PATH):
                return
            state = self._snapshot()
            self._respond(
                200,
                {
                    "playing": _serialize_envelope(state["playing"]),
                    "pending": [_serialize_envelope(e) for e in state["pending"]],
                },
            )
            return
        if not self._route(path, METADATA_PATH):
            return
        self._respond(200, self._metadata())

    def do_POST(self) -> None:
        if not self._route(urlsplit(self.path).path, PUBLISH_PATH):
            return

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

        body = self.rfile.read(length).decode("utf-8", errors="replace")
        envelope = parse(body, self._tags())
        queued = self._submit(envelope)
        self._respond(202 if queued else 503, {"status": "queued" if queued else "dropped"})


def build_http_server(
    host: str,
    port: int,
    auth_token: Optional[str],
    submit: Submit,
    metadata: Metadata,
    snapshot: Snapshot,
) -> ThreadingHTTPServer:
    handler = functools.partial(
        PublishHandler, submit=submit, metadata=metadata, snapshot=snapshot, auth_token=auth_token
    )
    return ThreadingHTTPServer((host, port), handler)
