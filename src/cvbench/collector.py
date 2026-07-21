from __future__ import annotations

import json
import threading
import time
from subprocess import Popen
from typing import Any

from .errors import ProtocolError
from .model import CollectedRecord
from .protocol import validate_track_record


class OutputCollector:
    def __init__(
        self,
        process: Popen[str],
        readiness_pattern: str,
        max_records: int,
        frame_sizes: dict[tuple[str, int], tuple[int, int]],
        out_of_bounds: str,
    ):
        self.process = process
        self.readiness_pattern = readiness_pattern
        self.max_records = max_records
        self.frame_sizes = frame_sizes
        self.out_of_bounds = out_of_bounds
        self.records: list[CollectedRecord] = []
        self.errors: list[str] = []
        self.invalid_record_count = 0
        self.stderr: list[str] = []
        self.ready = threading.Event()
        self.flooded = threading.Event()
        self.first_output_timestamp_ns: int | None = None
        self._lock = threading.Lock()
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)

    def start(self) -> None:
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for raw_line in self.process.stdout:
            received = time.monotonic_ns()
            line = raw_line.strip()
            if not line:
                continue
            if not self.ready.is_set() and self.readiness_pattern in line:
                self.ready.set()
                continue
            try:
                raw: Any = json.loads(line)
                size = None
                if isinstance(raw, dict):
                    size = self.frame_sizes.get((raw.get("sequence_id"), raw.get("source_timestamp_ns")))
                record = validate_track_record(raw, frame_size=size, out_of_bounds=self.out_of_bounds)
            except (json.JSONDecodeError, ProtocolError) as exc:
                with self._lock:
                    self.invalid_record_count += 1
                    if len(self.errors) < 1000:
                        self.errors.append(f"malformed output: {exc}; line={line[:300]}")
                continue
            with self._lock:
                if len(self.records) >= self.max_records:
                    if not self.flooded.is_set():
                        self.flooded.set()
                        self.errors.append(f"output record limit exceeded ({self.max_records})")
                    continue
                if self.first_output_timestamp_ns is None:
                    self.first_output_timestamp_ns = received
                self.records.append(CollectedRecord(received, record))

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            with self._lock:
                if len(self.stderr) < 1000:
                    self.stderr.append(line.rstrip())

    def snapshot(self) -> tuple[list[CollectedRecord], list[str], list[str]]:
        with self._lock:
            return list(self.records), list(self.errors), list(self.stderr)

    def join(self, timeout: float = 2.0) -> None:
        self._stdout_thread.join(timeout)
        self._stderr_thread.join(timeout)
