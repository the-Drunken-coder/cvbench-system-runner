import json
from pathlib import Path

import pytest

from scripts.assert_docker_report import _parse_mode
from scripts.sanitize_ci_report import sanitize_runs
from scripts.verify_ci_evidence import _assert_safe, main


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["assert_docker_report.py", "runs"], "synthetic"),
        (["assert_docker_report.py", "runs", "--real-video"], "real-video"),
        (["assert_docker_report.py", "runs", "--combined"], "combined"),
    ],
)
def test_docker_report_mode_flags_reach_their_named_contract(argv: list[str], expected: str) -> None:
    assert _parse_mode(argv) == expected


def test_safe_report_and_resources_are_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = tmp_path / "runs" / "one"
    run.mkdir(parents=True)
    (run / "report.json").write_text('{"metrics": {"ground_truth_records": 3}, "resources": {}}')
    (run / "resources.csv").write_text("elapsed_ms,process_count\n100,1\n")
    manifest = tmp_path / "artifacts.sha256"
    manifest.write_text("a" * 64 + "  frame.jpg\n")
    monkeypatch.setattr("sys.argv", ["verify_ci_evidence.py", str(tmp_path / "runs"), str(manifest)])
    main()


def test_restricted_ground_truth_payload_is_rejected(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text('{"bbox_xyxy": [1, 2, 3, 4]}')
    with pytest.raises(AssertionError):
        _assert_safe(report)


def test_ci_sanitization_writes_a_safe_copy_without_mutating_core_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_run = tmp_path / "source" / "run-1"
    source_run.mkdir(parents=True)
    core = {
        "audit_evidence": {
            "schema_version": "cvbench.audit/v1",
            "frame_samples": [{"ground_truth": [{"bbox_xyxy": [1, 2, 3, 4]}]}],
        },
        "diagnostics": {"sut_stderr": ["secret-model-output"]},
        "runtime_isolation": {
            "expected_mount": {"source": "/tmp/socket-dir", "destination": "/run/cvbench"},
            "mounts": [{"source": "/tmp/socket-dir", "destination": "/run/cvbench"}],
        },
        "metrics": {"sample_counts": {"matches": 3}},
    }
    source_report = source_run / "report.json"
    source_report.write_text(json.dumps(core))
    (source_run / "resources.csv").write_text("elapsed_ms,process_count\n100,1\n")
    original = source_report.read_text()

    destination_run = sanitize_runs(tmp_path / "source", tmp_path / "safe")
    assert source_report.read_text() == original
    safe = json.loads((destination_run / "report.json").read_text())
    assert safe["audit_evidence"]["redacted"] is True
    assert safe["runtime_isolation"]["mounts"][0]["source"] == "<socket-only-runtime-dir>"
    manifest = tmp_path / "artifacts.sha256"
    manifest.write_text("a" * 64 + "  frame.jpg\n")
    monkeypatch.setattr(
        "sys.argv",
        ["verify_ci_evidence.py", str(destination_run.parent), str(manifest)],
    )
    main()
    _assert_safe(destination_run / "report.json")


def test_failed_isolation_remains_unknown_in_public_safe_copy(tmp_path: Path) -> None:
    source_run = tmp_path / "source" / "run-unknown"
    source_run.mkdir(parents=True)
    core = {
        "outcome": {"status": "failed", "errors": ["container ID was not created"]},
        "runtime_isolation": {
            "status": "verification_failed",
            "future_frame_isolation": None,
            "ground_truth_access": None,
            "repository_access": None,
            "media_access": None,
            "mounts": None,
            "network_mode": None,
            "image_identity_verified": None,
            "container_user_alignment_verified": None,
        },
        "metrics": {"sample_counts": {"matches": 0}},
    }
    (source_run / "report.json").write_text(json.dumps(core))
    (source_run / "resources.csv").write_text("elapsed_ms,process_count\n")

    destination = sanitize_runs(tmp_path / "source", tmp_path / "safe")
    safe = json.loads((destination / "report.json").read_text())
    isolation = safe["runtime_isolation"]
    assert safe["outcome"]["status"] == "failed"
    assert isolation["status"] == "verification_failed"
    assert isolation["future_frame_isolation"] is None
    assert isolation["ground_truth_access"] is None
    assert isolation["repository_access"] is None
    assert isolation["media_access"] is None
    assert isolation["image_identity_verified"] is None
