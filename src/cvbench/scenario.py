from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigurationError, ProtocolError
from .model import Frame, Scenario
from .protocol import validate_ground_truth


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path).resolve()
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"cannot load scenario {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != "cvbench.scenario/v1":
        raise ConfigurationError(f"{path} is not a cvbench.scenario/v1 manifest")
    root = path.parent
    frames: list[Frame] = []
    last_timestamp = -1
    for raw in data.get("frames", []):
        try:
            frame = Frame(
                sequence_id=data["sequence_id"],
                frame_index=int(raw["frame_index"]),
                relative_timestamp_ns=int(raw["source_timestamp_ns"]),
                width=int(raw["width"]),
                height=int(raw["height"]),
                path=(root / raw["path"]).resolve(),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError(f"invalid frame in {path}: {exc}") from exc
        if frame.relative_timestamp_ns <= last_timestamp:
            raise ConfigurationError(f"frame timestamps in {path} must be strictly increasing")
        if not frame.path.is_file():
            raise ConfigurationError(f"scenario frame does not exist: {frame.path}")
        last_timestamp = frame.relative_timestamp_ns
        frames.append(frame)
    if not frames:
        raise ConfigurationError(f"scenario {path} has no frames")
    frame_keys = {(frame.sequence_id, frame.relative_timestamp_ns) for frame in frames}
    gt_path = root / data.get("ground_truth", "ground_truth.jsonl")
    ground_truth: list[dict[str, Any]] = []
    try:
        for _line_number, line in enumerate(gt_path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            record = validate_ground_truth(json.loads(line))
            if record["sequence_id"] != data["sequence_id"]:
                raise ProtocolError("ground truth sequence_id does not match scenario")
            if (record["sequence_id"], record["source_timestamp_ns"]) not in frame_keys:
                raise ProtocolError("ground truth timestamp does not match a scenario frame")
            ground_truth.append(record)
    except (OSError, json.JSONDecodeError, ProtocolError) as exc:
        raise ConfigurationError(f"invalid ground truth {gt_path}: {exc}") from exc
    return Scenario(
        id=str(data["id"]),
        family=str(data["family"]),
        root=root,
        frames=frames,
        ground_truth=ground_truth,
        faults=list(data.get("faults", [])),
    )
