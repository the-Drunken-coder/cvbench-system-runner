# Unix-domain-socket frame protocol

Version 1 uses a progressive Unix-domain stream socket. It is local, easy to mount into a container, not exposed on a network, and prevents access to future frames. The runner sends exactly one message at a time in source order.

Each message is:

```text
4-byte unsigned big-endian JSON length
4-byte unsigned big-endian payload length
UTF-8 JSON metadata
payload bytes
```

Limits are 1 MB of metadata and 100 MB of payload. Frame messages carry JPEG bytes and the canonical metadata fields from `schemas/frame-v1.schema.json`. Control events are `stream_start`, `stream_end`, `feed_interruption_start`, `feed_interruption_end`, and `benchmark_end`.

The input socket is supplied in `CVBENCH_INPUT_SOCKET`. A SUT connects, then prints `CVBENCH_READY` to stdout. Every subsequent non-empty stdout line must be one complete `cvbench.track/v1` JSON object. Diagnostic text belongs on stderr. Malformed stdout is recorded as a protocol failure rather than silently skipped.

Track boxes use `[x_min, y_min, x_max, y_max]` in source pixels. `observed` means supported by the current image; prediction and coasting must use `predicted`. The runner never accepts SUT timing as authoritative.
