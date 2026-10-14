"""Streamable-HTTP app with a single-user shared-secret guard.

Claude's custom connectors can call `https://host/mcp/<secret>` (no extra auth
config needed). Other clients, such as Claude Code, can instead call `/mcp`
with `Authorization: Bearer <secret>`.
"""

from __future__ import annotations

import hmac

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings

MCP_PATH = "/mcp"
MIN_SECRET_LENGTH = 24


class SecretGuard:
    def __init__(self, app: ASGIApp, secret: str):
        self.app = app
        self.secret = secret.encode()

    def _ok(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self.secret)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if path == "/healthz":
            await _respond(send, 200, b"ok")
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
        # Don't reveal whether the endpoint exists.
        await _respond(send, 404, b"not found")


async def _respond(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def build_http_app(server: MCPServer, settings: Settings) -> ASGIApp:
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
    app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host=settings.host,
    )
    return SecretGuard(app, settings.auth_secret)
