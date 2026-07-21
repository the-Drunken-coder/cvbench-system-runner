import json
from pathlib import Path
from subprocess import Popen
from unittest.mock import MagicMock, patch

from cvbench.config import load_system
from cvbench.runtime import StartedRuntime, start_runtime, verify_docker_isolation

ROOT = Path(__file__).parents[1]


def test_docker_command_mounts_only_socket_and_disables_network(tmp_path: Path) -> None:
    config = load_system(ROOT / "systems/example-good-docker.yaml")
    socket_dir = tmp_path / "socket"
    socket_dir.mkdir()
    with (
        patch("cvbench.runtime._resolve_image", return_value="sha256:immutable"),
        patch("cvbench.runtime.subprocess.Popen") as popen,
    ):
        popen.return_value = MagicMock(spec=Popen)
        runtime = start_runtime(config, socket_dir, tmp_path)
    command = runtime.command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--volume") + 1] == f"{socket_dir}:/run/cvbench"
    assert str(ROOT) not in " ".join(command)
    assert runtime.isolation["status"] == "pending_verification"


def test_docker_inspection_distinguishes_applied_limits(tmp_path: Path) -> None:
    cidfile = tmp_path / "container.cid"
    cidfile.write_text("abc")
    process = MagicMock(spec=Popen)
    runtime = StartedRuntime(
        process=process,
        cidfile=cidfile,
        resolved_image="sha256:image",
        command=[],
        isolation={
            "requested": {"cpu_limit": 4, "memory_limit_mb": 2048, "network_access": False},
            "status": "pending_verification",
            "future_frame_isolation": True,
        },
    )
    inspected = [
        {
            "HostConfig": {"NanoCpus": 4_000_000_000, "Memory": 2048 * 1024 * 1024, "NetworkMode": "none"},
            "Mounts": [{"Source": str(tmp_path / "socket"), "Destination": "/run/cvbench"}],
        }
    ]
    result = MagicMock(returncode=0, stdout=json.dumps(inspected), stderr="")
    with patch("cvbench.runtime.subprocess.run", return_value=result):
        evidence = verify_docker_isolation(runtime, tmp_path / "socket")
    assert evidence["status"] == "verified"
    assert evidence["applied"] == {"cpu_limit": 4.0, "memory_limit_mb": 2048.0}
    assert evidence["future_frame_isolation"] is True


def test_example_image_does_not_copy_scenarios_or_workspace() -> None:
    dockerfile = (ROOT / "examples/Dockerfile.good").read_text()
    dockerignore = (ROOT / ".dockerignore").read_text()
    assert "COPY ." not in dockerfile
    assert "scenarios" not in dockerfile
    assert dockerignore.splitlines()[0] == "*"
    assert "!src/**" in dockerignore
