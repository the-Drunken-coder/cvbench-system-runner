#!/usr/bin/env python3
"""Hydrate the exact public real-video runtime corpus from committed frame archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

SCENARIO_IDS = ("rvmot-a1c9", "rvmot-b7e2", "rvmot-c4f6")
FRAME_COUNT = 150


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_expected(repo_root: Path) -> dict[str, str]:
    manifest = repo_root / "scenarios" / "real-video-v2" / "expected-frame-sha256.txt"
    expected: dict[str, str] = {}
    for line in manifest.read_text().splitlines():
        digest, relative = line.split("  ", 1)
        if len(digest) != 64 or relative in expected:
            raise RuntimeError("malformed real-video frame hash manifest")
        expected[relative] = digest
    if len(expected) != len(SCENARIO_IDS) * FRAME_COUNT:
        raise RuntimeError(f"expected {len(SCENARIO_IDS) * FRAME_COUNT} frame hashes, found {len(expected)}")
    return expected


def _load_archives(repo_root: Path) -> dict:
    path = repo_root / "scenarios" / "real-video-v2" / "archives.json"
    value = json.loads(path.read_text())
    if set(value) != {"archives", "frame_count", "schema_version"}:
        raise RuntimeError("real-video archive manifest has undeclared fields")
    if value["schema_version"] != "cvbench.real-video-archives/v1" or value["frame_count"] != 450:
        raise RuntimeError("invalid real-video archive manifest")
    if set(value["archives"]) != set(SCENARIO_IDS):
        raise RuntimeError("real-video archive manifest has the wrong scenario set")
    return value


def _validated_archive(repo_root: Path, declaration: dict, scenario_id: str) -> Path:
    if set(declaration) != {"bytes", "path", "sha256"}:
        raise RuntimeError(f"{scenario_id} archive declaration has undeclared fields")
    expected = f"scenarios/real-video-v2/archives/{scenario_id}.frames.tar"
    if declaration["path"] != expected:
        raise RuntimeError(f"{scenario_id} archive path is not allowlisted")
    unresolved = repo_root / declaration["path"]
    cursor = repo_root
    for part in unresolved.relative_to(repo_root).parts:
        cursor /= part
        if cursor.is_symlink():
            raise RuntimeError(f"{scenario_id} archive path contains a symlink")
    path = unresolved.resolve()
    allowed = (repo_root / "scenarios" / "real-video-v2" / "archives").resolve()
    if path.parent != allowed or not path.is_file():
        raise RuntimeError(f"{scenario_id} archive is not a regular allowlisted file")
    if path.stat().st_size != declaration["bytes"] or _sha256(path) != declaration["sha256"]:
        raise RuntimeError(f"{scenario_id} archive hash or size mismatch")
    return path


def _extract_frames(archive: Path, output: Path, scenario_id: str, expected: dict[str, str]) -> None:
    names = {f"frames/frame-{index:04d}.jpg" for index in range(FRAME_COUNT)}
    with tarfile.open(archive, "r:") as handle:
        members = handle.getmembers()
        if len(members) != FRAME_COUNT or {member.name for member in members} != names:
            raise RuntimeError(f"{scenario_id} archive entries do not match the declared frame set")
        for member in members:
            if not member.isfile() or member.issym() or member.islnk():
                raise RuntimeError(f"{scenario_id} archive contains a non-regular entry")
            source = handle.extractfile(member)
            if source is None:
                raise RuntimeError(f"could not read {scenario_id}/{member.name}")
            body = source.read()
            relative = f"{scenario_id}/{member.name}"
            if hashlib.sha256(body).hexdigest() != expected[relative]:
                raise RuntimeError(f"frame hash mismatch for {relative}")
            destination = output / scenario_id / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(body)


def hydrate(repo_root: Path, output: Path | None = None) -> Path:
    repo_root = repo_root.resolve()
    output = (output or repo_root / "data" / "real-video-v2").resolve()
    if output != repo_root / "data" / "real-video-v2":
        raise RuntimeError("hydration output must be the dedicated data/real-video-v2 directory")
    expected = _load_expected(repo_root)
    archive_manifest = _load_archives(repo_root)
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)
    for scenario_id in SCENARIO_IDS:
        declaration = archive_manifest["archives"][scenario_id]["frame_archive"]
        archive = _validated_archive(repo_root, declaration, scenario_id)
        _extract_frames(archive, output, scenario_id, expected)
        source_gt = repo_root / "scenarios" / "real-video-v2" / scenario_id / "ground_truth.jsonl"
        shutil.copyfile(source_gt, output / scenario_id / "ground_truth.jsonl")
    source_manifest = repo_root / "scenarios" / "real-video-v2" / "expected-frame-sha256.txt"
    shutil.copyfile(source_manifest, output / "expected-frame-sha256.txt")
    shutil.copyfile(
        repo_root / "scenarios" / "real-video-v2" / "corpus-fingerprint.txt",
        output / "corpus-fingerprint.txt",
    )
    shutil.copyfile(repo_root / "scenarios" / "real-video-v2" / "archives.json", output / "archives.json")
    entries = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "artifacts.sha256":
            entries.append(f"{_sha256(path)}  {path.relative_to(output).as_posix()}")
    (output / "artifacts.sha256").write_text("\n".join(entries) + "\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(hydrate(args.repo_root, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
