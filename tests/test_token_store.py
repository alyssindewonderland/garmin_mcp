import io
import json

from fakes import FakeGarmin

from garmin_mcp.config import Settings
from garmin_mcp.garmin_client import GarminService, default_store
from garmin_mcp.token_store import FileTokenStore, UpstashTokenStore


class MemoryStore:
    def __init__(self, value=None):
        self.value = value
        self.saves = []

    def load(self):
        return self.value

    def save(self, blob):
        self.saves.append(blob)
        self.value = blob


def test_saved_tokens_win_over_env(tmp_path):
    store = MemoryStore('{"di_token": "fresh"}')
    svc = GarminService(Settings(garmin_tokens='{"di_token": "old"}'), FakeGarmin, store)
    svc.call("get_hrv_data", "2026-10-01")
    assert FakeGarmin.instances[-1].login_tokenstore == '{"di_token": "fresh"}'


def test_refreshed_tokens_are_saved_once(tmp_path):
    store = MemoryStore()
    svc = GarminService(Settings(garmin_tokens='{"di_token": "old"}'), FakeGarmin, store)
    svc.call("get_hrv_data", "2026-10-01")
    svc.call("get_hrv_data", "2026-10-02")
    assert store.saves == [FakeGarmin.instances[-1].client.blob]


def test_file_store_roundtrip(tmp_path):
    store = FileTokenStore(tmp_path / "tokens")
    assert store.load() is None
    store.save('{"a": 1}')
    assert store.load() == '{"a": 1}'
    assert (store.path.stat().st_mode & 0o777) == 0o600


def test_upstash_store(monkeypatch):
    sent = []

    def fake_urlopen(req, timeout):
        sent.append((req.full_url, req.headers["Authorization"], json.loads(req.data)))
        cmd = json.loads(req.data)
        result = "OK" if cmd[0] == "SET" else '{"di_token": "x"}'
        return io.BytesIO(json.dumps({"result": result}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    store = UpstashTokenStore("https://example.upstash.io/", "tok")
    store.save('{"di_token": "x"}')
    assert store.load() == '{"di_token": "x"}'
    assert sent[0] == ("https://example.upstash.io", "Bearer tok",
                       ["SET", "garmin-mcp:tokens", '{"di_token": "x"}'])


def test_store_selection(tmp_path):
    assert isinstance(default_store(Settings(token_dir=tmp_path)), FileTokenStore)
    s = Settings(redis_url="https://x.upstash.io", redis_token="t")
    assert isinstance(default_store(s), UpstashTokenStore)


def test_redis_env_names(monkeypatch):
    for k in list(__import__("os").environ):
        if "REST_API" in k or "UPSTASH" in k:
            monkeypatch.delenv(k)
    monkeypatch.setenv("STORAGE_KV_REST_API_URL", "https://p.upstash.io")
    monkeypatch.setenv("STORAGE_KV_REST_API_TOKEN", "pt")
    s = Settings.from_env()
    assert (s.redis_url, s.redis_token) == ("https://p.upstash.io", "pt")
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://u.upstash.io")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "ut")
    s = Settings.from_env()
    assert (s.redis_url, s.redis_token) == ("https://u.upstash.io", "ut")
