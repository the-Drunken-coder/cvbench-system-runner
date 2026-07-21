import json
import sys
import time
from pathlib import Path

import psutil
import yaml

from cvbench.runner import run_benchmark
from cvbench.synthetic import generate_synthetic_pack

ROOT = Path(__file__).parents[1]


def _definitions(tmp_path: Path, script: Path, grace: float = 1, max_records: int = 100_000) -> tuple[Path, Path]:
    manifests = generate_synthetic_pack(tmp_path / "pack")
    benchmark = {
        "schema_version": "cvbench.benchmark/v1",
        "id": "failure-test",
        "version": "1",
        "input": {"mode": "online_replay", "protocol": "frame_socket_v1", "playback_rate": 100},
        "thresholds": {"minimum_match_iou": 0.3, "max_match_center_error_px": 20},
        "scenarios": [str(manifests[0])],
        "reporting": {"generate_failure_packets": False},
        "max_run_seconds": 5,
        "max_output_records": max_records,
    }
    benchmark_path = tmp_path / "benchmark.yaml"
    benchmark_path.write_text(yaml.safe_dump(benchmark))
    system = {
        "schema_version": "cvbench.system/v1",
        "id": script.stem,
        "revision": "test",
        "runtime": {
            "type": "local",
            "command": [sys.executable, str(script)],
            "environment": {"CVBENCH_TEST_PID_FILE": str(tmp_path / "sut.pid")},
        },
        "readiness": {"type": "stdout_pattern", "pattern": "CVBENCH_READY", "timeout_seconds": 2},
        "shutdown": {"grace_period_seconds": grace},
    }
    system_path = tmp_path / "systems" / "system.yaml"
    system_path.parent.mkdir()
    system_path.write_text(yaml.safe_dump(system))
    return benchmark_path, system_path


def _report(tmp_path: Path, fixture: str, grace: float = 1, max_records: int = 100_000) -> dict:
    benchmark, system = _definitions(tmp_path, ROOT / "tests/fixtures" / fixture, grace, max_records)
    artifacts = run_benchmark(benchmark, system, tmp_path / "runs")
    return json.loads(artifacts.report_json.read_text())


def test_sut_crash_is_reported(tmp_path: Path) -> None:
    report = _report(tmp_path, "sut_crash.py")
    assert report["outcome"]["status"] == "failed"
    assert report["outcome"]["crashed"] is True
    assert any(item["finding_id"] == "RUN-CRASH-001" for item in report["findings"])


def test_sut_shutdown_timeout_is_reported(tmp_path: Path) -> None:
    report = _report(tmp_path, "sut_timeout.py", grace=0.05)
    assert report["outcome"]["timed_out"] is True
    assert any(item["finding_id"] == "RUN-TIMEOUT-001" for item in report["findings"])
    pid = int((tmp_path / "sut.pid").read_text())
    time.sleep(0.05)
    assert not psutil.pid_exists(pid)


def test_malformed_output_is_rejected_and_reported(tmp_path: Path) -> None:
    report = _report(tmp_path, "sut_malformed.py")
    assert report["outcome"]["status"] == "failed"
    assert report["diagnostics"]["collector_errors"]
    assert any(item["finding_id"] == "OUTPUT-INVALID-001" for item in report["findings"])


def test_missing_readiness_is_bounded_and_reported(tmp_path: Path) -> None:
    report = _report(tmp_path, "sut_missing_readiness.py")
    assert report["outcome"]["timed_out"] is True
    assert "readiness timeout" in report["outcome"]["errors"]
    pid = int((tmp_path / "sut.pid").read_text())
    time.sleep(0.05)
    assert not psutil.pid_exists(pid)


def test_output_flood_is_bounded_and_reported(tmp_path: Path) -> None:
    report = _report(tmp_path, "sut_flood.py", max_records=5)
    assert report["outcome"]["status"] == "failed"
    assert "output flooding" in report["outcome"]["errors"]
    assert report["metrics"]["sample_counts"]["output_records"] == 5
