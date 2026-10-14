# garmin-mcp

An MCP server that connects Claude to your Garmin Connect account. Ask Claude things like
"how did I sleep this week?", "am I recovered enough for intervals today?" or "put a
6x800 session on my calendar for Thursday" and it reads (and, if you allow it, writes)
your Garmin data.

It uses the unofficial Garmin Connect API via
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect), for your
own account only. Garmin's official developer APIs require business approval; all
Garmin calls live in `garmin_client.py` so that route can be added later.

## Tools

| Area | Tools |
|---|---|
| Profile | `get_profile` |
| Daily health | `get_daily_summary`, `get_sleep`, `get_hrv`, `get_body_battery`, `get_stress`, `get_heart_rate`, `get_body_composition`, `get_health_range` (multi-day table) |
| Training | `get_training_readiness`, `get_training_status`, `get_race_predictions`, `get_personal_records`, `list_activities`, `get_recent_activities`, `get_activity` |
| Workouts | `list_workouts`, `get_scheduled_workouts` |
| Writes (off unless `GARMIN_ALLOW_WRITES=true`) | `create_workout`, `schedule_workout`, `unschedule_workout`, `delete_workout`, `update_activity`, `add_weigh_in` |
| Prompts | `morning_check`, `weekly_review` |

Responses are trimmed summaries; every read tool accepts `raw=true` for Garmin's full JSON.

## 1. Log in to Garmin once (on your own computer)

Your password never leaves your machine and is never stored. The login script prints
a token that the server uses instead.

```bash
pipx install git+https://github.com/<you>/garmin-mcp   # or: pip install -e .
garmin-mcp-login                 # asks for email, password and MFA code
garmin-mcp-login --save ~/.garmin-tokens   # also keep a copy for local use
```

The last line it prints is your `GARMIN_TOKENS` value. Treat it like a password: it
grants access to your Garmin account. The server refreshes it automatically and keeps
the refreshed copy in `GARMIN_TOKEN_DIR`; if it ever stops working, run the script again.

## 2a. Use it locally (Claude Desktop / Claude Code)

Claude Code:

```bash
claude mcp add garmin -e GARMIN_TOKEN_DIR=$HOME/.garmin-tokens -- garmin-mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "garmin": {
      "command": "garmin-mcp",
      "env": { "GARMIN_TOKEN_DIR": "/Users/you/.garmin-tokens" }
    }
  }
}
```

## 2b. Deploy it so claude.ai (web, mobile) can use it

The server runs streamable HTTP at `/mcp`, protected by a random secret
(`MCP_AUTH_SECRET`). Clients either call `https://<host>/mcp/<secret>` or send
`Authorization: Bearer <secret>`. Everything else gets a 404.

### Fly.io

```bash
fly launch --no-deploy --copy-config      # pick a unique app name, keep fly.toml
fly volumes create garmin_data --size 1
fly secrets set \
  GARMIN_TOKENS='<output of garmin-mcp-login>' \
  MCP_AUTH_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  MCP_ALLOWED_HOSTS=<app>.fly.dev
fly deploy
```

### Anywhere else

Any Docker host with HTTPS in front works (Railway, Render, a VPS, a home server behind
Cloudflare Tunnel). Run the image with the same env vars and a persistent volume at `/data`:

```bash
docker build -t garmin-mcp .
docker run -d -p 8000:8000 -v garmin_data:/data \
  -e GARMIN_TOKENS='...' -e MCP_AUTH_SECRET='...' garmin-mcp
```

### Add the connector in Claude

In claude.ai: **Settings → Connectors → Add custom connector**, name it "Garmin", and use
`https://<host>/mcp/<MCP_AUTH_SECRET>` as the URL. Then enable it in a chat from the
tools menu.

For Claude Code against the deployed server:

```bash
claude mcp add --transport http garmin https://<host>/mcp --header "Authorization: Bearer <secret>"
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GARMIN_TOKENS` | | Token JSON from `garmin-mcp-login` |
| `GARMIN_TOKEN_DIR` | `~/.garmin-tokens` | Where refreshed tokens are kept (use a volume) |
| `GARMIN_EMAIL`, `GARMIN_PASSWORD` | | Fallback login; fails if your account has MFA |
| `GARMIN_IS_CN` | `false` | Use garmin.cn |
| `GARMIN_ALLOW_WRITES` | `false` | Enable write tools |
| `GARMIN_CACHE_TTL` | `300` | Seconds to cache Garmin responses |
| `MCP_AUTH_SECRET` | | Required for HTTP mode, 24+ characters |
| `MCP_ALLOWED_HOSTS` | | Comma-separated public hostnames for Host header checks |
| `HOST`, `PORT` | `0.0.0.0`, `8000` | HTTP bind |

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest && ruff check .
```

Tests use a fake Garmin client, so they never touch your account.

## Caveats

- This uses Garmin's private API. Garmin can change it at any time; pin and update
  `garminconnect` when things break. Use it for your own account only.
- Logins from cloud data-center IPs are more likely to be challenged, which is why login
  happens on your computer and the server only refreshes tokens.
