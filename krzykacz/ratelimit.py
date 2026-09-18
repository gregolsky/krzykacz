from __future__ import annotations

import threading
import time
from typing import Dict

from .audit import log_call


class IpRateLimiter:
    """Allows at most one call every `min_interval` seconds per source IP.

    Thread-safe -- shared between the HTTP server's worker threads and the
    MCP server's asyncio loop, since both transports can trigger the same
    physical action (light + speaker) and a per-IP budget should apply
    regardless of which one a caller uses. `min_interval <= 0` disables
    limiting (every call is allowed).
    """

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._last_call: Dict[str, float] = {}
        self._lock = threading.Lock()

    @classmethod
    def disabled(cls) -> "IpRateLimiter":
        return cls(0.0)

    @property
    def min_interval(self) -> float:
        return self._min_interval

    def allow(self, ip: str) -> bool:
        if self._min_interval <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            last = self._last_call.get(ip)
            if last is not None and now - last < self._min_interval:
                return False
            self._last_call[ip] = now
            if len(self._last_call) > 1000:
                self._prune(now)
            return True

    def _prune(self, now: float) -> None:
        # Called with the lock held. Bounds memory against many distinct IPs
        # (e.g. a scan or spoofed source) by dropping entries already stale
        # enough that they can't affect a future decision.
        cutoff = now - self._min_interval
        stale = [ip for ip, ts in self._last_call.items() if ts <= cutoff]
        for ip in stale:
            del self._last_call[ip]


def rate_limit_or_log(rate_limiter: IpRateLimiter, ip: str, action: str) -> bool:
    """Checks `ip`'s budget for `action`; on rejection, logs the audit line
    and returns False so the caller can respond however fits its transport.
    On acceptance, returns True and logs nothing -- the caller logs its own
    outcome (queued/dropped) once it knows it. Shared by the HTTP and MCP
    endpoints so the check-then-log sequence can't drift between them."""
    if rate_limiter.allow(ip):
        return True
    log_call(action, ip, status="rate_limited")
    return False
