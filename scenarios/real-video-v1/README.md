# Real-video manifests

The three `scenario.yaml` files in this directory are checked-in contracts;
their frame and annotation paths point to the ignored, checksum-verified
prepared assets under `data/real-video-v1/`. Run
`scripts/prepare_real_video_container.sh --output data/real-video-v1` before
validation or scoring. The importer regenerates these manifests
deterministically and verifies `data/real-video-v1/artifacts.sha256`. Raw
media, prepared frames, and annotations remain ignored. The model container
receives only the owner-only frame socket; repository paths, media, and
ground truth are not mounted. Each selected frame has only narrow, reviewed
ignore boxes around visible non-target objects; genuine background remains
scoreable. Deterministic review contact sheets for all clips live under each
clip's `review/` directory. Scoreable target matches are resolved first, and
only then are unmatched predictions neutralized using
intersection-over-prediction-area.
