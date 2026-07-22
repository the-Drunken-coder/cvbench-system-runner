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
| False detections | 328 | 105 |
| Neutral ignored predictions | 97 | 0 |
| Latency p99 | 22.15 ms | 90.55 ms |

The real run was `20260722T064009Z-a223e33f` with report SHA-256 `5829d72c94043fc6dcf9d27b77d871d5053da487ef44e8b7af33735297d96f40`; the synthetic run was `20260722T064018Z-4b7a61c2` with report SHA-256 `0f0e648f62541fcdb13552656584f53fc2f8c10148f529cdbaf142bb64030470`. The benchmark fingerprints differ (`f36540e5a0038dfe873462a86cb540e28f52ca80b3db8a74884b776be9e06b5d` real versus `08205802398fa9dffa29f10fab3ce24bc8976e2eadea7975d09626f7c6a58587` synthetic), so this is deliberately marked non-comparable; the runner's comparison gate returns inconclusive rather than inventing score deltas. The behavior contrast is still useful: real footage exposes identity switches, incomplete coverage, and background hallucinations inside genuine negative-background space, while this deliberately simple motion detector fails to acquire the synthetic contract. Duplicate target predictions remain identity penalties; legitimate non-target objects are neutral only inside the fixed reviewed ROI and its narrow per-frame ignore boxes, including the trailing Prius in moving-camera frames 20–30.

Preparation and verification:

```bash
scripts/prepare_real_video_container.sh --output data/real-video-v1
scripts/prepare_real_video_container.sh --output data/real-video-v1 --verify-only
```

The preparation toolchain is pinned by the digest-addressed Dockerfile and `requirements-real-video.lock`; the generated `data/real-video-v1/artifacts.sha256` is verified by the importer and is ignored with the raw media. The checked-in 78-JPEG manifest and corpus fingerprint must match.
