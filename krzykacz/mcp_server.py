from __future__ import annotations

from typing import Optional

from .announcer import Submit
from .auth import check_bearer_token
from .protocol import build_msg, build_repeat

# The `mcp` package requires Python >=3.10 and isn't in requirements.txt --
# it's an opt-in extra, installed only when KRZYKACZ_MCP_ENABLED is used
# (same pattern as piper-tts, see README). Importing it lazily here keeps
# installs that don't use MCP unaffected, including on Python 3.9 (Raspberry
# Pi OS bullseye), where `mcp` can't even be installed.


def _submit_message(
    submit: Submit, content: str, voice: Optional[str], repeat: Optional[int]
) -> bool:
    return submit(build_msg(content, voice=voice, repeat=repeat))


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


def build_mcp_app(host: str, auth_token: Optional[str], submit: Submit):
    """Builds the Streamable HTTP ASGI app exposing the krzykacz MCP tools.

    Separate from `run_mcp_server` so the app (tool registration + auth
    wiring) can be built and inspected without binding a port.
    """
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise RuntimeError(
            "KRZYKACZ_MCP_ENABLED is set but the 'mcp' package isn't installed "
            "(requires Python >=3.10). Install it with: pip install mcp"
        ) from exc

    mcp = MCPServer("krzykacz")

    @mcp.tool()
    def send_message(content: str, voice: Optional[str] = None, repeat: Optional[int] = None) -> str:
        """Speak a message aloud through krzykacz."""
        queued = _submit_message(submit, content, voice, repeat)
        return "queued" if queued else "dropped (queue full)"

    @mcp.tool()
    def repeat_message(number: int = -1) -> str:
        """Repeat a previous message by history index (-1 = most recent)."""
        queued = _submit_repeat(submit, number)
        return "queued" if queued else "dropped (queue full)"

    return _wrap_auth(mcp.streamable_http_app(host=host), auth_token)


def run_mcp_server(host: str, port: int, auth_token: Optional[str], submit: Submit) -> None:
    """Blocking call -- run in a dedicated thread."""
    # Build first: it raises the actionable "pip install mcp" error, whereas
    # importing uvicorn (an mcp dependency) first would fail with a bare
    # ModuleNotFoundError that doesn't say what to install.
    app = build_mcp_app(host, auth_token, submit)

    import uvicorn

    uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info")).run()
