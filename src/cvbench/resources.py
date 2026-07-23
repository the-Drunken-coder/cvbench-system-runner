from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import psutil


def parse_size(value: str) -> float:
    match = re.fullmatch(r"\s*([0-9.]+)\s*([kKmMgGtT]?i?[bB])?\s*", value)
    if not match:
        raise ValueError(f"invalid size: {value}")
    number = float(match.group(1))
    unit = (match.group(2) or "B").lower()
    factors = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
        "tb": 1000**4,
        "tib": 1024**4,
    }
    return number * factors[unit]


def _pair_sizes(value: str) -> tuple[float, float]:
    left, _, right = value.partition("/")
    return parse_size(left.strip()), parse_size(right.strip())


def parse_docker_stats(payload: str | dict[str, Any]) -> dict[str, float | int | None]:
    data = json.loads(payload) if isinstance(payload, str) else payload
    memory, memory_limit = _pair_sizes(str(data.get("MemUsage", "0B / 0B")))
    network_rx, network_tx = _pair_sizes(str(data.get("NetIO", "0B / 0B")))
    disk_read, disk_write = _pair_sizes(str(data.get("BlockIO", "0B / 0B")))
    return {
        "cpu_percent": float(str(data.get("CPUPerc", "0")).rstrip("%")),
        "memory_bytes": memory,
        "memory_limit_bytes": memory_limit,
        "disk_read_bytes": disk_read,
        "disk_write_bytes": disk_write,
        "network_rx_bytes": network_rx,
        "network_tx_bytes": network_tx,
        "process_count": int(data.get("PIDs", 0) or 0),
        "thread_count": None,
    }


def parse_cpu_stat(payload: str) -> float | None:
    for line in payload.splitlines():
        key, _, value = line.partition(" ")
        if key == "usage_usec":
            return int(value) / 1_000_000
        if key == "usage_nsec":
            return int(value) / 1_000_000_000
    return None


class ResourceMonitor:
    def __init__(self, process: subprocess.Popen[str], interval_seconds: float = 0.1, cidfile: Path | None = None):
        self.process = process
        self.interval_seconds = interval_seconds
        self.cidfile = cidfile
        self.samples: list[dict[str, Any]] = []
        self.context: dict[str, Any] = {
            "phase": "startup",
            "scenario": None,
            "target_count": None,
            "fault_injection": False,
        }
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._started_ns = time.monotonic_ns()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(3)

    def set_context(
        self,
        scenario: str | None,
        target_count: int | None,
        fault_injection: bool,
        *,
        phase: str = "delivery",
    ) -> None:
        self.context = {
            "phase": phase,
            "scenario": scenario,
            "target_count": target_count,
            "fault_injection": fault_injection,
        }

    def _run(self) -> None:
        if self.cidfile is not None:
            self._run_docker()
        else:
            self._run_process()

    def _run_process(self) -> None:
        try:
            root = psutil.Process(self.process.pid)
            root.cpu_percent(None)
        except psutil.Error:
            return
        while not self._stop.wait(self.interval_seconds):
            try:
                processes = [root, *root.children(recursive=True)]
                cpu = memory = read_bytes = write_bytes = cpu_time = 0.0
                threads = 0
                alive = 0
                for process in processes:
                    try:
                        cpu += process.cpu_percent(None)
                        memory += process.memory_info().rss
                        if hasattr(process, "io_counters"):
                            io = process.io_counters()
                            read_bytes += io.read_bytes
                            write_bytes += io.write_bytes
                        times = process.cpu_times()
                        cpu_time += times.user + times.system
                        threads += process.num_threads()
                        alive += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                self.samples.append(
                    {
                        "elapsed_ms": (time.monotonic_ns() - self._started_ns) / 1_000_000,
                        "cpu_percent": cpu,
                        "cpu_time_seconds": cpu_time,
                        "memory_bytes": memory,
                        "disk_read_bytes": read_bytes,
                        "disk_write_bytes": write_bytes,
                        "network_rx_bytes": None,
                        "network_tx_bytes": None,
                        "process_count": alive,
                        "thread_count": threads,
                        "gpu_percent": None,
                        "vram_bytes": None,
                        **self.context,
                    }
                )
            except psutil.Error:
                break

    def _run_docker(self) -> None:
        deadline = time.monotonic() + 10
        container_id = ""
        while time.monotonic() < deadline and not self._stop.is_set():
            if self.cidfile.exists():
                container_id = self.cidfile.read_text().strip()
                if container_id:
                    break
            self._stop.wait(0.05)
        if not container_id:
            return
        while not self._stop.wait(self.interval_seconds):
            result = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}", container_id],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode:
                if self.process.poll() is not None:
                    break
                continue
            try:
                sample = parse_docker_stats(result.stdout.strip())
            except (ValueError, json.JSONDecodeError, KeyError):
                continue
            cpu_stat = subprocess.run(
                ["docker", "exec", container_id, "cat", "/sys/fs/cgroup/cpu.stat"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            sample.update(
                {
                    "elapsed_ms": (time.monotonic_ns() - self._started_ns) / 1_000_000,
                    "cpu_time_seconds": parse_cpu_stat(cpu_stat.stdout) if cpu_stat.returncode == 0 else None,
                    "gpu_percent": None,
                    "vram_bytes": None,
                    **self.context,
                }
            )
            self.samples.append(sample)

    def add_gpu_snapshot(self) -> None:
        if not shutil.which("nvidia-smi"):
            return
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode or not result.stdout.strip():
            return
        try:
            rows = [[float(value.strip()) for value in line.split(",")] for line in result.stdout.splitlines()]
            gpu = sum(row[0] for row in rows) / len(rows)
            vram = sum(row[1] for row in rows) * 1024 * 1024
        except (ValueError, IndexError):
            return
        if self.samples:
            self.samples[-1]["gpu_percent"] = gpu
            self.samples[-1]["vram_bytes"] = vram

    def summary(self, runtime_seconds: float) -> dict[str, Any]:
        memory = [float(sample["memory_bytes"]) for sample in self.samples if sample.get("memory_bytes") is not None]
        cpu = [float(sample["cpu_percent"]) for sample in self.samples if sample.get("cpu_percent") is not None]
        vram = [float(sample["vram_bytes"]) for sample in self.samples if sample.get("vram_bytes") is not None]
        grouped_scenario: dict[str, list[dict[str, Any]]] = {}
        grouped_targets: dict[str, list[dict[str, Any]]] = {}
        grouped_phase: dict[str, list[dict[str, Any]]] = {}
        for sample in self.samples:
            grouped_scenario.setdefault(str(sample.get("scenario") or "startup"), []).append(sample)
            grouped_targets.setdefault(str(sample.get("target_count") or 0), []).append(sample)
            grouped_phase.setdefault(str(sample.get("phase") or "startup"), []).append(sample)

        def grouped_summary(groups: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, float | int | None]]:
            result = {}
            for key, rows in sorted(groups.items()):
                cpu_times = [
                    float(row["cpu_time_seconds"])
                    for row in rows
                    if row.get("cpu_time_seconds") is not None
                ]
                result[key] = {
                    "sample_count": len(rows),
                    "average_cpu_percent": sum(float(row.get("cpu_percent") or 0) for row in rows) / len(rows),
                    "peak_ram_bytes": max((float(row.get("memory_bytes") or 0) for row in rows), default=None),
                    "cpu_time_delta_seconds": (
                        max(cpu_times) - min(cpu_times) if len(cpu_times) >= 2 else None
                    ),
                }
            return result

        phase_summary = grouped_summary(grouped_phase)
        return {
            "sample_count": len(self.samples),
            "runtime_seconds": runtime_seconds,
            "average_cpu_percent": sum(cpu) / len(cpu) if cpu else None,
            "peak_cpu_percent": max(cpu) if cpu else None,
            "cpu_time_seconds": max(
                (sample["cpu_time_seconds"] for sample in self.samples if sample.get("cpu_time_seconds") is not None),
                default=None,
            ),
            "average_ram_bytes": sum(memory) / len(memory) if memory else None,
            "peak_ram_bytes": max(memory) if memory else None,
            "gpu_available": bool(vram),
            "peak_vram_bytes": max(vram) if vram else None,
            "disk_read_bytes": max((sample.get("disk_read_bytes") or 0 for sample in self.samples), default=None),
            "disk_write_bytes": max((sample.get("disk_write_bytes") or 0 for sample in self.samples), default=None),
            "network_rx_bytes": max((sample.get("network_rx_bytes") or 0 for sample in self.samples), default=None),
            "network_tx_bytes": max((sample.get("network_tx_bytes") or 0 for sample in self.samples), default=None),
            "peak_process_count": max((sample.get("process_count") or 0 for sample in self.samples), default=None),
            "peak_thread_count": max((sample.get("thread_count") or 0 for sample in self.samples), default=None),
            "memory_growth_bytes": memory[-1] - memory[0] if len(memory) >= 2 else None,
            "by_scenario": grouped_summary(grouped_scenario),
            "by_target_count": grouped_summary(grouped_targets),
            "by_phase": phase_summary,
            "cpu_time_by_phase_seconds": {
                phase: summary["cpu_time_delta_seconds"]
                for phase, summary in phase_summary.items()
            },
            "fault_injection_samples": sum(bool(sample.get("fault_injection")) for sample in self.samples),
            "over_time": self.samples,
        }

    def write_csv(self, path: Path) -> None:
        fields = [
            "elapsed_ms",
            "cpu_percent",
            "cpu_time_seconds",
            "memory_bytes",
            "disk_read_bytes",
            "disk_write_bytes",
            "network_rx_bytes",
            "network_tx_bytes",
            "process_count",
            "thread_count",
            "gpu_percent",
            "vram_bytes",
            "phase",
            "scenario",
            "target_count",
            "fault_injection",
        ]
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.samples)
