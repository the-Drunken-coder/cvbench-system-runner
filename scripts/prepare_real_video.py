#!/usr/bin/env python3
"""Download and deterministically prepare the small CVBench real-video pack.

The source files are downloaded only after the caller explicitly runs this
command.  Raw media and generated frames live below the ignored data root;
only manifests, provenance, and code are versioned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import urllib.request
from pathlib import Path
from typing import Any

import cv2
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "real-video-v1"
FPS_NS = 40_000_000

SOURCES: dict[str, dict[str, Any]] = {
    "pedestrian-area": {
        "filename": "Video_Codec_Test_pedestrian_area_1080p25.y4m.webm",
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/ae/Video_Codec_Test_pedestrian_area_1080p25.y4m.webm",
        "sha1": "51e89a672896e45cca17aa46cd223630a6266e26",
        "license": "CC0-1.0",
        "attribution": "Taurus Media Technik",
        "source_page": "https://commons.wikimedia.org/wiki/File:Video_Codec_Test_pedestrian_area_1080p25.y4m.webm",
    },
    "cars-night": {
        "filename": "Cars_driving_at_night.webm",
        "url": "https://upload.wikimedia.org/wikipedia/commons/8/82/Cars_driving_at_night.webm",
        "sha1": "53fc56b2e6c053243f9ef377ada946abce4dcf63",
        "license": "CC BY 3.0",
        "attribution": "Editor (YouTube user Editor)",
        "source_page": "https://commons.wikimedia.org/wiki/File:Cars_driving_at_night.webm",
    },
    "self-driving-amsterdam": {
        "filename": "Self_driving_cars_-_EU2016NL.webm",
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/a9/Self_driving_cars_-_EU2016NL.webm",
        "sha1": "6f26ef4a7bdb39ba361bf86563c82442dcdd2475",
        "license": "CC BY 3.0",
        "attribution": "EU2016NL",
        "source_page": "https://commons.wikimedia.org/wiki/File:Self_driving_cars_-_EU2016NL.webm",
    },
}

# Keyframes were selected after a complete decoded-frame contact-sheet review.
# Boxes are source-pixel xyxy coordinates and are linearly interpolated only
# between these human-reviewed anchors.  The output sequence IDs are opaque.
CLIPS: tuple[dict[str, Any], ...] = (
    {
        "id": "rv1-crowd-a7f3",
        "sequence_id": "s-4e3a",
        "family": "real_crowding_occlusion",
        "source": "pedestrian-area",
        "start_frame": 80,
        "end_frame": 160,
        "stride": 4,
        "class_id": "target",
        "target_id": "t-01",
        "occlusion_frames": list(range(8, 14)),
        "keyframes": [
            {"source_frame": 80, "bbox": [230, 220, 450, 820]},
            {"source_frame": 100, "bbox": [513, 204, 733, 804]},
            {"source_frame": 120, "bbox": [897, 184, 1117, 784]},
            {"source_frame": 140, "bbox": [1263, 172, 1483, 772]},
            {"source_frame": 160, "bbox": [1477, 122, 1697, 722]},
        ],
        "why": (
            "Static pedestrian-area camera; close foreground passers and a dense crossing crowd "
            "create partial occlusion and identity pressure."
        ),
    },
    {
        "id": "rv1-night-b2c8",
        "sequence_id": "s-91bd",
        "family": "real_low_light_crowding",
        "source": "cars-night",
        "start_frame": 320,
        "end_frame": 370,
        "stride": 2,
        "class_id": "target",
        "target_id": "t-01",
        "occlusion_frames": [],
        "keyframes": [
            {"source_frame": 320, "bbox": [460, 520, 660, 740]},
            {"source_frame": 330, "bbox": [476, 536, 676, 756]},
            {"source_frame": 340, "bbox": [497, 556, 697, 776]},
            {"source_frame": 350, "bbox": [517, 584, 717, 804]},
            {"source_frame": 370, "bbox": [546, 639, 746, 859]},
        ],
        "why": (
            "Fixed highway camera at night; headlights, taillights, dark bodies, and dense adjacent "
            "traffic stress localization under glare."
        ),
    },
    {
        "id": "rv1-motion-c3d1",
        "sequence_id": "s-6c20",
        "family": "real_camera_motion_scale",
        "source": "self-driving-amsterdam",
        "start_frame": 300,
        "end_frame": 360,
        "stride": 2,
        "class_id": "target",
        "target_id": "t-01",
        "occlusion_frames": [],
        "keyframes": [
            {"source_frame": 300, "bbox": [900, 430, 1550, 830]},
            {"source_frame": 320, "bbox": [820, 410, 1470, 810]},
            {"source_frame": 340, "bbox": [618, 375, 1418, 875]},
            {"source_frame": 360, "bbox": [247, 373, 1747, 1023]},
        ],
        "why": (
            "Moving-camera highway shot with a marked test vehicle approaching rapidly; scale, "
            "viewpoint, and background motion change continuously."
        ),
    },
)


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(source: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and _sha1(destination) == source["sha1"]:
        return
    request = urllib.request.Request(source["url"], headers={"User-Agent": "CVBench-real-video-prep/1.0"})
    with urllib.request.urlopen(request) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)
    actual = _sha1(destination)
    if actual != source["sha1"]:
        raise RuntimeError(f"checksum mismatch for {destination.name}: expected {source['sha1']}, got {actual}")


def _interpolate_box(keyframes: list[dict[str, Any]], source_frame: int) -> list[float]:
    if source_frame <= keyframes[0]["source_frame"]:
        return [float(v) for v in keyframes[0]["bbox"]]
    for left, right in zip(keyframes, keyframes[1:], strict=True):
        if source_frame <= right["source_frame"]:
            span = right["source_frame"] - left["source_frame"]
            fraction = (source_frame - left["source_frame"]) / span
            return [
                float(a + fraction * (b - a))
                for a, b in zip(left["bbox"], right["bbox"], strict=True)
            ]
    return [float(v) for v in keyframes[-1]["bbox"]]


def _decode_clip(source_path: Path, clip: dict[str, Any], output: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    frames_path = output / "frames"
    frames_path.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(source_path))
    selected: list[dict[str, Any]] = []
    source_frame = 0
    while True:
        ok, image = cap.read()
        if not ok:
            break
        if clip["start_frame"] <= source_frame <= clip["end_frame"] and (
            source_frame - clip["start_frame"]
        ) % clip["stride"] == 0:
            frame_index = len(selected)
            filename = f"frame-{frame_index:04d}.jpg"
            ok_encoded, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if not ok_encoded:
                raise RuntimeError(f"could not encode {filename}")
            (frames_path / filename).write_bytes(encoded.tobytes())
            box = _interpolate_box(clip["keyframes"], source_frame)
            visible_fraction = 0.65 if frame_index in clip["occlusion_frames"] else 1.0
            selected.append(
                {
                    "target_id": clip["target_id"],
                    "sequence_id": clip["sequence_id"],
                    "source_timestamp_ns": frame_index * clip["stride"] * FPS_NS,
                    "on_screen": True,
                    "eligible_for_detection": visible_fraction >= 0.5,
                    "visibility_fraction": visible_fraction,
                    "occlusion": "partial" if visible_fraction < 1 else "none",
                    "class_id": clip["class_id"],
                    "bbox_xyxy": box,
                    "entry_event": frame_index == 0,
                    "reappearance_event": False,
                    "source_frame_index": source_frame,
                }
            )
        source_frame += 1
    cap.release()
    if not selected:
        raise RuntimeError(f"no frames selected for {clip['id']}")
    return selected, {"decoded_source_frames": source_frame, "selected_frames": len(selected)}


def _write_manifest(
    output: Path,
    clip: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    asset_root: Path | None = None,
) -> None:
    asset_root = asset_root or output
    manifest = {
        "schema_version": "cvbench.scenario/v1",
        "id": clip["id"],
        "family": clip["family"],
        "sequence_id": clip["sequence_id"],
        "license": SOURCES[clip["source"]]["license"],
        "source": "see docs/real-video-sources.md; generated by scripts/prepare_real_video.py",
        "ground_truth": os.path.relpath(asset_root / "ground_truth.jsonl", output),
        "frames": [
            {
                "frame_index": index,
                "source_timestamp_ns": row["source_timestamp_ns"],
                "width": 1920,
                "height": 1080,
                "path": os.path.relpath(asset_root / "frames" / f"frame-{index:04d}.jpg", output),
            }
            for index, row in enumerate(rows)
        ],
    }
    (output / "scenario.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    clean_rows = ({key: value for key, value in row.items() if key != "source_frame_index"} for row in rows)
    (output / "ground_truth.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in clean_rows)
    )


def prepare(output: Path) -> list[Path]:
    output = output.resolve()
    sources_path = output / "sources"
    for source in SOURCES.values():
        _download(source, sources_path / source["filename"])
    provenance: dict[str, Any] = {"schema_version": "cvbench.real-video-provenance/v1", "clips": []}
    for clip in CLIPS:
        source = SOURCES[clip["source"]]
        clip_output = output / clip["id"]
        rows, decode_info = _decode_clip(sources_path / source["filename"], clip, clip_output)
        _write_manifest(clip_output, clip, rows)
        checked_in_manifest = ROOT / "scenarios" / "real-video-v1" / clip["id"]
        checked_in_manifest.mkdir(parents=True, exist_ok=True)
        _write_manifest(
            checked_in_manifest,
            clip,
            rows,
            asset_root=clip_output,
        )
        provenance["clips"].append(
            {
                "scenario_id": clip["id"],
                "sequence_id": clip["sequence_id"],
                "source": source,
                "source_frame_range": [clip["start_frame"], clip["end_frame"]],
                "source_stride": clip["stride"],
                "normalization": (
                    "decoded with OpenCV; re-encoded JPEG quality 90; EXIF/container metadata "
                    "omitted; timestamps normalized to 25 fps frame ordinals"
                ),
                "decode": decode_info,
                "annotation": (
                    "human-reviewed keyframes with linear interpolation; source-frame index is "
                    "retained only in this local provenance record"
                ),
            }
        )
    (output / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    )
    return [output / clip["id"] / "scenario.yaml" for clip in CLIPS]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    paths = prepare(args.output)
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
