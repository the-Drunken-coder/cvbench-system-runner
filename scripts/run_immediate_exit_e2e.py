#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import threading
from pathlib import Path

from cvbench.collector import OutputCollector
from cvbench.reporting import validate_report
from cvbench.runner import run_benchmark


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    consume_started = threading.Event()
    boundary_requested = threading.Event()
    original_consume = OutputCollector._consume_line
    original_request = OutputCollector.request_output_boundary

    def delayed_final_consume(self, raw_line, recent_records):
        if b'"source_timestamp_ns":1100000000' in raw_line:
            consume_started.set()
            if not boundary_requested.wait(2):
                raise AssertionError("runner did not request the stdout completion boundary")
        return original_consume(self, raw_line, recent_records)

    def observed_request(self):
        boundary_requested.set()
        return original_request(self)

    OutputCollector._consume_line = delayed_final_consume
    OutputCollector.request_output_boundary = observed_request
    try:
        artifacts = run_benchmark(
            "benchmarks/timing-compute-evidence.yaml",
            "systems/pacing-immediate-exit-docker.yaml",
            args.output,
        )
    finally:
        OutputCollector._consume_line = original_consume
        OutputCollector.request_output_boundary = original_request

    report = json.loads(artifacts.report_json.read_text())
    validate_report(report)
    assert consume_started.is_set()
    assert boundary_requested.is_set()
    assert report["outcome"]["status"] == "completed"
    assert report["metrics"]["sample_counts"]["output_records"] > 0
    assert report["resources"]["authoritative"] is True
    assert report["resources"]["over_time"][-1]["final_cumulative"] is True
    assert report["leaderboard"]["eligible"] is True

    run_dir = artifacts.report_json.parent
    container_id = (run_dir / "container.cid").read_text().strip()
    inspected = subprocess.run(
        ["docker", "inspect", container_id],
        capture_output=True,
        text=True,
        check=False,
    )
    assert inspected.returncode != 0, "retained Docker container leaked after accounting"
    cgroup_name = f"cvbench-{run_dir.name}"
    assert not (Path("/sys/fs/cgroup") / cgroup_name).exists()
    assert not (Path("/sys/fs/cgroup") / f"{cgroup_name}.slice").exists()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
