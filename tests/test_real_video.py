from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

from cvbench.config import load_benchmark
from cvbench.examples.real_video_baseline import _lifecycle_event
from cvbench.metrics import calculate_metrics
from cvbench.model import Frame, Scenario
from cvbench.protocol import receive_message
from cvbench.runner import _deliver_scenarios, _filter_outputs_to_scoreable_rois, _load_unique_scenarios
from cvbench.scenario import load_scenario
from scripts.prepare_real_video import (
    CLIPS,
    FPS_NS,
    SOURCES,
    _interpolate_box,
    _sha1,
    _sha256,
    _verify_source_checksum,
    verify_artifacts,
)
from tests.helpers import gt, output

ROOT = Path(__file__).parents[1]


class _Monitor:
    def set_context(self, *_args: object) -> None:
        return None


class _Collector:
    flooded = threading.Event()
    limit_reason = ""


def test_real_video_catalog_is_opaque_and_checksum_pinned() -> None:
    assert len(CLIPS) == 3
    assert {clip["family"] for clip in CLIPS} == {
        "real_crowding_occlusion",
        "real_low_light_crowding",
        "real_camera_motion_scale",
    }
    assert all(len(source["sha1"]) == 40 for source in SOURCES.values())
    assert all(len(source["sha256"]) == 64 for source in SOURCES.values())
    assert all(
        not any(label in clip["id"] for label in ("person", "car", "crowd", "night", "motion"))
        for clip in CLIPS
    )
    assert all(
        region.get("frames") != "all" or region.get("bbox")
        for clip in CLIPS
        for region in clip["ignore_regions"]
    )
    assert "full_frame" not in json.dumps(CLIPS)


def test_keyframe_interpolation_is_deterministic() -> None:
    keyframes = [
        {"source_frame": 10, "bbox": [0, 10, 20, 30]},
        {"source_frame": 20, "bbox": [10, 20, 30, 40]},
    ]
    assert _interpolate_box(keyframes, 10) == [0.0, 10.0, 20.0, 30.0]
    assert _interpolate_box(keyframes, 15) == [5.0, 15.0, 25.0, 35.0]
    assert _interpolate_box(keyframes, 20) == [10.0, 20.0, 30.0, 40.0]


def test_all_real_clips_have_ignore_coverage_and_crowd_frames_are_locked() -> None:
    for clip in CLIPS:
        rows = [
            json.loads(line)
            for line in (ROOT / "scenarios/real-video-v1" / clip["id"] / "ground_truth.jsonl").read_text().splitlines()
        ]
        target_timestamps = {row["source_timestamp_ns"] for row in rows if not row.get("ignore")}
        ignored_timestamps = {row["source_timestamp_ns"] for row in rows if row.get("ignore_region")}
        assert target_timestamps <= ignored_timestamps
        assert all(row.get("ignore_region_id") for row in rows if row.get("ignore_region"))
    crowd_rows = [
        json.loads(line)
        for line in (ROOT / "scenarios/real-video-v1/rv1-a7f3/ground_truth.jsonl").read_text().splitlines()
        if not json.loads(line).get("ignore")
    ]
    by_frame = {row["source_timestamp_ns"] // (4 * FPS_NS): row for row in crowd_rows}
    assert by_frame[16]["bbox_xyxy"] == [1300, 170, 1535, 850]
    assert by_frame[17]["bbox_xyxy"] == [1435, 155, 1665, 850]
    assert by_frame[18]["eligible_for_detection"] is False
    assert by_frame[19]["eligible_for_detection"] is False
    assert by_frame[20]["on_screen"] is False


def test_static_roi_object_coverage_and_fairness_regressions() -> None:
    for clip in CLIPS:
        manifest = ROOT / "scenarios/real-video-v1" / clip["id"] / "scenario.yaml"
        manifest_data = yaml.safe_load(manifest.read_text())
        roi = tuple(float(value) for value in manifest_data["scoreable_roi"])
        ground_truth = [
            json.loads(line)
            for line in manifest.parent.joinpath("ground_truth.jsonl").read_text().splitlines()
            if line.strip()
        ]
        targets = [row for row in ground_truth if not row.get("ignore")]
        assert all(
            min(row["bbox_xyxy"][2], roi[2]) > max(row["bbox_xyxy"][0], roi[0])
            and min(row["bbox_xyxy"][3], roi[3]) > max(row["bbox_xyxy"][1], roi[1])
            for row in targets
            if row["on_screen"]
        )
        for timestamp in {row["source_timestamp_ns"] for row in targets}:
            ignored = [
                row
                    for row in ground_truth
                if row.get("ignore") and row["source_timestamp_ns"] == timestamp
            ]
            assert ignored, f"no reviewed object annotations at {clip['id']}:{timestamp}"
        roi_ignored = [
            row
            for row in ground_truth
            if row.get("ignore")
            and min(row["bbox_xyxy"][2], roi[2]) > max(row["bbox_xyxy"][0], roi[0])
            and min(row["bbox_xyxy"][3], roi[3]) > max(row["bbox_xyxy"][1], roi[1])
        ]
        assert roi_ignored
        assert all(row.get("ignore_region_id") for row in roi_ignored)
        for ignored in roi_ignored[:3]:
            target = next(row for row in targets if row["source_timestamp_ns"] == ignored["source_timestamp_ns"])
            target_output = output(
                target["source_timestamp_ns"],
                sequence=target["sequence_id"],
                box=target["bbox_xyxy"],
            )
            target_output.system_record["class_id"] = target["class_id"]
            ignored_output = output(
                target["source_timestamp_ns"],
                sequence=target["sequence_id"],
                track="reviewed-object",
                box=ignored["bbox_xyxy"],
            )
            ignored_output.system_record["class_id"] = target["class_id"]
            metrics, _ = calculate_metrics(
                [target, ignored],
                [target_output, ignored_output],
                load_benchmark(ROOT / "benchmarks/real-video-v1.yaml").thresholds,
            )
            assert metrics["false_detections"]["neutral_ignored_predictions"] == 1
            assert metrics["false_detections"]["detections"] == 0

    crowd_manifest = ROOT / "scenarios/real-video-v1/rv1-a7f3/scenario.yaml"
    crowd_manifest_data = yaml.safe_load(crowd_manifest.read_text())
    crowd_ground_truth = [
        json.loads(line)
        for line in crowd_manifest.parent.joinpath("ground_truth.jsonl").read_text().splitlines()
        if line.strip()
    ]
    crowd_target = next(
        row
        for row in crowd_ground_truth
        if not row.get("ignore") and row["source_timestamp_ns"] == 10 * 4 * FPS_NS
    )
    foreground = next(
        row
        for row in crowd_ground_truth
        if row.get("ignore_region_id") == "foreground-pedestrian-mid"
        and row["source_timestamp_ns"] == crowd_target["source_timestamp_ns"]
    )
    target_output = output(
        crowd_target["source_timestamp_ns"],
        sequence=crowd_target["sequence_id"],
        box=crowd_target["bbox_xyxy"],
    )
    target_output.system_record["class_id"] = crowd_target["class_id"]
    foreground_output = output(
        crowd_target["source_timestamp_ns"],
        sequence=crowd_target["sequence_id"],
        track="foreground",
        box=foreground["bbox_xyxy"],
    )
    foreground_output.system_record["class_id"] = crowd_target["class_id"]
    metrics, _ = calculate_metrics(
        [crowd_target, foreground],
        [target_output, foreground_output],
        load_benchmark(ROOT / "benchmarks/real-video-v1.yaml").thresholds,
    )
    assert metrics["false_detections"]["neutral_ignored_predictions"] == 1
    assert metrics["false_detections"]["detections"] == 0

    hallucination = output(
        crowd_target["source_timestamp_ns"],
        sequence=crowd_target["sequence_id"],
        track="background",
        box=[1200, 850, 1300, 950],
    )
    hallucination.system_record["class_id"] = crowd_target["class_id"]
    duplicate = output(
        crowd_target["source_timestamp_ns"],
        sequence=crowd_target["sequence_id"],
        track="duplicate",
        box=crowd_target["bbox_xyxy"],
    )
    duplicate.system_record["class_id"] = crowd_target["class_id"]
    metrics, _ = calculate_metrics(
        [crowd_target, foreground],
        [target_output, hallucination, duplicate],
        load_benchmark(ROOT / "benchmarks/real-video-v1.yaml").thresholds,
    )
    assert metrics["false_detections"]["detections"] == 2
    assert metrics["identity"]["duplicate_tracks"] == 1
    assert metrics["identity"]["track_splits"] == 1


def test_static_scoreable_roi_filters_out_of_scope_predictions() -> None:
    sequence = "run-fixture-seq-00"
    scenarios = [
        Scenario(
            id="roi-fixture",
            family="fixture",
            root=ROOT,
            frames=[Frame(sequence, 0, 0, 1920, 1080, ROOT / "fixture.jpg")],
            ground_truth=[],
            scoreable_roi=(0.0, 100.0, 1800.0, 1000.0),
        )
    ]
    timestamp = scenarios[0].frames[0].relative_timestamp_ns
    kept = output(timestamp, sequence=sequence, box=[100, 100, 200, 200])
    dropped = output(timestamp, sequence=sequence, box=[1810, 100, 1900, 200])
    assert _filter_outputs_to_scoreable_rois([kept, dropped], scenarios) == [kept]


def test_canonical_frame_manifest_has_exactly_78_hashes_and_prep_is_container_only() -> None:
    expected = ROOT / "scenarios/real-video-v1/expected-frame-sha256.txt"
    assert len(expected.read_text().splitlines()) == 78
    result = subprocess.run(
        [sys.executable, "scripts/prepare_real_video.py", "--verify-only", "--output", "data/real-video-v1"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "native host preparation is unsupported" in result.stderr + result.stdout


def test_prep_toolchain_uses_digest_addressed_base_image() -> None:
    dockerfile = (ROOT / "examples/Dockerfile.real-video-prep").read_text()
    assert "python:3.12-slim@sha256:" in dockerfile
    assert "FROM --platform=linux/amd64" in dockerfile
    assert "--platform linux/amd64" in (ROOT / "scripts/prepare_real_video_container.sh").read_text()
    assert "scripts/prepare_real_video_container.sh" in (ROOT / "docs/real-video-sources.md").read_text()


def test_source_checksum_verification_checks_content(tmp_path: Path) -> None:
    copied = tmp_path / "fixture.bin"
    copied.write_bytes(b"verified fixture\n")
    source = {"sha1": _sha1(copied), "sha256": _sha256(copied)}
    _verify_source_checksum(copied, source)
    copied.write_bytes(copied.read_bytes() + b"tamper")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        _verify_source_checksum(copied, source)


def test_artifact_manifest_verifies_actual_bytes(tmp_path: Path) -> None:
    artifact = tmp_path / "frame.jpg"
    artifact.write_bytes(b"artifact")
    (tmp_path / "artifacts.sha256").write_text(f"{_sha256(artifact)}  frame.jpg\n")
    verify_artifacts(tmp_path)
    artifact.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="artifact checksum mismatch"):
        verify_artifacts(tmp_path)


def test_real_baseline_lifecycle_events_are_reachable() -> None:
    assert _lifecycle_event(created=True, was_missing=False) == "track_started"
    assert _lifecycle_event(created=False, was_missing=True) == "track_reacquired"
    assert _lifecycle_event(created=False, was_missing=False) == "track_update"


def test_execution_sequence_ids_are_run_scoped_and_order_is_private(tmp_path: Path) -> None:
    source_frame = ROOT / "scenarios/synthetic-v1/acquisition/frames/0000.jpg"
    paths = []
    for index, scenario_id in enumerate(("rv1-a7f3", "rv1-b2c8", "rv1-c3d1")):
        scenario_root = tmp_path / f"sequence-scenario-{index}"
        scenario_root.mkdir()
        manifest = scenario_root / "scenario.yaml"
        manifest.write_text(
            yaml.safe_dump(
                {
                    "schema_version": "cvbench.scenario/v1",
                    "id": scenario_id,
                    "family": "fixture_family",
                    "sequence_id": f"public-{index}",
                    "ground_truth": "ground_truth.jsonl",
                    "frames": [
                        {
                            "frame_index": 0,
                            "source_timestamp_ns": 0,
                            "width": 160,
                            "height": 120,
                            "path": str(source_frame),
                        }
                    ],
                }
            )
        )
        (scenario_root / "ground_truth.jsonl").write_text(json.dumps(gt(0, sequence=f"public-{index}")) + "\n")
        paths.append(manifest)
    first = _load_unique_scenarios(tuple(paths), "20260722T010416Z-aaaa1111")
    second = _load_unique_scenarios(tuple(paths), "20260722T010416Z-bbbb2222")
    assert all(scenario.frames[0].sequence_id.startswith("run-aaaa1111-seq-") for scenario in first)
    assert all(scenario.frames[0].sequence_id.startswith("run-bbbb2222-seq-") for scenario in second)
    assert {scenario.id for scenario in first} == {scenario.id for scenario in second}


def test_real_video_delivery_strips_source_frame_metadata(tmp_path: Path) -> None:
    scenario_root = tmp_path / "scenario"
    frames = scenario_root / "frames"
    frames.mkdir(parents=True)
    source_frame = ROOT / "scenarios/synthetic-v1/acquisition/frames/0000.jpg"
    (frames / "frame-0000.jpg").write_bytes(source_frame.read_bytes())
    (scenario_root / "ground_truth.jsonl").write_text(json.dumps(gt(0, sequence="opaque-seq")) + "\n")
    manifest = scenario_root / "scenario.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": "cvbench.scenario/v1",
                "id": "opaque-real-fixture",
                "family": "real_fixture",
                "sequence_id": "opaque-seq",
                "ground_truth": "ground_truth.jsonl",
                "frames": [
                    {
                        "frame_index": 0,
                        "source_timestamp_ns": 0,
                        "width": 160,
                        "height": 120,
                        "path": "frames/frame-0000.jpg",
                    }
                ],
            }
        )
    )
    benchmark_path = tmp_path / "benchmark.yaml"
    benchmark_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "cvbench.benchmark/v1",
                "id": "real-fixture",
                "version": "1",
                "input": {"mode": "online_replay", "protocol": "frame_socket_v1", "playback_rate": 100},
                "scenarios": [str(manifest)],
            }
        )
    )
    benchmark = load_benchmark(benchmark_path)
    scenario = load_scenario(manifest)
    sender, receiver = socket.socketpair()
    stream = None
    try:
        _deliver_scenarios(
            sender,
            [scenario],
            benchmark,
            run_deadline=10**12,
            frame_sizes={},
            monitor=_Monitor(),
            collector=_Collector(),
        )
        messages = []
        receiver.settimeout(1)
        stream = receiver.makefile("rb")
        while True:
            metadata, payload = receive_message(stream)
            messages.append((metadata, payload))
            if metadata["event"] == "benchmark_end":
                break
        frame_metadata = next(metadata for metadata, _payload in messages if metadata["event"] == "frame")
        assert "scenario_source_timestamp_ns" not in frame_metadata
        assert "source_frame_index" not in frame_metadata
        assert payload == b""
    finally:
        if stream is not None:
            stream.close()
        sender.close()
        receiver.close()
