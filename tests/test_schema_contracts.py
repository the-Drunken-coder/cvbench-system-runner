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
