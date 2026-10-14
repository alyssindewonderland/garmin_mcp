"""One-time local login: `garmin-mcp-login`.

Run this on your own computer. It asks for your Garmin email, password and MFA
code (if enabled), then prints a token blob. Store that blob as the
GARMIN_TOKENS secret on your server. Your password is never stored anywhere.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from garminconnect import Garmin


def main() -> None:
    parser = argparse.ArgumentParser(prog="garmin-mcp-login", description=__doc__.split("\n\n")[1])
    parser.add_argument("--cn", action="store_true", help="use garmin.cn (China accounts)")
    parser.add_argument(
        "--save",
        metavar="DIR",
        help="also save tokens to DIR/garmin_tokens.json (for running the server locally)",
    )
    args = parser.parse_args()

    email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = os.getenv("GARMIN_PASSWORD") or getpass.getpass("Garmin password: ")

    api = Garmin(
        email=email,
        password=password,
        is_cn=args.cn,
        prompt_mfa=lambda: input("MFA code from Garmin: ").strip(),
    )
    api.login()
    print(f"\nLogged in as {api.get_full_name()}.", file=sys.stderr)

    blob = api.client.dumps()
    if args.save:
        api.client.dump(str(Path(args.save).expanduser()))
        print(f"Saved tokens to {args.save}", file=sys.stderr)

    print(
        "\nSet this as the GARMIN_TOKENS secret on your server "
        "(it grants access to your Garmin account; keep it private):\n",
        file=sys.stderr,
    )
    print(blob)


if __name__ == "__main__":
    main()
