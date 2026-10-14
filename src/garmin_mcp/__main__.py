"""Entry point: `garmin-mcp` (stdio, for Claude Desktop/Code) or `garmin-mcp --http`."""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="garmin-mcp")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    args = parser.parse_args()

    from .config import Settings
    from .server import build_server

    settings = Settings.from_env()
    server = build_server(settings)
    if not args.http:
        server.run("stdio")
        return

    import uvicorn

    from .http_app import build_http_app

    logging.basicConfig(level=logging.INFO)
    app = build_http_app(server, settings)
    uvicorn.run(app, host=settings.host, port=settings.port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
