# Deterministic metrics

For each `(sequence_id, source_timestamp_ns)`, ground-truth targets and track records are sorted by stable IDs. The matcher constructs a gated cost matrix. A pair is eligible when classes are compatible and either IoU meets `minimum_match_iou` or center distance is within `max_match_center_error_px`. A deterministic Hungarian assignment minimizes `1 - IoU` with a small center-distance and stable-index tie break.

The report keeps categories separate:

- Acquisition: eligible/acquired/never acquired, rate, exact per-target latency, percentiles, and configurable deadlines.
- Coverage and continuity: frame-duration-weighted observed coverage versus observed-or-predicted continuity, by target, scenario, and visibility.
- Visible dropouts: contiguous eligible-visible intervals without a correct observed match, above the declared tolerance.
- Localization: IoU, pixel and normalized center error, size error, visibility groups, and size groups.
- Identity: switches, fragments, duplicates, merges/splits, and normalized switch rate.
- False output: unmatched detections, births, persistence, track-seconds, confidence, and scenario.
- Robustness: controlled loss intervals, correct and same-ID reacquisition, gap duration, and latency.
- Latency: externally measured first and per-update values, percentiles, deadline misses, count groups, and time series.
- Resources: CPU, time, RAM, disk, network where available, process/thread count, GPU/VRAM where NVIDIA tooling exists, and memory growth.

No overall score is computed. Baseline comparison labels improvement, regression, unchanged, or inconclusive and exposes sample count/confidence.

## Time semantics

- A tracking update is fresh only for the exact authoritative source timestamp it names. It is never carried forward as a visual observation.
- Sample intervals are half-open: `[timestamp_i, timestamp_i+1)`. At EOF, the final sample is extended by the median positive cadence from that sequence; a one-frame sequence has zero inferred duration.
- A dropout starts at the first eligible interval without a correct observed update and ends at the next fresh observed update. Durations equal the sum of those half-open intervals.
- Percentiles use sorted samples with linear interpolation at `(n - 1) * p`.
- Predicted/coasting matches contribute to continuity and occlusion survival only. They never contribute to observed coverage, acquisition, or reacquisition.
- Output at a timestamp absent from the scenario is stale/unmatched, even if its box overlaps a target from another frame.
