import random
from dataclasses import replace
from pathlib import Path

from cvbench.comparison import compare_reports
from cvbench.config import load_benchmark
from cvbench.runner import _comparison_fingerprint, _load_unique_scenarios
from cvbench.scenario import load_scenario

ROOT = Path(__file__).parents[1]


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


def test_comparison_fingerprint_is_independent_of_private_delivery_order() -> None:
    benchmark = load_benchmark(ROOT / "benchmarks/persistent-target-tracking.yaml")
    scenarios = [load_scenario(path) for path in benchmark.scenarios]
    shuffled = list(scenarios)
    random.Random(7).shuffle(shuffled)
    first_fingerprint, first_inputs = _comparison_fingerprint(benchmark, scenarios)
    second_fingerprint, second_inputs = _comparison_fingerprint(benchmark, shuffled)
    assert first_fingerprint == second_fingerprint
    assert first_inputs == second_inputs

    changed_corpus = [replace(shuffled[0], id=f"{shuffled[0].id}-changed"), *shuffled[1:]]
    changed_config = replace(benchmark, version="changed")
    assert _comparison_fingerprint(benchmark, changed_corpus)[0] != first_fingerprint
    assert _comparison_fingerprint(changed_config, shuffled)[0] != first_fingerprint


def test_run_scoped_order_still_has_stable_comparison_fingerprint() -> None:
    benchmark = load_benchmark(ROOT / "benchmarks/persistent-target-tracking.yaml")
    first = _load_unique_scenarios(benchmark.scenarios, "run-a-11111111")
    second = _load_unique_scenarios(benchmark.scenarios, "run-b-22222222")
    assert _comparison_fingerprint(benchmark, first)[0] == _comparison_fingerprint(benchmark, second)[0]
