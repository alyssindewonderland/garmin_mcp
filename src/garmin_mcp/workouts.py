"""Build Garmin workout JSON from a small, model-friendly spec.

Spec example (what Claude sends to `create_workout`):

    {
      "name": "6x800 @ 5k pace",
      "sport": "running",
      "description": "optional",
      "steps": [
        {"type": "warmup", "duration_s": 600, "target": {"type": "hr_zone", "zone": 2}},
        {"repeat": 6, "steps": [
          {"type": "interval", "distance_m": 800, "target": {"type": "pace", "min_per_km": ["3:50", "4:00"]}},
          {"type": "recovery", "duration_s": 120}
        ]},
        {"type": "cooldown", "lap_button": true}
      ]
    }
"""

from __future__ import annotations

from typing import Any

SPORTS = {
    "running": 1,
    "cycling": 2,
    "other": 3,
    "swimming": 4,
    "strength_training": 5,
    "cardio_training": 6,
    "yoga": 7,
    "pilates": 8,
    "hiit": 9,
    "mobility": 11,
    "walking": 3,  # Garmin files walking under "other" for structured workouts
    "hiking": 3,
}

STEP_TYPES = {
    "warmup": 1,
    "cooldown": 2,
    "interval": 3,
    "recovery": 4,
    "rest": 5,
    "repeat": 6,
    "other": 7,
}

CONDITIONS = {"lap.button": 1, "time": 2, "distance": 3, "iterations": 7}

TARGETS = {
    "no.target": 1,
    "power.zone": 2,
    "cadence": 3,
    "heart.rate.zone": 4,
    "speed.zone": 5,
    "pace.zone": 6,
}


class WorkoutSpecError(ValueError):
    pass


def _parse_pace(value: str | float) -> float:
    """'4:30' (min/km) -> speed in m/s."""
    if isinstance(value, (int, float)):
        secs = float(value) * 60
    else:
        try:
            minutes, seconds = str(value).strip().split(":")
            secs = int(minutes) * 60 + int(seconds)
        except ValueError as e:
            raise WorkoutSpecError(f"pace must look like '4:30', got {value!r}") from e
    if secs <= 0:
        raise WorkoutSpecError("pace must be positive")
    return round(1000 / secs, 3)


def _pair(value: Any, name: str) -> tuple[Any, Any]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0], value[1]
    raise WorkoutSpecError(f"{name} must be a [low, high] pair")


def _target(t: dict[str, Any] | None) -> dict[str, Any]:
    def tt(key: str) -> dict[str, Any]:
        return {"workoutTargetTypeId": TARGETS[key], "workoutTargetTypeKey": key}

    if not t or t.get("type") in (None, "none"):
        return {"targetType": tt("no.target")}
    kind = t["type"]
    if kind == "hr_zone":
        return {"targetType": tt("heart.rate.zone"), "zoneNumber": int(t["zone"])}
    if kind == "hr":
        lo, hi = _pair(t.get("bpm"), "bpm")
        return {"targetType": tt("heart.rate.zone"), "targetValueOne": lo, "targetValueTwo": hi}
    if kind == "power_zone":
        return {"targetType": tt("power.zone"), "zoneNumber": int(t["zone"])}
    if kind == "power":
        lo, hi = _pair(t.get("watts"), "watts")
        return {"targetType": tt("power.zone"), "targetValueOne": lo, "targetValueTwo": hi}
    if kind == "cadence":
        lo, hi = _pair(t.get("rpm") or t.get("spm"), "rpm/spm")
        return {"targetType": tt("cadence"), "targetValueOne": lo, "targetValueTwo": hi}
    if kind == "pace":
        a, b = _pair(t.get("min_per_km"), "min_per_km")
        speeds = sorted([_parse_pace(a), _parse_pace(b)])
        return {"targetType": tt("pace.zone"), "targetValueOne": speeds[0], "targetValueTwo": speeds[1]}
    if kind == "speed":
        lo, hi = _pair(t.get("kmh"), "kmh")
        return {
            "targetType": tt("speed.zone"),
            "targetValueOne": round(lo / 3.6, 3),
            "targetValueTwo": round(hi / 3.6, 3),
        }
    raise WorkoutSpecError(f"unknown target type {kind!r}")


def _end_condition(step: dict[str, Any]) -> tuple[dict[str, Any], float | None, float]:
    """Return (endCondition, endConditionValue, estimated seconds)."""

    def cond(key: str) -> dict[str, Any]:
        return {"conditionTypeId": CONDITIONS[key], "conditionTypeKey": key}

    if "duration_s" in step:
        secs = float(step["duration_s"])
        return cond("time"), secs, secs
    if "distance_m" in step:
        meters = float(step["distance_m"])
        return cond("distance"), meters, meters / 3.0  # rough estimate at ~5:30/km
    if step.get("lap_button", True):
        return cond("lap.button"), None, 300.0
    raise WorkoutSpecError("step needs duration_s, distance_m or lap_button")


class _Builder:
    def __init__(self) -> None:
        self.order = 0
        self.child_id = 0
        self.estimated = 0.0

    def steps(self, specs: list[dict[str, Any]], child_id: int | None = None) -> list[dict]:
        if not isinstance(specs, list) or not specs:
            raise WorkoutSpecError("steps must be a non-empty list")
        return [self.step(s, child_id) for s in specs]

    def step(self, spec: dict[str, Any], child_id: int | None) -> dict[str, Any]:
        self.order += 1
        if "repeat" in spec:
            n = int(spec["repeat"])
            if n < 1:
                raise WorkoutSpecError("repeat must be >= 1")
            self.child_id += 1
            my_child = self.child_id
            order = self.order
            before = self.estimated
            children = self.steps(spec.get("steps") or [], my_child)
            one_pass = self.estimated - before
            self.estimated = before + one_pass * n
            return {
                "type": "RepeatGroupDTO",
                "stepOrder": order,
                "stepType": {"stepTypeId": STEP_TYPES["repeat"], "stepTypeKey": "repeat"},
                "childStepId": my_child,
                "numberOfIterations": n,
                "smartRepeat": False,
                "endCondition": {"conditionTypeId": CONDITIONS["iterations"], "conditionTypeKey": "iterations"},
                "endConditionValue": float(n),
                "workoutSteps": children,
            }
        kind = spec.get("type", "interval")
        if kind not in STEP_TYPES or kind == "repeat":
            raise WorkoutSpecError(f"unknown step type {kind!r}")
        end, value, est = _end_condition(spec)
        self.estimated += est
        out: dict[str, Any] = {
            "type": "ExecutableStepDTO",
            "stepOrder": self.order,
            "stepType": {"stepTypeId": STEP_TYPES[kind], "stepTypeKey": kind},
            "endCondition": end,
            "endConditionValue": value,
            **_target(spec.get("target")),
        }
        if child_id is not None:
            out["childStepId"] = child_id
        if spec.get("note"):
            out["description"] = str(spec["note"])[:250]
        return out


def build_workout(spec: dict[str, Any]) -> dict[str, Any]:
    name = (spec.get("name") or "").strip()
    if not name:
        raise WorkoutSpecError("workout needs a name")
    sport = spec.get("sport", "running")
    if sport not in SPORTS:
        raise WorkoutSpecError(f"sport must be one of {sorted(SPORTS)}")
    sport_type = {"sportTypeId": SPORTS[sport], "sportTypeKey": sport if SPORTS[sport] != 3 else "other"}
    b = _Builder()
    steps = b.steps(spec.get("steps") or [])
    workout = {
        "workoutName": name[:80],
        "sportType": sport_type,
        "estimatedDurationInSecs": int(b.estimated),
        "workoutSegments": [{"segmentOrder": 1, "sportType": sport_type, "workoutSteps": steps}],
    }
    if spec.get("description"):
        workout["description"] = str(spec["description"])[:1000]
    return workout
