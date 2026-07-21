from cvbench.stability import evaluate_long_run_assertions


def test_all_long_run_release_assertions_are_explicit_and_passed() -> None:
    observations = {
        "unique_track_ids": 42,
        "track_id_exhaustion_detected": False,
        "state_contamination_events": 0,
        "false_positive_accumulation_per_camera_minute": -0.5,
        "interruption_recovery_rate": 1.0,
        "latency_drift_ms": -12.0,
    }
    declared = {
        "max_unique_track_ids": 100,
        "require_no_track_id_exhaustion": True,
        "max_state_contamination_events": 0,
        "max_false_positive_accumulation_per_camera_minute": 0,
        "min_interruption_recovery_rate": 1.0,
        "max_absolute_latency_drift_ms": 20,
        "max_memory_growth_bytes": 1_000_000,
    }
    result = evaluate_long_run_assertions(observations, {"memory_growth_bytes": 500_000}, declared)
    assert result["passed"] is True
    assert set(result["assertions"]) == set(declared)


def test_missing_soak_evidence_fails_the_relevant_assertion() -> None:
    result = evaluate_long_run_assertions(
        {"interruption_recovery_rate": None},
        {"memory_growth_bytes": None},
        {"min_interruption_recovery_rate": 1.0, "max_memory_growth_bytes": 10},
    )
    assert result["passed"] is False
