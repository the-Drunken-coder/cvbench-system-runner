import json
from pathlib import Path

import pytest

from scripts.sanitize_ci_report import sanitize_runs
from scripts.verify_ci_evidence import _assert_safe, main


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
