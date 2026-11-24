from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Callable, Optional

from .announcer import Submit
from .protocol import Envelope
from .ratelimit import IpRateLimiter, rate_limit_or_log


class Outcome(enum.Enum):
    QUEUED = "queued"
    DROPPED_FULL = "dropped"
    DROPPED_MUTED = "dropped_muted"
    RATE_LIMITED = "rate_limited"
    NOTHING_TO_PLAY = "nothing_to_play"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    # What was submitted (or would have been), for transports that echo it.
    envelope: Optional[Envelope] = None

    @property
    def status(self) -> str:
        """The audit-log status: queued, dropped, rate_limited or nothing_to_play."""
        return "dropped" if self.outcome is Outcome.DROPPED_MUTED else self.outcome.value


class Actions:
    """What every transport does to make krzykacz play something: check the
    caller's rate budget, build the envelope, submit it, and say what
    happened. Transports (HTTP, MCP) only translate a Result into their own
    wire format, so the sequence and its outcomes can't drift between them."""

    def __init__(self, submit: Submit, is_muted: Callable[[], bool], rate_limiter: IpRateLimiter):
        self._submit = submit
        self._is_muted = is_muted
        self._rate_limiter = rate_limiter

    def trigger(self, ip: str, action: str, make: Callable[[], Optional[Envelope]]) -> Result:
        """`make` runs only once the rate check passed, so a refused call
        doesn't even read its request body. It returns None when there is
        nothing to play (e.g. no curated sounds installed)."""
        if not rate_limit_or_log(self._rate_limiter, ip, action):
            return Result(Outcome.RATE_LIMITED)
        envelope = make()
        if envelope is None:
            return Result(Outcome.NOTHING_TO_PLAY)
        if self._submit(envelope):
            return Result(Outcome.QUEUED, envelope)
        muted = self._is_muted()
        return Result(Outcome.DROPPED_MUTED if muted else Outcome.DROPPED_FULL, envelope)
