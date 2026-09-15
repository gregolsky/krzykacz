from __future__ import annotations

import hmac
from typing import Optional


def check_bearer_token(header_value: Optional[str], token: str) -> bool:
    """Compares an `Authorization` header value against the expected bearer
    token in constant time. `header_value` is the raw header (e.g.
    "Bearer abc123"), or None/empty if the header was absent."""
    if not header_value:
        return False
    prefix = "Bearer "
    if not header_value.startswith(prefix):
        return False
    return hmac.compare_digest(header_value[len(prefix):], token)
