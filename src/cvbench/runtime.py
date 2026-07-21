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

UNPRIVILEGED_SOCKET_UID = 65532


@dataclass
class StartedRuntime:
    process: subprocess.Popen[str]
    cidfile: Path | None
    resolved_image: str | None
    command: list[str]
    isolation: dict[str, object]
    process_group_id: int | None = None
    resolved_image_id: str | None = None


@dataclass(frozen=True)
class ResolvedImage:
    reference: str
    image_id: str


def _align_docker_socket_owner(socket_dir: Path) -> tuple[int, int]:
    socket_path = socket_dir / "input.sock"
    owner = socket_path.stat()
    uid, gid = owner.st_uid, owner.st_gid
    if uid == 0:
        uid = gid = UNPRIVILEGED_SOCKET_UID
        os.chown(socket_dir, uid, gid)
        os.chown(socket_path, uid, gid)
    return uid, gid


def _resolve_image(image: str) -> ResolvedImage:
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
    if not image_id.startswith("sha256:"):
        raise RuntimeFailure(f"could not resolve an immutable image ID for Docker image {image}")
    if digests not in {"", "null", "[]"}:
        values = json.loads(digests)
        if values:
            return ResolvedImage(str(values[0]), image_id)
    return ResolvedImage(image_id, image_id)


def start_runtime(config: SystemConfig, socket_dir: Path, run_dir: Path) -> StartedRuntime:
    environment = os.environ.copy()
    environment.update(config.environment)
    environment["CVBENCH_INPUT_SOCKET"] = str(socket_dir / "input.sock")
    cidfile: Path | None = None
    resolved_image: str | None = None
    resolved_image_id: str | None = None
    container_user: str | None = None
    socket_access: dict[str, object] | None = None
    if config.runtime_type == "local":
        command = [sys.executable if value == "{python}" else value for value in config.command]
        cwd = config.path.parent.parent
    else:
        assert config.image is not None
        resolution = _resolve_image(config.image)
        resolved_image = resolution.reference
        resolved_image_id = resolution.image_id
        socket_uid, socket_gid = _align_docker_socket_owner(socket_dir)
        container_user = f"{socket_uid}:{socket_gid}"
        socket_access = {
            "owner_uid": socket_uid,
            "owner_gid": socket_gid,
            "directory_mode": oct(socket_dir.stat().st_mode & 0o777),
            "socket_mode": oct((socket_dir / "input.sock").stat().st_mode & 0o777),
        }
        cidfile = run_dir / "container.cid"
        command = [
            "docker",
            "run",
            "--rm",
            "--cidfile",
            str(cidfile),
            "--network",
            "none",
            "--user",
            container_user,
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
        command.extend([resolved_image, *config.command])
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
        "expected_container_user": container_user,
        "socket_access": socket_access,
        "expected_mount": (
            {"source": str(socket_dir), "destination": "/run/cvbench"}
            if config.runtime_type == "docker"
            else None
        ),
        "image_identity": {
            "configured_reference": config.image,
            "resolved_reference": resolved_image,
            "resolved_image_id": resolved_image_id,
            "executed_reference": None,
            "executed_image_id": None,
        },
    }
    return StartedRuntime(
        process,
        cidfile,
        resolved_image,
        command,
        isolation,
        process.pid if config.runtime_type == "local" else None,
        resolved_image_id,
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
    executed_image_id = inspected.get("Image")
    container_config = inspected.get("Config", {})
    executed_reference = container_config.get("Image")
    executed_user = container_config.get("User")
    image_identity = runtime.isolation["image_identity"]
    assert isinstance(image_identity, dict)
    image_identity.update(
        {
            "executed_reference": executed_reference,
            "executed_image_id": executed_image_id,
        }
    )
    identity_ok = (
        executed_image_id == runtime.resolved_image_id and executed_reference == runtime.resolved_image
    )
    user_ok = executed_user == runtime.isolation.get("expected_container_user")
    expected_mount = runtime.isolation.get("expected_mount")
    expected_mount_ok = (
        isinstance(expected_mount, dict)
        and expected_mount.get("destination") == "/run/cvbench"
        and Path(str(expected_mount.get("source"))).resolve() == socket_dir.resolve()
    )
    mount_ok = expected_mount_ok and mount_pairs == [expected_mount]
    network_ok = host.get("NetworkMode") == "none"
    limits_ok = (expected_cpu is None or float(expected_cpu) == cpu_applied) and (
        expected_memory is None or float(expected_memory) == memory_applied
    )
    runtime.isolation.update(
        {
            "status": "verified"
            if mount_ok and network_ok and limits_ok and identity_ok and user_ok
            else "verification_failed",
            "container_id": container_id,
            "mounts": mount_pairs,
            "network_mode": host.get("NetworkMode"),
            "applied": {"cpu_limit": cpu_applied, "memory_limit_mb": memory_applied},
            "future_frame_isolation": mount_ok and network_ok,
            "image_identity_verified": identity_ok,
            "executed_container_user": executed_user,
            "container_user_alignment_verified": user_ok,
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
