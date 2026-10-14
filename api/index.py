"""Vercel entry point: serves the MCP server as a serverless ASGI app."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from garmin_mcp.config import Settings
from garmin_mcp.http_app import build_http_app, config_error_app
from garmin_mcp.server import build_server


def _make_app():
    try:
        settings = Settings.from_env()
        return build_http_app(build_server(settings), settings, serverless=True)
    except (SystemExit, Exception) as e:  # noqa: BLE001
        # Show the setup problem on every URL instead of Vercel's opaque 500 page.
        return config_error_app(str(e) or type(e).__name__)


# Vercel looks for a module-level `app` assignment.
app = _make_app()
