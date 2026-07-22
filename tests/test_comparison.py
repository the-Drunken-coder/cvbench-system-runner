import random
from dataclasses import replace
from pathlib import Path

from cvbench.comparison import compare_reports
from cvbench.config import load_benchmark
from cvbench.runner import _comparison_fingerprint, _load_unique_scenarios, _portable_path
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
    first_fingerprint, first_inputs = _comparison_fingerprint(benchmark, scenarios)
    for seed in range(8):
        shuffled = list(scenarios)
        random.Random(seed).shuffle(shuffled)
        second_fingerprint, second_inputs = _comparison_fingerprint(benchmark, shuffled)
        assert second_fingerprint == first_fingerprint
        assert second_inputs == first_inputs

    changed_corpus = [replace(scenarios[0], id=f"{scenarios[0].id}-changed"), *scenarios[1:]]
    changed_config = replace(benchmark, version="changed")
    assert _comparison_fingerprint(benchmark, changed_corpus)[0] != first_fingerprint
    assert _comparison_fingerprint(changed_config, shuffled)[0] != first_fingerprint


def test_run_scoped_order_still_has_stable_comparison_fingerprint() -> None:
    benchmark = load_benchmark(ROOT / "benchmarks/persistent-target-tracking.yaml")
    first = _load_unique_scenarios(benchmark.scenarios, "run-a-11111111")
    second = _load_unique_scenarios(benchmark.scenarios, "run-b-22222222")
    assert _comparison_fingerprint(benchmark, first)[0] == _comparison_fingerprint(benchmark, second)[0]


def test_configured_order_seed_is_honored_and_fingerprinted() -> None:
    benchmark = load_benchmark(ROOT / "benchmarks/persistent-target-tracking.yaml")
    configured = replace(benchmark, evaluation_order_seed=17)
    first = _load_unique_scenarios(configured.scenarios, "run-a-11111111", configured.evaluation_order_seed)
    second = _load_unique_scenarios(configured.scenarios, "run-b-22222222", configured.evaluation_order_seed)
    assert [scenario.id for scenario in first] == [scenario.id for scenario in second]
    first_fingerprint, inputs = _comparison_fingerprint(configured, first)
    fallback_fingerprint, fallback_inputs = _comparison_fingerprint(benchmark, first)
    assert first_fingerprint != fallback_fingerprint
    assert inputs["evaluation_order"] == {"mode": "configured_seed", "seed": 17}
    assert fallback_inputs["evaluation_order"] == {"mode": "private_per_run_fallback", "seed": None}


def test_published_provenance_paths_are_portable() -> None:
    assert _portable_path(Path("/Users/alice/worktree/scenarios/rv1/scenario.yaml")) == "scenarios/rv1/scenario.yaml"
    assert _portable_path(Path("/private/tmp/run-output")) == "run-output"
