"""Vercel entry point: serves the MCP server as a serverless ASGI app."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from garmin_mcp.config import Settings
from garmin_mcp.http_app import build_http_app
from garmin_mcp.server import build_server

settings = Settings.from_env()
app = build_http_app(build_server(settings), settings, serverless=True)
