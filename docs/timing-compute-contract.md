# Timing and compute contract

`cvbench.timing-compute/v1` keeps camera truth, delivery pace, and system work as separate clocks. A system may need more time, but slower delivery or completion never changes the native source FPS or hides compute cost.

## Clock model

Every frame has one immutable native timestamp from the scenario. The runner creates a run-scoped source epoch and adds the native relative timestamp without scaling it. Frame metadata also carries `native_source_timestamp_ns`, and each `stream_start` states native frame count, duration, and nominal FPS.

The delivery clock is independent:

```text
scheduled delivery = delivery epoch + native relative timestamp / replay rate
source timestamp    = source epoch   + native relative timestamp
```

The completion clock is external `time.monotonic_ns()` observed by the runner and output collector. This provides:

- startup duration;
- stream-delivery duration;
- bounded post-stream drain duration;
- total wall and completion duration;
- real-time factor = completion seconds / native source seconds;
- source-time latency and processing latency after frame delivery.

Frames stay ordered and progressive. The SUT receives no future frame, scenario annotation, scoring ROI, or ground-truth hint. Its socket remains open across frames so temporal state and multiple cooperating processes are allowed.

## Replay profiles

Benchmark YAML selects `input.replay_profile`, not an arbitrary value:

| Profile | Exact rate | Leaderboard use |
| --- | ---: | --- |
| `quarter-speed` | 0.25 | Separate quarter-speed class |
| `half-speed` | 0.5 | Separate half-speed class |
| `native` | 1.0 | Standard native real-time capability |
| `accelerated-test-20x` | 20.0 | Test-only, ineligible |
| `accelerated-test-100x` | 100.0 | Test-only, ineligible |

For configuration compatibility, `input.playback_rate` is still accepted only when it exactly matches this versioned allowlist. When both fields appear they must agree. The selected profile, exact rate, timing contract, and delivery policy are part of the comparison fingerprint and provenance.

Public `/api/v1` submissions remain compatible and fixed to `native` at 1.0x. The request does not accept a benchmark, replay, environment, or shell override. A slower public category would require a new fixed suite/class assignment; it cannot enter the native class through a request parameter.

## Backpressure and causal output

`cvbench.delivery-lossless/v1` uses an independent source schedule and ordered socket delivery:

- a slow reader never moves source timestamps or changes native FPS;
- ordinary pacing never intentionally drops a frame;
- declared fault-injection drops remain explicit;
- sender calls over 5 ms are transport-pressure samples;
- delivery backlog is measured from the independent scheduled time;
- a frame misses its delivery deadline when completion is later than one scheduled median frame interval, with a 5 ms minimum tolerance;
- portable Unix sockets do not expose a reliable queue-depth value, so queue depth is reported unavailable rather than invented.

Per-frame delivery records retain native timestamp, scheduled offset, sender-call duration, backlog, delivery status, drop reason, and deadline status.
The aggregate reports both configured replay rate and measured effective replay rate (native source seconds delivered per wall second), plus delivered frames per wall second.

Output is causal only when its `(sequence_id, source_timestamp_ns)` names a frame already released by the runner. Guessed future, unknown, or rewritten timestamps are malformed output. Exact outputs may arrive after `benchmark_end` during the bounded drain window and remain scored with their external latency. The hard overall run deadline, `max_drain_seconds`, Docker cleanup, record count, line bytes, total stdout bytes, and output-rate limits remain enforced.

## Container compute accounting

Leaderboard resource evidence is sampled from the Docker container/cgroup, never from SUT self-report. It includes:

- wall, startup, delivery, completion, and drain duration;
- cgroup CPU time and CPU-seconds per native source-second;
- average and peak CPU;
- average and peak RAM;
- disk read/write bytes;
- process count, so child work stays charged;
- output records per native source-second;
- per-output processing latency plus delivery backlog/deadline misses.

Local process-tree measurements remain useful for development but are labeled best-effort and are not leaderboard-authoritative. GPU/VRAM values are omitted unless the runner genuinely assigns and isolates a GPU; host-wide `nvidia-smi` snapshots are not treated as a submitted system's cost.

Sleeping can reduce CPU-seconds, but it increases real-time factor. Background children stay in the container cgroup. Neither tactic improves every raw efficiency axis.

## Leaderboard semantics

`cvbench.pareto/v1` has no composite score. Accuracy metrics remain unchanged. Results expose raw accuracy and efficiency axes plus a class:

```text
<replay profile>/<CPU tier>/<completion tier>
```

CPU tiers use CPU-seconds per native source-second: `cpu-1` (≤1), `cpu-2` (≤2), `cpu-4` (≤4), and `cpu-over-4`. Completion tiers use real-time factor: `realtime` (≤1.05), `completion-2x` (≤2.05), `completion-4x` (≤4.05), and `completion-over-4x`.

Only identical benchmark fingerprints and class IDs are equal-category comparisons. Within a class, one result Pareto-dominates another only when it is no worse on every declared raw axis and strictly better on at least one. Higher accuracy bought with more compute or slower completion therefore remains visible and cannot silently win an equal-budget category.

The fixed public Docker envelope is still 4 CPUs, 2048 MiB RAM, no network, one owner-only socket mount, an unprivileged UID/GID, and an immutable image identity.

## Evidence

The Linux Docker gate runs the same scored acquisition workload as:

- the fast OpenCV baseline;
- a 150 ms/frame CPU-bound system;
- a 150 ms/frame idle system;
- the fast system with a CPU-bound child process.

`scripts/assert_pacing_evidence.py` requires identical accuracy and native source duration, verifies cgroup authority, proves the slow systems' real-time-factor cost, proves sleeping trades CPU for completion time, and proves child CPU/process use remains charged. It publishes the compact `cvbench.timing-compute-evidence/v1` artifact in CI.
