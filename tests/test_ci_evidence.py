from pathlib import Path

import pytest

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
