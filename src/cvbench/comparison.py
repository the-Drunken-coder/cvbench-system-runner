from __future__ import annotations

from typing import Any

METRICS = {
    "acquisition.rate": "higher",
    "acquisition.median": "lower",
    "coverage.overall_observed": "higher",
    "coverage.overall_continuity": "higher",
    "visible_dropouts.per_target_minute": "lower",
    "localization.mean_iou": "higher",
    "identity.id_switches_per_target_minute": "lower",
    "false_detections.detections_per_camera_minute": "lower",
    "reacquisition.same_id_rate": "higher",
    "reacquisition.correct_target_rate": "higher",
    "latency.p99": "lower",
}


def _get(data: dict[str, Any], dotted: str) -> Any:
    value: Any = data
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    baseline_metrics = baseline.get("metrics", baseline)
    candidate_metrics = candidate.get("metrics", candidate)
    candidate_samples = int(_get(candidate_metrics, "sample_counts.matches") or 0)
    baseline_fingerprint = _get(baseline, "provenance.comparison_fingerprint")
    candidate_fingerprint = _get(candidate, "provenance.comparison_fingerprint")
    if not baseline_fingerprint or baseline_fingerprint != candidate_fingerprint:
        names = [*METRICS, "resources.average_cpu_percent", "resources.peak_ram_bytes"]
        return [
            {
                "metric": metric,
                "baseline": _get(baseline, metric)
                if metric.startswith("resources.")
                else _get(baseline_metrics, metric),
                "candidate": _get(candidate, metric)
                if metric.startswith("resources.")
                else _get(candidate_metrics, metric),
                "delta": None,
                "direction": "inconclusive",
                "confidence": "low",
                "sample_count": candidate_samples,
                "reason": "benchmark scenarios or scoring configuration are incompatible",
            }
            for metric in names
        ]
    results: list[dict[str, Any]] = []
    for metric, preferred in METRICS.items():
        old = _get(baseline_metrics, metric)
        new = _get(candidate_metrics, metric)
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            results.append(
                {
                    "metric": metric,
                    "baseline": old,
                    "candidate": new,
                    "delta": None,
                    "direction": "inconclusive",
                    "confidence": "low",
                    "sample_count": candidate_samples,
                }
            )
            continue
        delta = new - old
        if abs(delta) <= 1e-12:
            direction = "unchanged"
        elif (delta > 0 and preferred == "higher") or (delta < 0 and preferred == "lower"):
            direction = "improvement"
        else:
            direction = "regression"
        results.append(
            {
                "metric": metric,
                "baseline": old,
                "candidate": new,
                "delta": delta,
                "direction": direction,
                "confidence": "moderate" if candidate_samples >= 30 else "low",
                "sample_count": candidate_samples,
            }
        )
    resource_pairs = {
        "resources.average_cpu_percent": "lower",
        "resources.peak_ram_bytes": "lower",
    }
    for metric, preferred in resource_pairs.items():
        old = _get(baseline, metric)
        new = _get(candidate, metric)
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            direction, delta = "inconclusive", None
        else:
            delta = new - old
            direction = (
                "unchanged"
                if abs(delta) <= 1e-12
                else ("improvement" if (delta < 0 and preferred == "lower") else "regression")
            )
        results.append(
            {
                "metric": metric,
                "baseline": old,
                "candidate": new,
                "delta": delta,
                "direction": direction,
                "confidence": "low",
                "sample_count": candidate_samples,
            }
        )
    return results
