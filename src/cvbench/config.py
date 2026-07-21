from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigurationError


@dataclass(frozen=True)
class Thresholds:
    confirmed_track_min_duration_ms: int = 250
    visible_dropout_tolerance_ms: int = 100
    max_match_center_error_px: float = 50.0
    minimum_match_iou: float = 0.3
    acquisition_deadlines_ms: tuple[int, ...] = (100, 250, 500, 1000)
    latency_deadline_ms: float = 250.0
    high_confidence_threshold: float = 0.8
    out_of_bounds: str = "reject"
    class_agnostic: bool = False


@dataclass(frozen=True)
class BenchmarkConfig:
    path: Path
    id: str
    version: str
    input_mode: str
    playback_rate: float
    thresholds: Thresholds
    scenarios: tuple[Path, ...]
    reporting: dict[str, bool]
    resources: dict[str, Any]
    max_run_seconds: float
    max_output_records: int
    baseline_report: Path | None


@dataclass(frozen=True)
class SystemConfig:
    path: Path
    id: str
    revision: str
    runtime_type: str
    command: tuple[str, ...]
    image: str | None
    environment: dict[str, str]
    readiness_pattern: str
    readiness_timeout_seconds: float
    grace_period_seconds: float
    resources: dict[str, Any]


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"{path} must contain a YAML object")
    return data


def _require(data: dict[str, Any], key: str, kind: type) -> Any:
    value = data.get(key)
    if not isinstance(value, kind) or isinstance(value, bool) and kind in (int, float):
        raise ConfigurationError(f"{key} must be {kind.__name__}")
    return value


def load_benchmark(path: str | Path) -> BenchmarkConfig:
    path = Path(path).resolve()
    data = _load_yaml(path)
    if data.get("schema_version") != "cvbench.benchmark/v1":
        raise ConfigurationError("benchmark schema_version must be cvbench.benchmark/v1")
    input_config = data.get("input", {})
    if input_config.get("mode") not in {"online_replay", "offline_debug"}:
        raise ConfigurationError("input.mode must be online_replay or offline_debug")
    if input_config.get("protocol") != "frame_socket_v1":
        raise ConfigurationError("Version 1 input.protocol must be frame_socket_v1")
    playback_rate = float(input_config.get("playback_rate", 1.0))
    if playback_rate <= 0:
        raise ConfigurationError("input.playback_rate must be positive")
    raw_thresholds = data.get("thresholds", {})
    out_of_bounds = raw_thresholds.get("out_of_bounds", "reject")
    if out_of_bounds not in {"reject", "clip"}:
        raise ConfigurationError("thresholds.out_of_bounds must be reject or clip")
    thresholds = Thresholds(
        confirmed_track_min_duration_ms=int(raw_thresholds.get("confirmed_track_min_duration_ms", 250)),
        visible_dropout_tolerance_ms=int(raw_thresholds.get("visible_dropout_tolerance_ms", 100)),
        max_match_center_error_px=float(raw_thresholds.get("max_match_center_error_px", 50)),
        minimum_match_iou=float(raw_thresholds.get("minimum_match_iou", 0.3)),
        acquisition_deadlines_ms=tuple(
            int(v) for v in raw_thresholds.get("acquisition_deadlines_ms", [100, 250, 500, 1000])
        ),
        latency_deadline_ms=float(raw_thresholds.get("latency_deadline_ms", 250)),
        high_confidence_threshold=float(raw_thresholds.get("high_confidence_threshold", 0.8)),
        out_of_bounds=out_of_bounds,
        class_agnostic=bool(raw_thresholds.get("class_agnostic", False)),
    )
    scenario_items = data.get("scenarios")
    if not isinstance(scenario_items, list) or not scenario_items:
        raise ConfigurationError("benchmark scenarios must be a non-empty list")
    scenarios: list[Path] = []
    for item in scenario_items:
        raw_path = item.get("path") if isinstance(item, dict) else item
        if not isinstance(raw_path, str):
            raise ConfigurationError("each scenario must provide a path")
        scenarios.append((path.parent / raw_path).resolve())
    reporting = data.get("reporting", {})
    resources = data.get("resources", {})
    baseline = data.get("baseline_report")
    return BenchmarkConfig(
        path=path,
        id=_require(data, "id", str),
        version=_require(data, "version", str),
        input_mode=input_config["mode"],
        playback_rate=playback_rate,
        thresholds=thresholds,
        scenarios=tuple(scenarios),
        reporting={
            "generate_json": bool(reporting.get("generate_json", True)),
            "generate_html": bool(reporting.get("generate_html", True)),
            "generate_failure_packets": bool(reporting.get("generate_failure_packets", True)),
        },
        resources=resources,
        max_run_seconds=float(data.get("max_run_seconds", 120)),
        max_output_records=int(data.get("max_output_records", 100_000)),
        baseline_report=(path.parent / baseline).resolve() if isinstance(baseline, str) else None,
    )


def load_system(path: str | Path) -> SystemConfig:
    path = Path(path).resolve()
    data = _load_yaml(path)
    if data.get("schema_version") != "cvbench.system/v1":
        raise ConfigurationError("system schema_version must be cvbench.system/v1")
    runtime = data.get("runtime", {})
    runtime_type = runtime.get("type")
    if runtime_type not in {"local", "docker"}:
        raise ConfigurationError("runtime.type must be local or docker")
    command = runtime.get("command", [])
    if not isinstance(command, list) or not command or not all(isinstance(v, str) for v in command):
        raise ConfigurationError("runtime.command must be a non-empty string list")
    image = runtime.get("image")
    if runtime_type == "docker" and not isinstance(image, str):
        raise ConfigurationError("Docker runtime requires an image")
    environment = runtime.get("environment", {})
    if not isinstance(environment, dict) or not all(
        isinstance(k, str) and isinstance(v, (str, int, float, bool)) for k, v in environment.items()
    ):
        raise ConfigurationError("runtime.environment must contain scalar values")
    readiness = data.get("readiness", {})
    if readiness.get("type", "stdout_pattern") != "stdout_pattern":
        raise ConfigurationError("Version 1 readiness.type must be stdout_pattern")
    pattern = readiness.get("pattern", "CVBENCH_READY")
    if not isinstance(pattern, str) or not pattern:
        raise ConfigurationError("readiness.pattern must be a non-empty string")
    shutdown = data.get("shutdown", {})
    return SystemConfig(
        path=path,
        id=_require(data, "id", str),
        revision=_require(data, "revision", str),
        runtime_type=runtime_type,
        command=tuple(command),
        image=image,
        environment={str(k): str(v) for k, v in environment.items()},
        readiness_pattern=pattern,
        readiness_timeout_seconds=float(readiness.get("timeout_seconds", 30)),
        grace_period_seconds=float(shutdown.get("grace_period_seconds", 10)),
        resources=data.get("resources", {}),
    )
