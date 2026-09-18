from krzykacz.ratelimit import IpRateLimiter


def test_first_call_is_always_allowed():
    limiter = IpRateLimiter(10)

    assert limiter.allow("1.2.3.4") is True


def test_second_call_within_interval_is_blocked():
    limiter = IpRateLimiter(10)

    limiter.allow("1.2.3.4")

    assert limiter.allow("1.2.3.4") is False


def test_call_after_interval_elapses_is_allowed(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("krzykacz.ratelimit.time.monotonic", lambda: now[0])
    limiter = IpRateLimiter(10)

    assert limiter.allow("1.2.3.4") is True
    now[0] += 10.0

    assert limiter.allow("1.2.3.4") is True


def test_call_just_before_interval_elapses_is_blocked(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("krzykacz.ratelimit.time.monotonic", lambda: now[0])
    limiter = IpRateLimiter(10)

    assert limiter.allow("1.2.3.4") is True
    now[0] += 9.999

    assert limiter.allow("1.2.3.4") is False


def test_different_ips_have_independent_budgets():
    limiter = IpRateLimiter(10)

    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("5.6.7.8") is True


def test_zero_interval_disables_limiting():
    limiter = IpRateLimiter(0)

    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True


def test_negative_interval_disables_limiting():
    limiter = IpRateLimiter(-1)

    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True


def test_min_interval_property_reports_configured_value():
    limiter = IpRateLimiter(10)

    assert limiter.min_interval == 10


def test_stale_entries_are_pruned_once_many_ips_have_called(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("krzykacz.ratelimit.time.monotonic", lambda: now[0])
    limiter = IpRateLimiter(10)

    for i in range(1001):
        limiter.allow(f"10.0.{i // 256}.{i % 256}")

    now[0] += 20.0
    # If pruning worked, none of the old entries block a fresh call from the
    # same address -- otherwise this would still be inside the old window.
    assert limiter.allow("10.0.0.1") is True
    assert len(limiter._last_call) < 1001
