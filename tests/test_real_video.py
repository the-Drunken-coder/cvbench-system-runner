from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cvbench.config import Thresholds
from cvbench.metrics import calculate_metrics
from cvbench.model import CollectedRecord
from scripts.prepare_real_video import CLIPS, FPS, FRAME_COUNT, MEVA_ANNOTATION_COMMIT, SOURCES

ROOT = Path(__file__).parents[1]


def _manifest(clip_id: str) -> dict:
    return yaml.safe_load((ROOT / "scenarios" / "real-video-v2" / clip_id / "scenario.yaml").read_text())


def _rows(clip_id: str) -> list[dict]:
    path = ROOT / "scenarios" / "real-video-v2" / clip_id / "ground_truth.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _output(row: dict, *, track_id: str | None = None, box: list[float] | None = None) -> CollectedRecord:
    record = {
        "schema_version": "cvbench.track/v1",
        "event": "track_update",
        "sequence_id": row["sequence_id"],
        "source_timestamp_ns": row["source_timestamp_ns"],
        "track_id": track_id or row["target_id"],
        "state": "confirmed",
        "support": "observed",
        "class_id": row["class_id"],
        "confidence": 0.9,
        "geometry": {
            "type": "bbox_xyxy",
            "space": "source_pixels",
            "value": box or row["bbox_xyxy"],
        },
    }
    return CollectedRecord(row["source_timestamp_ns"] + 1_000_000, record)


def test_replacement_sources_are_pinned_upstream_labeled_meva_sequences() -> None:
    assert len(CLIPS) == 3
    assert set(SOURCES) == {"G328", "G340"}
    assert len(MEVA_ANNOTATION_COMMIT) == 40
    assert all(source["fps"] >= 24 for source in SOURCES.values())
    assert all(len(source["sha256"]) == 64 for source in SOURCES.values())
    assert all(len(source["geom_sha256"]) == 64 for source in SOURCES.values())
    assert all(len(source["types_sha256"]) == 64 for source in SOURCES.values())
    assert all(
        source["annotation_prefix"].startswith("annotation/DIVA-phase-2/MEVA/kitware/")
        for source in SOURCES.values()
    )


def test_real_scenarios_are_full_frame_native_cadence_and_have_no_ignores() -> None:
    for clip in CLIPS:
        manifest = _manifest(clip["id"])
        rows = _rows(clip["id"])
        assert manifest["id"] == clip["id"]
        assert manifest["annotation_scope"] == "exhaustive_full_frame_moving_objects"
        assert manifest["ontology"] == ["person", "vehicle", "dog"]
        assert "scoreable_roi" not in manifest
        assert len(manifest["frames"]) == FRAME_COUNT
        assert manifest["frames"][0]["source_timestamp_ns"] == 0
        assert manifest["frames"][-1]["source_timestamp_ns"] == round((FRAME_COUNT - 1) * 1_000_000_000 / FPS)
        intervals = [
            right["source_timestamp_ns"] - left["source_timestamp_ns"]
            for left, right in zip(manifest["frames"], manifest["frames"][1:], strict=False)
        ]
        assert set(intervals) == {33_333_333, 33_333_334}
        assert all(not row.get("ignore", False) for row in rows)
        assert all(row["on_screen"] and row["eligible_for_detection"] for row in rows)
        assert {row["class_id"] for row in rows} <= {"person", "vehicle", "dog"}
        assert {row["class_id"] for row in rows} >= {"person", "vehicle"}


def test_public_suite_includes_every_synthetic_and_replacement_real_scenario() -> None:
    public = yaml.safe_load((ROOT / "benchmarks" / "public-whole-system-v2.yaml").read_text())
    declared = {
        yaml.safe_load((ROOT / "benchmarks" / relative).resolve().read_text())["id"]
        for relative in public["scenarios"]
    }
    expected_synthetic = {
        yaml.safe_load(path.read_text())["id"]
        for path in (ROOT / "scenarios" / "synthetic-v1").glob("*/scenario.yaml")
    }
    assert public["id"] == "public-whole-system-tracking"
    assert public["version"] == "2.0.0"
    assert declared == expected_synthetic | {clip["id"] for clip in CLIPS}
    assert len(declared) == 16


def test_physical_track_corrections_are_explicit_and_frame_unique() -> None:
    for clip in CLIPS:
        source_ids = [source_id for group in clip["track_groups"] for source_id in group["source_ids"]]
        assert len(source_ids) == len(set(source_ids))
        rows = _rows(clip["id"])
        keys = [(row["source_timestamp_ns"], row["target_id"]) for row in rows]
        assert len(keys) == len(set(keys))
        assert all(isinstance(row["truncated"], bool) for row in rows)
        assert all(row["occlusion"] in {"none", "partial"} for row in rows)
        assert all(0 < row["visibility_fraction"] <= 1 for row in rows)
        review = ROOT / "scenarios" / "real-video-v2" / clip["id"] / "review"
        assert len(list(review.glob("*.jpg"))) == 6


def test_expected_frame_manifest_covers_exact_corpus() -> None:
    lines = (ROOT / "scenarios" / "real-video-v2" / "expected-frame-sha256.txt").read_text().splitlines()
    assert len(lines) == len(CLIPS) * FRAME_COUNT
    assert len({line.split("  ", 1)[1] for line in lines}) == len(lines)
    assert all(len(line.split("  ", 1)[0]) == 64 for line in lines)


def test_perfect_multi_object_tracking_scores_perfect_hota_and_idf1() -> None:
    rows = _rows("rvmot-b7e2")
    collected = [_output(row) for row in rows]
    metrics, _ = calculate_metrics(rows, collected, Thresholds())
    mot = metrics["multi_object_tracking"]
    assert mot["hota"] == pytest.approx(1.0)
    assert mot["idf1"] == pytest.approx(1.0)
    assert mot["ground_truth_tracks"] == 3
    assert mot["ground_truth_detections"] == len(rows)
    assert all(item["hota"] == pytest.approx(1.0) for item in mot["hota_by_iou_threshold"])


def test_identity_switch_misses_and_false_tracks_reduce_mot_scores() -> None:
    rows = _rows("rvmot-b7e2")
    collected = []
    for index, row in enumerate(rows):
        if row["target_id"] == "p-001" and index % 4 == 0:
            continue
        track_id = row["target_id"]
        if row["target_id"] == "p-002" and row["source_timestamp_ns"] >= 2_500_000_000:
            track_id = "p-002-switched"
        collected.append(_output(row, track_id=track_id))
    exemplar = rows[0]
    for timestamp in sorted({row["source_timestamp_ns"] for row in rows})[:30]:
        false_row = {**exemplar, "source_timestamp_ns": timestamp, "target_id": "false"}
        collected.append(_output(false_row, track_id="false-track", box=[600, 20, 680, 90]))
    metrics, _ = calculate_metrics(rows, collected, Thresholds())
    mot = metrics["multi_object_tracking"]
    assert 0 < mot["hota"] < 1
    assert 0 < mot["idf1"] < 1
    assert mot["identity_false_negatives"] > 0
    assert mot["identity_false_positives"] > 0
    assert metrics["identity"]["id_switches"] > 0


def test_real_ground_truth_is_never_part_of_frame_protocol_metadata() -> None:
    runner = (ROOT / "src" / "cvbench" / "runner.py").read_text()
    metadata_block = runner[runner.index('metadata = {', runner.index("def _deliver_scenarios")) :]
    metadata_block = metadata_block[: metadata_block.index("send_message(connection, metadata")]
    assert "bbox_xyxy" not in metadata_block
    assert "ground_truth" not in metadata_block
    assert "target_id" not in metadata_block
    assert "scoreable_roi" not in metadata_block


def test_real_video_docs_state_evaluation_not_required_training_data() -> None:
    docs = (ROOT / "docs" / "real-video-sources.md").read_text()
    assert "CC BY 4.0" in docs
    assert "evaluation data" in docs
    assert "not model training data" in docs
    assert "never sent to submitted systems" in docs
