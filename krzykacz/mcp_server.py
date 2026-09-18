from __future__ import annotations

import contextvars
from typing import Dict, Optional

from .announcer import Submit
from .audit import log_call
from .auth import check_bearer_token
from .metadata import Describers
from .protocol import build_msg, build_repeat
from .ratelimit import IpRateLimiter, rate_limit_or_log

# The `mcp` package requires Python >=3.10 and isn't in requirements.txt --
# it's an opt-in extra, installed only when KRZYKACZ_MCP_ENABLED is used
# (same pattern as piper-tts, see README). Importing it lazily here keeps
# installs that don't use MCP unaffected, including on Python 3.9 (Raspberry
# Pi OS bullseye), where `mcp` can't even be installed.

# Tracks the calling IP for the duration of one HTTP request, set by
# _wrap_client_ip. The SDK hands tool functions a Context object that
# exposes headers but not the transport-level client address (see
# mcp.server.mcpserver.context.Context.headers), so this is how a tool
# attributes its rate-limit check and audit line to a source IP.
_client_ip: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("client_ip", default=None)


def _submit_message(
    submit: Submit,
    content: str,
    voice: Optional[str],
    repeat: Optional[int],
    speed: Optional[float] = None,
    variation: Optional[float] = None,
    rhythm: Optional[float] = None,
) -> bool:
    return submit(
        build_msg(
            content, voice=voice, repeat=repeat, speed=speed, variation=variation, rhythm=rhythm
        )
    )


def _submit_repeat(submit: Submit, number: int) -> bool:
    return submit(build_repeat(number))


def _wrap_auth(app, token: Optional[str]):
    """Wraps a Streamable HTTP ASGI app with a bearer-token check.

    Plain ASGI middleware rather than the SDK's OAuth-oriented
    TokenVerifier/AuthSettings machinery -- this only needs a single static
    shared secret, not a full authorization-server integration.
    """
    if not token:
        return app

    async def middleware(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization")
            auth_str = auth.decode("latin-1") if auth is not None else None
            if not check_bearer_token(auth_str, token):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": b'{"error": "unauthorized"}'})
                return
        await app(scope, receive, send)

    return middleware


def _wrap_client_ip(app):
    """Captures the ASGI scope's client IP into `_client_ip` for the life of
    the request. Always applied -- unlike `_wrap_auth`, this doesn't depend
    on KRZYKACZ_AUTH_TOKEN, since rate limiting and the audit log need a
    source IP regardless of whether auth is configured."""

    async def middleware(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        client = scope.get("client")
        reset_token = _client_ip.set(client[0] if client else None)
        try:
            await app(scope, receive, send)
        finally:
            _client_ip.reset(reset_token)

    return middleware


_RATE_LIMITED_REPLY = "rate limited: this IP called too recently -- wait a bit and retry"

_PLAY_RECENT_MESSAGE_DESCRIPTION = (
    "Replay a message krzykacz already spoke instead of resubmitting its "
    'text -- use this for requests like "say that again" or "one more '
    'time, louder". `number` indexes into the last several spoken '
    "messages: -1 (the default) is the most recent, -2 the one before "
    "that, and so on; there is no way to replay by content, only by this "
    'relative index. Returns "queued" once accepted, "dropped (queue '
    'full)" if the pending queue was already full, or a rate-limit '
    "notice if this caller's IP called too recently."
)

_LIST_VOICES_DESCRIPTION = (
    "List the voice names this instance accepts as send_message's "
    "`voice`, plus which one it defaults to and which TTS backend is "
    "running (piper voice names vs. espeak-ng language codes). Read-only "
    "and not rate-limited -- call it instead of guessing a voice name, "
    "and prefer it over the list baked into these descriptions if a "
    "voice was added since this session started."
)

_LIST_EFFECTS_DESCRIPTION = (
    "List the sound-effect filenames this instance can play before a "
    "message. To use one, prefix send_message's `content` with it in "
    'angle brackets, e.g. "<game_over> Tests failed" -- the effect plays '
    "first, then the rest of the text is read aloud. A name that isn't "
    "in this list is skipped silently rather than failing the message. "
    "Read fresh from disk, read-only, and not rate-limited."
)


def _caller_ip() -> str:
    # Falls back to a shared "unknown" bucket, rather than skipping the rate
    # limit, if a call ever arrives without _wrap_client_ip having captured
    # a real IP -- fails safe (one shared budget) rather than open (no limit).
    return _client_ip.get() or "unknown"


def _queue_reply(queued: bool) -> str:
    return "queued" if queued else "dropped (queue full)"


def _voice_names(voices: Dict[str, object]) -> str:
    return ", ".join(str(v) for v in voices.get("voices", []))


def _send_message_description(voices: Dict[str, object]) -> str:
    names = _voice_names(voices)
    voice_hint = f" One of: {names}." if names else ""
    return (
        "Speak `content` aloud in Polish through krzykacz's physical speaker, "
        "blinking its USB lamp while it talks. `voice` selects a voice by "
        f"name{voice_hint} Omit it for the default voice -- an unknown name "
        "silently falls back to the default rather than erroring, so it's "
        "safe to guess. `repeat` reads `content` that many times back to "
        'back, separated by "Powtarzam!" (Polish for "Repeating!"), capped '
        "at 10; omit it or pass 1 for a single reading. Three optional knobs "
        "shape the delivery, each clamped to a safe range and each best left "
        "unset unless asked for: `speed` (1.0 normal, 1.5 half again as "
        "fast, 0.7 slower), `variation` (how much the voice strays from its "
        "average -- ~0.667 normal, lower is flatter and more monotone, "
        "higher is livelier but can wobble), and `rhythm` (how much "
        "per-syllable timing strays -- ~0.8 normal). `content` longer "
        "than about a minute of speech is truncated. Returns \"queued\" once "
        'accepted, "dropped (queue full)" if the pending queue was already '
        "full, or a rate-limit notice if this caller's IP called too "
        "recently -- in that case, wait and retry rather than resubmitting "
        "immediately."
    )


def _instructions(voices: Dict[str, object]) -> str:
    base = (
        "krzykacz reads text aloud in Polish through a physical speaker and "
        "blinks a USB lamp while speaking -- it's a one-way announcer, not a "
        "conversational voice: there's no way to listen for a spoken reply. "
        "Use send_message to speak new text, or play_recent_message to "
        "replay one of the last few messages instead of retyping it; "
        "list_voices and list_effects report what this particular instance "
        "can play, without triggering anything. Each caller's IP is limited "
        "to about one call at a time for the two speaking tools; a "
        "rate-limit result from either is expected under bursty use -- wait "
        "a moment and retry rather than looping on it."
    )
    return (
        f"{base} TTS backend: {voices.get('tts')}. Available voices: "
        f"{_voice_names(voices) or '(none configured)'} "
        f"(default: {voices.get('default_voice')})."
    )


def _build_mcp_server(submit: Submit, describers: Describers, rate_limiter: IpRateLimiter):
    """Builds the MCPServer with its tools registered, but not yet bound to
    a host/port or wrapped for auth/IP capture. Split out from
    `build_mcp_app` so tests can inspect registered tool descriptions
    directly, without going through ASGI."""
    from mcp.server.mcpserver import MCPServer

    # The voice view only, fetched once because it's baked into static tool
    # descriptions; the live list_voices tool below re-reads it per call.
    voices = describers.voices()

    mcp = MCPServer("krzykacz", instructions=_instructions(voices))

    @mcp.tool(title="Send message", description=_send_message_description(voices))
    def send_message(
        content: str,
        voice: Optional[str] = None,
        repeat: Optional[int] = None,
        speed: Optional[float] = None,
        variation: Optional[float] = None,
        rhythm: Optional[float] = None,
    ) -> str:
        ip = _caller_ip()
        if not rate_limit_or_log(rate_limiter, ip, "mcp.send_message"):
            return _RATE_LIMITED_REPLY
        queued = _submit_message(submit, content, voice, repeat, speed, variation, rhythm)
        log_call(
            "mcp.send_message",
            ip,
            voice=voice,
            repeat=repeat,
            speed=speed,
            variation=variation,
            rhythm=rhythm,
            status="queued" if queued else "dropped",
        )
        return _queue_reply(queued)

    @mcp.tool(
        name="play_recent_message",
        title="Play recent message",
        description=_PLAY_RECENT_MESSAGE_DESCRIPTION,
    )
    def play_recent_message(number: int = -1) -> str:
        ip = _caller_ip()
        if not rate_limit_or_log(rate_limiter, ip, "mcp.play_recent_message"):
            return _RATE_LIMITED_REPLY
        queued = _submit_repeat(submit, number)
        log_call("mcp.play_recent_message", ip, number=number, status="queued" if queued else "dropped")
        return _queue_reply(queued)

    # Read-only views: audited like everything else, but not rate-limited --
    # they touch neither the light nor the speaker, matching the unlimited
    # GET /v1/voices and GET /v1/effects on the HTTP side.
    @mcp.tool(title="List voices", description=_LIST_VOICES_DESCRIPTION)
    def list_voices() -> Dict[str, object]:
        log_call("mcp.list_voices", _caller_ip())
        return describers.voices()

    @mcp.tool(title="List sound effects", description=_LIST_EFFECTS_DESCRIPTION)
    def list_effects() -> Dict[str, object]:
        log_call("mcp.list_effects", _caller_ip())
        return describers.effects()

    return mcp


def build_mcp_app(
    host: str,
    auth_token: Optional[str],
    submit: Submit,
    describers: Describers,
    rate_limiter: Optional[IpRateLimiter] = None,
):
    """Builds the Streamable HTTP ASGI app exposing the krzykacz MCP tools.

    Separate from `run_mcp_server` so the app (tool registration + auth
    wiring) can be built and inspected without binding a port.
    """
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: F401 -- import-time check only
    except ImportError as exc:
        raise RuntimeError(
            "KRZYKACZ_MCP_ENABLED is set but the 'mcp' package isn't installed "
            "(requires Python >=3.10). Install it with: pip install mcp"
        ) from exc

    limiter = rate_limiter if rate_limiter is not None else IpRateLimiter.disabled()
    mcp = _build_mcp_server(submit, describers, limiter)
    app = _wrap_auth(mcp.streamable_http_app(host=host), auth_token)
    return _wrap_client_ip(app)


def run_mcp_server(
    host: str,
    port: int,
    auth_token: Optional[str],
    submit: Submit,
    describers: Describers,
    rate_limiter: Optional[IpRateLimiter] = None,
) -> None:
    """Blocking call -- run in a dedicated thread."""
    # Build first: it raises the actionable "pip install mcp" error, whereas
    # importing uvicorn (an mcp dependency) first would fail with a bare
    # ModuleNotFoundError that doesn't say what to install.
    app = build_mcp_app(host, auth_token, submit, describers, rate_limiter)

    import uvicorn

    uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info")).run()
