import json
import os
import stat
from pathlib import Path
from subprocess import Popen
from unittest.mock import MagicMock, patch

from cvbench.config import load_system
from cvbench.runner import _restrict_socket_access
from cvbench.runtime import ResolvedImage, StartedRuntime, start_runtime, verify_docker_isolation

ROOT = Path(__file__).parents[1]
EXPECTED_UID = os.getuid() or 65532
EXPECTED_GID = os.getgid() if os.getuid() else 65532


def test_docker_command_mounts_only_socket_and_disables_network(tmp_path: Path) -> None:
    config = load_system(ROOT / "systems/example-good-docker.yaml")
    socket_dir = tmp_path / "socket"
    socket_dir.mkdir()
    socket_path = socket_dir / "input.sock"
    socket_path.touch()
    _restrict_socket_access(socket_dir, socket_path)
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
    assert command[command.index("--user") + 1] == f"{EXPECTED_UID}:{EXPECTED_GID}"
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
    assert runtime.isolation["expected_container_user"] == f"{EXPECTED_UID}:{EXPECTED_GID}"
    assert runtime.isolation["ground_truth_access"] is False
    assert runtime.isolation["repository_access"] is False
    assert runtime.isolation["media_access"] is False
    assert runtime.isolation["socket_access"] == {
        "owner_uid": EXPECTED_UID,
        "owner_gid": EXPECTED_GID,
        "directory_mode": "0o700",
        "socket_mode": "0o600",
    }


def test_control_plane_job_id_adds_a_unique_docker_label(tmp_path: Path) -> None:
    config = load_system(ROOT / "systems/example-good-docker.yaml")
    socket_dir = tmp_path / "socket"
    socket_dir.mkdir()
    socket_path = socket_dir / "input.sock"
    socket_path.touch()
    _restrict_socket_access(socket_dir, socket_path)
    job_id = "12345678-1234-4123-8123-123456789abc"
    with (
        patch.dict(os.environ, {"CVBENCH_DOCKER_JOB_ID": job_id}),
        patch(
            "cvbench.runtime._resolve_image",
            return_value=ResolvedImage("registry.example/good@sha256:immutable", "sha256:image-id"),
        ),
        patch("cvbench.runtime.subprocess.Popen") as popen,
    ):
        popen.return_value = MagicMock(spec=Popen)
        runtime = start_runtime(config, socket_dir, tmp_path)

    label_index = runtime.command.index("--label")
    assert runtime.command[label_index + 1] == f"cvbench.control-plane-job={job_id}"


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
            "expected_container_user": f"{EXPECTED_UID}:{EXPECTED_GID}",
            "socket_access": {
                "owner_uid": EXPECTED_UID,
                "owner_gid": EXPECTED_GID,
                "directory_mode": "0o700",
                "socket_mode": "0o600",
            },
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
            "Config": {"Image": "sha256:image", "User": f"{EXPECTED_UID}:{EXPECTED_GID}"},
        }
    ]
    result = MagicMock(returncode=0, stdout=json.dumps(inspected), stderr="")
    with patch("cvbench.runtime.subprocess.run", return_value=result):
        evidence = verify_docker_isolation(runtime, tmp_path / "socket")
    assert evidence["status"] == "verified"
    assert evidence["applied"] == {"cpu_limit": 4.0, "memory_limit_mb": 2048.0}
    assert evidence["future_frame_isolation"] is True
    assert evidence["ground_truth_access"] is False
    assert evidence["repository_access"] is False
    assert evidence["media_access"] is False
    assert evidence["image_identity_verified"] is True
    assert evidence["container_user_alignment_verified"] is True
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

    inspected[0]["Mounts"][0]["Source"] = str(tmp_path / "socket")
    inspected[0]["Config"]["User"] = "999:999"
    wrong_user = MagicMock(returncode=0, stdout=json.dumps(inspected), stderr="")
    with patch("cvbench.runtime.subprocess.run", return_value=wrong_user):
        evidence = verify_docker_isolation(runtime, tmp_path / "socket")
    assert evidence["status"] == "verification_failed"
    assert evidence["container_user_alignment_verified"] is False


def test_benchmark_socket_permissions_are_owner_only(tmp_path: Path) -> None:
    socket_dir = tmp_path / "socket"
    socket_dir.mkdir()
    socket_path = socket_dir / "input.sock"
    socket_path.touch()
    _restrict_socket_access(socket_dir, socket_path)
    assert stat.S_IMODE(socket_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
    assert socket_dir.stat().st_uid == os.getuid()
    assert socket_path.stat().st_uid == os.getuid()


def test_example_image_does_not_copy_scenarios_or_workspace() -> None:
    dockerfile = (ROOT / "examples/Dockerfile.good").read_text()
    dockerignore = (ROOT / ".dockerignore").read_text()
    assert "COPY ." not in dockerfile
    assert "scenarios" not in dockerfile
    assert "USER cvbench" in dockerfile
    assert dockerignore.splitlines()[0] == "*"
    assert "!src/**" in dockerignore


def test_real_video_image_has_no_scenario_or_media_mount_contract() -> None:
    dockerfile = (ROOT / "examples/Dockerfile.real-video-baseline").read_text()
    assert "COPY ." not in dockerfile
    assert "scenarios" not in dockerfile
    assert "data/" not in dockerfile
    assert "real_video_baseline" in dockerfile
