from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    reports = sorted(Path(sys.argv[1]).glob("*/report.json"))
    if len(reports) != 1:
        raise SystemExit(f"expected one Docker report, found {len(reports)}")
    report = json.loads(reports[0].read_text())
    isolation = report["runtime_isolation"]
    assert report["outcome"]["status"] == "completed", report["outcome"]
    assert report["metrics"]["sample_counts"]["matches"] > 0
    assert report["metrics"]["identity"]["id_switches"] == 0
    assert isolation["status"] == "verified", isolation
    assert isolation["future_frame_isolation"] is True
    assert isolation["network_mode"] == "none"
    assert isolation["mounts"] == [
        {"source": isolation["mounts"][0]["source"], "destination": "/run/cvbench"}
    ]
    assert isolation["applied"] == {"cpu_limit": 4.0, "memory_limit_mb": 2048.0}
    assert isolation["requested"] == {
        "cpu_limit": 4,
        "memory_limit_mb": 2048,
        "network_access": False,
    }
    assert report["resources"]["sample_count"] > 0
    assert report["resources"]["peak_process_count"] >= 1
    resolved = report["provenance"]["resolved_container_image"]
    assert resolved and "sha256:" in resolved


if __name__ == "__main__":
    main()
