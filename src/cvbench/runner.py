from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import tempfile
import time
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .collector import OutputCollector
from .comparison import compare_reports
from .config import BenchmarkConfig, load_benchmark, load_system
from .diagnostics import generate_findings
from .errors import RuntimeFailure
from .evidence import generate_evidence_packets
from .metrics import calculate_metrics
from .model import CollectedRecord, RunArtifacts, RuntimeOutcome, Scenario
from .protocol import send_message
from .reporting import write_report_files
from .resources import ResourceMonitor
from .runtime import StartedRuntime, cleanup_runtime, start_runtime, stop_runtime, verify_docker_isolation
from .scenario import load_scenario
from .stability import evaluate_long_run_assertions


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _comparison_fingerprint(benchmark: BenchmarkConfig, scenarios: list[Scenario]) -> tuple[str, dict[str, Any]]:
    inputs = {
        "benchmark_id": benchmark.id,
        "benchmark_version": benchmark.version,
        "input_mode": benchmark.input_mode,
        "playback_rate": benchmark.playback_rate,
        "thresholds": asdict(benchmark.thresholds),
        "scenarios": [
            {
                "id": scenario.id,
                "manifest_sha256": _sha256(next(path for path in benchmark.scenarios if path.parent == scenario.root)),
                "ground_truth_sha256": _sha256(scenario.root / "ground_truth.jsonl"),
                "frame_sha256": [_sha256(frame.path) for frame in scenario.frames],
            }
            for scenario in scenarios
        ],
    }
    encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), inputs


def _sleep_until(deadline_ns: int, run_deadline: float) -> None:
    while True:
        if time.monotonic() > run_deadline:
            raise TimeoutError("benchmark run deadline expired")
        remaining = (deadline_ns - time.monotonic_ns()) / 1_000_000_000
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.05))


def _faults_for_frame(scenario: Scenario, frame_index: int) -> list[dict[str, Any]]:
    result = []
    for fault in scenario.faults:
        indices = fault.get("frame_indices", [])
        if frame_index in indices or fault.get("after_frame") == frame_index:
            result.append(fault)
    return result


def _black_jpeg(width: int, height: int) -> bytes:
    ok, encoded = cv2.imencode(".jpg", np.zeros((height, width, 3), dtype=np.uint8))
    if not ok:
        raise RuntimeFailure("could not encode injected black frame")
    return encoded.tobytes()


def _shift_ground_truth(scenario: Scenario, base_ns: int, playback_rate: float) -> list[dict[str, Any]]:
    shifted = []
    for row in scenario.ground_truth:
        record = dict(row)
        record["scenario_source_timestamp_ns"] = row["source_timestamp_ns"]
        record["source_timestamp_ns"] = base_ns + int(row["source_timestamp_ns"] / playback_rate)
        record["scenario_family"] = scenario.family
        shifted.append(record)
    return shifted


def _deliver_scenarios(
    connection: socket.socket,
    scenarios: list[Scenario],
    config: BenchmarkConfig,
    run_deadline: float,
    frame_sizes: dict[tuple[str, int], tuple[int, int]],
    monitor: ResourceMonitor,
    collector: OutputCollector,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, list[int]], dict[tuple[str, int], list[str]]]:
    shifted_ground_truth: list[dict[str, Any]] = []
    counters = {
        "delivered_frames": 0,
        "dropped_frames": 0,
        "duplicate_frames": 0,
        "black_frames": 0,
        "feed_interruptions": 0,
        "delayed_frames": 0,
    }
    sequence_timestamps: dict[str, list[int]] = {}
    fault_events: dict[tuple[str, int], list[str]] = {}
    for scenario in scenarios:
        if collector.flooded.is_set():
            raise RuntimeFailure(f"output limit exceeded: {collector.limit_reason}")
        base_ns = time.monotonic_ns() + 20_000_000
        shifted_ground_truth.extend(_shift_ground_truth(scenario, base_ns, config.playback_rate))
        sequence_timestamps[scenario.frames[0].sequence_id] = []
        send_message(
            connection,
            {
                "event": "stream_start",
                "schema_version": "cvbench.frame/v1",
                "sequence_id": scenario.frames[0].sequence_id,
            },
        )
        for frame in scenario.frames:
            if collector.flooded.is_set():
                raise RuntimeFailure(f"output limit exceeded: {collector.limit_reason}")
            timestamp = base_ns + int(frame.relative_timestamp_ns / config.playback_rate)
            sequence_timestamps[frame.sequence_id].append(timestamp)
            if config.input_mode == "online_replay":
                _sleep_until(timestamp, run_deadline)
            if collector.flooded.is_set():
                raise RuntimeFailure(f"output limit exceeded: {collector.limit_reason}")
            fault_actions = _faults_for_frame(scenario, frame.frame_index)
            target_count = sum(
                row["on_screen"] and row["eligible_for_detection"]
                for row in scenario.ground_truth
                if row["source_timestamp_ns"] == frame.relative_timestamp_ns
            )
            monitor.set_context(scenario.family, target_count, bool(fault_actions))
            if fault_actions:
                fault_events[(frame.sequence_id, timestamp)] = [str(action.get("type")) for action in fault_actions]
            for fault in fault_actions:
                if fault.get("type") == "feed_interruption":
                    counters["feed_interruptions"] += 1
                    send_message(
                        connection,
                        {
                            "event": "feed_interruption_start",
                            "schema_version": "cvbench.frame/v1",
                            "sequence_id": frame.sequence_id,
                            "source_timestamp_ns": timestamp,
                        },
                    )
                    time.sleep(float(fault.get("duration_ms", 250)) / 1000)
                    send_message(
                        connection,
                        {
                            "event": "feed_interruption_end",
                            "schema_version": "cvbench.frame/v1",
                            "sequence_id": frame.sequence_id,
                            "source_timestamp_ns": time.monotonic_ns(),
                        },
                    )
                elif fault.get("type") == "delay":
                    counters["delayed_frames"] += 1
                    time.sleep(float(fault.get("duration_ms", 100)) / 1000)
            if any(fault.get("type") == "frame_drop" for fault in fault_actions):
                counters["dropped_frames"] += 1
                continue
            payload = frame.path.read_bytes()
            if any(fault.get("type") == "blackout" for fault in fault_actions):
                payload = _black_jpeg(frame.width, frame.height)
                counters["black_frames"] += 1
            metadata = {
                "event": "frame",
                "schema_version": "cvbench.frame/v1",
                "sequence_id": frame.sequence_id,
                "frame_index": frame.frame_index,
                "source_timestamp_ns": timestamp,
                "scenario_source_timestamp_ns": frame.relative_timestamp_ns,
                "width": frame.width,
                "height": frame.height,
                "pixel_format": "rgb24",
                "payload_encoding": "jpeg",
            }
            frame_sizes[(frame.sequence_id, timestamp)] = (frame.width, frame.height)
            send_message(connection, metadata, payload)
            counters["delivered_frames"] += 1
            if any(fault.get("type") == "duplicate" for fault in fault_actions):
                duplicate = dict(metadata)
                duplicate["duplicate"] = True
                send_message(connection, duplicate, payload)
                counters["duplicate_frames"] += 1
        send_message(
            connection,
            {
                "event": "stream_end",
                "schema_version": "cvbench.frame/v1",
                "sequence_id": scenario.frames[0].sequence_id,
            },
        )
    send_message(connection, {"event": "benchmark_end", "schema_version": "cvbench.frame/v1"})
    return shifted_ground_truth, counters, sequence_timestamps, fault_events


def _run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _restrict_socket_access(socket_dir: Path, socket_path: Path) -> None:
    socket_dir.chmod(0o700)
    socket_path.chmod(0o600)


def _wait_for_readiness(collector: OutputCollector, runtime: StartedRuntime, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if collector.ready.wait(min(0.05, max(0.0, deadline - time.monotonic()))):
            return True
        if collector.flooded.is_set():
            return False
        if runtime.process.poll() is not None:
            return False
    return collector.ready.is_set()


def _load_unique_scenarios(paths: tuple[Path, ...]) -> list[Scenario]:
    scenarios: list[Scenario] = []
    seen: dict[str, int] = {}
    for path in paths:
        scenario = load_scenario(path)
        sequence = scenario.frames[0].sequence_id
        seen[sequence] = seen.get(sequence, 0) + 1
        if seen[sequence] > 1:
            unique_sequence = f"{sequence}__repeat_{seen[sequence]}"
            scenario = Scenario(
                id=f"{scenario.id}__repeat_{seen[sequence]}",
                family=scenario.family,
                root=scenario.root,
                frames=[replace(frame, sequence_id=unique_sequence) for frame in scenario.frames],
                ground_truth=[{**row, "sequence_id": unique_sequence} for row in scenario.ground_truth],
                faults=scenario.faults,
            )
        scenarios.append(scenario)
    return scenarios


def run_benchmark(benchmark_path: str | Path, system_path: str | Path, output_root: str | Path) -> RunArtifacts:
    benchmark = load_benchmark(benchmark_path)
    system = load_system(system_path)
    scenarios = _load_unique_scenarios(benchmark.scenarios)
    run_dir = Path(output_root).resolve() / _run_id()
    run_dir.mkdir(parents=True)
    socket_dir = Path(tempfile.mkdtemp(prefix="cvb-", dir="/tmp"))
    socket_path = socket_dir / "input.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(socket_path))
        _restrict_socket_access(socket_dir, socket_path)
        server.listen(1)
        server.settimeout(system.readiness_timeout_seconds)
    except OSError:
        server.close()
        shutil.rmtree(socket_dir, ignore_errors=True)
        raise
    started_ns = time.monotonic_ns()
    started_wall = datetime.now(UTC).isoformat()
    outcome = RuntimeOutcome(status="failed")
    runtime: StartedRuntime | None = None
    monitor: ResourceMonitor | None = None
    collector: OutputCollector | None = None
    collected: list[CollectedRecord] = []
    collector_errors: list[str] = []
    stderr: list[str] = []
    ground_truth: list[dict[str, Any]] = []
    feed_counters = {
        "delivered_frames": 0,
        "dropped_frames": 0,
        "duplicate_frames": 0,
        "black_frames": 0,
        "feed_interruptions": 0,
        "delayed_frames": 0,
    }
    frame_sizes: dict[tuple[str, int], tuple[int, int]] = {}
    sequence_timestamps: dict[str, list[int]] = {}
    fault_events: dict[tuple[str, int], list[str]] = {}
    run_deadline = time.monotonic() + benchmark.max_run_seconds
    try:
        runtime = start_runtime(system, socket_dir, run_dir)
        collector = OutputCollector(
            runtime.process,
            system.readiness_pattern,
            benchmark.max_output_records,
            benchmark.max_output_line_bytes,
            benchmark.max_total_output_bytes,
            benchmark.max_output_records_per_second,
            frame_sizes,
            benchmark.thresholds.out_of_bounds,
        )
        collector.start()
        monitor = ResourceMonitor(runtime.process, cidfile=runtime.cidfile)
        monitor.start()
        if system.runtime_type == "docker":
            verify_docker_isolation(runtime, socket_dir)
        if not _wait_for_readiness(collector, runtime, system.readiness_timeout_seconds):
            exit_code = runtime.process.poll()
            outcome.exit_code = exit_code
            if collector.flooded.is_set():
                outcome.errors.append(f"output limit exceeded: {collector.limit_reason}")
            elif exit_code is None:
                outcome.errors.append("readiness timeout")
                outcome.timed_out = True
            else:
                outcome.errors.append("SUT exited before readiness")
                outcome.crashed = exit_code != 0
        else:
            outcome.startup_time_ms = (time.monotonic_ns() - started_ns) / 1_000_000
            connection, _ = server.accept()
            with connection:
                connection.settimeout(2)
                ground_truth, feed_counters, sequence_timestamps, fault_events = _deliver_scenarios(
                    connection, scenarios, benchmark, run_deadline, frame_sizes, monitor, collector
                )
            exit_code, forced = stop_runtime(runtime, system.grace_period_seconds)
            outcome.exit_code = exit_code
            outcome.timed_out = forced
            outcome.crashed = exit_code not in {0, None} and not forced
            outcome.status = "completed" if exit_code == 0 and not forced else "failed"
    except (OSError, RuntimeFailure, TimeoutError) as exc:
        outcome.errors.append(str(exc))
        outcome.timed_out = isinstance(exc, TimeoutError) or outcome.timed_out
        process_had_exited = runtime is not None and runtime.process.poll() is not None
        if runtime is not None:
            stop_runtime(runtime, 0)
        if runtime is not None:
            outcome.exit_code = runtime.process.poll()
            outcome.crashed = process_had_exited and outcome.exit_code not in {0, None} and not outcome.timed_out
    finally:
        server.close()
        if monitor is not None:
            monitor.stop()
            monitor.add_gpu_snapshot()
        if collector is not None:
            collector.join()
            collected, collector_errors, stderr = collector.snapshot()
            if collector.first_output_timestamp_ns is not None:
                outcome.time_to_first_output_ms = (collector.first_output_timestamp_ns - started_ns) / 1_000_000
            if collector.flooded.is_set():
                outcome.status = "failed"
                message = f"output limit exceeded: {collector.limit_reason}"
                if message not in outcome.errors:
                    outcome.errors.append(message)
        if runtime is not None:
            stop_runtime(runtime, 0)
            outcome.resolved_image = runtime.resolved_image
            cleanup_runtime(runtime)
        shutil.rmtree(socket_dir, ignore_errors=True)

    runtime_seconds = (time.monotonic_ns() - started_ns) / 1_000_000_000
    if not ground_truth:
        # Preserve scoreable ground truth even for startup/crash failures.
        cursor = started_ns
        for scenario in scenarios:
            ground_truth.extend(_shift_ground_truth(scenario, cursor, benchmark.playback_rate))
            cursor += int((scenario.frames[-1].relative_timestamp_ns + 100_000_000) / benchmark.playback_rate)
    scenario_families = {scenario.frames[0].sequence_id: scenario.family for scenario in scenarios}
    metrics, matches = calculate_metrics(
        ground_truth,
        collected,
        benchmark.thresholds,
        sequence_timestamps=sequence_timestamps or None,
        scenario_families=scenario_families,
        fault_timestamps=set(fault_events),
        fault_events=fault_events,
    )
    metrics["latency"]["authoritative"] = benchmark.input_mode == "online_replay"
    metrics["latency"]["mode"] = benchmark.input_mode
    feed_counters["input_queue_depth"] = None
    feed_counters["input_queue_depth_available"] = False
    resource_data = (
        monitor.summary(runtime_seconds)
        if monitor
        else {
            "sample_count": 0,
            "runtime_seconds": runtime_seconds,
            "gpu_available": False,
            "over_time": [],
        }
    )
    metrics["long_running_stability"] = evaluate_long_run_assertions(
        metrics["long_running_stability"], resource_data, benchmark.long_run_assertions
    )
    resources_csv = run_dir / "resources.csv"
    if monitor:
        monitor.write_csv(resources_csv)
    else:
        resources_csv.write_text("elapsed_ms,cpu_percent,memory_bytes\n")
    raw_output = run_dir / "system-output.jsonl"
    raw_output.write_text("".join(json.dumps(item.as_dict(), sort_keys=True) + "\n" for item in collected))
    (run_dir / "matching-decisions.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "sequence_id": match.sequence_id,
                    "source_timestamp_ns": match.source_timestamp_ns,
                    "target_id": match.target_id,
                    "track_id": match.track_id,
                    "iou": match.iou,
                    "center_error_px": match.center_error_px,
                    "support": match.output["support"],
                    "state": match.output["state"],
                },
                sort_keys=True,
            )
            + "\n"
            for match in matches
        )
    )
    (run_dir / "ground-truth.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in ground_truth))
    outcome.errors.extend(collector_errors)
    system_error_count = sum(item.system_record.get("event") == "system_error" for item in collected)
    if collector_errors or system_error_count:
        outcome.status = "failed"
    if system_error_count:
        outcome.errors.append(f"SUT emitted {system_error_count} system_error record(s)")
    findings = generate_findings(metrics, asdict(outcome), resource_data, collector_errors)
    comparison_fingerprint, comparison_inputs = _comparison_fingerprint(benchmark, scenarios)
    command = f"cvbench run --benchmark {benchmark.path} --system {system.path} --output {Path(output_root).resolve()}"
    report: dict[str, Any] = {
        "schema_version": "cvbench.report/v1",
        "run_id": run_dir.name,
        "started_at": started_wall,
        "mode": benchmark.input_mode,
        "benchmark": {"id": benchmark.id, "version": benchmark.version},
        "system": {"id": system.id, "revision": system.revision, "runtime": system.runtime_type},
        "outcome": asdict(outcome),
        "feed": feed_counters,
        "metrics": metrics,
        "resources": resource_data,
        "runtime_isolation": runtime.isolation
        if runtime
        else {"runtime": system.runtime_type, "status": "not_started", "future_frame_isolation": False},
        "findings": findings,
        "comparison": [],
        "provenance": {
            "benchmark_path": str(benchmark.path),
            "benchmark_sha256": _sha256(benchmark.path),
            "system_path": str(system.path),
            "system_sha256": _sha256(system.path),
            "scenario_manifests": [str(path) for path in benchmark.scenarios],
            "resolved_container_image": outcome.resolved_image,
            "resolved_container_image_id": runtime.resolved_image_id if runtime else None,
            "executed_container_image_id": (
                runtime.isolation.get("image_identity", {}).get("executed_image_id") if runtime else None
            ),
            "command": command,
            "matching": {
                "algorithm": "deterministic Hungarian assignment",
                "minimum_iou": benchmark.thresholds.minimum_match_iou,
                "maximum_center_error_px": benchmark.thresholds.max_match_center_error_px,
                "class_agnostic": benchmark.thresholds.class_agnostic,
            },
            "external_clock": "time.monotonic_ns",
            "comparison_fingerprint": comparison_fingerprint,
            "comparison_inputs": comparison_inputs,
            "platform": {"os": os.name},
        },
        "diagnostics": {
            "collector_errors": collector_errors,
            "sut_stderr": stderr,
            "match_count": len(matches),
        },
        "limitations": [
            "GPU metrics are reported only when nvidia-smi is available.",
            "Version 1 evidence overlays use synthetic source frames and MP4V when the local codec is available.",
            "Statistical comparison confidence is sample-count based; confidence intervals are not inferred.",
        ],
    }
    if benchmark.baseline_report:
        baseline = json.loads(benchmark.baseline_report.read_text())
        report["comparison"] = compare_reports(baseline, report)
    report_json, report_html = write_report_files(run_dir, report)
    if benchmark.reporting["generate_failure_packets"]:
        generate_evidence_packets(
            run_dir, findings, scenarios, ground_truth, collected, matches, resources_csv, command
        )
    return RunArtifacts(run_dir, report_json, report_html, raw_output, resources_csv)
