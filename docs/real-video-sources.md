# Real-video full-frame MOT corpus v2

`real-video-v2` replaces the retired Commons clips and sparse designated-target scoring. It is public evaluation data: three exact, consecutive, native-cadence sequences with exhaustive full-frame tracking annotations. It is not model training data unless a participant separately chooses to train on this public calibration material.

Submitted systems receive progressive JPEG frames and timestamps over `frame_socket_v1`. They never receive annotations, target boxes, masks, regions of interest, future frames, or hints about which object to follow. Viewer overlays are public human-inspection aids and are never sent to submitted systems.

## Source and redistribution basis

All replacement sequences come from the Multiview Extended Video with Activities (MEVA) Known Facility 1 dataset by Kitware Inc. and the Intelligence Advanced Research Projects Activity (IARPA). The [primary MEVA README](https://mevadata.org/resources/README-meva-kf1-data.html) identifies the public S3 corpus and its structure. The [primary MEVA data license](https://mevadata.org/resources/MEVA-data-license.txt) licenses the dataset under CC BY 4.0, permitting sharing and adaptation with attribution and modification notice.

Video sources are checksum-pinned r13 ground-camera files. Annotation sources are checksum-pinned `.geom.yml` and `.types.yml` records at Kitware repository commit `421841a75577b697c314e952e585aecbb1b99e17`. The upstream geometry provides a bounding box on every labeled frame, a class, and a stable activity-track ID. CVBench does not derive benchmark truth from an unlabeled ordinary video.

| Scenario | Upstream sequence and inclusive frames | Native cadence | Output | Physical tracks | Label rows |
| --- | --- | ---: | --- | ---: | ---: |
| `rvmot-a1c9` | `2018-03-05.13-15-00.13-20-00.bus.G340.r13.avi`, 2813–2962 | 30 FPS | 150 frames, 896×504, 4.967 s | 3 (2 person, 1 vehicle) | 354 |
| `rvmot-b7e2` | same G340 sequence, 3039–3188 | 30 FPS | 150 frames, 896×504, 4.967 s | 3 (2 person, 1 vehicle) | 360 |
| `rvmot-c4f6` | `2018-03-05.13-20-01.13-25-01.school.G328.r13.avi`, 3272–3421 | 30 FPS | 150 frames, 896×500, 4.967 s | 7 (4 person, 3 vehicle) | 680 |

All image pixels are scoreable. There are no real-video ignore rows and no scoreable ROI. Parked background objects that never move or participate during the clip are outside the explicit moving-object ontology; any person, vehicle, or dog that is visible and moves or participates during the sequence is labeled for its complete visible span.

## Ontology and visibility

The small closed ontology is:

- `person`: a human, including a temporarily stationary person within an active track;
- `vehicle`: a powered road vehicle, including a temporarily stopped vehicle within an active track;
- `dog`: a dog when present. None is present in these three sequences.

Boxes use output-image `xyxy` pixels. `truncated=true` means the object continues beyond the image boundary. `occlusion=partial` means another supported object visibly covers part of it; such rows remain detection-eligible. `visibility_fraction` is 1.0 when fully visible, 0.8 for boundary truncation, 0.7 for partial supported-object occlusion, and 0.55 when both apply. Full occlusion would remain part of the stable track with `on_screen=false` and would not be detection-eligible; no selected span contains a fully occluded interval.

## Audited corrections

MEVA activity annotation can assign more than one upstream activity ID to the same physical object. The committed `CLIPS[*].track_groups` mapping in `scripts/prepare_real_video.py` is the machine-auditable correction record. When duplicate source rows exist on one frame, CVBench takes the coordinate-wise median after visually confirming they refer to the same object. Distinct overlapping people remain distinct (notably `rvmot-c4f6` `p-001` and `p-003`). No frame, box, or identity is temporally interpolated.

Six committed 25-frame review sheets per scenario cover all 450 output frames. They were inspected against the source pixels after consolidation. The pinned annotation hashes, source-ID mapping, frame ranges, output transform, and review-sheet names are also emitted in `data/real-video-v2/provenance.json` during preparation. This evidence makes every normalization and correction reproducible without exposing any private scorer artifact—these annotations are already public.

## Deterministic preparation

Build the pinned linux/amd64 preparation image and recreate the corpus:

```sh
docker build --platform linux/amd64 -f examples/Dockerfile.real-video-prep -t cvbench-real-video-prep:v2 .
CVBENCH_REAL_VIDEO_PREP_IMAGE=cvbench-real-video-prep:v2 scripts/prepare_real_video_container.sh --output data/real-video-v2
CVBENCH_REAL_VIDEO_PREP_IMAGE=cvbench-real-video-prep:v2 scripts/prepare_real_video_container.sh --output data/real-video-v2 --verify-only
```

Preparation preserves all 150 consecutive source frames per scenario and the native 30 FPS rational timestamps. It performs one aspect-preserving downscale, then deterministic JPEG quality-78 encoding. It never duplicates, interpolates, or skips frames. `artifacts.sha256` binds the runtime corpus; `scenarios/real-video-v2/expected-frame-sha256.txt` binds all 450 published JPEGs.

## Scoring and execution

`benchmarks/real-video-v2.yaml` scores the isolated real tranche. `benchmarks/public-whole-system-v2.yaml` is the versioned public submission suite and contains all 13 synthetic scenarios plus these three real scenarios. Public v1 API submissions do not choose a hidden or different suite: the queued record, lease, report, catalog, contract, and OpenAPI identify `public-whole-system-tracking` version `2.0.0` and its 16 scenario IDs.

Real-video scoring is class-aware and full-frame. HOTA is reported across IoU thresholds 0.05–0.95 and IDF1 uses class-aware IoU 0.5 identity matching. Reports also retain misses, false detections and tracks, ID switches, fragmentation, completeness/coverage, duplicate tracks, localization, latency, resource use, and isolation evidence. There is no target-first or ignore matching in the real scenarios because every supported mover is scoreable.

All current scenarios are public and can be tuned to or memorized. Runtime network isolation and progressive delivery prevent host-data and future-frame access during a run; they do not make public evaluation data secret.
