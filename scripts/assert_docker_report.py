from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) not in {2, 3} or (len(sys.argv) == 3 and sys.argv[2] != "--real-video"):
        raise SystemExit("usage: assert_docker_report.py RUNS [--real-video]")
    real_video = len(sys.argv) == 3
    reports = sorted(Path(sys.argv[1]).glob("*/report.json"))
    if len(reports) != 1:
        raise SystemExit(f"expected one Docker report, found {len(reports)}")
    report = json.loads(reports[0].read_text())
    isolation = report["runtime_isolation"]
    assert report["outcome"]["status"] == "completed", report["outcome"]
    assert report["metrics"]["sample_counts"]["matches"] > 0
    if real_video:
        assert report["benchmark"]["id"] == "real-video-v1"
        assert report["metrics"]["sample_counts"]["neutral_ignored_predictions"] >= 1
    else:
        assert report["metrics"]["identity"]["id_switches"] == 0
    assert isolation["status"] == "verified", isolation
    assert isolation["future_frame_isolation"] is True
    assert isolation["ground_truth_access"] is False
    assert isolation["repository_access"] is False
    assert isolation["media_access"] is False
    assert isolation["image_identity_verified"] is True
    assert isolation["container_user_alignment_verified"] is True
    assert isolation["executed_container_user"] == isolation["expected_container_user"]
    uid, gid = (int(value) for value in isolation["expected_container_user"].split(":"))
    assert uid > 0 and gid >= 0
    assert isolation["socket_access"] == {
        "owner_uid": uid,
        "owner_gid": gid,
        "directory_mode": "0o700",
        "socket_mode": "0o600",
    }
    assert isolation["network_mode"] == "none"
    assert isolation["expected_mount"]["destination"] == "/run/cvbench"
    assert isolation["mounts"] == [isolation["expected_mount"]]
    assert isolation["applied"] == {"cpu_limit": 4.0, "memory_limit_mb": 2048.0}
    assert isolation["requested"] == {
        "cpu_limit": 4,
        "memory_limit_mb": 2048,
        "network_access": False,
    }
    if real_video:
        assert report["resources"]["sample_count"] >= 0
        assert report["resources"]["runtime_seconds"] >= 0
    else:
        assert report["resources"]["sample_count"] > 0
        assert report["resources"]["peak_process_count"] >= 1
    identity = isolation["image_identity"]
    assert identity["configured_reference"] != identity["resolved_reference"]
    assert identity["resolved_reference"] == report["provenance"]["resolved_container_image"]
    assert identity["resolved_image_id"] == report["provenance"]["resolved_container_image_id"]
    assert identity["executed_image_id"] == report["provenance"]["executed_container_image_id"]
    assert identity["executed_image_id"] == identity["resolved_image_id"]
    assert identity["executed_reference"] == identity["resolved_reference"]


if __name__ == "__main__":
    main()
