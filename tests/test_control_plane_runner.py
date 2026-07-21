from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.run_control_plane_job import (
    IMAGE_PATTERN,
    SECRET_ENVIRONMENT_KEYS,
    callback_path,
    cleanup_benchmark_containers,
    execute_submission,
    main,
    sanitized_environment,
    validate_lease,
    write_system_config,
)

IMAGE = f"ghcr.io/example/tracker@sha256:{'a' * 64}"


def test_image_pattern_requires_digest_and_rejects_shell_like_input() -> None:
    assert IMAGE_PATTERN.fullmatch(IMAGE)
    assert not IMAGE_PATTERN.fullmatch("ghcr.io/example/tracker:latest")
    assert not IMAGE_PATTERN.fullmatch(f"ghcr.io/example/tracker@sha256:{'A' * 64}")
    assert not IMAGE_PATTERN.fullmatch(f"ghcr.io/example/tracker;curl@sha256:{'a' * 64}")


def test_validate_lease_revalidates_untrusted_control_plane_data() -> None:
    submission, token = validate_lease(
        {
            "submission": {
                "id": "12345678-1234-4123-8123-123456789abc",
                "image": IMAGE,
                "argv": ["python", "-m", "tracker"],
            },
            "lease": {"token": "b" * 64},
        }
    )
    assert submission["image"] == IMAGE
    assert token == "b" * 64

    with pytest.raises(ValueError, match="argv"):
        validate_lease(
            {
                "submission": {
                    "id": "12345678-1234-4123-8123-123456789abc",
                    "image": IMAGE,
                    "argv": ["python\nmalicious"],
                },
                "lease": {"token": "b" * 64},
            }
        )

    with pytest.raises(ValueError, match="submission id"):
        validate_lease(
            {
                "submission": {"id": "../other-job", "image": IMAGE, "argv": ["python"]},
                "lease": {"token": "b" * 64},
            }
        )


def test_generated_system_config_preserves_argv_without_a_shell(tmp_path: Path) -> None:
    path = tmp_path / "system.json"
    write_system_config(
        path,
        {
            "id": "12345678-1234-4123-8123-123456789abc",
            "image": IMAGE,
            "argv": ["python", "-m", "tracker", "--threshold=0.7"],
            "model": {"version": "1"},
        },
    )
    config = json.loads(path.read_text())
    assert config["runtime"] == {
        "type": "docker",
        "image": IMAGE,
        "command": ["python", "-m", "tracker", "--threshold=0.7"],
    }
    assert config["resources"] == {"cpu_limit": 4, "memory_limit_mb": 2048, "network_access": False}


def test_callback_path_and_secret_scrubbing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert callback_path("12345678-1234-4123-8123-123456789abc").endswith("/result")
    with pytest.raises(ValueError):
        callback_path("../other-job")

    for key in SECRET_ENVIRONMENT_KEYS:
        monkeypatch.setenv(key, "secret")
    monkeypatch.setenv("SAFE_VALUE", "kept")
    environment = sanitized_environment()
    assert environment["SAFE_VALUE"] == "kept"
    assert SECRET_ENVIRONMENT_KEYS.isdisjoint(environment)


def test_cleanup_force_removes_only_containers_with_the_unique_job_label() -> None:
    job_id = "12345678-1234-4123-8123-123456789abc"
    container_id = "a" * 64
    listed = MagicMock(stdout=f"{container_id}\n")
    removed = MagicMock()
    empty = MagicMock(stdout="")
    environment = {"PATH": "/usr/bin"}
    with patch("scripts.run_control_plane_job.subprocess.run", side_effect=[listed, removed, empty]) as run:
        assert cleanup_benchmark_containers(job_id, environment) == 1

    assert run.call_args_list[0].args[0] == [
        "docker",
        "ps",
        "-aq",
        "--filter",
        f"label=cvbench.control-plane-job={job_id}",
    ]
    assert run.call_args_list[1].args[0] == ["docker", "rm", "--force", container_id]


def test_execution_timeout_still_runs_unique_label_cleanup(tmp_path: Path) -> None:
    submission = {
        "id": "12345678-1234-4123-8123-123456789abc",
        "image": IMAGE,
        "argv": ["python", "-m", "tracker"],
    }
    with (
        patch(
            "scripts.run_control_plane_job.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["docker", "pull"], 600),
        ),
        patch("scripts.run_control_plane_job.cleanup_benchmark_containers") as cleanup,
        pytest.raises(subprocess.TimeoutExpired),
    ):
        execute_submission(tmp_path, submission, tmp_path)

    cleanup.assert_called_once()
    assert cleanup.call_args.args[0] == submission["id"]
    assert cleanup.call_args.args[1]["CVBENCH_DOCKER_JOB_ID"] == submission["id"]


def test_success_callback_failure_is_not_converted_to_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    submission = {
        "id": "12345678-1234-4123-8123-123456789abc",
        "image": IMAGE,
        "argv": ["python", "-m", "tracker"],
    }
    lease = {"submission": submission, "lease": {"token": "b" * 64}}
    monkeypatch.setenv("CVBENCH_API_BASE_URL", "https://cvbench.test")
    monkeypatch.setenv("CVBENCH_RUNNER_TOKEN", "runner-token")
    with (
        patch(
            "scripts.run_control_plane_job.api_request",
            side_effect=[(200, lease), RuntimeError("success callback unavailable")],
        ) as request,
        patch("scripts.run_control_plane_job.execute_submission", return_value={"outcome": {"status": "completed"}}),
        pytest.raises(RuntimeError, match="success callback unavailable"),
    ):
        main()

    assert request.call_count == 2
    assert request.call_args_list[1].kwargs["body"]["status"] == "succeeded"
