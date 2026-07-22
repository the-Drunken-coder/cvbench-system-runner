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
| Delivered frames | 78 | 186 |
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

The real run was `20260722T073706Z-0ded35c4` with report SHA-256 `b5e2c64b2513f262b52daec169c11a700f1a0353ebfc4ba6fdd6b9fa9b92d64a`; the synthetic run was `20260722T073714Z-3d1386fb` with report SHA-256 `9a9f1749999198c142505734da985e2c72d7e3d080a934d162f3ea40ef8cc07c`. The benchmark fingerprints differ (`d3e01fbea9b045742e244ed7224b957e9b762116ace5a88f6825bbce6053e0da` real versus `1b963ea2421899b6572dca17ad07b6532c73d3c591e331bcb464597e3f8faa37` synthetic), so this is deliberately marked non-comparable; the runner's comparison gate returns inconclusive rather than inventing score deltas. The behavior contrast is still useful: real footage exposes identity switches, incomplete coverage, and background hallucinations inside genuine negative-background space, while this deliberately simple motion detector fails to acquire the synthetic contract. Duplicate target predictions remain identity penalties; legitimate non-target objects are neutral only inside the fixed reviewed ROI and its narrow per-frame ignore boxes, including the trailing Prius in moving-camera frames 20–30.

Preparation and verification:

```bash
scripts/prepare_real_video_container.sh --output data/real-video-v1
scripts/prepare_real_video_container.sh --output data/real-video-v1 --verify-only
```

The preparation toolchain is pinned by the digest-addressed Dockerfile and `requirements-real-video.lock`; the generated `data/real-video-v1/artifacts.sha256` is verified by the importer and is ignored with the raw media. The checked-in 78-JPEG manifest and corpus fingerprint must match.
