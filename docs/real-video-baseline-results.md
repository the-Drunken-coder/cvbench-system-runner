# Baseline evidence

The same `classical-motion-baseline` revision and system configuration were run through the online socket runner on the real tranche and the existing synthetic benchmark. Full JSON/HTML reports are checked in under [real-video-evidence](real-video-evidence/). The system config SHA-256 is `98baa39d445e5a96b062adad1e8031f155e22b634c325522ac12f1ce1a5ece16`.

| Metric | Real video | Synthetic, same SUT |
| --- | ---: | ---: |
| Delivered frames | 78 | 185 |
| Output records | 1,436 | 143 |
| Matched samples | 51 | 0 |
| Acquisition rate | 1.0000 | 0.0000 |
| Overall observed coverage | 0.6022 | 0.0000 |
| Overall continuity | 0.7634 | 0.0000 |
| Mean IoU | 0.2908 | n/a |
| ID switches | 13 | 0 |
| False detections | 572 | 105 |
| Neutral ignored predictions | 1 | 0 |
| Latency p99 | 163.74 ms | 99.35 ms |

The benchmark fingerprints differ, so this is deliberately marked non-comparable; the runner's comparison gate returns inconclusive rather than inventing score deltas. The behavior contrast is still useful: real footage exposes many foreground false tracks and identity switches, while this deliberately simple motion detector fails to acquire the synthetic contract. The real crowd and camera-motion scenarios are the weakest real cases; the low-light clip achieved full target coverage but still contributed heavy false output.

Preparation and verification:

```bash
python3 scripts/prepare_real_video.py --output data/real-video-v1
python3 scripts/prepare_real_video.py --output data/real-video-v1 --verify-only
```

The preparation toolchain is pinned in `requirements-real-video.lock`; the generated `data/real-video-v1/artifacts.sha256` is verified by the importer and is ignored with the raw media.
