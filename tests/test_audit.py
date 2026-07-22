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
    assert evidence["frame_samples"][0]["ground_truth"][0]["count_reason"] == "matched_observed"
    assert evidence["score_explanation"]["counted_observed"] == 80
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
