from __future__ import annotations

from typing import Any


def evaluate_long_run_assertions(
    observations: dict[str, Any], resources: dict[str, Any], declared: dict[str, Any]
) -> dict[str, Any]:
    result = dict(observations)
    result["memory_growth_bytes"] = resources.get("memory_growth_bytes")
    checks: dict[str, dict[str, Any]] = {}

    def maximum(name: str, field: str, *, absolute: bool = False) -> None:
        if name not in declared:
            return
        actual = result.get(field)
        compared = abs(actual) if absolute and actual is not None else actual
        checks[name] = {
            "actual": actual,
            "limit": declared[name],
            "passed": compared is not None and compared <= declared[name],
        }

    maximum("max_unique_track_ids", "unique_track_ids")
    maximum("max_state_contamination_events", "state_contamination_events")
    maximum(
        "max_false_positive_accumulation_per_camera_minute",
        "false_positive_accumulation_per_camera_minute",
    )
    maximum("max_absolute_latency_drift_ms", "latency_drift_ms", absolute=True)
    maximum("max_memory_growth_bytes", "memory_growth_bytes")
    if "min_interruption_recovery_rate" in declared:
        actual = result.get("interruption_recovery_rate")
        checks["min_interruption_recovery_rate"] = {
            "actual": actual,
            "limit": declared["min_interruption_recovery_rate"],
            "passed": actual is not None and actual >= declared["min_interruption_recovery_rate"],
        }
    if declared.get("require_no_track_id_exhaustion"):
        actual = result.get("track_id_exhaustion_detected")
        checks["require_no_track_id_exhaustion"] = {
            "actual": actual,
            "limit": False,
            "passed": actual is False,
        }
    result["assertions"] = checks
    result["assertions_declared"] = bool(checks)
    result["passed"] = all(check["passed"] for check in checks.values()) if checks else None
    return result
