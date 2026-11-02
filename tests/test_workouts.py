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


def test_strength_sets_and_roundtrip():
    from garmin_mcp.workouts import describe_workout

    spec = {
        "name": "Push day",
        "sport": "strength_training",
        "steps": [
            {"repeat": 3, "steps": [
                {"type": "interval", "reps": 8,
                 "exercise": {"category": "bench_press", "name": "barbell_bench_press"},
                 "weight_kg": 60},
                {"type": "rest", "duration_s": 90},
            ]},
        ],
    }
    w = build_workout(spec)
    work = w["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]
    assert work["endCondition"]["conditionTypeKey"] == "reps"
    assert work["category"] == "BENCH_PRESS" and work["exerciseName"] == "BARBELL_BENCH_PRESS"
    assert work["weightValue"] == 60000.0
    back = describe_workout(w)
    assert back["sport"] == "strength_training"
    assert back["steps"][0]["repeat"] == 3
    assert back["steps"][0]["steps"][0] == {
        "type": "interval", "reps": 8,
        "exercise": {"category": "BENCH_PRESS", "name": "BARBELL_BENCH_PRESS"}, "weight_kg": 60.0,
    }


def test_pace_target_roundtrip_and_walking_sport():
    from garmin_mcp.workouts import describe_workout

    w = build_workout({"name": "walk", "sport": "walking", "steps": [
        {"type": "interval", "duration_s": 600,
         "target": {"type": "pace", "min_per_km": ["9:00", "10:00"]}}]})
    assert w["sportType"] == {"sportTypeId": 17, "sportTypeKey": "walking"}
    step = describe_workout(w)["steps"][0]
    assert step["target"] == {"type": "pace", "min_per_km": ["10:00", "9:00"]}
