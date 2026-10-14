import pytest

from garmin_mcp.workouts import WorkoutSpecError, build_workout


def test_intervals_with_repeat():
    w = build_workout(
        {
            "name": "6x800",
            "sport": "running",
            "steps": [
                {"type": "warmup", "duration_s": 600, "target": {"type": "hr_zone", "zone": 2}},
                {"repeat": 6, "steps": [
                    {"type": "interval", "distance_m": 800,
                     "target": {"type": "pace", "min_per_km": ["4:00", "3:50"]}},
                    {"type": "recovery", "duration_s": 120},
                ]},
                {"type": "cooldown", "lap_button": True},
            ],
        }
    )
    steps = w["workoutSegments"][0]["workoutSteps"]
    assert [s["stepOrder"] for s in steps] == [1, 2, 5]
    warm, rep, cool = steps
    assert warm["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert warm["zoneNumber"] == 2
    assert rep["type"] == "RepeatGroupDTO" and rep["numberOfIterations"] == 6
    interval = rep["workoutSteps"][0]
    assert interval["stepOrder"] == 3 and interval["childStepId"] == 1
    assert interval["endCondition"]["conditionTypeKey"] == "distance"
    # pace range becomes speeds in m/s, slow then fast
    assert interval["targetValueOne"] == pytest.approx(4.167, abs=1e-3)
    assert interval["targetValueTwo"] == pytest.approx(4.348, abs=1e-3)
    assert cool["endCondition"]["conditionTypeKey"] == "lap.button"
    assert w["estimatedDurationInSecs"] > 600


def test_rejects_bad_spec():
    with pytest.raises(WorkoutSpecError):
        build_workout({"name": "x", "steps": []})
    with pytest.raises(WorkoutSpecError):
        build_workout({"name": "x", "sport": "curling", "steps": [{"duration_s": 1}]})
    with pytest.raises(WorkoutSpecError):
        build_workout({"name": "x", "steps": [{"duration_s": 60, "target": {"type": "pace",
                                                                            "min_per_km": "4:00"}}]})
