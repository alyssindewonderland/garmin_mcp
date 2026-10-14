"""Thin wrapper around python-garminconnect: lazy login, token persistence, caching.

Everything that talks to Garmin goes through `GarminService`, so swapping the
unofficial API for Garmin's official partner APIs later only touches this file.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from mcp.server.mcpserver.exceptions import ToolError

from .config import Settings
from .token_store import FileTokenStore, TokenStore, UpstashTokenStore

log = logging.getLogger(__name__)

class GarminError(ToolError):
    """An error that is safe to show to the model and the user."""


class TTLCache:
    def __init__(self, ttl_seconds: int, max_entries: int = 512):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple) -> tuple[bool, Any]:
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return False, None
            expires, value = hit
            if expires < time.monotonic():
                del self._data[key]
                return False, None
            return True, value

    def set(self, key: tuple, value: Any) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            if len(self._data) >= self.max_entries:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest]
            self._data[key] = (time.monotonic() + self.ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class GarminService:
    def __init__(
        self,
        settings: Settings,
        api_factory: Callable[..., Garmin] = Garmin,
        store: TokenStore | None = None,
    ):
        self.settings = settings
        self.store = store or default_store(settings)
        self._api_factory = api_factory
        self._api: Garmin | None = None
        self._lock = threading.RLock()
        self._last_persisted: str | None = None
        self.cache = TTLCache(settings.cache_ttl_seconds)

    # -- auth -----------------------------------------------------------------

    def _token_source(self) -> str | None:
        """Prefer tokens refreshed by a previous run over the (older) env secret."""
        try:
            saved = self.store.load()
        except Exception as e:  # noqa: BLE001 - fall back to the env secret
            log.warning("Could not read saved Garmin tokens: %s", type(e).__name__)
            saved = None
        if saved and saved.strip().startswith("{"):
            self._last_persisted = saved
            return saved
        return self.settings.garmin_tokens

    def _login(self) -> Garmin:
        s = self.settings
        api = self._api_factory(
            email=s.garmin_email, password=s.garmin_password, is_cn=s.garmin_is_cn
        )
        source = self._token_source()
        if source is None and not (s.garmin_email and s.garmin_password):
            raise GarminError(
                "Garmin is not configured. Run `garmin-mcp-login` on your own computer "
                "and set the printed value as the GARMIN_TOKENS secret on the server."
            )
        try:
            mfa_needed, _ = api.login(tokenstore=source)
        except GarminConnectAuthenticationError as e:
            raise GarminError(
                "Garmin rejected the stored login. Run `garmin-mcp-login` again and "
                "update the GARMIN_TOKENS secret."
            ) from e
        except GarminConnectTooManyRequestsError as e:
            raise GarminError("Garmin is rate limiting logins. Try again later.") from e
        if mfa_needed:
            raise GarminError(
                "This Garmin account needs an MFA code, which the server can't enter. "
                "Run `garmin-mcp-login` locally and use GARMIN_TOKENS instead."
            )
        self._api = api
        self._persist_tokens()
        return api

    def _persist_tokens(self) -> None:
        if self._api is None:
            return
        try:
            blob = self._api.client.dumps()
        except Exception:  # noqa: BLE001 - never let persistence break a call
            return
        if blob == self._last_persisted:
            return
        try:
            self.store.save(blob)
            self._last_persisted = blob
        except Exception as e:  # noqa: BLE001
            log.warning("Could not persist refreshed Garmin tokens: %s", type(e).__name__)

    @property
    def api(self) -> Garmin:
        with self._lock:
            return self._api or self._login()

    # -- calls ----------------------------------------------------------------

    def call(self, method: str, *args: Any, cache: bool = True, **kwargs: Any) -> Any:
        """Call a garminconnect method by name with caching and friendly errors."""
        key = (method, args, tuple(sorted(kwargs.items())))
        if cache:
            hit, value = self.cache.get(key)
            if hit:
                return value
        api = self.api
        fn = getattr(api, method)
        try:
            result = fn(*args, **kwargs)
        except GarminConnectAuthenticationError:
            # Token may have been revoked; force a fresh login once.
            with self._lock:
                self._api = None
            try:
                result = getattr(self.api, method)(*args, **kwargs)
            except GarminConnectAuthenticationError as e:
                raise GarminError("Garmin authentication failed; re-run garmin-mcp-login.") from e
        except GarminConnectTooManyRequestsError as e:
            raise GarminError("Garmin is rate limiting requests. Try again in a few minutes.") from e
        except GarminConnectConnectionError as e:
            raise GarminError(f"Garmin request failed: {e}") from e
        except ValueError as e:
            raise GarminError(str(e)) from e
        finally:
            with contextlib.suppress(Exception):
                self._persist_tokens()
        if cache:
            self.cache.set(key, result)
        return result

    def write(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if not self.settings.allow_writes:
            raise GarminError(
                "Writing to Garmin is disabled on this server. Set GARMIN_ALLOW_WRITES=true "
                "to enable it."
            )
        result = self.call(method, *args, cache=False, **kwargs)
        self.cache.clear()
        return result


def default_store(settings: Settings) -> TokenStore:
    if settings.redis_url and settings.redis_token:
        return UpstashTokenStore(settings.redis_url, settings.redis_token)
    return FileTokenStore(settings.token_dir)
