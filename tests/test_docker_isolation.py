import json
from pathlib import Path
from subprocess import Popen
from unittest.mock import MagicMock, patch

from cvbench.config import load_system
from cvbench.runtime import ResolvedImage, StartedRuntime, start_runtime, verify_docker_isolation

ROOT = Path(__file__).parents[1]


def test_docker_command_mounts_only_socket_and_disables_network(tmp_path: Path) -> None:
    config = load_system(ROOT / "systems/example-good-docker.yaml")
    socket_dir = tmp_path / "socket"
    socket_dir.mkdir()
    with (
        patch(
            "cvbench.runtime._resolve_image",
            return_value=ResolvedImage("registry.example/good@sha256:immutable", "sha256:image-id"),
        ),
        patch("cvbench.runtime.subprocess.Popen") as popen,
    ):
        popen.return_value = MagicMock(spec=Popen)
        runtime = start_runtime(config, socket_dir, tmp_path)
    command = runtime.command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--volume") + 1] == f"{socket_dir}:/run/cvbench"
    assert str(ROOT) not in " ".join(command)
    assert "registry.example/good@sha256:immutable" in command
    assert config.image not in command
    assert runtime.resolved_image_id == "sha256:image-id"
    assert runtime.isolation["status"] == "pending_verification"
    assert runtime.isolation["expected_mount"] == {
        "source": str(socket_dir.resolve()),
        "destination": "/run/cvbench",
    }


def test_docker_inspection_distinguishes_applied_limits(tmp_path: Path) -> None:
    cidfile = tmp_path / "container.cid"
    cidfile.write_text("abc")
    process = MagicMock(spec=Popen)
    runtime = StartedRuntime(
        process=process,
        cidfile=cidfile,
        resolved_image="sha256:image",
        resolved_image_id="sha256:image-id",
        command=[],
        isolation={
            "requested": {"cpu_limit": 4, "memory_limit_mb": 2048, "network_access": False},
            "status": "pending_verification",
            "future_frame_isolation": True,
            "expected_mount": {"source": str(tmp_path / "socket"), "destination": "/run/cvbench"},
            "image_identity": {
                "configured_reference": "good:latest",
                "resolved_reference": "sha256:image",
                "resolved_image_id": "sha256:image-id",
                "executed_reference": None,
                "executed_image_id": None,
            },
        },
    )
    inspected = [
        {
            "HostConfig": {"NanoCpus": 4_000_000_000, "Memory": 2048 * 1024 * 1024, "NetworkMode": "none"},
            "Mounts": [{"Source": str(tmp_path / "socket"), "Destination": "/run/cvbench"}],
            "Image": "sha256:image-id",
            "Config": {"Image": "sha256:image"},
        }
    ]
    result = MagicMock(returncode=0, stdout=json.dumps(inspected), stderr="")
    with patch("cvbench.runtime.subprocess.run", return_value=result):
        evidence = verify_docker_isolation(runtime, tmp_path / "socket")
    assert evidence["status"] == "verified"
    assert evidence["applied"] == {"cpu_limit": 4.0, "memory_limit_mb": 2048.0}
    assert evidence["future_frame_isolation"] is True
    assert evidence["image_identity_verified"] is True
    assert evidence["image_identity"]["executed_image_id"] == "sha256:image-id"

    inspected[0]["Image"] = "sha256:different-image"
    mismatch = MagicMock(returncode=0, stdout=json.dumps(inspected), stderr="")
    with patch("cvbench.runtime.subprocess.run", return_value=mismatch):
        evidence = verify_docker_isolation(runtime, tmp_path / "socket")
    assert evidence["status"] == "verification_failed"
    assert evidence["image_identity_verified"] is False

    inspected[0]["Image"] = "sha256:image-id"
    inspected[0]["Mounts"][0]["Source"] = str(tmp_path / "wrong-socket")
    wrong_mount = MagicMock(returncode=0, stdout=json.dumps(inspected), stderr="")
    with patch("cvbench.runtime.subprocess.run", return_value=wrong_mount):
        evidence = verify_docker_isolation(runtime, tmp_path / "socket")
    assert evidence["status"] == "verification_failed"
    assert evidence["future_frame_isolation"] is False


def test_example_image_does_not_copy_scenarios_or_workspace() -> None:
    dockerfile = (ROOT / "examples/Dockerfile.good").read_text()
    dockerignore = (ROOT / ".dockerignore").read_text()
    assert "COPY ." not in dockerfile
    assert "scenarios" not in dockerfile
    assert "USER cvbench" in dockerfile
    assert dockerignore.splitlines()[0] == "*"
    assert "!src/**" in dockerignore
