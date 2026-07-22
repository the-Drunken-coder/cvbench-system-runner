# Baseline evidence

The classical motion baseline was run end-to-end through the online socket
runner on all three prepared real clips. The full runner artifacts were
written locally to `reports/real-video-v1/20260722T010416Z-9373cc08/` as
`report.json` and `report.html`; those large run directories are ignored.
The compact, ground-truth-free evidence is checked in as
[JSON](real-video-baseline-results.json) and [HTML](real-video-baseline-results.html).

| Metric | Real-video tranche | Existing synthetic reference |
| --- | ---: | ---: |
| Delivered frames | 78 | 185 |
| Matched samples | 51 | 224 |
| Acquisition rate | 1.0000 | 1.0000 |
| Overall observed coverage | 0.5859 | 0.9905 |
| Overall continuity | 0.7778 | 0.9953 |
| Mean IoU | 0.2885 | 0.6496 |
| ID switches | 13 | 0 |
| False detections | 573 | 0 |
| Latency p99 | 153.41 ms | 21.84 ms |

The new failures are concrete: foreground motion and identity pressure reduce
crowd coverage to 0.3333, camera motion/scale reduces coverage to 0.5806, and
the low-light sequence produces many false foreground hypotheses despite full
target coverage. This is the intended weakness the synthetic-only pack did not
expose.
