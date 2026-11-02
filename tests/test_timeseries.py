from garmin_mcp.timeseries import build_timeseries


def details(n=3600):
    descriptors = [
        {"metricsIndex": 0, "key": "sumElapsedDuration"},
        {"metricsIndex": 1, "key": "directHeartRate"},
        {"metricsIndex": 2, "key": "directSpeed"},
        {"metricsIndex": 3, "key": "sumDistance"},
        {"metricsIndex": 4, "key": "directTimestamp"},
    ]
    rows = [{"metrics": [float(t), 120 + t % 60, 3.333, t * 3.333, 1_700_000_000_000 + t * 1000]}
            for t in range(n)]
    return {"metricDescriptors": descriptors, "activityDetailMetrics": rows}


def test_downsamples_to_max_points():
    ts = build_timeseries(details(), max_points=100)
    assert ts["native_samples"] == 3600
    assert ts["native_resolution_s"] == 1.0
    assert 95 <= ts["points"] <= 101
    assert len(ts["hr_bpm"]) == len(ts["t_s"]) == ts["points"]
    assert ts["speed_kmh"][0] == 12.0
    assert ts["pace_min_per_km"][0] == 5.0
    assert ts["hr_bpm"][0] == 137.5  # mean of 120..155 in first 36 s bucket
    assert set(ts) >= {"distance_km", "t_s"}
    assert "power_w" not in ts  # not recorded


def test_window_at_full_resolution():
    ts = build_timeseries(details(), start_s=600, end_s=659, max_points=5000)
    assert ts["resolution_s"] == 1.0
    assert ts["points"] == 60
    assert ts["t_s"][0] == 600.0
    assert ts["hr_bpm"][:3] == [120, 121, 122]


def test_metric_selection_and_empty():
    ts = build_timeseries(details(120), metrics=["hr_bpm"], max_points=10)
    assert "speed_kmh" not in ts and "hr_bpm" in ts
    assert build_timeseries({})["available"] is False
