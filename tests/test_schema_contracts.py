import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from tests.helpers import gt, output

ROOT = Path(__file__).parents[1]


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((ROOT / "schemas" / name).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_frame_event_requires_complete_frame_metadata() -> None:
    validator = _validator("frame-v1.schema.json")
    frame = {
        "schema_version": "cvbench.frame/v1",
        "event": "frame",
        "sequence_id": "sequence",
        "frame_index": 0,
        "source_timestamp_ns": 0,
        "width": 160,
        "height": 120,
        "pixel_format": "rgb24",
        "payload_encoding": "jpeg",
    }
    validator.validate(frame)
    for field in ("frame_index", "source_timestamp_ns", "width", "height", "pixel_format", "payload_encoding"):
        incomplete = dict(frame)
        incomplete.pop(field)
        with pytest.raises(ValidationError):
            validator.validate(incomplete)
    validator.validate(
        {"schema_version": "cvbench.frame/v1", "event": "stream_start", "sequence_id": "sequence"}
    )


def test_ground_truth_bbox_requirement_matches_runtime_contract() -> None:
    validator = _validator("ground-truth-v1.schema.json")
    visible = gt(0)
    validator.validate(visible)
    visible.pop("bbox_xyxy")
    with pytest.raises(ValidationError):
        validator.validate(visible)
    off_screen = {**visible, "on_screen": False}
    validator.validate(off_screen)


def test_ground_truth_ignore_region_conditionals_are_complete() -> None:
    validator = _validator("ground-truth-v1.schema.json")
    valid = {**gt(0), "ignore": True, "ignore_region": True, "ignore_region_id": "region"}
    validator.validate(valid)
    for invalid in (
        {**gt(0), "ignore_region": True, "ignore": False, "ignore_region_id": "region"},
        {**gt(0), "ignore": True, "ignore_region": True},
        {**gt(0), "ignore": True, "ignore_region": True, "ignore_region_id": ""},
        {**gt(0), "ignore": True, "ignore_region_id": "region"},
    ):
        with pytest.raises(ValidationError):
            validator.validate(invalid)


@pytest.mark.parametrize("field", ["sequence_id", "track_id"])
def test_track_schema_rejects_empty_identifiers(field: str) -> None:
    validator = _validator("track-v1.schema.json")
    record = output(0).system_record
    record[field] = ""
    with pytest.raises(ValidationError):
        validator.validate(record)


def test_timing_compute_schema_requires_immutable_source_and_allowlisted_replay() -> None:
    validator = _validator("timing-compute-v1.schema.json")
    timing = {
        "contract_version": "cvbench.timing-compute/v1",
        "source": {
            "immutable": True,
            "frame_count": 2,
            "duration_seconds": 1,
            "sequences": [],
        },
        "replay": {
            "profile": "half-speed",
            "rate": 0.5,
            "native_real_time": False,
            "allowlisted": True,
        },
        "durations": {
            "wall_seconds": 2,
            "startup_seconds": 0.1,
            "stream_delivery_seconds": 2,
            "completion_seconds": 2,
            "drain_seconds": 0,
            "real_time_factor": 2,
        },
        "delivery": {
            "policy_version": "cvbench.delivery-lossless/v1",
            "replay_profile": "half-speed",
            "replay_rate": 0.5,
            "effective_replay_rate": 0.5,
            "delivered_frames_per_second": 20,
            "deadline_missed_frames": 0,
            "sender_pressure_frames": 0,
            "delivery_backlog_ms": {},
            "per_frame": [],
        },
        "processing_latency_ms": {},
        "output": {},
        "clocks": {},
    }
    validator.validate(timing)
    timing["replay"]["rate"] = 0.3
    with pytest.raises(ValidationError):
        validator.validate(timing)
