# Version 1 capability matrix

Status is explicit so architecture hooks are not mistaken for delivered behavior.

| Required capability | Status | Evidence |
|---|---|---|
| Working local CLI and declarative validation | Implemented-tested | CLI/config unit tests and fresh-install validation |
| Unix-socket progressive JPEG delivery | Implemented-tested | Binary round-trip tests and real image E2E |
| Future-frame isolation | Implemented-tested | Docker command/inspection tests prove only the socket is mounted and networking is `none`; the example image excludes scenarios |
| Docker SUT execution | Implemented-tested | Runtime construction, immutable image resolution, isolation/limit inspection tests; live daemon command documented |
| Local-process adapter | Implemented-tested | Good, crash, timeout, malformed, missing-readiness, and flood E2E tests |
| JSONL output and strict schema/geometry validation | Implemented-tested | Valid, malformed, non-finite, bounds, and support tests |
| External monotonic timestamping and clock mapping | Implemented-tested | Exact latency fixture and real replay; relative timestamps are shifted before delivery |
| Ground-truth concepts and synthetic public pack | Implemented-tested | Six CC0 families, validated manifests and JSONL |
| Deterministic matching | Implemented-tested | Hungarian global assignment, reorder, tie, class-gate tests |
| Acquisition, coverage, dropout, localization | Implemented-tested | Hand-calculated fixtures and complete golden report |
| Identity and false-track metrics | Implemented-tested | Exact switch and 2.0-second false-track fixtures |
| Occlusion survival and reacquisition | Implemented-tested | Exact 180 ms same-ID test; predicted-output exclusion test |
| Multi-target grouping | Implemented-tested | 1, 4, and 8-target synthetic scenarios plus metric tests |
| Frame drop, blackout, interruption, delay, duplicate hooks | Implemented-tested | Online scenario injection and feed counters |
| External CPU/RAM/process/thread/disk collection | Implemented-tested | Child-process sampler and resource parser tests |
| Docker limits and container stats | Implemented-tested | Requested-versus-applied inspection and parser tests |
| NVIDIA GPU/VRAM collection | Implemented-tested when available | Bounded `nvidia-smi` probe; explicitly unavailable otherwise |
| Structured diagnostic findings | Implemented-tested | Crash, timeout, invalid output, dropout, false-track, reacquisition, latency tests |
| Failure evidence packets | Implemented-tested | Partial input/output, external timestamps, matching decisions, resources, finding, videos when codec exists, reproduction command |
| JSON and static HTML reports | Implemented-tested | Complete golden JSON and HTML renderer coverage |
| Compatible baseline comparison | Implemented-tested | Improvement/regression and incompatible-fingerprint tests |
| Good and intentionally broken systems | Implemented-tested | Novel shifted/new-seed image E2E and broken behavior runs |
| Long-running stability configuration | Implemented-tested | Repeatable concatenated stress benchmark and memory-growth reporting |
| Additional GPU vendors | Architecture only—not implemented | Resource fields/interface permit later collectors; Version 1 requires NVIDIA only |
| Alternate transports and geometry | Architecture only—not implemented | Explicit Version 1 non-goal |
| Cloud control plane/dashboard | Not implemented | Explicit Version 1 non-goal |
