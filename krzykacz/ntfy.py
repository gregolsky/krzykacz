from __future__ import annotations

import json
import logging
import time
import requests

from .announcer import Submit
from .protocol import parse, parse_tags, preview

logger = logging.getLogger(__name__)

MAX_BACKOFF_S = 30


def listen(server: str, topic: str, on_message: Submit) -> None:
    """Subscribes to the ntfy JSON stream and calls on_message for each parsed
    envelope. Reconnects with exponential backoff on any network error.
    Intentionally does not use `since=` — after a reconnect, missed messages
    are gone, not replayed."""
    url = f"{server.rstrip('/')}/{topic}/json"
    backoff = 1
    while True:
        try:
            logger.info("Connecting to %s", url)
            with requests.get(url, stream=True, timeout=(10, 90)) as resp:
                resp.raise_for_status()
                logger.info("Connected, listening for messages")
                backoff = 1
                for line in resp.iter_lines():
                    if not line:
                        continue
                    _handle_line(line, on_message)
        except requests.RequestException as exc:
            logger.warning("ntfy connection error: %s", exc)
        except Exception:
            logger.exception("Unexpected error in ntfy listener")

        logger.info("Reconnecting in %ss", backoff)
        time.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF_S)


def _handle_line(line: bytes, on_message: Submit) -> None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        logger.debug("Non-JSON line from ntfy stream: %r", line)
        return
    if not isinstance(event, dict):
        return
    kind = event.get("event")
    if kind != "message":
        logger.debug("ntfy stream event: %s", kind)
        return
    body = event.get("message") or ""
    tags = event.get("tags")
    # A `replay` request carries no meaningful body, so an empty message is
    # only dropped when it's not one of those.
    if not body and "replay" not in parse_tags(tags):
        return
    logger.info("Received from ntfy: %s", preview(body) if body else "(replay)")
    try:
        envelope = parse(body, tags)
    except Exception:
        logger.exception("Failed to parse message body: %r", body)
        return
    on_message(envelope)
