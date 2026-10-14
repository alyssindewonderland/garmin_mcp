import pytest
from starlette.testclient import TestClient

from garmin_mcp.config import Settings
from garmin_mcp.http_app import build_http_app
from garmin_mcp.server import build_server

SECRET = "s" * 32
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "t", "version": "0"}},
}
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.fixture
def client(tmp_path):
    settings = Settings(auth_secret=SECRET, token_dir=tmp_path)
    app = build_http_app(build_server(settings), settings)
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/healthz").text == "ok"


def test_rejects_without_secret(client):
    assert client.post("/mcp", json=INIT, headers=HEADERS).status_code == 404
    assert client.post("/mcp/wrong", json=INIT, headers=HEADERS).status_code == 404


def test_secret_path(client):
    r = client.post(f"/mcp/{SECRET}", json=INIT, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert "garmin" in r.text


def test_bearer(client):
    r = client.post("/mcp", json=INIT, headers={**HEADERS, "authorization": f"Bearer {SECRET}"})
    assert r.status_code == 200, r.text


def test_short_secret_refused(tmp_path):
    settings = Settings(auth_secret="short", token_dir=tmp_path)
    with pytest.raises(SystemExit):
        build_http_app(build_server(settings), settings)


def test_serverless_mode_without_lifespan(tmp_path):
    settings = Settings(auth_secret=SECRET, token_dir=tmp_path)
    app = build_http_app(build_server(settings), settings, serverless=True)
    c = TestClient(app)  # no `with`: lifespan never runs, like on Vercel
    for _ in range(2):
        r = c.post(f"/mcp/{SECRET}", json=INIT, headers=HEADERS)
        assert r.status_code == 200, r.text
    r = c.post(
        f"/mcp/{SECRET}",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers={**HEADERS, "mcp-protocol-version": "2025-06-18"},
    )
    assert r.status_code == 200 and "get_sleep" in r.text, r.text


def test_vercel_entrypoint(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path

    monkeypatch.setenv("MCP_AUTH_SECRET", SECRET)
    monkeypatch.setenv("GARMIN_TOKEN_DIR", str(tmp_path))
    path = Path(__file__).parent.parent / "api" / "index.py"
    spec = importlib.util.spec_from_file_location("vercel_index", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    r = TestClient(module.app).post(f"/mcp/{SECRET}", json=INIT, headers=HEADERS)
    assert r.status_code == 200, r.text


def test_vercel_entrypoint_reports_config_error(monkeypatch):
    import importlib.util
    from pathlib import Path

    monkeypatch.delenv("MCP_AUTH_SECRET", raising=False)
    path = Path(__file__).parent.parent / "api" / "index.py"
    spec = importlib.util.spec_from_file_location("vercel_index_err", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    r = TestClient(module.app).get("/healthz")
    assert r.status_code == 500 and "MCP_AUTH_SECRET" in r.text
