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
