from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from cvbench.config import load_benchmark, load_system
from cvbench.errors import ConfigurationError

ROOT = Path(__file__).parents[1]


def _benchmark_file(tmp_path: Path, change: Callable[[dict[str, Any]], None]) -> Path:
    data = yaml.safe_load((ROOT / "benchmarks/persistent-target-tracking.yaml").read_text())
    change(data)
    path = tmp_path / "benchmark.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _system_file(tmp_path: Path, change: Callable[[dict[str, Any]], None]) -> Path:
    data = yaml.safe_load((ROOT / "systems/example-good-local.yaml").read_text())
    change(data)
    path = tmp_path / "system.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_shipped_definitions_validate() -> None:
    benchmark = load_benchmark(ROOT / "benchmarks/persistent-target-tracking.yaml")
    system = load_system(ROOT / "systems/example-good-local.yaml")
    assert benchmark.input_mode == "online_replay"
    assert system.runtime_type == "local"


def test_mutually_invalid_input_mode_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "schema_version: cvbench.benchmark/v1\n"
        "id: x\nversion: '1'\n"
        "input: {mode: future, protocol: frame_socket_v1}\n"
        "scenarios: [x]\n"
    )
    with pytest.raises(ConfigurationError):
        load_benchmark(path)


@pytest.mark.parametrize("value", ["1", float("nan"), float("inf"), 0, -1])
def test_playback_rate_must_be_typed_positive_and_finite(tmp_path: Path, value: Any) -> None:
    path = _benchmark_file(tmp_path, lambda data: data["input"].__setitem__("playback_rate", value))
    with pytest.raises(ConfigurationError):
        load_benchmark(path)


@pytest.mark.parametrize("value", ["120", float("nan"), float("inf"), 0, -1])
def test_max_run_seconds_must_be_typed_positive_and_finite(tmp_path: Path, value: Any) -> None:
    path = _benchmark_file(tmp_path, lambda data: data.__setitem__("max_run_seconds", value))
    with pytest.raises(ConfigurationError):
        load_benchmark(path)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("minimum_match_iou", -0.01),
        ("minimum_match_iou", 1.01),
        ("minimum_match_iou", "0.5"),
        ("high_confidence_threshold", -0.01),
        ("high_confidence_threshold", 1.01),
        ("high_confidence_threshold", float("nan")),
        ("max_match_center_error_px", -1),
        ("max_match_center_error_px", "50"),
        ("latency_deadline_ms", float("inf")),
        ("latency_deadline_ms", -1),
        ("confirmed_track_min_duration_ms", "250"),
        ("visible_dropout_tolerance_ms", -1),
    ],
)
def test_thresholds_are_typed_finite_and_bounded(tmp_path: Path, name: str, value: Any) -> None:
    path = _benchmark_file(tmp_path, lambda data: data["thresholds"].__setitem__(name, value))
    with pytest.raises(ConfigurationError):
        load_benchmark(path)


@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_class_agnostic_requires_a_real_boolean(tmp_path: Path, value: Any) -> None:
    path = _benchmark_file(tmp_path, lambda data: data["thresholds"].__setitem__("class_agnostic", value))
    with pytest.raises(ConfigurationError):
        load_benchmark(path)


@pytest.mark.parametrize("value", [[], [0], ["100"], [True]])
def test_acquisition_deadlines_are_positive_integer_values(tmp_path: Path, value: Any) -> None:
    path = _benchmark_file(tmp_path, lambda data: data["thresholds"].__setitem__("acquisition_deadlines_ms", value))
    with pytest.raises(ConfigurationError):
        load_benchmark(path)


@pytest.mark.parametrize(
    "key",
    ["max_output_records", "max_output_line_bytes", "max_total_output_bytes", "max_output_records_per_second"],
)
@pytest.mark.parametrize("value", ["10", 0, -1, True])
def test_output_limits_are_positive_integers(tmp_path: Path, key: str, value: Any) -> None:
    path = _benchmark_file(tmp_path, lambda data: data.__setitem__(key, value))
    with pytest.raises(ConfigurationError):
        load_benchmark(path)


@pytest.mark.parametrize("section,key", [("readiness", "timeout_seconds"), ("shutdown", "grace_period_seconds")])
@pytest.mark.parametrize("value", ["1", float("nan"), float("inf"), -1])
def test_system_durations_are_typed_finite_and_nonnegative(
    tmp_path: Path, section: str, key: str, value: Any
) -> None:
    path = _system_file(tmp_path, lambda data: data.setdefault(section, {}).__setitem__(key, value))
    with pytest.raises(ConfigurationError):
        load_system(path)


def test_numeric_boundaries_are_accepted(tmp_path: Path) -> None:
    def update_benchmark(data: dict[str, Any]) -> None:
        data["input"]["playback_rate"] = 0.001
        data["max_run_seconds"] = 0.001
        data["thresholds"].update(
            {
                "confirmed_track_min_duration_ms": 0,
                "visible_dropout_tolerance_ms": 0,
                "max_match_center_error_px": 0,
                "minimum_match_iou": 0,
                "latency_deadline_ms": 0,
                "high_confidence_threshold": 1,
                "class_agnostic": False,
            }
        )

    benchmark = load_benchmark(_benchmark_file(tmp_path, update_benchmark))
    assert benchmark.playback_rate == 0.001
    assert benchmark.thresholds.minimum_match_iou == 0

    def update_system(data: dict[str, Any]) -> None:
        data["readiness"]["timeout_seconds"] = 0
        data["shutdown"]["grace_period_seconds"] = 0

    system = load_system(_system_file(tmp_path, update_system))
    assert system.readiness_timeout_seconds == 0
    assert system.grace_period_seconds == 0
