import threading
import time
from types import SimpleNamespace

from cvbench.model import Frame, Scenario
from cvbench.runner import _deliver_scenarios
from cvbench.timing import DeliveryRecorder


class _Collector:
    flooded = threading.Event()
    limit_reason = None

    def begin_frame(self, _key, _size):
        pass

    def finish_frame(self, _key, *, delivered):
        assert delivered


class _Monitor:
    def set_context(self, *_args, **_kwargs):
        pass


def test_benchmark_end_boundary_is_after_blocking_marker_send(tmp_path, monkeypatch) -> None:
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame")
    scenario = Scenario(
        "scenario",
        "test",
        tmp_path,
        [Frame("sequence", 0, 0, 10, 10, frame_path)],
        [],
    )
    config = SimpleNamespace(
        input_mode="online_replay",
        timing_compute_contract="cvbench.timing-compute/v1",
        delivery_policy="cvbench.delivery-lossless/v1",
        replay_profile="native",
        playback_rate=1.0,
    )
    recorder = DeliveryRecorder(
        config.delivery_policy,
        config.replay_profile,
        config.playback_rate,
    )
    marker_returned_ns = None

    def blocking_send(_connection, metadata, _payload=b""):
        nonlocal marker_returned_ns
        if metadata["event"] == "benchmark_end":
            assert recorder.benchmark_end_sent_ns is None
            time.sleep(0.02)
            marker_returned_ns = time.monotonic_ns()

    monkeypatch.setattr("cvbench.runner.send_message", blocking_send)
    _deliver_scenarios(
        SimpleNamespace(),
        [scenario],
        config,
        time.monotonic() + 1,
        _Monitor(),
        _Collector(),
        {},
        recorder,
    )

    assert marker_returned_ns is not None
    assert recorder.benchmark_end_sent_ns >= marker_returned_ns
    assert recorder.summary()["benchmark_end_sender_call_ms"] >= 20
