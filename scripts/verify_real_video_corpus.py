#!/usr/bin/env python3
"""Verify prepared real-video-v2 bytes against the committed public corpus."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

try:
    from scripts.prepare_real_video import CLIPS, FRAME_COUNT, verify_artifacts
except ModuleNotFoundError:  # Direct execution from the preparation image.
    from prepare_real_video import CLIPS, FRAME_COUNT, verify_artifacts


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(repo_root: Path) -> str:
    output = repo_root / "data" / "real-video-v2"
    verify_artifacts(output)
    prepared_manifest = output / "expected-frame-sha256.txt"
    committed_manifest = repo_root / "scenarios" / "real-video-v2" / "expected-frame-sha256.txt"
    if prepared_manifest.read_bytes() != committed_manifest.read_bytes():
        raise RuntimeError("prepared and committed frame manifests differ")
    expected = {}
    for line in committed_manifest.read_text().splitlines():
        digest, relative = line.split("  ", 1)
        expected[relative] = digest
    if len(expected) != len(CLIPS) * FRAME_COUNT:
        raise RuntimeError(f"expected {len(CLIPS) * FRAME_COUNT} frame hashes, found {len(expected)}")
    for relative, digest in expected.items():
        actual = _sha256(output / relative)
        if actual != digest:
            raise RuntimeError(f"frame checksum mismatch for {relative}: expected {digest}, got {actual}")
        published = repo_root / "scenario-catalog" / "media" / "real-video-v2" / relative
        if not published.is_file() or _sha256(published) != digest:
            raise RuntimeError(f"published frame checksum mismatch for {relative}")
    for clip in CLIPS:
        prepared = output / clip["id"] / "ground_truth.jsonl"
        committed = repo_root / "scenarios" / "real-video-v2" / clip["id"] / "ground_truth.jsonl"
        if prepared.read_bytes() != committed.read_bytes():
            raise RuntimeError(f"prepared and committed annotations differ for {clip['id']}")
    fingerprint = hashlib.sha256(committed_manifest.read_bytes()).hexdigest()
    declared = (repo_root / "scenarios" / "real-video-v2" / "corpus-fingerprint.txt").read_text().strip()
    if fingerprint != declared:
        raise RuntimeError(f"corpus fingerprint mismatch: expected {declared}, got {fingerprint}")
    return fingerprint


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args()
    print(verify(args.repo_root.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
