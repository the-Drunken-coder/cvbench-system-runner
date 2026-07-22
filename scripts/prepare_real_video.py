#!/usr/bin/env python3
"""Prepare the public MEVA full-frame multi-object tracking scenarios.

The committed catalog contains the exact prepared JPEG and annotation mirrors.
This script independently recreates the runtime corpus from checksum-pinned MEVA
video and annotation sources. Source activity tracks are consolidated into the
physical-object identities recorded in ``TRACK_GROUPS`` only after visual audit.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import io
import json
import os
import shutil
import statistics
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
FPS = 30
FRAME_COUNT = 150
TARGET_WIDTH = 896
JPEG_QUALITY = 78
TOOLCHAIN = {
    "python_major_minor": "3.12",
    "opencv-python-headless": "4.13.0.92",
    "numpy": "2.5.1",
    "PyYAML": "6.0.3",
}
PREPARATION_BASE_IMAGE = "python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
PREPARATION_PLATFORM = "linux/amd64"
MEVA_ANNOTATION_COMMIT = "421841a75577b697c314e952e585aecbb1b99e17"
MEVA_LICENSE_URL = "https://mevadata.org/resources/MEVA-data-license.txt"

SOURCES: dict[str, dict[str, Any]] = {
    "G340": {
        "filename": "2018-03-05.13-15-00.13-20-00.bus.G340.r13.avi",
        "url": "https://mevadata-public-01.s3.amazonaws.com/drops-123-r13/2018-03-05/13/2018-03-05.13-15-00.13-20-00.bus.G340.r13.avi",
        "sha256": "6a99ed045f9d29a77f726856298d75b8a0890621242f90535316b303e06afd34",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "frame_count": 9005,
        "annotation_prefix": "annotation/DIVA-phase-2/MEVA/kitware/2018-03-05/13/2018-03-05.13-15-00.13-20-00.bus.G340",
        "geom_sha256": "40236c84c263fb8c7764e888656600bfa720a5eaf2c96348b0567d4349dcdf1f",
        "types_sha256": "3702fb5c75dee26c7dcd28ca95b0ae49662455da60433ea8574819fcb0553eb7",
    },
    "G328": {
        "filename": "2018-03-05.13-20-01.13-25-01.school.G328.r13.avi",
        "url": "https://mevadata-public-01.s3.amazonaws.com/drops-123-r13/2018-03-05/13/2018-03-05.13-20-01.13-25-01.school.G328.r13.avi",
        "sha256": "64102272e6611681dbcf7d6fa5ac7aa939595ce29d54151ff604c077e9061dd1",
        "width": 1920,
        "height": 1072,
        "fps": 30,
        "frame_count": 9000,
        "annotation_prefix": (
            "annotation/DIVA-phase-2/MEVA/kitware/2018-03-05/13/"
            "2018-03-05.13-20-01.13-25-01.school.G328"
        ),
        "geom_sha256": "fa7c62bce3bac7a49189ff1eb36f7f0c17be8b42a2053aecf50cceaff956cd42",
        "types_sha256": "52a5e77f2372c6ab8ef130bcff6cad617aa96a8b33595175817d60c03812ac63",
    },
}

# Each tuple is one visually confirmed physical object. Multiple MEVA source
# IDs occur when the same actor participates in more than one labeled activity.
CLIPS: tuple[dict[str, Any], ...] = (
    {
        "id": "rvmot-a1c9",
        "sequence_id": "mot-a1c9",
        "family": "real_full_frame_loading",
        "source": "G340",
        "start_frame": 2813,
        "title": "Vehicle loading and partial occlusion",
        "track_groups": (
            {"target_id": "p-001", "class_id": "person", "source_ids": (3, 9, 13, 14)},
            {"target_id": "p-002", "class_id": "person", "source_ids": (6,)},
            {"target_id": "v-001", "class_id": "vehicle", "source_ids": (2, 4, 5, 7, 10)},
        ),
    },
    {
        "id": "rvmot-b7e2",
        "sequence_id": "mot-b7e2",
        "family": "real_full_frame_close_association",
        "source": "G340",
        "start_frame": 3039,
        "title": "Close-proximity handoff",
        "track_groups": (
            {"target_id": "p-001", "class_id": "person", "source_ids": (20, 22, 39)},
            {"target_id": "p-002", "class_id": "person", "source_ids": (21, 40)},
            {"target_id": "v-001", "class_id": "vehicle", "source_ids": (23,)},
        ),
    },
    {
        "id": "rvmot-c4f6",
        "sequence_id": "mot-c4f6",
        "family": "real_full_frame_parking_scene",
        "source": "G328",
        "start_frame": 3272,
        "title": "Parking-area people and vehicles",
        "track_groups": (
            {"target_id": "p-001", "class_id": "person", "source_ids": (16, 18, 20)},
            {"target_id": "p-002", "class_id": "person", "source_ids": (7, 10, 22)},
            {"target_id": "p-003", "class_id": "person", "source_ids": (11,)},
            {"target_id": "p-004", "class_id": "person", "source_ids": (35,)},
            {"target_id": "v-001", "class_id": "vehicle", "source_ids": (5, 8, 9, 12, 15, 17, 19, 21)},
            {"target_id": "v-002", "class_id": "vehicle", "source_ids": (36, 37)},
            {"target_id": "v-003", "class_id": "vehicle", "source_ids": (95,)},
        ),
    },
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and _sha256(destination) == expected_sha256:
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(5):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "cvbench-real-video-prep/2"})
            with urllib.request.urlopen(request, timeout=180) as response, partial.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            if _sha256(partial) != expected_sha256:
                raise RuntimeError(f"checksum mismatch for {url}")
            partial.replace(destination)
            return
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            partial.unlink(missing_ok=True)
            if attempt == 4:
                raise RuntimeError(f"could not download {url}: {exc}") from exc
            time.sleep(2**attempt)


def _annotation_url(prefix: str, suffix: str) -> str:
    from urllib.parse import quote

    path = quote(f"{prefix}.{suffix}", safe="")
    return (
        "https://gitlab.kitware.com/api/v4/projects/meva%2Fmeva-data-repo/"
        f"repository/files/{path}/raw?ref={MEVA_ANNOTATION_COMMIT}"
    )


def _verify_toolchain() -> None:
    actual = {
        "python_major_minor": f"{os.sys.version_info.major}.{os.sys.version_info.minor}",
        "opencv-python-headless": importlib.metadata.version("opencv-python-headless"),
        "numpy": importlib.metadata.version("numpy"),
        "PyYAML": importlib.metadata.version("PyYAML"),
    }
    if actual != TOOLCHAIN:
        raise RuntimeError(f"preparation toolchain mismatch: expected {TOOLCHAIN}, got {actual}")


def _parse_annotations(
    source: dict[str, Any], sources_dir: Path
) -> tuple[dict[int, str], dict[int, dict[int, list[int]]]]:
    stem = str(source["filename"]).removesuffix(".r13.avi")
    types_path = sources_dir / f"{stem}.types.yml"
    geom_path = sources_dir / f"{stem}.geom.yml"
    _download(_annotation_url(source["annotation_prefix"], "types.yml"), types_path, source["types_sha256"])
    _download(_annotation_url(source["annotation_prefix"], "geom.yml"), geom_path, source["geom_sha256"])
    classes: dict[int, str] = {}
    for line in types_path.read_text().splitlines():
        record = ast.literal_eval(line[2:])["types"]
        classes[int(record["id1"])] = next(iter(record["cset3"]))
    tracks: dict[int, dict[int, list[int]]] = {}
    for line in geom_path.read_text().splitlines():
        record = ast.literal_eval(line[2:])["geom"]
        tracks.setdefault(int(record["id1"]), {})[int(record["ts0"])] = [int(value) for value in record["g0"].split()]
    return classes, tracks


def _scaled_box(box: list[int], scale: float, width: int, height: int) -> list[float]:
    result = [round(value * scale, 3) for value in box]
    result[0] = max(0.0, min(result[0], width - 1.0))
    result[1] = max(0.0, min(result[1], height - 1.0))
    result[2] = max(result[0] + 1.0, min(result[2], float(width)))
    result[3] = max(result[1] + 1.0, min(result[3], float(height)))
    return result


def _median_box(boxes: list[list[int]]) -> list[int]:
    return [round(statistics.median(box[index] for box in boxes)) for index in range(4)]


def _visual_audit() -> dict[str, Any]:
    path = ROOT / "scenarios" / "real-video-v2" / "visual-audit.json"
    audit = json.loads(path.read_text())
    if audit.get("schema_version") != "cvbench.real-video-visual-audit/v1":
        raise RuntimeError("invalid real-video visual audit schema")
    if audit.get("annotation_commit") != MEVA_ANNOTATION_COMMIT:
        raise RuntimeError("visual audit is not bound to the pinned annotation commit")
    expected_manifest = ROOT / "scenarios" / "real-video-v2" / "expected-frame-sha256.txt"
    if audit.get("frame_manifest_sha256") != _sha256(expected_manifest):
        raise RuntimeError("visual audit is not bound to the exact prepared frame manifest")
    if set(audit.get("clips", {})) != {clip["id"] for clip in CLIPS}:
        raise RuntimeError("visual audit scenario set does not match the prepared corpus")
    return audit


def _apply_visual_corrections(
    clip: dict[str, Any], physical: dict[str, dict[int, list[float]]], audit: dict[str, Any]
) -> None:
    clip_audit = audit["clips"].get(clip["id"])
    if not clip_audit or clip_audit.get("status") not in {"complete", "corrected-and-complete"}:
        raise RuntimeError(f"{clip['id']} lacks a completed all-frame visual audit")
    if clip_audit.get("omitted_supported_movers") != []:
        raise RuntimeError(f"{clip['id']} still declares omitted supported movers")
    groups = {group["target_id"]: group for group in clip["track_groups"]}
    for correction in clip_audit.get("corrections", []):
        target_id = correction.get("target_id")
        group = groups.get(target_id)
        if not group or correction.get("class_id") != group["class_id"]:
            raise RuntimeError(f"invalid correction target for {clip['id']}:{target_id}")
        if correction.get("upstream_source_ids") != list(group["source_ids"]):
            raise RuntimeError(f"correction source IDs disagree for {clip['id']}:{target_id}")
        corrected = physical[target_id]
        for row in correction.get("rows", []):
            frame_index = row["frame_index"]
            corrected[clip["start_frame"] + frame_index] = [float(value) for value in row["bbox_xyxy"]]
        for item in correction.get("constant_box_ranges", []):
            first, last = item["frame_indexes"]
            for frame_index in range(first, last + 1):
                corrected[clip["start_frame"] + frame_index] = [float(value) for value in item["bbox_xyxy"]]


def _rows_for_clip(
    clip: dict[str, Any],
    source: dict[str, Any],
    tracks: dict[int, dict[int, list[int]]],
    output_height: int,
    audit: dict[str, Any],
) -> list[dict[str, Any]]:
    scale = TARGET_WIDTH / int(source["width"])
    physical: dict[str, dict[int, list[float]]] = {}
    for group in clip["track_groups"]:
        frames: dict[int, list[float]] = {}
        for source_frame in range(clip["start_frame"], clip["start_frame"] + FRAME_COUNT):
            boxes = [
                tracks[source_id][source_frame]
                for source_id in group["source_ids"]
                if source_frame in tracks.get(source_id, {})
            ]
            if boxes:
                frames[source_frame] = _scaled_box(_median_box(boxes), scale, TARGET_WIDTH, output_height)
        if not frames:
            raise RuntimeError(f"{clip['id']} physical track {group['target_id']} has no source geometry")
        physical[group["target_id"]] = frames
    _apply_visual_corrections(clip, physical, audit)

    rows: list[dict[str, Any]] = []
    for group in clip["track_groups"]:
        frames = physical[group["target_id"]]
        first, last = min(frames), max(frames)
        expected_span = set(range(first, last + 1))
        if set(frames) != expected_span:
            raise RuntimeError(f"{clip['id']} physical track {group['target_id']} has an unaudited visible-span gap")
        for source_frame, box in sorted(frames.items()):
            truncated = box[0] == 0 or box[1] == 0 or box[2] == TARGET_WIDTH or box[3] == output_height
            output_index = source_frame - clip["start_frame"]
            rows.append(
                {
                    "schema_version": "cvbench.ground-truth/v1",
                    "target_id": group["target_id"],
                    "sequence_id": clip["sequence_id"],
                    "source_timestamp_ns": round(output_index * 1_000_000_000 / FPS),
                    "on_screen": True,
                    "eligible_for_detection": True,
                    "visibility_fraction": None,
                    "occlusion": "unknown",
                    "truncated": truncated,
                    "class_id": group["class_id"],
                    "bbox_xyxy": box,
                    "entry_event": source_frame == first,
                    "exit_event": source_frame == last,
                }
            )
    return sorted(rows, key=lambda row: (row["source_timestamp_ns"], row["target_id"]))


def _decode_clip(
    source_path: Path, clip: dict[str, Any], source: dict[str, Any], output: Path
) -> tuple[list[dict[str, Any]], int]:
    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        raise RuntimeError(f"could not open {source_path}")
    observed = (
        round(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        capture.get(cv2.CAP_PROP_FPS),
        round(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
    )
    expected = (source["width"], source["height"], source["fps"], source["frame_count"])
    if observed != expected:
        raise RuntimeError(f"source metadata mismatch for {source_path.name}: expected {expected}, got {observed}")
    output_height = round(int(source["height"]) * TARGET_WIDTH / int(source["width"]))
    frames_path = output / "frames"
    frames_path.mkdir(parents=True, exist_ok=True)
    capture.set(cv2.CAP_PROP_POS_FRAMES, clip["start_frame"])
    for output_index in range(FRAME_COUNT):
        ok, frame = capture.read()
        if not ok:
            raise RuntimeError(f"could not decode {clip['id']} source frame {clip['start_frame'] + output_index}")
        resized = cv2.resize(frame, (TARGET_WIDTH, output_height), interpolation=cv2.INTER_AREA)
        destination = frames_path / f"frame-{output_index:04d}.jpg"
        if not cv2.imwrite(str(destination), resized, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
            raise RuntimeError(f"could not encode {destination}")
    capture.release()
    return observed, output_height


def _write_manifest(
    directory: Path,
    clip: dict[str, Any],
    rows: list[dict[str, Any]],
    height: int,
    asset_root: Path | None = None,
) -> None:
    asset_root = asset_root or directory
    manifest = {
        "schema_version": "cvbench.scenario/v1",
        "id": clip["id"],
        "family": clip["family"],
        "sequence_id": clip["sequence_id"],
        "license": "CC-BY-4.0",
        "source": "MEVA KF1; see docs/real-video-sources.md; generated by scripts/prepare_real_video.py",
        "annotation_scope": "exhaustive_full_frame_moving_objects",
        "ontology": ["person", "vehicle", "dog"],
        "ground_truth": os.path.relpath(asset_root / "ground_truth.jsonl", directory),
        "frames": [
            {
                "frame_index": index,
                "source_timestamp_ns": round(index * 1_000_000_000 / FPS),
                "width": TARGET_WIDTH,
                "height": height,
                "path": os.path.relpath(asset_root / "frames" / f"frame-{index:04d}.jpg", directory),
            }
            for index in range(FRAME_COUNT)
        ],
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "scenario.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    (directory / "ground_truth.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    )


def _write_review_contact_sheets(clip: dict[str, Any], rows: list[dict[str, Any]], data_root: Path) -> list[str]:
    review = data_root / "review"
    shutil.rmtree(review, ignore_errors=True)
    review.mkdir(parents=True, exist_ok=True)
    rows_by_time: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_time.setdefault(row["source_timestamp_ns"], []).append(row)
    names: list[str] = []
    for page in range(6):
        panels = []
        for index in range(page * 25, (page + 1) * 25):
            image = cv2.imread(str(data_root / "frames" / f"frame-{index:04d}.jpg"))
            if image is None:
                raise RuntimeError(f"missing review frame {clip['id']}:{index}")
            timestamp = round(index * 1_000_000_000 / FPS)
            for row in rows_by_time.get(timestamp, []):
                x1, y1, x2, y2 = [round(value) for value in row["bbox_xyxy"]]
                color = (60, 220, 60) if row["class_id"] == "person" else (20, 170, 255)
                cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    image, row["target_id"], (x1, max(14, y1 - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1
                )
            cv2.putText(
                image,
                f"{index} / {timestamp / 1e9:.3f}s",
                (8, 18),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                2,
            )
            panels.append(cv2.resize(image, (288, round(image.shape[0] * 288 / image.shape[1]))))
        panel_height = panels[0].shape[0]
        sheet = np.zeros((panel_height * 5, 288 * 5, 3), dtype=np.uint8)
        for panel_index, panel in enumerate(panels):
            y = panel_index // 5 * panel_height
            x = panel_index % 5 * 288
            sheet[y : y + panel_height, x : x + 288] = panel
        name = f"{clip['id']}-frames-{page * 25:04d}-{(page + 1) * 25 - 1:04d}.jpg"
        cv2.imwrite(str(review / name), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])
        names.append(name)
    return names


def _write_deterministic_tar(source: Path, destination: Path, names: list[str]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with tarfile.open(temporary, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name in names:
            body = (source / name).read_bytes()
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            archive.addfile(info, io.BytesIO(body))
    temporary.replace(destination)


def _write_artifact_manifest(output: Path) -> None:
    entries = [
        f"{_sha256(path)}  {path.relative_to(output).as_posix()}"
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "artifacts.sha256"
    ]
    (output / "artifacts.sha256").write_text("\n".join(entries) + "\n")


def _write_expected_frame_manifest(output: Path) -> None:
    entries = []
    for clip in CLIPS:
        for frame in sorted((output / clip["id"] / "frames").glob("frame-*.jpg")):
            entries.append(f"{_sha256(frame)}  {clip['id']}/frames/{frame.name}")
    body = "\n".join(entries) + "\n"
    (output / "expected-frame-sha256.txt").write_text(body)
    checked_in = ROOT / "scenarios" / "real-video-v2"
    checked_in.mkdir(parents=True, exist_ok=True)
    (checked_in / "expected-frame-sha256.txt").write_text(body)
    fingerprint = hashlib.sha256(body.encode()).hexdigest() + "\n"
    (output / "corpus-fingerprint.txt").write_text(fingerprint)
    (checked_in / "corpus-fingerprint.txt").write_text(fingerprint)


def verify_artifacts(output: Path) -> None:
    manifest = output / "artifacts.sha256"
    for line in manifest.read_text().splitlines():
        expected, relative = line.split("  ", 1)
        actual = _sha256(output / relative)
        if actual != expected:
            raise RuntimeError(f"artifact checksum mismatch for {relative}: expected {expected}, got {actual}")


def prepare(output: Path) -> list[Path]:
    _verify_toolchain()
    output = output.resolve()
    sources_dir = output / "sources"
    parsed: dict[str, tuple[dict[int, str], dict[int, dict[int, list[int]]]]] = {}
    for key, source in SOURCES.items():
        video = sources_dir / source["filename"]
        _download(source["url"], video, source["sha256"])
        parsed[key] = _parse_annotations(source, sources_dir)

    provenance: dict[str, Any] = {
        "schema_version": "cvbench.real-video-provenance/v2",
        "dataset": "MEVA KF1",
        "license": "CC BY 4.0",
        "license_url": MEVA_LICENSE_URL,
        "annotation_commit": MEVA_ANNOTATION_COMMIT,
        "preparation": {
            "base_image": PREPARATION_BASE_IMAGE,
            "platform": PREPARATION_PLATFORM,
            "requirements_lock_sha256": _sha256(ROOT / "requirements-real-video.lock"),
        },
        "clips": [],
    }
    paths = []
    audit = _visual_audit()
    archive_declarations: dict[str, dict[str, Any]] = {}
    for clip in CLIPS:
        source = SOURCES[clip["source"]]
        clip_output = output / clip["id"]
        shutil.rmtree(clip_output, ignore_errors=True)
        observed, height = _decode_clip(sources_dir / source["filename"], clip, source, clip_output)
        classes, tracks = parsed[clip["source"]]
        for group in clip["track_groups"]:
            if any(classes.get(source_id) != group["class_id"] for source_id in group["source_ids"]):
                raise RuntimeError(f"class reconciliation mismatch for {clip['id']}:{group['target_id']}")
        rows = _rows_for_clip(clip, source, tracks, height, audit)
        _write_manifest(clip_output, clip, rows, height)
        checked_in = ROOT / "scenarios" / "real-video-v2" / clip["id"]
        _write_manifest(checked_in, clip, rows, height, ROOT / "data" / "real-video-v2" / clip["id"])
        review_sheets = _write_review_contact_sheets(clip, rows, clip_output)
        archives = ROOT / "scenarios" / "real-video-v2" / "archives"
        frame_archive = archives / f"{clip['id']}.frames.tar"
        frame_names = [f"frames/frame-{index:04d}.jpg" for index in range(FRAME_COUNT)]
        _write_deterministic_tar(clip_output, frame_archive, frame_names)
        review_archive = archives / f"{clip['id']}.visual-audit.tar"
        _write_deterministic_tar(clip_output, review_archive, [f"review/{name}" for name in review_sheets])
        archive_declarations[clip["id"]] = {
            "frame_archive": {
                "bytes": frame_archive.stat().st_size,
                "path": frame_archive.relative_to(ROOT).as_posix(),
                "sha256": _sha256(frame_archive),
            },
            "review_archive": {
                "bytes": review_archive.stat().st_size,
                "path": review_archive.relative_to(ROOT).as_posix(),
                "sha256": _sha256(review_archive),
            },
        }
        provenance["clips"].append(
            {
                "scenario_id": clip["id"],
                "source": clip["source"],
                "source_file": source["filename"],
                "source_sha256": source["sha256"],
                "source_frame_range_inclusive": [clip["start_frame"], clip["start_frame"] + FRAME_COUNT - 1],
                "native_fps": FPS,
                "decoded_metadata": observed,
                "output_resolution": [TARGET_WIDTH, height],
                "output_jpeg_quality": JPEG_QUALITY,
                "annotation_source_sha256": {
                    "geom": source["geom_sha256"],
                    "types": source["types_sha256"],
                },
                "physical_track_reconciliation": [
                    {
                        "target_id": group["target_id"],
                        "class_id": group["class_id"],
                        "source_ids": list(group["source_ids"]),
                    }
                    for group in clip["track_groups"]
                ],
                "audit": {
                    "status": "every output frame visually inspected",
                    "review_sheets": review_sheets,
                    "automation_role": (
                        "MEVA activity geometry bootstrapped boxes; duplicate physical identities were "
                        "human-reconciled; no temporal interpolation was used"
                    ),
                },
            }
        )
        paths.append(clip_output / "scenario.yaml")
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    _write_expected_frame_manifest(output)
    archive_manifest = {
        "schema_version": "cvbench.real-video-archives/v1",
        "frame_count": len(CLIPS) * FRAME_COUNT,
        "archives": archive_declarations,
    }
    archive_body = json.dumps(archive_manifest, indent=2, sort_keys=True) + "\n"
    (ROOT / "scenarios" / "real-video-v2" / "archives.json").write_text(archive_body)
    (output / "archives.json").write_text(archive_body)
    _write_artifact_manifest(output)
    verify_artifacts(output)
    return paths


def _resolve_output(repo_root: Path, output: Path | None) -> Path:
    return (output or repo_root / "data" / "real-video-v2").resolve()


def main() -> int:
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args()
    if os.environ.get("CVBENCH_PREP_CONTAINER") != "1":
        raise SystemExit("native host preparation is unsupported; use scripts/prepare_real_video_container.sh")
    ROOT = args.repo_root.resolve()
    output = _resolve_output(ROOT, args.output)
    if args.verify_only:
        verify_artifacts(output)
        print(output / "artifacts.sha256")
        return 0
    for path in prepare(output):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
