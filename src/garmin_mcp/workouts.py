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
    "walking": 17,
    "hiking": 18,
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

CONDITIONS = {"lap.button": 1, "time": 2, "distance": 3, "iterations": 7, "reps": 10}

# Garmin stores strength weights in grams, tagged with the kilogram unit.
WEIGHT_UNIT_KILOGRAM = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}

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
    if "reps" in step:
        reps = int(step["reps"])
        if reps < 1:
            raise WorkoutSpecError("reps must be >= 1")
        return cond("reps"), float(reps), reps * 4.0
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
        exercise = spec.get("exercise")
        if exercise:
            if isinstance(exercise, str):
                exercise = {"category": exercise}
            out["category"] = str(exercise.get("category", "")).upper()
            out["exerciseName"] = str(exercise.get("name", "")).upper()
        if spec.get("weight_kg") is not None:
            out["weightValue"] = float(spec["weight_kg"]) * 1000.0
            out["weightUnit"] = dict(WEIGHT_UNIT_KILOGRAM)
        return out


def build_workout(spec: dict[str, Any]) -> dict[str, Any]:
    name = (spec.get("name") or "").strip()
    if not name:
        raise WorkoutSpecError("workout needs a name")
    sport = spec.get("sport", "running")
    if sport not in SPORTS:
        raise WorkoutSpecError(f"sport must be one of {sorted(SPORTS)}")
    sport_type = {"sportTypeId": SPORTS[sport], "sportTypeKey": sport}
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


# -- reading workouts back into the spec shape --------------------------------

_TARGET_BACK = {
    "heart.rate.zone": ("hr_zone", "hr", "bpm"),
    "power.zone": ("power_zone", "power", "watts"),
}


def _pace(speed: float) -> str:
    secs = round(1000 / speed)
    return f"{secs // 60}:{secs % 60:02d}"


def _target_back(step: dict[str, Any]) -> dict[str, Any] | None:
    key = (step.get("targetType") or {}).get("workoutTargetTypeKey")
    one, two, zone = step.get("targetValueOne"), step.get("targetValueTwo"), step.get("zoneNumber")
    if not key or key == "no.target":
        return None
    if key in _TARGET_BACK:
        zone_type, range_type, unit = _TARGET_BACK[key]
        if zone:
            return {"type": zone_type, "zone": zone}
        return {"type": range_type, unit: [one, two]}
    if key == "pace.zone" and one and two:
        return {"type": "pace", "min_per_km": [_pace(one), _pace(two)]}
    if key == "speed.zone" and one and two:
        return {"type": "speed", "kmh": [round(one * 3.6, 1), round(two * 3.6, 1)]}
    if key == "cadence":
        return {"type": "cadence", "rpm": [one, two]}
    return {"type": key, "values": [one, two], "zone": zone}


def _step_back(step: dict[str, Any]) -> dict[str, Any]:
    if step.get("type") == "RepeatGroupDTO":
        return {
            "repeat": int(step.get("numberOfIterations") or 1),
            "steps": [_step_back(s) for s in step.get("workoutSteps") or []],
        }
    out: dict[str, Any] = {"type": (step.get("stepType") or {}).get("stepTypeKey", "other")}
    cond = (step.get("endCondition") or {}).get("conditionTypeKey")
    value = step.get("endConditionValue")
    if cond == "time":
        out["duration_s"] = value
    elif cond == "distance":
        out["distance_m"] = value
    elif cond == "reps":
        out["reps"] = int(value or 0)
    else:
        out["lap_button"] = True
    target = _target_back(step)
    if target:
        out["target"] = target
    if step.get("category"):
        out["exercise"] = {"category": step["category"], "name": step.get("exerciseName") or ""}
    if step.get("weightValue"):
        out["weight_kg"] = round(step["weightValue"] / 1000.0, 2)
    if step.get("description"):
        out["note"] = step["description"]
    return out


def describe_workout(w: dict[str, Any]) -> dict[str, Any]:
    """Garmin workout JSON -> the same spec shape `build_workout` accepts."""
    steps = []
    for segment in w.get("workoutSegments") or []:
        steps.extend(_step_back(s) for s in segment.get("workoutSteps") or [])
    spec = {
        "id": w.get("workoutId"),
        "name": w.get("workoutName"),
        "sport": (w.get("sportType") or {}).get("sportTypeKey"),
        "description": w.get("description"),
        "estimated_duration_min": round((w.get("estimatedDurationInSecs") or 0) / 60, 1),
        "steps": steps,
    }
    return {k: v for k, v in spec.items() if v not in (None, "", [])}
