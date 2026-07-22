from __future__ import annotations

import json

from cvbench.audit import AUDIT_EVIDENCE_MAX_BYTES, build_audit_evidence
from cvbench.config import Thresholds
from cvbench.json_contract import serialized_json_bytes
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


def test_audit_evidence_hard_budget_truncates_near_limit_model_strings() -> None:
    ground_truth = [gt(0, sequence="near-limit")]
    records = [output(0, sequence="near-limit", track=f"track-{index}-" + "x" * 60_000) for index in range(16)]
    metrics, matches = calculate_metrics(ground_truth, records, Thresholds())

    evidence = build_audit_evidence(
        ground_truth,
        records,
        matches,
        metrics,
        {"delivered_frames": 1},
        {"sample_count": 1, "over_time": []},
        {"status": "verified", "network_mode": "none"},
    )

    serialized = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    assert len(serialized) <= AUDIT_EVIDENCE_MAX_BYTES
    assert evidence["serialized_byte_budget"]["truncated"] is True
    assert len(evidence["frame_samples"][0]["predictions"][0]["track_id"].encode("utf-8")) <= 256


def test_non_ascii_full_sample_grid_uses_wire_byte_budget() -> None:
    ground_truth = [gt(index * 1_000_000, sequence="non-ascii") for index in range(64)]
    records = [
        output(
            index * 1_000_000,
            sequence="non-ascii",
            track=f"track-{index}-{prediction}-" + "😀" * 200,
        )
        for index in range(64)
        for prediction in range(16)
    ]
    metrics, matches = calculate_metrics(ground_truth, records, Thresholds())
    evidence = build_audit_evidence(
        ground_truth,
        records,
        matches,
        metrics,
        {"delivered_frames": 64},
        {"sample_count": 64, "over_time": []},
        {"status": "verified", "network_mode": "none"},
    )

    assert evidence["source_frame_count"] == 64
    assert evidence["serialized_byte_budget"]["truncated"] is True
    assert len(serialized_json_bytes(evidence)) <= AUDIT_EVIDENCE_MAX_BYTES
    omitted = evidence["budget_omitted"]
    retained_frames = evidence["frame_samples"]
    assert omitted["frame_samples"] + len(retained_frames) == 64
    assert omitted["records_in_omitted_frames"]["ground_truth"] + sum(
        len(sample["ground_truth"]) + sample["ground_truth_omitted"] for sample in retained_frames
    ) == 64
    assert omitted["records_in_omitted_frames"]["predictions"] + sum(
        len(sample["predictions"]) + sample["predictions_omitted"] for sample in retained_frames
    ) == 64 * 16
    assert omitted["records_in_omitted_frames"]["matches"] + sum(
        len(sample["matches"]) + sample["matches_omitted"] for sample in retained_frames
    ) == 64
