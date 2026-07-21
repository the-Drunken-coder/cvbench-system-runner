from cvbench.comparison import compare_reports


def test_baseline_comparison_labels_direction_and_low_confidence() -> None:
    baseline = {
        "metrics": {"acquisition": {"rate": 0.5}, "sample_counts": {"matches": 10}},
        "resources": {"peak_ram_bytes": 200},
        "provenance": {"comparison_fingerprint": "same"},
    }
    candidate = {
        "metrics": {"acquisition": {"rate": 0.8}, "sample_counts": {"matches": 10}},
        "resources": {"peak_ram_bytes": 150},
        "provenance": {"comparison_fingerprint": "same"},
    }
    results = {item["metric"]: item for item in compare_reports(baseline, candidate)}
    assert results["acquisition.rate"]["direction"] == "improvement"
    assert results["acquisition.rate"]["confidence"] == "low"
    assert results["resources.peak_ram_bytes"]["direction"] == "improvement"


def test_incompatible_scenario_comparison_is_inconclusive() -> None:
    baseline = {"metrics": {"acquisition": {"rate": 0.5}}, "provenance": {"comparison_fingerprint": "a"}}
    candidate = {"metrics": {"acquisition": {"rate": 0.9}}, "provenance": {"comparison_fingerprint": "b"}}
    results = compare_reports(baseline, candidate)
    assert results
    assert all(result["direction"] == "inconclusive" for result in results)
