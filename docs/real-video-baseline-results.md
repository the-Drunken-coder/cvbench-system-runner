# Baseline evidence

The same `classical-motion-baseline` revision and system configuration were run through the online socket runner on the real tranche and the existing synthetic benchmark. Full JSON/HTML reports are checked in under [real-video-evidence](real-video-evidence/). The system config SHA-256 is `98baa39d445e5a96b062adad1e8031f155e22b634c325522ac12f1ce1a5ece16`.

| Metric | Real video | Synthetic, same SUT |
| --- | ---: | ---: |
| Delivered frames | 78 | 185 |
| Output records | 947 | 143 |
| Matched samples | 53 | 0 |
| Acquisition rate | 1.0000 | 0.0000 |
| Overall observed coverage | 0.6344 | 0.0000 |
| Overall continuity | 0.8065 | 0.0000 |
| Mean IoU | 0.2820 | n/a |
| ID switches | 13 | 0 |
| False detections | 329 | 105 |
| Neutral ignored predictions | 96 | 0 |
| Latency p99 | 43.00 ms | 100.20 ms |

The benchmark fingerprints differ (`c1bc8bf02139dfae092c1c6669c7b13e2070e8a539e7dde6b0d74db2eca1c157` real versus `08205802398fa9dffa29f10fab3ce24bc8976e2eadea7975d09626f7c6a58587` synthetic), so this is deliberately marked non-comparable; the runner's comparison gate returns inconclusive rather than inventing score deltas. The behavior contrast is still useful: real footage exposes identity switches, incomplete coverage, and background hallucinations inside genuine negative-background space, while this deliberately simple motion detector fails to acquire the synthetic contract. Duplicate target predictions remain identity penalties; legitimate non-target objects are neutral only inside the fixed reviewed ROI and its narrow ignore boxes.

Preparation and verification:

```bash
scripts/prepare_real_video_container.sh --output data/real-video-v1
scripts/prepare_real_video_container.sh --output data/real-video-v1 --verify-only
```

The preparation toolchain is pinned by the digest-addressed Dockerfile and `requirements-real-video.lock`; the generated `data/real-video-v1/artifacts.sha256` is verified by the importer and is ignored with the raw media. The checked-in 78-JPEG manifest and corpus fingerprint must match.
