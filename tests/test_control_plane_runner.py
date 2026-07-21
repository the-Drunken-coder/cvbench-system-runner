from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_control_plane_job import (
    IMAGE_PATTERN,
    SECRET_ENVIRONMENT_KEYS,
    callback_path,
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
                "submission": {"image": IMAGE, "argv": ["python\nmalicious"]},
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
