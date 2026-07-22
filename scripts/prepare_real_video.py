#!/usr/bin/env python3
"""Download and deterministically prepare the small CVBench real-video pack.

The source files are downloaded only after the caller explicitly runs this
command.  Raw media and generated frames live below the ignored data root;
only manifests, provenance, and code are versioned.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import cv2
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "real-video-v1"
FPS_NS = 40_000_000
TOOLCHAIN = {
    "python_major_minor": "3.12",
    "opencv-python-headless": "4.13.0.92",
    "numpy": "2.5.1",
    "PyYAML": "6.0.3",
}

SOURCES: dict[str, dict[str, Any]] = {
    "pedestrian-area": {
        "filename": "Video_Codec_Test_pedestrian_area_1080p25.y4m.webm",
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/ae/Video_Codec_Test_pedestrian_area_1080p25.y4m.webm",
        "sha1": "51e89a672896e45cca17aa46cd223630a6266e26",
        "sha256": "bfadaa62cccb42db875d50bb842aa0964fbf72040432e4097c1df59e043e0c26",
        "license": "CC0-1.0",
        "attribution": "Taurus Media Technik",
        "source_page": "https://commons.wikimedia.org/wiki/File:Video_Codec_Test_pedestrian_area_1080p25.y4m.webm",
    },
    "cars-night": {
        "filename": "Cars_driving_at_night.webm",
        "url": "https://upload.wikimedia.org/wikipedia/commons/8/82/Cars_driving_at_night.webm",
        "sha1": "53fc56b2e6c053243f9ef377ada946abce4dcf63",
        "sha256": "3dfc2c1ae60762e45ea68bc417695cc25a4fde5fcd771d4e3f8e59b0f2f7e9f8",
        "license": "CC BY 3.0",
        "attribution": "Editor (YouTube user Editor)",
        "source_page": "https://commons.wikimedia.org/wiki/File:Cars_driving_at_night.webm",
    },
    "self-driving-amsterdam": {
        "filename": "Self_driving_cars_-_EU2016NL.webm",
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/a9/Self_driving_cars_-_EU2016NL.webm",
        "sha1": "6f26ef4a7bdb39ba361bf86563c82442dcdd2475",
        "sha256": "7f0439180dd985eec74823418c521dd8d4092e3cfe653f2cd475b8412328d177",
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
        "id": "rv1-a7f3",
        "sequence_id": "s-4e3a",
        "family": "real_crowding_occlusion",
        "source": "pedestrian-area",
        "start_frame": 80,
        "end_frame": 160,
        "stride": 4,
        "class_id": "target",
        "target_id": "t-01",
        "occlusion_frames": list(range(8, 14)),
        "frame_overrides": {
            "16": {"bbox_xyxy": [1300, 170, 1535, 850], "visibility_fraction": 1.0},
            "17": {"bbox_xyxy": [1435, 155, 1665, 850], "visibility_fraction": 1.0},
            "18": {
                "bbox_xyxy": [1660, 120, 1920, 950],
                "visibility_fraction": 0.45,
                "eligible_for_detection": False,
                "occlusion": "partial",
            },
            "19": {
                "bbox_xyxy": [1770, 80, 1920, 900],
                "visibility_fraction": 0.3,
                "eligible_for_detection": False,
                "occlusion": "partial",
            },
            "20": {
                "on_screen": False,
                "eligible_for_detection": False,
                "visibility_fraction": 0.0,
                "occlusion": "full",
                "exit_event": True,
            },
        },
        "ignore_boxes": {
            "16": [[0, 150, 420, 1040]],
            "17": [[0, 140, 390, 1040]],
            "18": [[0, 140, 570, 1040]],
            "19": [[210, 140, 600, 1040]],
            "20": [[260, 130, 760, 1040]],
        },
        "ignore_regions": ["full_frame"],
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
        "id": "rv1-b2c8",
        "sequence_id": "s-91bd",
        "family": "real_low_light_crowding",
        "source": "cars-night",
        "start_frame": 320,
        "end_frame": 370,
        "stride": 2,
        "class_id": "target",
        "target_id": "t-01",
        "occlusion_frames": [],
        "ignore_regions": ["full_frame"],
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
        "id": "rv1-c3d1",
        "sequence_id": "s-6c20",
        "family": "real_camera_motion_scale",
        "source": "self-driving-amsterdam",
        "start_frame": 300,
        "end_frame": 360,
        "stride": 2,
        "class_id": "target",
        "target_id": "t-01",
        "occlusion_frames": [],
        "ignore_regions": ["full_frame"],
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_toolchain() -> None:
    actual = {
        "python_major_minor": ".".join(str(value) for value in sys.version_info[:2]),
        "opencv-python-headless": importlib.metadata.version("opencv-python-headless"),
        "numpy": importlib.metadata.version("numpy"),
        "PyYAML": importlib.metadata.version("PyYAML"),
    }
    if actual != TOOLCHAIN:
        raise RuntimeError(f"preparation toolchain mismatch: expected {TOOLCHAIN}, got {actual}")


def _verify_source_checksum(path: Path, source: dict[str, Any]) -> None:
    actual_sha1 = _sha1(path)
    actual_sha256 = _sha256(path)
    if actual_sha1 != source["sha1"] or actual_sha256 != source["sha256"]:
        raise RuntimeError(
            f"checksum mismatch for {path.name}: expected sha1={source['sha1']} sha256={source['sha256']}, "
            f"got sha1={actual_sha1} sha256={actual_sha256}"
        )


def _download(source: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        try:
            _verify_source_checksum(destination, source)
            return
        except RuntimeError:
            pass
    for attempt in range(5):
        request = urllib.request.Request(
            source["url"],
            headers={"User-Agent": "CVBench-real-video-prep/1.0", "Accept-Encoding": "identity"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            _verify_source_checksum(destination, source)
            return
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 4:
                raise
            retry_after = exc.headers.get("Retry-After")
            delay = int(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
            time.sleep(min(60, max(1, delay)))


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
            frame_index = sum(not row.get("ignore", False) for row in selected)
            filename = f"frame-{frame_index:04d}.jpg"
            ok_encoded, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if not ok_encoded:
                raise RuntimeError(f"could not encode {filename}")
            (frames_path / filename).write_bytes(encoded.tobytes())
            override = clip.get("frame_overrides", {}).get(str(frame_index), {})
            visible_fraction = 0.65 if frame_index in clip["occlusion_frames"] else 1.0
            annotation = {
                    "target_id": clip["target_id"],
                    "sequence_id": clip["sequence_id"],
                    "source_timestamp_ns": frame_index * clip["stride"] * FPS_NS,
                    "on_screen": True,
                    "eligible_for_detection": visible_fraction >= 0.5,
                    "visibility_fraction": visible_fraction,
                    "occlusion": "partial" if visible_fraction < 1 else "none",
                    "class_id": clip["class_id"],
                    "bbox_xyxy": _interpolate_box(clip["keyframes"], source_frame),
                    "entry_event": frame_index == 0,
                    "reappearance_event": False,
                    "source_frame_index": source_frame,
                }
            annotation.update(override)
            if not annotation["on_screen"]:
                annotation.pop("bbox_xyxy", None)
            selected.append(annotation)
            for ignore_index, ignore_box in enumerate(clip.get("ignore_boxes", {}).get(str(frame_index), []), 1):
                selected.append(
                    {
                        "target_id": f"ignore-{frame_index:02d}-{ignore_index:02d}",
                        "sequence_id": clip["sequence_id"],
                        "source_timestamp_ns": frame_index * clip["stride"] * FPS_NS,
                        "on_screen": True,
                        "eligible_for_detection": False,
                        "visibility_fraction": 1.0,
                        "occlusion": "none",
                        "class_id": clip["class_id"],
                        "bbox_xyxy": [float(v) for v in ignore_box],
                        "ignore": True,
                        "source_frame_index": source_frame,
                    }
                )
            for region_index, region in enumerate(clip.get("ignore_regions", []), 1):
                if region == "full_frame":
                    region_box = [0.0, 0.0, float(image.shape[1]), float(image.shape[0])]
                else:
                    region_box = [float(value) for value in region["bbox"]]
                selected.append(
                    {
                        "target_id": f"ignore-region-{frame_index:02d}-{region_index:02d}",
                        "sequence_id": clip["sequence_id"],
                        "source_timestamp_ns": frame_index * clip["stride"] * FPS_NS,
                        "on_screen": True,
                        "eligible_for_detection": False,
                        "visibility_fraction": 1.0,
                        "occlusion": "none",
                        "class_id": clip["class_id"],
                        "bbox_xyxy": region_box,
                        "ignore": True,
                        "ignore_region": True,
                        "source_frame_index": source_frame,
                    }
                )
        source_frame += 1
    cap.release()
    if not selected:
        raise RuntimeError(f"no frames selected for {clip['id']}")
    return selected, {
        "decoded_source_frames": source_frame,
        "selected_frames": sum(not row.get("ignore", False) for row in selected),
        "ground_truth_records": len(selected),
    }


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
            for index, row in enumerate(row for row in rows if not row.get("ignore", False))
        ],
    }
    (output / "scenario.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    clean_rows = ({key: value for key, value in row.items() if key != "source_frame_index"} for row in rows)
    (output / "ground_truth.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in clean_rows)
    )


def _write_crowd_review_overlay(rows: list[dict[str, Any]], output: Path) -> None:
    review_dir = ROOT / "scenarios" / "real-video-v1" / "rv1-a7f3" / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    data_root = output / "rv1-a7f3" / "frames"
    panels = []
    for frame_index in range(16, 21):
        image = cv2.imread(str(data_root / f"frame-{frame_index:04d}.jpg"))
        if image is None:
            raise RuntimeError(f"missing crowd review frame {frame_index}")
        frame_rows = [row for row in rows if row.get("source_frame_index") == 80 + frame_index * 4]
        for row in frame_rows:
            if row.get("bbox_xyxy"):
                box = [int(value) for value in row["bbox_xyxy"]]
                color = (0, 180, 255) if row.get("ignore") else (0, 0, 255)
                cv2.rectangle(image, (box[0], box[1]), (box[2], box[3]), color, 5)
                label = "IGNORE" if row.get("ignore") else "TARGET"
                cv2.putText(image, label, (box[0] + 8, max(34, box[1] - 10)), cv2.FONT_HERSHEY_SIMPLEX, 1.1, color, 3)
        cv2.putText(image, f"output frame {frame_index}", (28, 52), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 4)
        panels.append(cv2.resize(image, (480, 270), interpolation=cv2.INTER_AREA))
    cv2.imwrite(str(review_dir / "crowd-frames-16-20-overlay.jpg"), cv2.vconcat(panels), [cv2.IMWRITE_JPEG_QUALITY, 92])


def _write_artifact_manifest(output: Path) -> None:
    entries: list[str] = []
    for path in sorted(item for item in output.rglob("*") if item.is_file() and item.name != "artifacts.sha256"):
        entries.append(f"{_sha256(path)}  {path.relative_to(output).as_posix()}")
    (output / "artifacts.sha256").write_text("\n".join(entries) + "\n")


def verify_artifacts(output: Path) -> None:
    manifest = output / "artifacts.sha256"
    for line in manifest.read_text().splitlines():
        expected, relative = line.split("  ", 1)
        artifact = output / relative
        actual = _sha256(artifact)
        if actual != expected:
            raise RuntimeError(f"artifact checksum mismatch for {relative}: expected {expected}, got {actual}")


def prepare(output: Path) -> list[Path]:
    _verify_toolchain()
    output = output.resolve()
    sources_path = output / "sources"
    for source in SOURCES.values():
        _download(source, sources_path / source["filename"])
    provenance: dict[str, Any] = {"schema_version": "cvbench.real-video-provenance/v1", "clips": []}
    for clip in CLIPS:
        source = SOURCES[clip["source"]]
        clip_output = output / clip["id"]
        shutil.rmtree(clip_output, ignore_errors=True)
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
        if clip["id"] == "rv1-a7f3":
            _write_crowd_review_overlay(rows, output)
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
                    "human-reviewed keyframes with deterministic linear interpolation plus explicit "
                    "manual overrides for crowd output frames 16-20; source-frame index is retained "
                    "only in this local provenance record"
                ),
                "ignore_semantics": (
                    "every selected frame has a broad full-frame ignore region for non-target content; "
                    "the crowd also retains manually reviewed local ignore boxes at output frames 16-20. "
                    "Scoreable targets match first, then unmatched predictions overlapping ignore rows "
                    "at the locked benchmark threshold are neutral"
                ),
            }
        )
    (output / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    )
    _write_artifact_manifest(output)
    verify_artifacts(output)
    return [output / clip["id"] / "scenario.yaml" for clip in CLIPS]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        verify_artifacts(args.output.resolve())
        print(args.output.resolve() / "artifacts.sha256")
        return 0
    paths = prepare(args.output)
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
