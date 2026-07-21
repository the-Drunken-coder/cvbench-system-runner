from pathlib import Path

import pytest

from cvbench.config import load_benchmark, load_system
from cvbench.errors import ConfigurationError

ROOT = Path(__file__).parents[1]


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
