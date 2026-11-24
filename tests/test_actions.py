from krzykacz.actions import Actions, Outcome
from krzykacz.protocol import Msg
from krzykacz.ratelimit import IpRateLimiter


def make(accept=True, muted=False, interval=0.0):
    submitted = []

    def submit(envelope):
        submitted.append(envelope)
        return accept

    return Actions(submit, lambda: muted, IpRateLimiter(interval)), submitted


def test_queued():
    actions, submitted = make()

    result = actions.trigger("1.2.3.4", "test", lambda: Msg(content="hej"))

    assert (result.outcome, result.status) == (Outcome.QUEUED, "queued")
    assert submitted == [Msg(content="hej")]


def test_dropped_because_queue_full():
    actions, _ = make(accept=False)

    result = actions.trigger("1.2.3.4", "test", lambda: Msg(content="hej"))

    assert (result.outcome, result.status) == (Outcome.DROPPED_FULL, "dropped")


def test_dropped_because_muted():
    actions, _ = make(accept=False, muted=True)

    result = actions.trigger("1.2.3.4", "test", lambda: Msg(content="hej"))

    assert (result.outcome, result.status) == (Outcome.DROPPED_MUTED, "dropped")


def test_nothing_to_play_submits_nothing():
    actions, submitted = make()

    assert actions.trigger("1.2.3.4", "test", lambda: None).outcome is Outcome.NOTHING_TO_PLAY
    assert submitted == []


def test_rate_limited_call_never_builds_its_envelope():
    actions, submitted = make(interval=60)
    built = []

    def build():
        built.append(1)
        return Msg(content="hej")

    assert actions.trigger("1.2.3.4", "test", build).outcome is Outcome.QUEUED
    assert actions.trigger("1.2.3.4", "test", build).outcome is Outcome.RATE_LIMITED
    assert len(built) == 1
    assert len(submitted) == 1
