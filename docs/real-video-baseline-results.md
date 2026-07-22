# Baseline evidence

# Reporting boundary

The authoritative Docker report and the private runner-to-Worker callback retain
the bounded `cvbench.audit/v1` evidence needed for authenticated operator review.
The public submission summary deliberately omits that evidence. Before GitHub CI
upload, `scripts/sanitize_ci_report.py` writes a separate safe report copy with
aggregate scores, provenance, hashes, resource, and isolation evidence intact;
only that copy, its resources CSV, and the checksum manifest are public. The safe
copy records `cvbench.audit/v1-redacted` and contains no bounding boxes, raw JSONL,
local paths, secrets, media, or ground-truth payload.

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
| Latency p99 | 34.82 ms | 100.35 ms |

The real run was `20260722T103200Z-176ee432` with report SHA-256 `b34d12c4e95d91d5cd94bb4bd0b085cc1bd8a7d8420c188cf180dbcceca78701`; the synthetic run was `20260722T103200Z-9b581b84` with report SHA-256 `da3fbc84a4498ab4dd2426d4251b4fab959dbb925d7aaf90e38b1b6586822afb`. The benchmark fingerprints differ (`6e18e89020104547168ed590732d531e71f7f57437836f549f62e34218f23798` real versus `8a9b95b57027b529acbdbe12477cee237654b3c9c177c03febe603c0369ed294` synthetic), so this is deliberately marked non-comparable; the runner's comparison gate returns inconclusive rather than inventing score deltas. The behavior contrast is still useful: real footage exposes identity switches, incomplete coverage, and background hallucinations inside genuine negative-background space, while this deliberately simple motion detector fails to acquire the synthetic contract. Duplicate target predictions remain identity penalties; legitimate non-target objects are neutral only inside the fixed reviewed ROI and its narrow per-frame ignore boxes, including the trailing Prius in moving-camera frames 20–30.

Preparation and verification:

```bash
scripts/prepare_real_video_container.sh --output data/real-video-v1
scripts/prepare_real_video_container.sh --output data/real-video-v1 --verify-only
```

The preparation toolchain is pinned by the digest-addressed Dockerfile and `requirements-real-video.lock`; the generated `data/real-video-v1/artifacts.sha256` is verified by the importer and is ignored with the raw media. The checked-in 78-JPEG manifest and corpus fingerprint must match.
