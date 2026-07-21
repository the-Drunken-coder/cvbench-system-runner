from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
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
        max_line_bytes: int,
        max_total_bytes: int,
        max_records_per_second: int,
        frame_sizes: dict[tuple[str, int], tuple[int, int]],
        out_of_bounds: str,
    ):
        self.process = process
        self.readiness_pattern = readiness_pattern
        self.max_records = max_records
        self.max_line_bytes = max_line_bytes
        self.max_total_bytes = max_total_bytes
        self.max_records_per_second = max_records_per_second
        self.frame_sizes = frame_sizes
        self.out_of_bounds = out_of_bounds
        self.records: list[CollectedRecord] = []
        self.errors: list[str] = []
        self.invalid_record_count = 0
        self.stderr: list[str] = []
        self.ready = threading.Event()
        self.flooded = threading.Event()
        self.limit_reason: str | None = None
        self.first_output_timestamp_ns: int | None = None
        self._lock = threading.Lock()
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)

    def start(self) -> None:
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        file_descriptor = self.process.stdout.fileno()
        buffer = bytearray()
        total_bytes = 0
        recent_records: deque[int] = deque()
        while not self.flooded.is_set():
            chunk = os.read(file_descriptor, 65536)
            if not chunk:
                if buffer:
                    self._consume_line(bytes(buffer), recent_records)
                return
            total_bytes += len(chunk)
            if total_bytes > self.max_total_bytes:
                self._set_limit(f"total stdout byte limit exceeded ({self.max_total_bytes})")
                return
            buffer.extend(chunk)
            while True:
                newline = buffer.find(b"\n")
                if newline < 0:
                    break
                raw_line = bytes(buffer[:newline])
                del buffer[: newline + 1]
                if len(raw_line) > self.max_line_bytes:
                    self._set_limit(f"stdout line byte limit exceeded ({self.max_line_bytes})")
                    return
                self._consume_line(raw_line, recent_records)
                if self.flooded.is_set():
                    return
            if len(buffer) > self.max_line_bytes:
                self._set_limit(f"stdout line byte limit exceeded ({self.max_line_bytes})")
                return

    def _consume_line(self, raw_line: bytes, recent_records: deque[int]) -> None:
        received = time.monotonic_ns()
        try:
            line = raw_line.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            self._record_invalid(f"output is not UTF-8: {exc}", raw_line[:300].decode("utf-8", "replace"))
            return
        if not line:
            return
        if not self.ready.is_set() and self.readiness_pattern in line:
            self.ready.set()
            return
        with self._lock:
            record_number = len(self.records) + self.invalid_record_count + 1
        if record_number > self.max_records:
            self._set_limit(f"output record limit exceeded ({self.max_records})")
            return
        cutoff = received - 1_000_000_000
        while recent_records and recent_records[0] <= cutoff:
            recent_records.popleft()
        recent_records.append(received)
        if len(recent_records) > self.max_records_per_second:
            self._set_limit(f"output rate limit exceeded ({self.max_records_per_second} records/second)")
            return
        try:
            raw: Any = json.loads(line)
            size = None
            if isinstance(raw, dict):
                size = self.frame_sizes.get((raw.get("sequence_id"), raw.get("source_timestamp_ns")))
            record = validate_track_record(raw, frame_size=size, out_of_bounds=self.out_of_bounds)
        except (json.JSONDecodeError, ProtocolError) as exc:
            self._record_invalid(str(exc), line[:300])
            return
        with self._lock:
            if self.first_output_timestamp_ns is None:
                self.first_output_timestamp_ns = received
            self.records.append(CollectedRecord(received, record))

    def _record_invalid(self, error: str, sample: str) -> None:
        with self._lock:
            self.invalid_record_count += 1
            if len(self.errors) < 1000:
                self.errors.append(f"malformed output: {error}; line={sample}")

    def _set_limit(self, reason: str) -> None:
        with self._lock:
            if self.flooded.is_set():
                return
            self.limit_reason = reason
            self.errors.append(f"output limit exceeded: {reason}")
            self.flooded.set()

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        file_descriptor = self.process.stderr.fileno()
        buffer = bytearray()
        while chunk := os.read(file_descriptor, 65536):
            buffer.extend(chunk)
            while True:
                newline = buffer.find(b"\n")
                if newline < 0:
                    break
                line = bytes(buffer[: min(newline, 4096)]).decode("utf-8", "replace")
                del buffer[: newline + 1]
                with self._lock:
                    if len(self.stderr) < 1000:
                        self.stderr.append(line)
            if len(buffer) > 4096:
                del buffer[4096:]

    def snapshot(self) -> tuple[list[CollectedRecord], list[str], list[str]]:
        with self._lock:
            return list(self.records), list(self.errors), list(self.stderr)

    def join(self, timeout: float = 2.0) -> None:
        self._stdout_thread.join(timeout)
        self._stderr_thread.join(timeout)
