import copy
import json
import sys
import threading
import time
from pathlib import Path

import psutil
import yaml

import cvbench.runner as runner_module
from cvbench.collector import OutputCollector
from cvbench.comparison import compare_reports
from cvbench.reporting import validate_report
from cvbench.runner import run_benchmark
from cvbench.synthetic import generate_synthetic_pack

ROOT = Path(__file__).parents[1]


def _definitions(
    tmp_path: Path,
    script: Path,
    grace: float = 1,
    max_records: int = 100_000,
    output_limits: dict[str, int] | None = None,
    environment: dict[str, str] | None = None,
    max_run_seconds: float = 5,
    replay_profile: str = "accelerated-test-100x",
) -> tuple[Path, Path]:
    manifests = generate_synthetic_pack(tmp_path / "pack")
    benchmark = {
        "schema_version": "cvbench.benchmark/v1",
        "id": "failure-test",
        "version": "1",
        "input": {
            "mode": "online_replay",
            "protocol": "frame_socket_v1",
            "replay_profile": replay_profile,
        },
        "thresholds": {"minimum_match_iou": 0.3, "max_match_center_error_px": 20},
        "scenarios": [str(manifests[0])],
        "reporting": {"generate_failure_packets": False},
        "max_run_seconds": max_run_seconds,
        "max_output_records": max_records,
        **(output_limits or {}),
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
            "environment": {
                "CVBENCH_TEST_PID_FILE": str(tmp_path / "sut.pid"),
                "CVBENCH_TEST_CHILD_PID_FILE": str(tmp_path / "sut-child.pid"),
                **(environment or {}),
            },
        },
        "readiness": {"type": "stdout_pattern", "pattern": "CVBENCH_READY", "timeout_seconds": 2},
        "shutdown": {"grace_period_seconds": grace},
    }
    system_path = tmp_path / "systems" / "system.yaml"
    system_path.parent.mkdir()
    system_path.write_text(yaml.safe_dump(system))
    return benchmark_path, system_path


def _report(
    tmp_path: Path,
    fixture: str,
    grace: float = 1,
    max_records: int = 100_000,
    output_limits: dict[str, int] | None = None,
    environment: dict[str, str] | None = None,
    max_run_seconds: float = 5,
    replay_profile: str = "accelerated-test-100x",
) -> dict:
    benchmark, system = _definitions(
        tmp_path,
        ROOT / "tests/fixtures" / fixture,
        grace,
        max_records,
        output_limits,
        environment,
        max_run_seconds,
        replay_profile,
    )
    artifacts = run_benchmark(benchmark, system, tmp_path / "runs")
    return json.loads(artifacts.report_json.read_text())


def _emulate_authoritative_docker_accounting(monkeypatch) -> None:
    original_build = runner_module.build_leaderboard_semantics

    def build_with_authoritative_resources(**kwargs):
        resources = kwargs["resources"]
        resources.update(
            {
                "authoritative": True,
                "accounting_availability": {
                    "external_cgroup_v2": True,
                    "final_cumulative_cpu_sample": True,
                    "cpu_time": True,
                    "cpu_percent": True,
                    "peak_ram": True,
                    "disk_io": True,
                },
            }
        )
        for key in (
            "cpu_time_seconds",
            "average_cpu_percent",
            "peak_cpu_percent",
            "peak_ram_bytes",
            "disk_read_bytes",
            "disk_write_bytes",
        ):
            resources.setdefault(key, 0)
        kwargs["runtime_type"] = "docker"
        return original_build(**kwargs)

    monkeypatch.setattr(
        runner_module,
        "build_leaderboard_semantics",
        build_with_authoritative_resources,
    )


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


def test_outputs_emitted_during_forced_teardown_are_not_scored(tmp_path: Path) -> None:
    report = _report(tmp_path, "sut_sigterm_output.py", grace=0.05)
    assert report["outcome"]["timed_out"] is True
    assert report["metrics"]["sample_counts"]["output_records"] == 0
    assert report["timing"]["durations"]["drain_seconds"] <= 0.1
    assert report["timing"]["durations"]["teardown_seconds"] >= 1.9


def test_malformed_output_is_rejected_and_reported(tmp_path: Path) -> None:
    report = _report(tmp_path, "sut_malformed.py")
    assert report["outcome"]["status"] == "failed"
    assert report["diagnostics"]["collector_errors"]
    assert any(item["finding_id"] == "OUTPUT-INVALID-001" for item in report["findings"])


def test_future_timestamp_spoofing_is_rejected_as_noncausal(tmp_path: Path) -> None:
    report = _report(tmp_path, "sut_timestamp_spoof.py")
    assert report["outcome"]["status"] == "failed"
    assert report["metrics"]["sample_counts"]["output_records"] == 0
    assert any(
        "does not identify a successfully delivered frame" in error
        for error in report["diagnostics"]["collector_errors"]
    )


def test_exact_timestamp_post_stream_output_is_scored_during_bounded_drain(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path, "sut_post_stream.py")
    assert report["outcome"]["status"] == "completed"
    assert report["metrics"]["sample_counts"]["output_records"] == 1
    assert report["timing"]["output"]["late_after_benchmark_end"] == 1
    assert "bounded drain window" in report["timing"]["output"]["late_output_policy"]


def test_half_close_waits_for_delayed_collector_and_scores_buffered_output(
    tmp_path: Path, monkeypatch
) -> None:
    consume_started = threading.Event()
    boundary_requested = threading.Event()
    original_consume = OutputCollector._consume_line
    original_request = OutputCollector.request_output_boundary

    def delayed_consume(self, raw_line, recent_records):
        if b'"schema_version":"cvbench.track/v1"' in raw_line and not consume_started.is_set():
            consume_started.set()
            assert boundary_requested.wait(1)
        return original_consume(self, raw_line, recent_records)

    def observed_request(self):
        boundary_requested.set()
        return original_request(self)

    monkeypatch.setattr(OutputCollector, "_consume_line", delayed_consume)
    monkeypatch.setattr(OutputCollector, "request_output_boundary", observed_request)

    report = _report(tmp_path, "sut_half_close_output.py")

    assert consume_started.is_set()
    assert boundary_requested.is_set()
    assert report["outcome"]["status"] == "completed"
    assert report["metrics"]["sample_counts"]["output_records"] == 2
    assert report["diagnostics"]["collector_errors"] == []


def test_immediate_clean_exit_drains_delayed_stdout_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    consume_started = threading.Event()
    boundary_requested = threading.Event()
    original_consume = OutputCollector._consume_line
    original_request = OutputCollector.request_output_boundary
    original_stop = runner_module.stop_runtime

    def delayed_consume(self, raw_line, recent_records):
        if b'"schema_version":"cvbench.track/v1"' in raw_line and not consume_started.is_set():
            consume_started.set()
            assert boundary_requested.wait(1)
        return original_consume(self, raw_line, recent_records)

    def observed_request(self):
        boundary_requested.set()
        return original_request(self)

    def stop_after_clean_exit(runtime, *args, **kwargs):
        assert runtime.process.wait(timeout=1) == 0
        return original_stop(runtime, *args, **kwargs)

    monkeypatch.setattr(OutputCollector, "_consume_line", delayed_consume)
    monkeypatch.setattr(OutputCollector, "request_output_boundary", observed_request)
    monkeypatch.setattr(runner_module, "stop_runtime", stop_after_clean_exit)

    report = _report(
        tmp_path,
        "sut_half_close_output.py",
        environment={"CVBENCH_HALF_CLOSE_MODE": "immediate-clean-exit"},
    )

    assert consume_started.is_set()
    assert boundary_requested.is_set()
    assert report["outcome"]["status"] == "completed"
    assert report["metrics"]["sample_counts"]["output_records"] == 2
    assert report["diagnostics"]["collector_errors"] == []


def test_immediate_clean_exit_without_boundary_ack_fails_and_cleans_up(
    tmp_path: Path, monkeypatch
) -> None:
    original_consume = OutputCollector._consume_line
    original_stop = runner_module.stop_runtime

    def delayed_consume(self, raw_line, recent_records):
        if b'"schema_version":"cvbench.track/v1"' in raw_line:
            time.sleep(0.15)
        return original_consume(self, raw_line, recent_records)

    def stop_after_clean_exit(runtime, *args, **kwargs):
        assert runtime.process.wait(timeout=1) == 0
        return original_stop(runtime, *args, **kwargs)

    monkeypatch.setattr(OutputCollector, "_consume_line", delayed_consume)
    monkeypatch.setattr(runner_module, "stop_runtime", stop_after_clean_exit)

    report = _report(
        tmp_path,
        "sut_half_close_output.py",
        grace=0.03,
        environment={"CVBENCH_HALF_CLOSE_MODE": "immediate-clean-exit"},
    )

    assert report["outcome"]["status"] == "failed"
    assert report["outcome"]["timed_out"] is True
    assert "scoring drain deadline expired before stdout completion" in report["outcome"]["errors"]
    assert report["metrics"]["sample_counts"]["output_records"] == 0
    assert report["timing"]["durations"]["drain_seconds"] <= 0.1
    pid = int((tmp_path / "sut.pid").read_text())
    assert not psutil.pid_exists(pid)


def test_clean_exit_malformed_boundary_is_failed_and_leaderboard_ineligible(
    tmp_path: Path, monkeypatch
) -> None:
    _emulate_authoritative_docker_accounting(monkeypatch)
    report = _report(
        tmp_path,
        "sut_half_close_output.py",
        environment={"CVBENCH_HALF_CLOSE_MODE": "malformed-immediate-clean-exit"},
        replay_profile="native",
    )

    validate_report(report)
    assert report["outcome"]["status"] == "failed"
    assert len(report["diagnostics"]["collector_errors"]) == 1
    assert report["leaderboard"]["eligible"] is False
    assert "run did not complete" in report["leaderboard"]["disqualifications"]
    assert report["leaderboard"]["ranking_method"] == "pareto"
    assert report["leaderboard"]["composite_score"] is None

    baseline = copy.deepcopy(report)
    baseline["outcome"]["status"] = "completed"
    baseline["leaderboard"]["eligible"] = True
    baseline["leaderboard"]["disqualifications"] = []
    comparisons = compare_reports(baseline, report)
    assert comparisons
    assert all(item["direction"] == "inconclusive" for item in comparisons)
    assert all("both reports must be eligible" in item["reason"] for item in comparisons)


def test_clean_exit_valid_boundary_remains_eligible_and_schema_valid(
    tmp_path: Path, monkeypatch
) -> None:
    _emulate_authoritative_docker_accounting(monkeypatch)
    report = _report(
        tmp_path,
        "sut_half_close_output.py",
        environment={"CVBENCH_HALF_CLOSE_MODE": "immediate-clean-exit"},
        replay_profile="native",
    )

    validate_report(report)
    assert report["outcome"]["status"] == "completed"
    assert report["diagnostics"]["collector_errors"] == []
    assert report["leaderboard"]["eligible"] is True
    comparisons = {
        item["metric"]: item
        for item in compare_reports(copy.deepcopy(report), report)
    }
    assert comparisons["acquisition.rate"]["direction"] == "unchanged"


def test_malformed_before_half_close_is_reported_but_late_lines_are_not_scored(
    tmp_path: Path,
) -> None:
    report = _report(
        tmp_path,
        "sut_half_close_output.py",
        environment={"CVBENCH_HALF_CLOSE_MODE": "malformed-before"},
    )

    assert report["outcome"]["status"] == "failed"
    assert report["metrics"]["sample_counts"]["output_records"] == 2
    assert len(report["diagnostics"]["collector_errors"]) == 1
    assert "malformed-before-boundary" in report["diagnostics"]["collector_errors"][0]
    assert "malformed-late" not in report["diagnostics"]["collector_errors"][0]


def test_blocked_reader_cannot_extend_socket_send_past_overall_deadline(
    tmp_path: Path,
) -> None:
    benchmark, system = _definitions(
        tmp_path,
        ROOT / "tests/fixtures/sut_blocked_reader.py",
        max_run_seconds=0.3,
    )
    scenario = yaml.safe_load(Path(yaml.safe_load(benchmark.read_text())["scenarios"][0]).read_text())
    frame_path = Path(yaml.safe_load(benchmark.read_text())["scenarios"][0]).parent / scenario["frames"][0]["path"]
    frame_path.write_bytes(b"x" * 4_000_000)
    started = time.monotonic()

    artifacts = run_benchmark(benchmark, system, tmp_path / "blocked-runs")
    elapsed = time.monotonic() - started
    report = json.loads(artifacts.report_json.read_text())

    assert elapsed < 1.5
    assert report["outcome"]["status"] == "failed"
    assert report["outcome"]["timed_out"] is True
    assert report["feed"]["delivered_frames"] == 0
    assert any(
        "benchmark run deadline expired during" in error
        for error in report["outcome"]["errors"]
    )
    pid = int((tmp_path / "sut.pid").read_text())
    assert not psutil.pid_exists(pid)


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
    assert any("output record limit exceeded" in error for error in report["outcome"]["errors"])
    assert report["metrics"]["sample_counts"]["output_records"] == 5
    assert any(item["finding_id"] == "OUTPUT-FLOOD-001" for item in report["findings"])


def test_oversized_stdout_line_is_bounded_before_json_decode(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        "sut_oversized_line.py",
        output_limits={"max_output_line_bytes": 1024, "max_total_output_bytes": 20_000},
    )
    assert report["outcome"]["status"] == "failed"
    assert any("stdout line byte limit exceeded (1024)" in error for error in report["outcome"]["errors"])
    assert report["metrics"]["sample_counts"]["output_records"] == 0
    assert any(item["finding_id"] == "OUTPUT-FLOOD-001" for item in report["findings"])


def test_total_stdout_bytes_are_bounded_before_json_decode(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        "sut_oversized_line.py",
        output_limits={"max_output_line_bytes": 20_000, "max_total_output_bytes": 4096},
    )
    assert any("total stdout byte limit exceeded (4096)" in error for error in report["outcome"]["errors"])
    assert report["metrics"]["sample_counts"]["output_records"] == 0


def test_continuous_output_rate_is_bounded_and_process_is_reaped(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        "sut_continuous_flood.py",
        output_limits={"max_output_records_per_second": 20},
    )
    assert any("output rate limit exceeded (20 records/second)" in error for error in report["outcome"]["errors"])
    assert report["metrics"]["sample_counts"]["output_records"] == 0
    assert any(
        "does not identify a successfully delivered frame" in error
        for error in report["diagnostics"]["collector_errors"]
    )
    pid = int((tmp_path / "sut.pid").read_text())
    time.sleep(0.05)
    assert not psutil.pid_exists(pid)


def test_local_descendant_resources_are_sampled_and_process_group_is_reaped(tmp_path: Path) -> None:
    report = _report(tmp_path, "sut_child.py")
    assert report["outcome"]["status"] == "completed"
    assert report["resources"]["peak_process_count"] >= 2
    assert report["timing"]["durations"]["drain_seconds"] <= 1.1
    assert report["timing"]["durations"]["teardown_seconds"] >= 0.015
    child_pid = int((tmp_path / "sut-child.pid").read_text())
    deadline = time.monotonic() + 2
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not psutil.pid_exists(child_pid)
