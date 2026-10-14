"""Streamable-HTTP app with a single-user shared-secret guard.

Claude's custom connectors can call `https://host/mcp/<secret>` (no extra auth
config needed). Other clients, such as Claude Code, can instead call `/mcp`
with `Authorization: Bearer <secret>`.
"""

from __future__ import annotations

import hmac
import logging

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings

log = logging.getLogger(__name__)

MCP_PATH = "/mcp"
MIN_SECRET_LENGTH = 24


class SecretGuard:
    def __init__(self, app: ASGIApp, secret: str):
        self.app = app
        self.secret = secret.encode()

    def _ok(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self.secret)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if path == "/healthz":
            await _respond(send, 200, b"ok")
            return
        if path == "/":
            await _respond(
                send, 200, b"garmin-mcp is running. Claude connects at /mcp/<MCP_AUTH_SECRET>.\n"
            )
            return
        prefix = MCP_PATH + "/"
        if path.startswith(prefix) and self._ok(path[len(prefix):].rstrip("/")):
            scope = dict(scope, path=MCP_PATH, raw_path=MCP_PATH.encode())
            await self.app(scope, receive, send)
            return
        if path.rstrip("/") == MCP_PATH:
            auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
            if auth.lower().startswith("bearer ") and self._ok(auth[7:].strip()):
                await self.app(scope, receive, send)
                return
        # Don't reveal whether the endpoint exists, but log enough to debug setup
        # (never the secret itself).
        if path.startswith(MCP_PATH):
            log.warning(
                "Rejected %s %s... (secret mismatch or missing)", scope.get("method"), path[:8]
            )
        await _respond(send, 404, b"not found")


def config_error_app(message: str) -> ASGIApp:
    """An app that answers every request with a setup error (no secrets in it)."""
    body = f"garmin-mcp is not configured correctly: {message}\n".encode()

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await _ack_lifespan(receive, send)
        elif scope["type"] == "http":
            await _respond(send, 500, body)

    return app


async def _respond(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class PerRequestApp:
    """Serverless mode: build the MCP app and run its session manager per request.

    Serverless runtimes (e.g. Vercel) don't reliably run ASGI lifespan events, and
    the SDK's session manager needs one. In stateless mode nothing is shared between
    requests anyway, so a fresh manager per request costs little.
    """

    def __init__(self, server: MCPServer, **app_kwargs):
        self.server = server
        self.app_kwargs = app_kwargs

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await _ack_lifespan(receive, send)
            return
        app = self.server.streamable_http_app(**self.app_kwargs)
        manager = self.server.session_manager
        async with manager.run():
            await app(scope, receive, send)


async def _ack_lifespan(receive: Receive, send: Send) -> None:
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


def build_http_app(server: MCPServer, settings: Settings, serverless: bool = False) -> ASGIApp:
    if not settings.auth_secret or len(settings.auth_secret) < MIN_SECRET_LENGTH:
        raise SystemExit(
            f"MCP_AUTH_SECRET must be set to a random string of at least {MIN_SECRET_LENGTH} "
            "characters (e.g. `python -c 'import secrets; print(secrets.token_urlsafe(32))'`)."
        )
    security = None
    if settings.allowed_hosts:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=[f"https://{h}" for h in settings.allowed_hosts] + ["https://claude.ai"],
        )
    app_kwargs = {
        "streamable_http_path": MCP_PATH,
        "stateless_http": True,
        "json_response": True,
        "transport_security": security,
        "host": settings.host,
    }
    app = PerRequestApp(server, **app_kwargs) if serverless else server.streamable_http_app(**app_kwargs)
    return SecretGuard(app, settings.auth_secret)
