import json
import shutil
from pathlib import Path

import pytest
import yaml

from cvbench.errors import ConfigurationError
from cvbench.scenario import load_scenario
from tests.helpers import gt

ROOT = Path(__file__).parents[1]


def test_ground_truth_timestamp_must_match_an_exact_scenario_frame(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    shutil.copy2(ROOT / "scenarios/synthetic-v1/acquisition/frames/0000.jpg", frames / "0000.jpg")
    manifest = {
        "schema_version": "cvbench.scenario/v1",
        "id": "timestamp-mismatch",
        "family": "test",
        "sequence_id": "seq",
        "ground_truth": "ground_truth.jsonl",
        "frames": [
            {
                "frame_index": 0,
                "source_timestamp_ns": 0,
                "width": 160,
                "height": 120,
                "path": "frames/0000.jpg",
            }
        ],
    }
    (tmp_path / "scenario.yaml").write_text(yaml.safe_dump(manifest))
    (tmp_path / "ground_truth.jsonl").write_text(json.dumps(gt(1)) + "\n")

    with pytest.raises(ConfigurationError, match="timestamp does not match"):
        load_scenario(tmp_path / "scenario.yaml")
