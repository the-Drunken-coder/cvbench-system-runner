from __future__ import annotations

import json

from scripts.run_control_plane_job import stage_evidence_artifacts


def test_stage_evidence_artifacts_creates_hashed_expiring_manifest(tmp_path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "system-output.jsonl").write_text('{"safe":"<script>"}\n')
    (run_dir / "ground-truth.jsonl").write_text('{"target_id":"target"}\n')
    (run_dir / "matching-decisions.jsonl").write_text("{}\n")
    (run_dir / "resources.csv").write_text("elapsed_ms,cpu_percent,memory_bytes\n")
    (run_dir / "report.html").write_text("<pre>&lt;script&gt;</pre>")
    monkeypatch.setenv("CVBENCH_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")

    report = stage_evidence_artifacts(
        run_dir,
        "00000000-0000-4000-8000-000000000001",
        {"provenance": {}, "audit_evidence": {"schema_version": "cvbench.audit/v1"}},
    )
    artifact = report["provenance"]["evidence_artifacts"][0]
    manifest_path = tmp_path / "artifacts" / "00000000-0000-4000-8000-000000000001" / "evidence-manifest.json"
    manifest = json.loads(manifest_path.read_text())

    assert artifact["name"] == "cvbench-evidence-12345"
    assert artifact["retention_days"] == 7
    assert artifact["manifest_sha256"]
    assert manifest["access"].startswith("GitHub Actions artifact")
    assert all(entry["sha256"] and entry["bytes"] > 0 for entry in manifest["files"])
    assert (manifest_path.parent / "report.json").exists()
