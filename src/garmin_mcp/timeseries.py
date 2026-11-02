"""Second-by-second activity samples (the charts in Garmin Connect), downsampled.

Garmin's activity details endpoint returns samples positionally, with a
`metricDescriptors` list naming each column. We resolve those, pick friendly
channels, cut an optional time window and average into evenly sized time
buckets so the response stays a sensible size for a conversation.
"""

from __future__ import annotations

from typing import Any

# Garmin key -> (output name, converter)
CHANNELS: dict[str, tuple[str, Any]] = {
    "directHeartRate": ("hr_bpm", lambda v: v),
    "directSpeed": ("speed_kmh", lambda v: v * 3.6),
    "directPower": ("power_w", lambda v: v),
    "directRunCadence": ("cadence_spm", lambda v: v),
    "directDoubleCadence": ("cadence_spm", lambda v: v),
    "directBikeCadence": ("cadence_rpm", lambda v: v),
    "directElevation": ("elevation_m", lambda v: v),
    "sumDistance": ("distance_km", lambda v: v / 1000),
    "directGradeAdjustedSpeed": ("gap_speed_kmh", lambda v: v * 3.6),
    "directAirTemperature": ("temp_c", lambda v: v),
    "directRespirationRate": ("respiration_brpm", lambda v: v),
    "directStrideLength": ("stride_m", lambda v: v / 100 if v and v > 10 else v),
    "directGroundContactTime": ("ground_contact_ms", lambda v: v),
    "directVerticalOscillation": ("vertical_osc_cm", lambda v: v),
    "directVerticalRatio": ("vertical_ratio_pct", lambda v: v),
    "directPerformanceCondition": ("performance_condition", lambda v: v),
    "directLatitude": ("lat", lambda v: v),
    "directLongitude": ("lon", lambda v: v),
}

DEFAULT_METRICS = ["hr_bpm", "speed_kmh", "pace_min_per_km", "power_w", "cadence_spm",
                   "cadence_rpm", "elevation_m", "distance_km"]
# These are sampled, not averaged, when bucketing.
POINT_METRICS = {"distance_km", "lat", "lon"}
MAX_POINTS = 5000


def _samples(details: dict[str, Any]) -> list[dict[str, Any]]:
    index_to_key: dict[int, str] = {}
    for d in details.get("metricDescriptors") or []:
        if isinstance(d.get("key"), str) and isinstance(d.get("metricsIndex"), int):
            index_to_key[d["metricsIndex"]] = d["key"]
    out = []
    for sample in details.get("activityDetailMetrics") or []:
        m = sample.get("metrics") or []
        out.append({k: m[i] for i, k in index_to_key.items() if i < len(m)})
    return out


def _elapsed(sample: dict[str, Any], t0: float | None) -> float | None:
    for key in ("sumElapsedDuration", "sumDuration"):
        if isinstance(sample.get(key), (int, float)):
            return float(sample[key])
    ts = sample.get("directTimestamp")
    if isinstance(ts, (int, float)) and t0 is not None:
        return (ts - t0) / 1000.0
    return None


def _pace(speed_kmh: float | None) -> float | None:
    """Pace in decimal minutes per km (e.g. 5.25 = 5:15/km)."""
    if not speed_kmh or speed_kmh < 1:
        return None
    return round(60 / speed_kmh, 2)


def build_timeseries(
    details: dict[str, Any],
    metrics: list[str] | None = None,
    max_points: int = 600,
    start_s: float | None = None,
    end_s: float | None = None,
) -> dict[str, Any]:
    samples = _samples(details)
    if not samples:
        return {"available": False, "reason": "Garmin returned no samples for this activity."}

    ts0 = next((s["directTimestamp"] for s in samples if isinstance(s.get("directTimestamp"), (int, float))), None)
    rows: list[tuple[float, dict[str, float]]] = []
    present: set[str] = set()
    for s in samples:
        t = _elapsed(s, ts0)
        if t is None:
            continue
        row: dict[str, float] = {}
        for key, (name, conv) in CHANNELS.items():
            v = s.get(key)
            if isinstance(v, (int, float)) and name not in row:
                row[name] = conv(v)
        if "speed_kmh" in row:
            row["pace_min_per_km"] = _pace(row["speed_kmh"])
        present.update(k for k, v in row.items() if v is not None)
        rows.append((t, row))
    rows.sort(key=lambda r: r[0])
    if not rows:
        return {"available": False, "reason": "Samples had no time information."}

    total_s = rows[-1][0]
    native_resolution = round(total_s / max(len(rows) - 1, 1), 2)
    lo = start_s if start_s is not None else rows[0][0]
    hi = end_s if end_s is not None else total_s
    rows = [r for r in rows if lo <= r[0] <= hi]
    if not rows:
        return {"available": False, "reason": f"No samples between {lo}s and {hi}s."}

    wanted = [m for m in (metrics or DEFAULT_METRICS) if m in present]
    max_points = max(10, min(int(max_points), MAX_POINTS))
    span = max(rows[-1][0] - rows[0][0], 1.0)
    bucket = max(span / max_points, native_resolution or 1.0, 1.0)

    out_t: list[float] = []
    cols: dict[str, list[float | None]] = {m: [] for m in wanted}
    i = 0
    start = rows[0][0]
    while i < len(rows):
        edge = start + bucket * (int((rows[i][0] - start) / bucket) + 1)
        chunk = []
        while i < len(rows) and rows[i][0] < edge:
            chunk.append(rows[i])
            i += 1
        out_t.append(round(chunk[0][0], 1))
        for m in wanted:
            vals = [r[m] for _, r in chunk if r.get(m) is not None]
            if not vals:
                cols[m].append(None)
            elif m in POINT_METRICS:
                cols[m].append(round(vals[-1], 6 if m in ("lat", "lon") else 3))
            else:
                cols[m].append(round(sum(vals) / len(vals), 2))

    return {
        "available": True,
        "duration_s": round(total_s, 1),
        "native_samples": len(samples),
        "native_resolution_s": native_resolution,
        "resolution_s": round(bucket, 2),
        "window_s": [round(lo, 1), round(hi, 1)],
        "points": len(out_t),
        "available_metrics": sorted(present),
        "note": "Values are averages over each resolution_s bucket; pace is decimal min/km.",
        "t_s": out_t,
        **cols,
    }
