import json
from collections import deque
from types import SimpleNamespace

from cvbench.collector import OutputCollector
from tests.helpers import output


def _collector() -> OutputCollector:
    return OutputCollector(
        SimpleNamespace(),
        "CVBENCH_READY",
        100,
        10_000,
        100_000,
        100,
        "reject",
    )


def _line(timestamp_ns: int) -> bytes:
    return json.dumps(output(timestamp_ns).system_record).encode()


def test_immediate_output_is_pending_until_frame_send_succeeds() -> None:
    collector = _collector()
    key = ("seq", 10)
    collector.begin_frame(key, (100, 100))
    collector._consume_line(_line(10), deque())
    assert collector.snapshot()[:2] == ([], [])

    collector.finish_frame(key, delivered=True)
    records, errors, _stderr = collector.snapshot()
    assert len(records) == 1
    assert errors == []


def test_output_for_transport_failed_frame_is_rejected() -> None:
    collector = _collector()
    key = ("seq", 20)
    collector.begin_frame(key, (100, 100))
    collector._consume_line(_line(20), deque())
    collector.finish_frame(key, delivered=False)

    records, errors, _stderr = collector.snapshot()
    assert records == []
    assert any("delivery failed" in error for error in errors)
