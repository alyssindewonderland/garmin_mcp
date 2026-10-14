"""MCP server exposing Garmin Connect data as tools."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any, Literal

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import summarize as S
from .config import Settings
from .garmin_client import GarminError, GarminService
from .workouts import WorkoutSpecError, build_workout

INSTRUCTIONS = """\
Tools for the user's own Garmin Connect account: daily health (sleep, HRV, stress,
Body Battery, resting HR, steps), training (activities, splits, readiness, status,
VO2max, race predictions) and, when enabled, writing workouts to their calendar.
Dates are YYYY-MM-DD in the user's local time; "today" is the default. Prefer the
summarized tools; pass raw=true only when a field you need is missing. For multi-day
questions use get_health_range instead of calling day tools in a loop.
"""

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

DateStr = Annotated[str | None, Field(description="YYYY-MM-DD; defaults to today")]
Raw = Annotated[bool, Field(description="Return Garmin's full JSON instead of a summary")]

MAX_RANGE_DAYS = 92


def _day(value: str | None) -> str:
    if not value or value == "today":
        return date.today().isoformat()
    if value == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as e:
        raise GarminError(f"Dates must be YYYY-MM-DD, got {value!r}") from e


def _range(start: str | None, end: str | None, default_days: int = 7) -> tuple[str, str]:
    end_d = date.fromisoformat(_day(end))
    start_d = date.fromisoformat(_day(start)) if start else end_d - timedelta(days=default_days - 1)
    if start_d > end_d:
        raise GarminError("start must be on or before end")
    if (end_d - start_d).days + 1 > MAX_RANGE_DAYS:
        raise GarminError(f"Ranges are limited to {MAX_RANGE_DAYS} days")
    return start_d.isoformat(), end_d.isoformat()


def build_server(settings: Settings | None = None, service: GarminService | None = None) -> MCPServer:
    settings = settings or Settings.from_env()
    garmin = service or GarminService(settings)
    mcp = MCPServer(name="garmin", title="Garmin Connect", instructions=INSTRUCTIONS)

    async def call(method: str, *args: Any, **kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(lambda: garmin.call(method, *args, **kwargs))

    async def write(method: str, *args: Any, **kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(lambda: garmin.write(method, *args, **kwargs))

    # -- profile ---------------------------------------------------------------

    @mcp.tool(annotations=READ)
    async def get_profile() -> dict[str, Any]:
        """The user's name, unit system, devices and heart-rate zones."""
        await call("get_user_summary", date.today().isoformat())  # forces profile load
        devices = await call("get_devices")
        zones = await call("get_heart_rate_zones")
        api = garmin.api
        return {
            "full_name": api.get_full_name(),
            "unit_system": api.get_unit_system(),
            "devices": [
                {k: d.get(k) for k in ("productDisplayName", "deviceId", "lastSyncTime") if d.get(k)}
                for d in devices or []
            ],
            "heart_rate_zones": zones,
        }

    # -- daily health ----------------------------------------------------------

    @mcp.tool(annotations=READ)
    async def get_daily_summary(day: DateStr = None, raw: Raw = False) -> dict[str, Any]:
        """Steps, calories, resting HR, stress, Body Battery, intensity minutes, SpO2 for one day."""
        data = await call("get_user_summary", _day(day))
        return S.raw(data) if raw else S.daily_summary(data)

    @mcp.tool(annotations=READ)
    async def get_sleep(day: DateStr = None, raw: Raw = False) -> dict[str, Any]:
        """Sleep for the night ending on `day`: duration, stages, score, HRV, SpO2, respiration."""
        data = await call("get_sleep_data", _day(day))
        return S.raw(data) if raw else S.sleep(data)

    @mcp.tool(annotations=READ)
    async def get_hrv(day: DateStr = None, raw: Raw = False) -> dict[str, Any]:
        """Overnight HRV: last-night average, 7-day average, baseline and status."""
        data = await call("get_hrv_data", _day(day))
        return S.raw(data) if raw else S.hrv(data)

    @mcp.tool(annotations=READ)
    async def get_body_battery(
        start: DateStr = None, end: DateStr = None, raw: Raw = False
    ) -> dict[str, Any]:
        """Body Battery charged/drained/high/low per day (defaults to the last 7 days)."""
        s, e = _range(start, end)
        data = await call("get_body_battery", s, e)
        return S.raw(data) if raw else {"days": S.body_battery(data)}

    @mcp.tool(annotations=READ)
    async def get_stress(day: DateStr = None, raw: Raw = False) -> dict[str, Any]:
        """Average and max stress for one day."""
        data = await call("get_stress_data", _day(day))
        return S.raw(data) if raw else S.stress(data)

    @mcp.tool(annotations=READ)
    async def get_heart_rate(day: DateStr = None, raw: Raw = False) -> dict[str, Any]:
        """Resting, min and max heart rate for one day (raw=true for the intraday series)."""
        data = await call("get_heart_rates", _day(day))
        if raw:
            return S.raw(data)
        return {
            k: data.get(k)
            for k in ("calendarDate", "restingHeartRate", "minHeartRate", "maxHeartRate",
                      "lastSevenDaysAvgRestingHeartRate")
            if data.get(k) is not None
        }

    @mcp.tool(annotations=READ)
    async def get_body_composition(
        start: DateStr = None, end: DateStr = None, raw: Raw = False
    ) -> dict[str, Any]:
        """Weight and body composition entries (defaults to the last 30 days)."""
        s, e = _range(start, end, default_days=30)
        data = await call("get_body_composition", s, e)
        if raw:
            return S.raw(data)
        entries = []
        for w in (data or {}).get("dateWeightList") or []:
            entries.append(
                {
                    k: v
                    for k, v in {
                        "date": w.get("calendarDate"),
                        "weight_kg": round(w["weight"] / 1000, 2) if w.get("weight") else None,
                        "bmi": w.get("bmi"),
                        "body_fat_pct": w.get("bodyFat"),
                        "muscle_mass_kg": round(w["muscleMass"] / 1000, 2)
                        if w.get("muscleMass")
                        else None,
                        "source": w.get("sourceType"),
                    }.items()
                    if v is not None
                }
            )
        return {"start": s, "end": e, "entries": entries}

    @mcp.tool(annotations=READ)
    async def get_health_range(
        start: DateStr = None,
        end: DateStr = None,
        metrics: Annotated[
            list[Literal["summary", "sleep", "hrv"]],
            Field(description="Which per-day metrics to include"),
        ] = ["summary", "sleep"],  # noqa: B006
    ) -> dict[str, Any]:
        """Per-day table of health metrics for trend questions (defaults to the last 7 days).

        One row per day; much cheaper for the conversation than calling the day tools in a loop.
        """
        s, e = _range(start, end)
        days = []
        d = date.fromisoformat(s)
        while d <= date.fromisoformat(e):
            iso = d.isoformat()
            row: dict[str, Any] = {"date": iso}
            if "summary" in metrics:
                row["summary"] = S.daily_summary(await call("get_user_summary", iso))
            if "sleep" in metrics:
                row["sleep"] = S.sleep(await call("get_sleep_data", iso))
            if "hrv" in metrics:
                row["hrv"] = S.hrv(await call("get_hrv_data", iso))
            days.append(row)
            d += timedelta(days=1)
        return {"start": s, "end": e, "days": days}

    # -- training --------------------------------------------------------------

    @mcp.tool(annotations=READ)
    async def get_training_readiness(day: DateStr = None, raw: Raw = False) -> dict[str, Any]:
        """Training readiness score and its contributing factors for a day."""
        data = await call("get_training_readiness", _day(day))
        return S.raw(data) if raw else {"entries": S.training_readiness(data)}

    @mcp.tool(annotations=READ)
    async def get_training_status(day: DateStr = None, raw: Raw = False) -> dict[str, Any]:
        """Training status, acute/chronic load, load focus, VO2max and fitness age."""
        data = await call("get_training_status", _day(day))
        return S.raw(data) if raw else S.training_status(data)

    @mcp.tool(annotations=READ)
    async def get_race_predictions() -> dict[str, Any]:
        """Garmin's predicted 5K, 10K, half marathon and marathon times."""
        return await call("get_race_predictions")

    @mcp.tool(annotations=READ)
    async def get_personal_records() -> dict[str, Any]:
        """The user's personal records across sports."""
        return {"records": S.raw(await call("get_personal_record"))}

    @mcp.tool(annotations=READ)
    async def list_activities(
        start: DateStr = None,
        end: DateStr = None,
        activity_type: Annotated[
            str | None, Field(description="Garmin type key, e.g. running, cycling, swimming")
        ] = None,
        raw: Raw = False,
    ) -> dict[str, Any]:
        """Activities between two dates, newest first (defaults to the last 14 days)."""
        s, e = _range(start, end, default_days=14)
        data = await call("get_activities_by_date", s, e, activity_type)
        if raw:
            return S.raw(data)
        return {"activities": [S.activity(a) for a in data or []]}

    @mcp.tool(annotations=READ)
    async def get_recent_activities(
        limit: Annotated[int, Field(ge=1, le=50)] = 10, raw: Raw = False
    ) -> dict[str, Any]:
        """The most recent activities."""
        data = await call("get_activities", 0, limit)
        return S.raw(data) if raw else {"activities": [S.activity(a) for a in data or []]}

    @mcp.tool(annotations=READ)
    async def get_activity(
        activity_id: int,
        include: Annotated[
            list[Literal["splits", "hr_zones", "weather"]],
            Field(description="Extra sections to include"),
        ] = ["splits", "hr_zones"],  # noqa: B006
        raw: Raw = False,
    ) -> dict[str, Any]:
        """One activity in detail: summary plus optional laps/splits, time in HR zones, weather."""
        data = await call("get_activity", activity_id)
        if raw:
            return S.raw(data)
        out: dict[str, Any] = {"summary": S.activity(data)}
        if "splits" in include:
            out["laps"] = S.splits(await call("get_activity_splits", activity_id))
        if "hr_zones" in include:
            out["hr_zones"] = S.hr_zones(await call("get_activity_hr_in_timezones", activity_id))
        if "weather" in include:
            w = await call("get_activity_weather", activity_id) or {}
            out["weather"] = {
                k: w.get(k)
                for k in ("temp", "apparentTemp", "relativeHumidity", "windSpeed", "weatherTypeDTO")
                if w.get(k) is not None
            }
        return out

    # -- workouts --------------------------------------------------------------

    @mcp.tool(annotations=READ)
    async def list_workouts(limit: Annotated[int, Field(ge=1, le=100)] = 30) -> dict[str, Any]:
        """Saved workouts in the user's Garmin library."""
        return {"workouts": [S.workout(w) for w in await call("get_workouts", 0, limit) or []]}

    @mcp.tool(annotations=READ)
    async def get_scheduled_workouts(
        year: Annotated[int | None, Field(description="Defaults to this year")] = None,
        month: Annotated[int | None, Field(ge=1, le=12, description="Defaults to this month")] = None,
    ) -> dict[str, Any]:
        """Workouts and other items on the Garmin calendar for a month."""
        today = date.today()
        data = await call("get_scheduled_workouts", year or today.year, month or today.month)
        items = [
            {
                k: item.get(k)
                for k in ("id", "date", "itemType", "title", "workoutId", "sportTypeKey")
                if item.get(k) is not None
            }
            for item in (data or {}).get("calendarItems") or []
        ]
        return {"items": items}

    @mcp.tool(annotations=WRITE)
    async def create_workout(
        spec: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "{name, sport: running|cycling|swimming|walking|hiking|strength_training|"
                    "cardio_training|hiit|yoga|pilates|mobility|other, description?, steps: [...]}. "
                    "Each step is {type: warmup|interval|recovery|rest|cooldown|other, one of "
                    "duration_s | distance_m | lap_button: true, target?, note?} or "
                    "{repeat: N, steps: [...]}. Targets: {type: hr_zone, zone}, {type: hr, "
                    "bpm: [lo, hi]}, {type: power_zone, zone}, {type: power, watts: [lo, hi]}, "
                    "{type: pace, min_per_km: ['4:50', '5:10']}, {type: speed, kmh: [lo, hi]}, "
                    "{type: cadence, rpm: [lo, hi]}."
                )
            ),
        ],
        schedule_on: DateStr = None,
    ) -> dict[str, Any]:
        """Create a structured workout in Garmin Connect, optionally scheduling it on a date.

        It syncs to the user's watch on the next sync. Requires GARMIN_ALLOW_WRITES=true.
        """
        try:
            payload = build_workout(spec)
        except WorkoutSpecError as e:
            raise GarminError(f"Invalid workout: {e}") from e
        created = await write("upload_workout", payload)
        result: dict[str, Any] = {"created": S.workout(created or {})}
        if schedule_on and created and created.get("workoutId"):
            sched = await write("schedule_workout", created["workoutId"], _day(schedule_on))
            result["scheduled"] = {
                "date": _day(schedule_on),
                "scheduled_workout_id": (sched or {}).get("workoutScheduleId"),
            }
        return result

    @mcp.tool(annotations=WRITE)
    async def schedule_workout(workout_id: int, day: DateStr = None) -> dict[str, Any]:
        """Put an existing saved workout on the Garmin calendar."""
        res = await write("schedule_workout", workout_id, _day(day))
        return {"date": _day(day), "scheduled_workout_id": (res or {}).get("workoutScheduleId")}

    @mcp.tool(annotations=DESTRUCTIVE)
    async def unschedule_workout(scheduled_workout_id: int) -> dict[str, Any]:
        """Remove a workout from the calendar (the saved workout itself stays)."""
        await write("unschedule_workout", scheduled_workout_id)
        return {"unscheduled": scheduled_workout_id}

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_workout(workout_id: int) -> dict[str, Any]:
        """Delete a saved workout from the user's Garmin library."""
        await write("delete_workout", workout_id)
        return {"deleted": workout_id}

    @mcp.tool(annotations=WRITE)
    async def update_activity(
        activity_id: int, name: str | None = None, description: str | None = None
    ) -> dict[str, Any]:
        """Rename an activity and/or set its description."""
        if name:
            await write("set_activity_name", activity_id, name)
        if description is not None:
            await write("set_activity_description", activity_id, description)
        return {"updated": activity_id, "name": name, "description": description}

    @mcp.tool(annotations=WRITE)
    async def add_weigh_in(
        weight: float, unit: Literal["kg", "lbs"] = "kg", day: DateStr = None
    ) -> dict[str, Any]:
        """Log a weight measurement."""
        await write("add_weigh_in", weight=weight, unitKey=unit, timestamp=f"{_day(day)}T08:00:00")
        return {"logged": weight, "unit": unit, "date": _day(day)}

    # -- prompts ---------------------------------------------------------------

    @mcp.prompt()
    def morning_check() -> str:
        """How recovered am I today, and what should I train?"""
        return (
            "Use the Garmin tools to check last night's sleep, HRV, Body Battery and today's "
            "training readiness. Compare with my last 7 days (get_health_range). Then tell me "
            "in a few lines how recovered I am and what kind of session fits today."
        )

    @mcp.prompt()
    def weekly_review() -> str:
        """Review my last week of training and recovery."""
        return (
            "Review my last 7 days on Garmin: list activities with key stats, training status "
            "and load, and sleep/HRV/Body Battery trends. Point out anything notable and "
            "suggest focus for next week."
        )

    return mcp
