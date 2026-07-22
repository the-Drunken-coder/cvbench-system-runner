from __future__ import annotations

from cvbench.audit import build_audit_evidence
from cvbench.config import Thresholds
from cvbench.metrics import calculate_metrics
from tests.helpers import gt, output


def test_audit_evidence_is_bounded_and_keeps_fairness_signals() -> None:
    ground_truth = [gt(index * 1_000_000, sequence="audit") for index in range(80)]
    records = [output(index * 1_000_000, sequence="audit", received_offset_ns=5_000_000) for index in range(80)]
    records.extend(
        output(index * 1_000_000, sequence="audit", track="false", box=[50, 50, 60, 60])
        for index in range(80)
    )
    metrics, matches = calculate_metrics(ground_truth, records, Thresholds())
    evidence = build_audit_evidence(
        ground_truth,
        records,
        matches,
        metrics,
        {"delivered_frames": 80},
        {"sample_count": 200, "over_time": [{"elapsed_ms": index} for index in range(200)]},
        {"status": "verified", "network_mode": "none"},
    )

    assert evidence["sampled_frame_count"] == 64
    assert len(evidence["frame_samples"]) == 64
    assert len(evidence["resources_and_isolation"]["resources"]["over_time"]) == 64
    assert evidence["timeline"]["support_counts"]["observed"] == 160
    assert evidence["false_track_segments"][0]["track_id"] == "false"
    assert evidence["frame_samples"][0]["ground_truth"][0]["count_reason"] == "matched_observed_and_counted"
    assert evidence["score_explanation"]["matched_observed"] == 80
    assert all(flag["review_aid_only"] for flag in evidence["flags"])
    assert any(flag["id"] == "exact_ground_truth_replay" and flag["status"] == "flagged" for flag in evidence["flags"])


def test_audit_marks_impossible_latency_without_disqualifying() -> None:
    ground_truth = [gt(10_000_000, sequence="timing")]
    records = [output(10_000_000, sequence="timing", received_offset_ns=-1)]
    metrics, matches = calculate_metrics(ground_truth, records, Thresholds())
    evidence = build_audit_evidence(
        ground_truth,
        records,
        matches,
        metrics,
        {"delivered_frames": 1},
        {"sample_count": 0},
        {"status": "verified", "network_mode": "none"},
    )

    assert any(flag["id"] == "impossible_latency" and flag["status"] == "flagged" for flag in evidence["flags"])
    assert evidence["review_disposition"].startswith("review_aid_only")


def test_matched_ineligible_rows_are_not_claimed_as_coverage_score() -> None:
    ground_truth = [
        gt(index * 1_000_000, sequence="twelve", eligible=index < 10, visibility=1 if index < 10 else 0)
        for index in range(12)
    ]
    records = [output(index * 1_000_000, sequence="twelve") for index in range(12)]
    metrics, matches = calculate_metrics(ground_truth, records, Thresholds())
    evidence = build_audit_evidence(
        ground_truth,
        records,
        matches,
        metrics,
        {"delivered_frames": 12},
        {"sample_count": 1},
        {"status": "verified", "network_mode": "none"},
    )

    assert metrics["sample_counts"]["matches"] == 12
    assert metrics["coverage"]["overall_observed"] == 1
    assert metrics["localization"]["sample_count"] == 12
    ineligible = evidence["frame_samples"][-1]["ground_truth"][0]
    assert ineligible["matched"] is True
    assert ineligible["counted_toward_score"]["observed_coverage"] is False
    assert ineligible["counted_toward_score"]["localization"] is True
    assert ineligible["count_reason"] == "matched_but_ineligible_for_detection"
    assert evidence["score_explanation"]["ineligible_rows_with_matches"] == 2
    assert "including an ineligible row" in evidence["score_explanation"]["component_eligibility"]["localization"]
    assert evidence["score_explanation"]["component_counts"]["observed_coverage"] == 10
    assert evidence["score_explanation"]["component_counts"]["localization"] == 12


def test_eligible_unmatched_frame_is_a_denominator_miss_not_not_counted() -> None:
    ground_truth = [gt(0, sequence="two-frame"), gt(1_000_000, sequence="two-frame")]
    records = [output(0, sequence="two-frame")]
    metrics, matches = calculate_metrics(ground_truth, records, Thresholds())
    evidence = build_audit_evidence(
        ground_truth,
        records,
        matches,
        metrics,
        {"delivered_frames": 2},
        {"sample_count": 1},
        {"status": "verified", "network_mode": "none"},
    )

    assert metrics["coverage"]["overall_observed"] == 0.5
    assert evidence["score_explanation"]["coverage_denominators"]["observed_coverage"] == 2
    assert evidence["score_explanation"]["component_counts"]["observed_coverage"] == 1
    assert evidence["score_explanation"]["eligible_without_gated_match"] == 1
    miss = evidence["frame_samples"][1]["ground_truth"][0]
    assert miss["denominator_eligible"]["observed_coverage"] is True
    assert miss["matched"] is False
    assert miss["counted_toward_score"]["observed_coverage"] is False
    assert miss["count_reason"] == "eligible_without_gated_match"
