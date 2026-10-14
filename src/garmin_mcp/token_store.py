"""Where refreshed Garmin tokens are kept between runs.

Garmin rotates the refresh token, so the copy in GARMIN_TOKENS goes stale after
the first refresh. The server therefore saves every refreshed token:
- `FileTokenStore`: a directory on disk (local runs, Docker/Fly volume).
- `UpstashTokenStore`: Upstash Redis over its REST API (serverless hosts such as
  Vercel, where the disk does not survive between requests).
"""

from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

TOKEN_FILENAME = "garmin_tokens.json"
REDIS_KEY = "garmin-mcp:tokens"


class TokenStore(Protocol):
    def load(self) -> str | None: ...

    def save(self, blob: str) -> None: ...


class FileTokenStore:
    def __init__(self, directory: Path):
        self.directory = directory

    @property
    def path(self) -> Path:
        return self.directory / TOKEN_FILENAME

    def load(self) -> str | None:
        try:
            return self.path.read_text(encoding="utf-8") if self.path.is_file() else None
        except OSError:
            return None

    def save(self, blob: str) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.touch(mode=0o600)
        tmp.write_text(blob, encoding="utf-8")
        tmp.replace(self.path)


class UpstashTokenStore:
    def __init__(self, url: str, token: str, key: str = REDIS_KEY, timeout: float = 5.0):
        self.url = url.rstrip("/")
        self.token = token
        self.key = key
        self.timeout = timeout

    def _command(self, *args: str) -> object:
        req = urllib.request.Request(
            self.url,
            data=json.dumps(list(args)).encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read())
        if "error" in body:
            raise RuntimeError(f"Upstash error: {body['error']}")
        return body.get("result")

    def load(self) -> str | None:
        result = self._command("GET", self.key)
        return result if isinstance(result, str) else None

    def save(self, blob: str) -> None:
        self._command("SET", self.key, blob)
