import asyncio
import json

import pytest
from fakes import FakeGarmin

from garmin_mcp.config import Settings
from garmin_mcp.garmin_client import GarminError, GarminService
from garmin_mcp.server import build_server


def make(tmp_path, **overrides):
    FakeGarmin.instances.clear()
    settings = Settings(garmin_tokens='{"di_token": "x"}', token_dir=tmp_path, **overrides)
    service = GarminService(settings, api_factory=FakeGarmin)
    return build_server(settings, service), service


def call(server, name, args=None):
    result = asyncio.run(server.call_tool(name, args or {}))
    text = result.content[0].text
    if result.is_error:
        raise RuntimeError(text)
    return json.loads(text)


def test_daily_summary_is_compact(tmp_path):
    server, _ = make(tmp_path)
    out = call(server, "get_daily_summary", {"day": "2026-10-01"})
    assert out["steps"] == 12345
    assert out["distance_km"] == 9.88
    assert out["body_battery"] == {"high": 88, "low": 21}
    assert out["sleep_hours"] == 7.5
    assert "someUnusedField" not in json.dumps(out)


def test_raw_returns_full_payload(tmp_path):
    server, _ = make(tmp_path)
    out = call(server, "get_daily_summary", {"day": "2026-10-01", "raw": True})
    assert out["totalSteps"] == 12345


def test_sleep_summary(tmp_path):
    server, _ = make(tmp_path)
    out = call(server, "get_sleep", {"day": "2026-10-01"})
    assert out["total_hours"] == 7.33
    assert out["score"] == 82
    assert out["stages_minutes"]["deep"] == 90.0
    assert out["score_factors"] == {"remPercentage": "EXCELLENT"}


def test_results_are_cached(tmp_path):
    server, _ = make(tmp_path)
    call(server, "get_hrv", {"day": "2026-10-01"})
    call(server, "get_hrv", {"day": "2026-10-01"})
    api = FakeGarmin.instances[-1]
    assert [c for c in api.calls if c[0] == "get_hrv_data"] == [("get_hrv_data", "2026-10-01")]


def test_health_range_one_row_per_day(tmp_path):
    server, _ = make(tmp_path)
    out = call(server, "get_health_range",
               {"start": "2026-10-01", "end": "2026-10-03", "metrics": ["summary", "hrv"]})
    assert [d["date"] for d in out["days"]] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert out["days"][0]["hrv"]["last_night_avg_ms"] == 61


def test_range_limit(tmp_path):
    server, _ = make(tmp_path)
    with pytest.raises(Exception, match="limited"):
        call(server, "get_health_range", {"start": "2025-01-01", "end": "2026-01-01"})


def test_activities_pace_vs_speed(tmp_path):
    server, _ = make(tmp_path)
    run, ride = call(server, "list_activities", {"end": "2026-10-01"})["activities"]
    assert run["avg_pace_min_per_km"] == "5:00"
    assert "avg_speed_kmh" not in run
    assert ride["avg_speed_kmh"] == 24.0


def test_body_battery(tmp_path):
    server, _ = make(tmp_path)
    out = call(server, "get_body_battery", {"start": "2026-10-01", "end": "2026-10-01"})
    assert out["days"] == [{"date": "2026-10-01", "charged": 60, "drained": 55, "high": 90, "low": 30,
                    "end_of_day": 40}]


def test_writes_disabled_by_default(tmp_path):
    server, _ = make(tmp_path)
    spec = {"name": "Easy", "steps": [{"type": "interval", "duration_s": 1800}]}
    with pytest.raises(Exception, match="disabled"):
        call(server, "create_workout", {"spec": spec})


def test_create_and_schedule_workout(tmp_path):
    server, _ = make(tmp_path, allow_writes=True)
    spec = {"name": "Easy", "steps": [{"type": "interval", "duration_s": 1800}]}
    out = call(server, "create_workout", {"spec": spec, "schedule_on": "2026-10-20"})
    assert out["created"]["id"] == 999
    assert out["scheduled"] == {"date": "2026-10-20", "scheduled_workout_id": 555}


def test_tokens_persisted_and_preferred(tmp_path):
    server, service = make(tmp_path)
    call(server, "get_hrv", {"day": "2026-10-01"})
    api = FakeGarmin.instances[-1]
    assert api.login_tokenstore == '{"di_token": "x"}'
    saved = (tmp_path / "garmin_tokens.json").read_text()
    assert saved == api.client.blob
    # Once a refreshed token file exists, it wins over the env secret.
    assert service._token_source() == saved


def test_unconfigured_gives_setup_hint(tmp_path):
    settings = Settings(token_dir=tmp_path)
    service = GarminService(settings, api_factory=FakeGarmin)
    with pytest.raises(GarminError, match="garmin-mcp-login"):
        service.call("get_user_summary", "2026-10-01")


def test_bad_date(tmp_path):
    server, _ = make(tmp_path)
    with pytest.raises(Exception, match="YYYY-MM-DD"):
        call(server, "get_sleep", {"day": "10/01/2026"})
