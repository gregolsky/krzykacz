from __future__ import annotations

import json
import logging
import time
from typing import Callable, Optional

import requests

from .announcer import Submit
from .protocol import Command, parse, parse_command, parse_tags, preview

logger = logging.getLogger(__name__)

MAX_BACKOFF_S = 30

# Handles a control request (see protocol.parse_command) -- mute, status.
OnCommand = Callable[[Command], None]


def listen(
    server: str, topic: str, on_message: Submit, on_command: Optional[OnCommand] = None
) -> None:
    """Subscribes to the ntfy JSON stream and calls on_message for each parsed
    envelope, or on_command for each control request. Reconnects with
    exponential backoff on any network error. Intentionally does not use
    `since=` — after a reconnect, missed messages are gone, not replayed."""
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
                    _handle_line(line, on_message, on_command)
        except requests.RequestException as exc:
            logger.warning("ntfy connection error: %s", exc)
        except Exception:
            logger.exception("Unexpected error in ntfy listener")

        logger.info("Reconnecting in %ss", backoff)
        time.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF_S)


def _handle_line(
    line: bytes, on_message: Submit, on_command: Optional[OnCommand] = None
) -> None:
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
    # Checked before anything is spoken: a control request's body is
    # meaningless (ntfy fills an empty one with "triggered").
    command = parse_command(tags)
    if command is not None:
        if on_command is None:
            logger.info("Ignoring control request from ntfy (not enabled): %r", command)
        else:
            logger.info("Control request from ntfy: %r", command)
            try:
                on_command(command)
            except Exception:
                logger.exception("Failed to handle control request %r", command)
        return
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
