"""Turn Garmin's large JSON payloads into compact, Claude-friendly dicts.

Garmin field names drift over time, so every accessor is defensive: missing
fields are dropped rather than raising.
"""

from __future__ import annotations

import json
from typing import Any

MAX_RAW_CHARS = 60_000


def _get(d: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None and v != {} and v != []}


def _minutes(seconds: Any) -> float | None:
    return round(seconds / 60, 1) if isinstance(seconds, (int, float)) else None


def _km(meters: Any) -> float | None:
    return round(meters / 1000, 2) if isinstance(meters, (int, float)) else None


def _pace_min_per_km(speed_mps: Any) -> str | None:
    if not isinstance(speed_mps, (int, float)) or speed_mps <= 0:
        return None
    secs = round(1000 / speed_mps)
    return f"{secs // 60}:{secs % 60:02d}"


def raw(data: Any) -> Any:
    """Return data as-is, truncated if it would flood the context window."""
    text = json.dumps(data, default=str)
    if len(text) <= MAX_RAW_CHARS:
        return data
    return {"truncated": True, "chars": len(text), "json_prefix": text[:MAX_RAW_CHARS]}


def daily_summary(s: dict[str, Any]) -> dict[str, Any]:
    return _clean(
        {
            "date": s.get("calendarDate"),
            "steps": s.get("totalSteps"),
            "step_goal": s.get("dailyStepGoal"),
            "distance_km": _km(s.get("totalDistanceMeters")),
            "floors_climbed": s.get("floorsAscended"),
            "calories_total": s.get("totalKilocalories"),
            "calories_active": s.get("activeKilocalories"),
            "resting_hr": s.get("restingHeartRate"),
            "min_hr": s.get("minHeartRate"),
            "max_hr": s.get("maxHeartRate"),
            "avg_stress": s.get("averageStressLevel"),
            "max_stress": s.get("maxStressLevel"),
            "stress_minutes": _clean(
                {
                    "rest": _minutes(s.get("restStressDuration")),
                    "low": _minutes(s.get("lowStressDuration")),
                    "medium": _minutes(s.get("mediumStressDuration")),
                    "high": _minutes(s.get("highStressDuration")),
                }
            ),
            "body_battery": _clean(
                {
                    "high": s.get("bodyBatteryHighestValue"),
                    "low": s.get("bodyBatteryLowestValue"),
                    "latest": s.get("bodyBatteryMostRecentValue"),
                    "charged": s.get("bodyBatteryChargedValue"),
                    "drained": s.get("bodyBatteryDrainedValue"),
                }
            ),
            "intensity_minutes": _clean(
                {
                    "moderate": s.get("moderateIntensityMinutes"),
                    "vigorous": s.get("vigorousIntensityMinutes"),
                    "goal": s.get("intensityMinutesGoal"),
                }
            ),
            "avg_spo2": s.get("averageSpo2"),
            "lowest_spo2": s.get("lowestSpo2"),
            "avg_waking_respiration": s.get("avgWakingRespirationValue"),
            "sleep_hours": round(s["sleepingSeconds"] / 3600, 2)
            if isinstance(s.get("sleepingSeconds"), (int, float))
            else None,
        }
    )


def sleep(data: dict[str, Any]) -> dict[str, Any]:
    d = data.get("dailySleepDTO") or {}
    scores = d.get("sleepScores") or {}
    return _clean(
        {
            "date": d.get("calendarDate"),
            "start_local_ms": d.get("sleepStartTimestampLocal"),
            "end_local_ms": d.get("sleepEndTimestampLocal"),
            "total_hours": round(d["sleepTimeSeconds"] / 3600, 2)
            if isinstance(d.get("sleepTimeSeconds"), (int, float))
            else None,
            "stages_minutes": _clean(
                {
                    "deep": _minutes(d.get("deepSleepSeconds")),
                    "light": _minutes(d.get("lightSleepSeconds")),
                    "rem": _minutes(d.get("remSleepSeconds")),
                    "awake": _minutes(d.get("awakeSleepSeconds")),
                }
            ),
            "score": _get(scores, "overall", "value"),
            "score_quality": _get(scores, "overall", "qualifierKey"),
            "score_factors": _clean(
                {
                    k: v.get("qualifierKey")
                    for k, v in scores.items()
                    if k != "overall" and isinstance(v, dict)
                }
            ),
            "awake_count": d.get("awakeCount"),
            "avg_sleep_stress": d.get("avgSleepStress"),
            "avg_spo2": d.get("averageSpO2Value"),
            "lowest_spo2": d.get("lowestSpO2Value"),
            "avg_respiration": d.get("averageRespirationValue"),
            "resting_hr": data.get("restingHeartRate"),
            "avg_overnight_hrv": data.get("avgOvernightHrv"),
            "hrv_status": data.get("hrvStatus"),
            "body_battery_change": data.get("bodyBatteryChange"),
            "feedback": d.get("sleepScoreFeedback"),
        }
    )


def hrv(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {"available": False}
    s = data.get("hrvSummary") or {}
    baseline = s.get("baseline") or {}
    return _clean(
        {
            "date": s.get("calendarDate"),
            "last_night_avg_ms": s.get("lastNightAvg"),
            "last_night_5min_high_ms": s.get("lastNight5MinHigh"),
            "weekly_avg_ms": s.get("weeklyAvg"),
            "status": s.get("status"),
            "baseline_balanced_range_ms": [baseline.get("balancedLow"), baseline.get("balancedUpper")]
            if baseline.get("balancedLow") is not None
            else None,
            "feedback": s.get("feedbackPhrase"),
        }
    )


def body_battery(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for d in days or []:
        values = [
            v[1]
            for v in d.get("bodyBatteryValuesArray") or []
            if isinstance(v, list) and len(v) > 1 and v[1] is not None
        ]
        out.append(
            _clean(
                {
                    "date": d.get("date"),
                    "charged": d.get("charged"),
                    "drained": d.get("drained"),
                    "high": max(values) if values else None,
                    "low": min(values) if values else None,
                    "end_of_day": values[-1] if values else None,
                }
            )
        )
    return out


def stress(data: dict[str, Any]) -> dict[str, Any]:
    return _clean(
        {
            "date": data.get("calendarDate"),
            "avg_stress": data.get("avgStressLevel"),
            "max_stress": data.get("maxStressLevel"),
        }
    )


def training_readiness(entries: list[dict[str, Any]] | dict[str, Any] | None) -> list[dict]:
    if isinstance(entries, dict):
        entries = [entries]
    out = []
    for e in entries or []:
        out.append(
            _clean(
                {
                    "timestamp": e.get("timestampLocal") or e.get("timestamp"),
                    "score": e.get("score"),
                    "level": e.get("level"),
                    "feedback": e.get("feedbackShort") or e.get("feedbackLong"),
                    "sleep_score": e.get("sleepScore"),
                    "recovery_time_hours": round(e["recoveryTime"] / 60, 1)
                    if isinstance(e.get("recoveryTime"), (int, float))
                    else None,
                    "acute_load": e.get("acuteLoad"),
                    "hrv_weekly_avg": e.get("hrvWeeklyAverage"),
                    "factor_percents": _clean(
                        {
                            "sleep": e.get("sleepScoreFactorPercent"),
                            "recovery_time": e.get("recoveryTimeFactorPercent"),
                            "acwr": e.get("acwrFactorPercent"),
                            "stress_history": e.get("stressHistoryFactorPercent"),
                            "hrv": e.get("hrvFactorPercent"),
                            "sleep_history": e.get("sleepHistoryFactorPercent"),
                        }
                    ),
                }
            )
        )
    return out


def training_status(data: dict[str, Any]) -> dict[str, Any]:
    latest = _get(data, "mostRecentTrainingStatus", "latestTrainingStatusData") or {}
    device_status = next(iter(latest.values()), {}) if isinstance(latest, dict) else {}
    vo2 = _get(data, "mostRecentVO2Max", "generic") or {}
    vo2_cycling = _get(data, "mostRecentVO2Max", "cycling") or {}
    load = _get(data, "mostRecentTrainingLoadBalance", "metricsTrainingLoadBalanceDTOMap") or {}
    load_status = next(iter(load.values()), {}) if isinstance(load, dict) else {}
    acute = device_status.get("acuteTrainingLoadDTO") or {}
    return _clean(
        {
            "status": device_status.get("trainingStatusFeedbackPhrase")
            or device_status.get("trainingStatus"),
            "date": device_status.get("calendarDate"),
            "acute_load": acute.get("dailyTrainingLoadAcute"),
            "chronic_load": acute.get("dailyTrainingLoadChronic"),
            "acwr_status": acute.get("acwrStatus"),
            "load_focus": _clean(
                {
                    "low_aerobic": load_status.get("monthlyLoadAerobicLow"),
                    "high_aerobic": load_status.get("monthlyLoadAerobicHigh"),
                    "anaerobic": load_status.get("monthlyLoadAnaerobic"),
                    "feedback": load_status.get("trainingBalanceFeedbackPhrase"),
                }
            ),
            "vo2max_running": vo2.get("vo2MaxPreciseValue") or vo2.get("vo2MaxValue"),
            "vo2max_cycling": vo2_cycling.get("vo2MaxPreciseValue") or vo2_cycling.get("vo2MaxValue"),
            "fitness_age": vo2.get("fitnessAge"),
        }
    )


def activity(a: dict[str, Any]) -> dict[str, Any]:
    """Summary for an item from the activity list or the activity summary endpoint."""
    summary = a.get("summaryDTO") or a  # get_activity() nests stats under summaryDTO
    type_key = _get(a, "activityType", "typeKey") or _get(a, "activityTypeDTO", "typeKey")
    speed = summary.get("averageSpeed")
    is_foot = type_key and any(t in type_key for t in ("run", "walk", "hik", "trail"))
    return _clean(
        {
            "id": a.get("activityId"),
            "name": a.get("activityName"),
            "type": type_key,
            "start_local": summary.get("startTimeLocal") or a.get("startTimeLocal"),
            "distance_km": _km(summary.get("distance")),
            "duration_min": _minutes(summary.get("duration")),
            "moving_min": _minutes(summary.get("movingDuration")),
            "elevation_gain_m": summary.get("elevationGain"),
            "avg_pace_min_per_km": _pace_min_per_km(speed) if is_foot else None,
            "avg_speed_kmh": round(speed * 3.6, 1)
            if isinstance(speed, (int, float)) and not is_foot
            else None,
            "avg_hr": summary.get("averageHR"),
            "max_hr": summary.get("maxHR"),
            "calories": summary.get("calories"),
            "avg_power": summary.get("averagePower") or summary.get("avgPower"),
            "normalized_power": summary.get("normalizedPower") or summary.get("normPower"),
            "avg_cadence": summary.get("averageRunCadence")
            or summary.get("averageRunningCadenceInStepsPerMinute")
            or summary.get("averageBikeCadence")
            or summary.get("averageBikingCadenceInRevPerMinute"),
            "aerobic_te": summary.get("trainingEffect") or summary.get("aerobicTrainingEffect"),
            "anaerobic_te": summary.get("anaerobicTrainingEffect"),
            "training_effect_label": summary.get("trainingEffectLabel")
            or a.get("trainingEffectLabel"),
            "training_load": summary.get("activityTrainingLoad") or a.get("activityTrainingLoad"),
            "vo2max": a.get("vO2MaxValue"),
            "description": a.get("description"),
        }
    )


def splits(data: dict[str, Any]) -> list[dict[str, Any]]:
    laps = data.get("lapDTOs") or []
    out = []
    for i, lap in enumerate(laps, start=1):
        speed = lap.get("averageSpeed")
        out.append(
            _clean(
                {
                    "lap": i,
                    "distance_km": _km(lap.get("distance")),
                    "duration_min": _minutes(lap.get("duration")),
                    "pace_min_per_km": _pace_min_per_km(speed),
                    "avg_hr": lap.get("averageHR"),
                    "max_hr": lap.get("maxHR"),
                    "avg_power": lap.get("averagePower"),
                    "elevation_gain_m": lap.get("elevationGain"),
                    "avg_cadence": lap.get("averageRunCadence") or lap.get("averageBikeCadence"),
                }
            )
        )
    return out


def hr_zones(data: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        _clean(
            {
                "zone": z.get("zoneNumber"),
                "min_hr": z.get("zoneLowBoundary"),
                "minutes": _minutes(z.get("secsInZone")),
            }
        )
        for z in data or []
    ]


def workout(w: dict[str, Any]) -> dict[str, Any]:
    return _clean(
        {
            "id": w.get("workoutId"),
            "name": w.get("workoutName"),
            "sport": _get(w, "sportType", "sportTypeKey"),
            "estimated_duration_min": _minutes(w.get("estimatedDurationInSecs")),
            "description": w.get("description"),
            "updated": w.get("updatedDate"),
        }
    )
