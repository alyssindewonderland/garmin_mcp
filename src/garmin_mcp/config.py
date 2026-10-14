"""Settings, all read from environment variables (never from code or chat)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _redis_env(kind: str) -> str | None:
    """Upstash REST URL/TOKEN under any of the names Vercel or Upstash use.

    Vercel's Upstash integration lets you pick a prefix (e.g. STORAGE_KV_REST_API_URL),
    so any variable ending in the standard names is accepted.
    """
    for name in (f"UPSTASH_REDIS_REST_{kind}", f"KV_REST_API_{kind}"):
        if os.getenv(name):
            return os.getenv(name)
    for name, value in sorted(os.environ.items()):
        if value and name.endswith((f"_UPSTASH_REDIS_REST_{kind}", f"_KV_REST_API_{kind}")):
            return value
    return None


@dataclass(frozen=True)
class Settings:
    # Garmin auth. GARMIN_TOKENS (inline JSON from `garmin-mcp-login`) is preferred;
    # email/password is a fallback and will fail if the account needs MFA.
    garmin_tokens: str | None = None
    garmin_email: str | None = None
    garmin_password: str | None = None
    garmin_is_cn: bool = False
    # Where refreshed tokens are persisted (mount a volume here in production).
    token_dir: Path = field(default_factory=lambda: Path.home() / ".garmin-tokens")

    # Upstash Redis REST credentials (Vercel's Upstash integration sets the KV_* names).
    redis_url: str | None = None
    redis_token: str | None = None

    allow_writes: bool = False
    cache_ttl_seconds: int = 300

    # HTTP transport
    host: str = "0.0.0.0"
    port: int = 8000
    # Secret that protects the HTTP endpoint. Accepted as `/mcp/<secret>` or
    # `Authorization: Bearer <secret>`.
    auth_secret: str | None = None
    allowed_hosts: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            garmin_tokens=(os.getenv("GARMIN_TOKENS") or "").strip() or None,
            garmin_email=os.getenv("GARMIN_EMAIL") or None,
            garmin_password=os.getenv("GARMIN_PASSWORD") or None,
            garmin_is_cn=_bool("GARMIN_IS_CN"),
            token_dir=Path(os.getenv("GARMIN_TOKEN_DIR", str(Path.home() / ".garmin-tokens"))),
            redis_url=_redis_env("URL"),
            redis_token=_redis_env("TOKEN"),
            allow_writes=_bool("GARMIN_ALLOW_WRITES"),
            cache_ttl_seconds=int(os.getenv("GARMIN_CACHE_TTL", "300")),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            auth_secret=(os.getenv("MCP_AUTH_SECRET") or "").strip().strip("\"'") or None,
            allowed_hosts=_list("MCP_ALLOWED_HOSTS"),
        )
