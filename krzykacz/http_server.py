from __future__ import annotations

import functools
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional
from urllib.parse import urlsplit

from .announcer import Submit
from .auth import check_bearer_token
from .protocol import MAX_CONTENT_BYTES, parse

logger = logging.getLogger(__name__)

PUBLISH_PATH = "/v1/publish"
METADATA_PATH = "/v1/metadata"

# Callable returning the /v1/metadata payload; see krzykacz.metadata.describe.
Metadata = Callable[[], Dict[str, object]]

# Leaves room for the JSON wrapper ({"type":"msg","voice":...,"repeat":...})
# around a MAX_CONTENT_BYTES "content" field.
MAX_BODY_BYTES = MAX_CONTENT_BYTES + 1024


class PublishHandler(BaseHTTPRequestHandler):
    """POST /v1/publish with the same JSON body ntfy accepts (see
    protocol.parse), and GET /v1/metadata for the voices and effects this
    instance can play."""

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
        auth_token: Optional[str],
        **kwargs,
    ):
        self._submit = submit
        self._metadata = metadata
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
        """Authenticates, then matches the path. Returns False (having already
        responded) when the request should not reach a handler."""
        if not self._authorized():
            self._respond(401, {"error": "unauthorized"})
            return False
        if path == allowed:
            return True
        if path in (PUBLISH_PATH, METADATA_PATH):
            self._respond(405, {"error": "method not allowed"})
        else:
            self._respond(404, {"error": "not found"})
        return False

    def do_GET(self) -> None:
        if not self._route(urlsplit(self.path).path, METADATA_PATH):
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
        envelope = parse(body)
        queued = self._submit(envelope)
        self._respond(202 if queued else 503, {"status": "queued" if queued else "dropped"})


def build_http_server(
    host: str, port: int, auth_token: Optional[str], submit: Submit, metadata: Metadata
) -> ThreadingHTTPServer:
    handler = functools.partial(
        PublishHandler, submit=submit, metadata=metadata, auth_token=auth_token
    )
    return ThreadingHTTPServer((host, port), handler)
