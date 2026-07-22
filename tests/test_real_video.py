from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest
import yaml

from cvbench.config import load_benchmark
from cvbench.examples.real_video_baseline import _lifecycle_event
from cvbench.protocol import receive_message
from cvbench.runner import _deliver_scenarios, _load_unique_scenarios
from cvbench.scenario import load_scenario
from scripts.prepare_real_video import (
    CLIPS,
    SOURCES,
    _interpolate_box,
    _sha1,
    _sha256,
    _verify_source_checksum,
    verify_artifacts,
)
from tests.helpers import gt

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


def test_keyframe_interpolation_is_deterministic() -> None:
    keyframes = [
        {"source_frame": 10, "bbox": [0, 10, 20, 30]},
        {"source_frame": 20, "bbox": [10, 20, 30, 40]},
    ]
    assert _interpolate_box(keyframes, 10) == [0.0, 10.0, 20.0, 30.0]
    assert _interpolate_box(keyframes, 15) == [5.0, 15.0, 25.0, 35.0]
    assert _interpolate_box(keyframes, 20) == [10.0, 20.0, 30.0, 40.0]


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
