import signal
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

from cvbench.runner import _scoring_complete
from cvbench.runtime import StartedRuntime, cleanup_runtime, stop_runtime


class _Process:
    def __init__(self, *, wait_timeouts: int = 0):
        self.exited = False
        self.wait_timeouts = wait_timeouts
        self.signals = []

    def poll(self):
        return 0 if self.exited else None

    def wait(self, timeout):
        if self.wait_timeouts:
            self.wait_timeouts -= 1
            raise subprocess.TimeoutExpired(["sut"], timeout)
        self.exited = True
        return -signal.SIGKILL if signal.SIGKILL in self.signals else 0

    def send_signal(self, sent):
        self.signals.append(sent)


def _runtime(process, cidfile: Path | None = None) -> StartedRuntime:
    return StartedRuntime(process, cidfile, None, ["sut"], {})


def test_normal_exit_is_released_only_after_final_scoring_checkpoint() -> None:
    process = _Process()
    events = []

    stopped = stop_runtime(
        _runtime(process),
        1,
        lambda: events.append("checkpoint"),
        lambda: events.append("final-accounting"),
        lambda: events.append("release"),
        lambda: True,
    )

    assert events == ["checkpoint", "final-accounting", "release"]
    assert stopped.exit_code == 0
    assert stopped.forced is False
    assert process.signals == []
    assert stopped.scoring_finished_ns <= stopped.teardown_finished_ns


def test_system_half_close_marks_output_complete_without_releasing_input() -> None:
    runner, system = socket.socketpair()
    collector = SimpleNamespace(stdout_closed=SimpleNamespace(is_set=lambda: False))
    try:
        assert _scoring_complete(runner, collector) is False
        system.shutdown(socket.SHUT_WR)
        assert _scoring_complete(runner, collector) is True
        runner.sendall(b"input remains available")
        assert system.recv(64) == b"input remains available"
    finally:
        runner.close()
        system.close()


def test_timeout_uses_terminate_then_kill_after_scoring_is_closed() -> None:
    process = _Process(wait_timeouts=2)
    events = []

    stopped = stop_runtime(
        _runtime(process),
        0,
        lambda: events.append("checkpoint"),
        lambda: events.append("final-accounting"),
        lambda: events.append("release"),
    )

    assert events == ["checkpoint", "final-accounting", "release"]
    assert stopped.forced is True
    assert stopped.exit_code == -signal.SIGKILL
    assert process.signals == [signal.SIGTERM, signal.SIGKILL]


def test_docker_cleanup_happens_after_accounting_and_removes_the_container(
    tmp_path, monkeypatch
) -> None:
    process = _Process()
    events = []
    cidfile = tmp_path / "container.cid"
    cidfile.write_text("container-id")
    runtime = _runtime(process, cidfile)

    stop_runtime(
        runtime,
        0,
        lambda: events.append("checkpoint"),
        lambda: events.append("final-accounting"),
        lambda: events.append("release"),
    )
    monkeypatch.setattr("cvbench.runtime.shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        "cvbench.runtime.subprocess.run",
        lambda command, **_kwargs: events.append(command) or SimpleNamespace(returncode=0),
    )
    cleanup_runtime(runtime)

    assert events == [
        "checkpoint",
        "final-accounting",
        "release",
        ["docker", "rm", "--force", "container-id"],
    ]
