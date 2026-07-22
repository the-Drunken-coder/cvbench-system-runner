# Deterministic metrics

For each `(sequence_id, source_timestamp_ns)`, ground-truth targets and track records are sorted by stable IDs. The matcher constructs a gated cost matrix. A pair is eligible when classes are compatible and either IoU meets `minimum_match_iou` or center distance is within `max_match_center_error_px`. A deterministic Hungarian assignment minimizes `1 - IoU` with a small center-distance and stable-index tie break.

The report keeps categories separate:

- Acquisition: eligible/acquired/never acquired, rate, exact per-target latency, percentiles, and configurable deadlines.
- Coverage and continuity: frame-duration-weighted observed coverage versus observed-or-predicted continuity, by target, scenario, and visibility.
- Visible dropouts: contiguous eligible-visible intervals without a correct observed match, above the declared tolerance.
- Localization: IoU, pixel and normalized center error, size error, visibility groups, and size groups.
- Identity: switches, fragments, duplicates, merges/splits, and normalized switch rate.
- False output: unmatched detections, births, persistence, track-seconds, confidence, and scenario.
- Ignore/unlabeled annotations are matched only after scoreable targets have been assigned. An unmatched observed
  prediction with IoU at least the benchmark's locked `ignore_match_iou` threshold (0.5 in real-video-v1) is neutral,
  excluded from every false-detection denominator, and reported separately as `neutral_ignored_predictions`.
- Robustness: controlled loss intervals, correct and same-ID reacquisition, gap duration, and latency.
- Latency: externally measured first and per-update values, percentiles, deadline misses, count groups, and time series.
- Resources: CPU, time, RAM, disk, network where available, process/thread count, GPU/VRAM where NVIDIA tooling exists, and memory growth.
- Long-running stability: track-ID cardinality/exhaustion signals, cross-stream state contamination, first/second-half false-positive rate, interruption recovery, latency drift, memory growth, and declared assertion results. Assertions with unavailable evidence report `evaluated: false` and `passed: null`; they are never coerced to zero or counted as failures.

## Track-ID lifecycle

Within one sequence, a Version 1 track ID is permanently bound to its first deterministically matched physical target. Repeated observations and reacquisition of that same target remain legitimate, including after a `track_ended` or `lost` event. Assigning the ID to a different physical target is a reuse event: it is reported as `reuse_after_terminal` when the prior lifecycle was closed, or `active_target_alias` when the prior lifecycle was still active. Track-ID exhaustion is derived from these matched lifecycle violations rather than SUT diagnostic wording.

No overall score is computed. Baseline comparison labels improvement, regression, unchanged, or inconclusive and exposes sample count/confidence.

## Time semantics

- A tracking update is fresh only for the exact authoritative source timestamp it names. It is never carried forward as a visual observation.
- Sample intervals are half-open: `[timestamp_i, timestamp_i+1)`. At EOF, the final sample is extended by the median positive cadence from that sequence; a one-frame sequence has zero inferred duration.
- A dropout starts at the first eligible interval without a correct observed update and ends at the next fresh observed update. Durations equal the sum of those half-open intervals.
- Percentiles use sorted samples with linear interpolation at `(n - 1) * p`.
- Predicted/coasting matches contribute to continuity and occlusion survival only. They never contribute to observed coverage, acquisition, or reacquisition.
- Output at a timestamp absent from the scenario is stale/unmatched, even if its box overlaps a target from another frame.
