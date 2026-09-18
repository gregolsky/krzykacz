from __future__ import annotations

import logging

logger = logging.getLogger("krzykacz.audit")


def log_call(action: str, ip: str, **fields: object) -> None:
    """One line per externally triggered call: who (`ip`) did what
    (`action`), plus transport-specific details in `fields`. Shared by the
    HTTP and MCP endpoints under a single logger name (`krzykacz.audit`), so
    filtering on it shows every externally triggered call regardless of
    which transport it came in on.

    Field values are `repr()`-ed, not interpolated raw -- some of them (e.g.
    MCP's `voice`, HTTP's request path) are caller-controlled text that has
    not been validated yet at the point this is called, and an unescaped
    value containing "=" or a newline could otherwise forge extra fields or
    extra log lines."""
    # The line is built eagerly (the repr()s can't be deferred to logging's
    # own %-formatting), so skip building it when nothing would record it.
    if not logger.isEnabledFor(logging.INFO):
        return
    parts = [f"ip={ip}", f"action={action}"]
    parts.extend(f"{key}={value!r}" for key, value in fields.items())
    logger.info("%s", " ".join(parts))
