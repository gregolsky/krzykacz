from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# The queue view as served by GET /v1/queue -- see krzykacz.metadata.describe_queue.
View = Callable[[], Dict[str, object]]

# How often the view is compared against what was last published.
POLL_INTERVAL_S = 1.0
# Floor between two publishes, so a burst of queue changes (several messages
# arriving at once) becomes one or two status messages rather than one each --
# the status topic lives on a shared ntfy server.
MIN_PUBLISH_INTERVAL_S = 2.0
PUBLISH_TIMEOUT_S = 10.0


class StatusPublisher:
    """Publishes the queue view (what's playing, what's pending, whether it's
    muted) to a second ntfy topic, so a client on another network can see it
    -- the HTTP/MCP endpoints that serve the same view only reach the LAN.

    Polls the view rather than being told about changes: the announcer stays
    unaware of this, and a change is picked up within POLL_INTERVAL_S however
    it happened (a message queued or finished, mute from any transport). A
    view identical to the last published one isn't sent again; `request()`
    forces a publish anyway, for a client that just started and has nothing
    to show yet.

    Every publish failure is logged and otherwise ignored -- status is a
    convenience, never a reason for the announcer to stop."""

    def __init__(
        self,
        server: str,
        topic: str,
        view: View,
        poll_interval: float = POLL_INTERVAL_S,
        min_publish_interval: float = MIN_PUBLISH_INTERVAL_S,
        post: Callable[..., requests.Response] = requests.post,
    ):
        self._url = f"{server.rstrip('/')}/{topic}"
        self._view = view
        self._poll_interval = poll_interval
        self._min_publish_interval = min_publish_interval
        self._post = post
        self._requested = threading.Event()
        self._last_view: Optional[Dict[str, object]] = None
        self._last_publish = float("-inf")

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="status-publisher").start()

    def request(self) -> None:
        """Publishes the current view on the next poll, even if unchanged."""
        self._requested.set()

    def _run(self) -> None:
        # Published once at startup, so a client polling the topic sees this
        # instance is up without waiting for the first change.
        self._requested.set()
        while True:
            try:
                self.poll_once()
            except Exception:
                logger.exception("Status publisher failed")
            # A plain sleep, not a wait on `_requested`: while a request is
            # pending but throttled, that wait would return at once and spin.
            # A request is served within one poll anyway.
            time.sleep(self._poll_interval)

    def poll_once(self) -> bool:
        """One check-and-maybe-publish step. Returns whether it published.
        Public so tests can drive it without the thread."""
        view = self._view()
        forced = self._requested.is_set()
        if view == self._last_view and not forced:
            return False
        if time.monotonic() - self._last_publish < self._min_publish_interval:
            # Too soon -- the next poll will still see the difference (or
            # the pending request) and publish then.
            return False
        self._requested.clear()
        self._last_publish = time.monotonic()
        try:
            response = self._post(
                self._url,
                data=json.dumps(view, ensure_ascii=False).encode("utf-8"),
                timeout=PUBLISH_TIMEOUT_S,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            # Not remembered as published, so the next poll (after the
            # throttle) tries again instead of waiting for another change.
            logger.warning("Cannot publish status to %s: %s", self._url, exc)
            return False
        self._last_view = view
        return True
