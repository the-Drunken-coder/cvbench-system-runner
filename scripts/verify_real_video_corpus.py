#!/usr/bin/env python3
"""Verify the prepared real-video corpus against its canonical fingerprint."""

from __future__ import annotations

import argparse
from pathlib import Path

from cvbench.config import load_benchmark
from cvbench.runner import _comparison_fingerprint
from cvbench.scenario import load_scenario


def verify(repo_root: Path) -> str:
    benchmark = load_benchmark(repo_root / "benchmarks/real-video-v1.yaml")
    scenarios = [load_scenario(path) for path in benchmark.scenarios]
    fingerprint, _inputs = _comparison_fingerprint(benchmark, scenarios)
    expected_path = repo_root / "scenarios/real-video-v1/corpus-fingerprint.txt"
    expected = expected_path.read_text().strip()
    if fingerprint != expected:
        raise RuntimeError(f"corpus fingerprint mismatch: expected {expected}, got {fingerprint}")
    return fingerprint


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args()
    print(verify(args.repo_root.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
