from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import yaml

from cvbench.config import load_benchmark
from cvbench.protocol import receive_message
from cvbench.runner import _deliver_scenarios
from cvbench.scenario import load_scenario
from scripts.prepare_real_video import CLIPS, SOURCES, _interpolate_box
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
    assert all("person" not in clip["id"] and "car" not in clip["id"] for clip in CLIPS)


def test_keyframe_interpolation_is_deterministic() -> None:
    keyframes = [
        {"source_frame": 10, "bbox": [0, 10, 20, 30]},
        {"source_frame": 20, "bbox": [10, 20, 30, 40]},
    ]
    assert _interpolate_box(keyframes, 10) == [0.0, 10.0, 20.0, 30.0]
    assert _interpolate_box(keyframes, 15) == [5.0, 15.0, 25.0, 35.0]
    assert _interpolate_box(keyframes, 20) == [10.0, 20.0, 30.0, 40.0]


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
