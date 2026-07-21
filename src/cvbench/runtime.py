from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import SystemConfig
from .errors import RuntimeFailure


@dataclass
class StartedRuntime:
    process: subprocess.Popen[str]
    cidfile: Path | None
    resolved_image: str | None
    command: list[str]
    isolation: dict[str, object]
    process_group_id: int | None = None


def _resolve_image(image: str) -> str:
    if not shutil.which("docker"):
        raise RuntimeFailure("Docker runtime requested, but docker is not installed")
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}|{{.Id}}", image],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise RuntimeFailure(f"Docker image is unavailable: {image}: {result.stderr.strip()}")
    digests, _, image_id = result.stdout.strip().partition("|")
    if digests not in {"", "null", "[]"}:
        values = json.loads(digests)
        if values:
            return str(values[0])
    if image_id.startswith("sha256:"):
        return image_id
    raise RuntimeFailure(f"could not resolve an immutable digest for Docker image {image}")


def start_runtime(config: SystemConfig, socket_dir: Path, run_dir: Path) -> StartedRuntime:
    environment = os.environ.copy()
    environment.update(config.environment)
    environment["CVBENCH_INPUT_SOCKET"] = str(socket_dir / "input.sock")
    cidfile: Path | None = None
    resolved_image: str | None = None
    if config.runtime_type == "local":
        command = [sys.executable if value == "{python}" else value for value in config.command]
        cwd = config.path.parent.parent
    else:
        assert config.image is not None
        resolved_image = _resolve_image(config.image)
        cidfile = run_dir / "container.cid"
        command = [
            "docker",
            "run",
            "--rm",
            "--cidfile",
            str(cidfile),
            "--network",
            "none",
            "--volume",
            f"{socket_dir}:/run/cvbench",
            "--env",
            "CVBENCH_INPUT_SOCKET=/run/cvbench/input.sock",
        ]
        cpu_limit = config.resources.get("cpu_limit")
        memory_limit = config.resources.get("memory_limit_mb")
        if cpu_limit:
            command.extend(["--cpus", str(cpu_limit)])
        if memory_limit:
            command.extend(["--memory", f"{memory_limit}m"])
        for key, value in sorted(config.environment.items()):
            command.extend(["--env", f"{key}={value}"])
        command.extend([config.image, *config.command])
        cwd = config.path.parent.parent
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=config.runtime_type == "local",
        )
    except OSError as exc:
        raise RuntimeFailure(f"could not start SUT: {exc}") from exc
    isolation: dict[str, object] = {
        "runtime": config.runtime_type,
        "requested": {
            "cpu_limit": config.resources.get("cpu_limit"),
            "memory_limit_mb": config.resources.get("memory_limit_mb"),
            "network_access": config.resources.get("network_access", False),
        },
        "status": "not_enforced_local" if config.runtime_type == "local" else "pending_verification",
        "future_frame_isolation": config.runtime_type == "docker",
    }
    return StartedRuntime(
        process,
        cidfile,
        resolved_image,
        command,
        isolation,
        process.pid if config.runtime_type == "local" else None,
    )


def _signal_process_group(runtime: StartedRuntime, sig: signal.Signals) -> None:
    if runtime.process_group_id is None:
        if runtime.process.poll() is None:
            runtime.process.send_signal(sig)
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(runtime.process_group_id, sig)


def stop_runtime(runtime: StartedRuntime, grace: float) -> tuple[int | None, bool]:
    """Stop the runtime and every local descendant owned by its process group."""
    forced = False
    try:
        exit_code = runtime.process.wait(timeout=max(0, grace))
    except subprocess.TimeoutExpired:
        forced = True
        _signal_process_group(runtime, signal.SIGTERM)
        try:
            exit_code = runtime.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _signal_process_group(runtime, signal.SIGKILL)
            exit_code = runtime.process.wait(timeout=2)
    if runtime.process_group_id is not None:
        # The direct child may exit while descendants remain. They are still
        # runner-owned and must not survive any benchmark outcome.
        _signal_process_group(runtime, signal.SIGTERM)
        time.sleep(0.02)
        _signal_process_group(runtime, signal.SIGKILL)
    return exit_code, forced


def verify_docker_isolation(runtime: StartedRuntime, socket_dir: Path, timeout: float = 10) -> dict[str, object]:
    if runtime.cidfile is None:
        return runtime.isolation
    deadline = time.monotonic() + timeout
    container_id = ""
    while time.monotonic() < deadline:
        if runtime.cidfile.exists():
            container_id = runtime.cidfile.read_text().strip()
            if container_id:
                break
        time.sleep(0.02)
    if not container_id:
        runtime.isolation.update({"status": "verification_failed", "error": "container ID was not created"})
        return runtime.isolation
    result = subprocess.run(
        ["docker", "inspect", container_id], capture_output=True, text=True, timeout=10, check=False
    )
    if result.returncode:
        runtime.isolation.update({"status": "verification_failed", "error": result.stderr.strip()})
        return runtime.isolation
    inspected = json.loads(result.stdout)[0]
    host = inspected.get("HostConfig", {})
    mounts = inspected.get("Mounts", [])
    mount_pairs = [{"source": item.get("Source"), "destination": item.get("Destination")} for item in mounts]
    requested = runtime.isolation["requested"]
    assert isinstance(requested, dict)
    expected_cpu = requested.get("cpu_limit")
    expected_memory = requested.get("memory_limit_mb")
    cpu_applied = host.get("NanoCpus", 0) / 1_000_000_000 if host.get("NanoCpus") else None
    memory_applied = host.get("Memory", 0) / (1024 * 1024) if host.get("Memory") else None
    mount_ok = (
        len(mount_pairs) == 1
        and Path(str(mount_pairs[0]["source"])).resolve() == socket_dir.resolve()
        and mount_pairs[0]["destination"] == "/run/cvbench"
    )
    network_ok = host.get("NetworkMode") == "none"
    limits_ok = (expected_cpu is None or float(expected_cpu) == cpu_applied) and (
        expected_memory is None or float(expected_memory) == memory_applied
    )
    runtime.isolation.update(
        {
            "status": "verified" if mount_ok and network_ok and limits_ok else "verification_failed",
            "container_id": container_id,
            "mounts": mount_pairs,
            "network_mode": host.get("NetworkMode"),
            "applied": {"cpu_limit": cpu_applied, "memory_limit_mb": memory_applied},
            "future_frame_isolation": mount_ok and network_ok,
        }
    )
    return runtime.isolation


def cleanup_runtime(runtime: StartedRuntime) -> None:
    if runtime.cidfile is None or not runtime.cidfile.exists() or not shutil.which("docker"):
        return
    container_id = runtime.cidfile.read_text().strip()
    if container_id:
        subprocess.run(
            ["docker", "rm", "--force", container_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
