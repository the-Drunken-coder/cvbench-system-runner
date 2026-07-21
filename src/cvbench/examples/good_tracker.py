from __future__ import annotations

import errno
import json
import os
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from cvbench.protocol import receive_message


@dataclass
class Track:
    identifier: str
    box: list[float]
    center: tuple[float, float]
    velocity: tuple[float, float] = (0.0, 0.0)
    hits: int = 1
    misses: int = 0
    was_missing: bool = False


def _detections(payload: bytes) -> list[list[float]]:
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return []
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([35, 80, 50]), np.array([90, 255, 255]))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if width * height >= 40:
            boxes.append([float(x), float(y), float(x + width), float(y + height)])
    return sorted(boxes, key=lambda box: (box[0], box[1]))


def _center(box: list[float]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _emit(event: str, metadata: dict[str, Any], track: Track, state: str, support: str) -> None:
    print(
        json.dumps(
            {
                "schema_version": "cvbench.track/v1",
                "event": event,
                "sequence_id": metadata["sequence_id"],
                "source_timestamp_ns": metadata["source_timestamp_ns"],
                "track_id": track.identifier,
                "state": state,
                "support": support,
                "class_id": "synthetic_target",
                "confidence": 0.92 if support == "observed" else max(0.2, 0.8 - track.misses * 0.12),
                "geometry": {"type": "bbox_xyxy", "space": "source_pixels", "value": track.box},
            },
            separators=(",", ":"),
        ),
        flush=True,
    )


def _connect(path: str) -> socket.socket:
    deadline = time.monotonic() + 20
    while True:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(path)
            return sock
        except OSError as exc:
            sock.close()
            if exc.errno in {errno.ENOTSUP, errno.EOPNOTSUPP}:
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def main() -> int:
    path = os.environ.get("CVBENCH_INPUT_SOCKET", "/run/cvbench/input.sock")
    sock = _connect(path)
    print("CVBENCH_READY", flush=True)
    tracks: dict[str, Track] = {}
    next_identifier = 1
    with sock, sock.makefile("rb") as stream:
        while True:
            try:
                metadata, payload = receive_message(stream)
            except EOFError:
                break
            event = metadata.get("event")
            if event == "benchmark_end":
                break
            if event == "stream_start":
                tracks.clear()
                continue
            if event != "frame":
                continue
            detections = _detections(payload)
            available = set(tracks)
            assignments: list[tuple[str, list[float]]] = []
            for box in detections:
                center = _center(box)
                candidates = sorted(
                    (
                        (
                            (track.center[0] + track.velocity[0] - center[0]) ** 2
                            + (track.center[1] + track.velocity[1] - center[1]) ** 2,
                            identifier,
                        )
                        for identifier, track in tracks.items()
                        if identifier in available
                    )
                )
                if candidates and candidates[0][0] <= 45**2:
                    identifier = candidates[0][1]
                    available.remove(identifier)
                else:
                    identifier = f"classical-{next_identifier}"
                    next_identifier += 1
                    tracks[identifier] = Track(identifier, box, center)
                assignments.append((identifier, box))
            matched = {identifier for identifier, _ in assignments}
            for identifier, box in assignments:
                track = tracks[identifier]
                new_center = _center(box)
                track.velocity = (new_center[0] - track.center[0], new_center[1] - track.center[1])
                track.center = new_center
                track.box = box
                track.hits += 1 if track.identifier in matched else 0
                state = "reacquired" if track.was_missing else ("confirmed" if track.hits >= 2 else "tentative")
                event_name = "track_started" if track.hits <= 2 else "track_update"
                track.misses = 0
                track.was_missing = False
                _emit(event_name, metadata, track, state, "observed")
            for identifier in list(tracks):
                if identifier in matched:
                    continue
                track = tracks[identifier]
                track.misses += 1
                track.was_missing = True
                track.box = [
                    track.box[0] + track.velocity[0],
                    track.box[1] + track.velocity[1],
                    track.box[2] + track.velocity[0],
                    track.box[3] + track.velocity[1],
                ]
                track.center = _center(track.box)
                if track.misses <= 5:
                    _emit("track_update", metadata, track, "coasting", "predicted")
                else:
                    _emit("track_ended", metadata, track, "lost", "predicted")
                    del tracks[identifier]
    return 0


if __name__ == "__main__":
    sys.exit(main())
